# Old 3DS engine performance: summary (2026-10-15/16)

Branch `o3ds-engine-perf` (a separate worktree; merged into main, docs/o3ds-merged-build.md),
on top of a baseline commit of the main checkout's working tree (low-poly
overlay, profiler work). Goal: Old 3DS from about 23 FPS to a steady 30 FPS
with byte-identical simulation. No Old 3DS hardware was available; every
number below comes from the worktree's Azahar copy in Old 3DS mode, plus a
hardware model stated explicitly. Details: `docs/o3ds-perf-log.md` (every
change) and `docs/o3ds-perf-research-log.md` (sources, reasoning, backlog).

## Verification applied to every change

- Physics replay test (Funky Kong ghost, Luigi Circuit, race timer and three
  lap splits, two finishes, identical heap): passed for every change.
- CPU racer log: a scripted 12-racer VS race (11 CPU racers, player driven
  by the CPU route, 1500 steps) logs position, speed, rank, lap, items,
  status and AI state of every racer every 30 steps (920 lines). Identical to
  the baseline build for every change, with and without profiling, on Old
  and New 3DS settings.
- 52 lockstep race captures (top and lower screen) pixel-identical to the
  baseline for every change, including mid-race layout switching (change 9)
  and in New 3DS mode for the final build. Changes 13 and 14 also passed
  152/152 captures taken every 20 steps, on Old and New 3DS settings; a
  mutation build without tubes changes 14 of those captures, so they cover
  the shell tails and drafting effects.
- Host tests: `tools/test_core_calibration.cpp`; `tools/test_wrap_angle.cpp`
  (bit-identical to `std::remainder` for every float in [−5π, 5π]).

## What changed

| # | Change | Old 3DS only? |
| --- | --- | --- |
| 1 | Race GPU command building on the main core (immediate mode) | yes |
| 2 | QA: simulation time split by system (`sim_*` columns) | QA only |
| 3 | Presentation jobs (object recording, posing) inline on the main core | yes |
| 4 | `-fno-math-errno` (no sqrt NaN check stalls) | both, exact |
| 5 | Render profiler scopes stop making kernel calls unless profiling | both, exact |
| 6 | `VehicleAnimation`: wheel/accessory matrices only when needed; no `sin 0`/`cos 0` | both, exact |
| 7 | Link-time optimization for app code as well as Kinoko | both, exact |
| 8 | citro3d: dirty-uniform bitmask, inline shadow compares (patch regenerated) | both, exact |
| 9 | Per-race core-layout calibration during the countdown (picks the fastest of three layouts on the console itself) | yes |
| 10 | `VehicleAnimation`: constant arm rotations and shared steering rotation reused | both, exact |
| 11 | Exact 2π remainder for wheel roll (no newlib `fmodf`) | both, exact |
| 12 | Profile-guided optimization (`pgo/race-profile.tar.gz`, `tools/o3ds_pgo_profile.py`) | both, exact |
| 13 | Tube particle stripes (shell tails, slipstream) make each vertex ring once per pass | both, exact |
| 14 | Tube stripes drawn as GPU triangle strips (3× fewer vertices built, uploaded and shaded) | both, exact |

Nothing under `vendor/kinoko` changed (the worktree's Kinoko copy still
equals upstream + `patches/kinoko-arm11.patch`). No visual quality was traded:
all captures are pixel-identical.

## Measured effect (emulated ARM11 ms per presented frame)

Scripted Luigi Circuit race, lockstep, QA log frames excluded:

| | Main core mean | Main core p90 | Core 1 (render thread + jobs) |
| --- | --- | --- | --- |
| Baseline | 5.34 | 7.08 | 4.47, plus ~0.95 audio |
| After 1+3 (work moved to main core) | 8.01 | 9.94 | 0, audio only |
| Final (main-only layout) | 6.87 | 8.37 | 0, audio only |

Changes 4–14 cut main-core work 14% on average (8.01 → 6.87) and 16% at
p90 (9.94 → 8.37). Kinoko's step fell 2.64 →
2.26 (LTO, PGO), draw-call CPU cost fell 15% (citro3d), opponent posing fell
45% and per-tick opponent presentation 21%.

## Estimated Old 3DS frame time against 23 → 30 FPS

Model (`docs/o3ds-perf-research-log.md`): the system core lends an
application only part of its time (usually reported as 30%); R is hardware
time ÷ emulated time (the emulator counts instructions, not VFP latency or
cache misses). The baseline's 43 ms (23 FPS) is matched by core 1 being the
bottleneck with R ≈ 2.4 at a 30% share. Core 1 was the bottleneck in every
(share, R) pair that reproduces 43 ms.

| Scenario | Baseline frame | Final frame (mean / p90) | FPS (capped at 30) |
| --- | --- | --- | --- |
| 30% share, R = 2.4 (fits 23 FPS) | 43 ms (core 1) | 16.5 / 20.1 ms (main core) | 30, ~13 ms headroom |
| 40% share, R = 3.2 | 43 ms | 22.0 / 26.8 ms | 30 |
| 50% share, R = 4.0 | 43 ms | 27.5 / 33.5 ms | 30; heaviest frames at the limit |

Savings the emulator cannot show come on top: ~580 fewer kernel calls per
frame (change 5, ~0.3 ms), no sqrt status stalls (change 4), 11% less code
for the 16 KB instruction cache (change 12), and ~170 fewer software
sin/cos/fmod calls per frame (changes 6, 10, 11).

If the model is wrong and core 1 is generous, change 9 keeps the original
render-thread layout on that console, so the result is never slower than the
baseline layout.

## How to confirm on a real Old 3DS

1. Build the QA 3DSX (`tools/build.py --external-assets`), put
   `performance.txt` in `sdmc:/3ds/mkw-3ds/`, and play or autoplay a race.
2. `runtime.log` records the calibration, for example
   `race cores chose main-only; main-core ms per frame: main-only 18.40,
   main+worker 19.10, render-thread 41.70`. Those three numbers are the
   hardware answer to this model.
3. `performance.csv`: `interval_ms` per frame (33.3 = 30 FPS on Old 3DS),
   with the `sim_*` and drawing columns per stage.
4. A/B a single layout with `o3ds-main-only.txt`, `o3ds-core1-jobs.txt` or
   `o3ds-render-thread.txt`; `MKW_PGO=off` builds without the profile.
   `tools/compare_o3ds_performance.py` compares paired hardware captures.

## Not done (ranked backlog)

1. Particle effects drive the heaviest frames: frames with live particles
   spend +1.1 ms building and uploading NW4R draw lists and +0.44 ms
   simulating them (emulated). Two tube-shaped stripes produce almost all of
   the vertices: `rk_kouraTailG` (green shell tails, ~630 vertices per active
   frame) and `rk_slipStream` (drafting wind, ~980); every other effect stays
   under ~40 per frame. Tubes are built twice (inside, then outside pass) and
   expanded from strips into triangle lists (3 vertices per strip vertex).
   Changes 13 and 14 now build each ring once and draw tubes as triangle
   strips (−20% for particle frames). What remains is the per-particle TEV
   register and matrix work and the particle simulation (+0.44 ms); on Old
   3DS, fewer tube sides would also work but changes their look.
2. Packed per-prism KCL records (vertex + normals in one cache line) for
   Kinoko's collision tests: same arithmetic, fewer FCRAM misses; needs
   hardware to measure.
3. Presentation-only approximate sin/cos/atan2 in `VehicleAnimation`
   (~0.3 ms on hardware; changes low bits, so captures would need a
   tolerance check).
4. Old 3DS LOD options that change visuals (not taken): half-rate
   posing for far opponents, 15 Hz lower-screen map.
5. Cached GPU command list for the static course draw (`GPUREG_CMDBUF_JUMP`).
6. Regenerate `pgo/race-profile.tar.gz` (`tools/o3ds_pgo_profile.py`) after
   large code changes, and from more courses.

## Merging into the main checkout

Commits are on `o3ds-engine-perf` only (nothing pushed). Files touched outside
docs/tools: `src/main.cpp`, `src/render_thread.cpp`, `src/racer_visuals.cpp`,
`src/course_objects.cpp`, `src/vehicle_animation.cpp`,
`include/mkw/{job_worker,render_profile,runtime_profile,vehicle_animation}.hpp`,
new `include/mkw/{core_calibration,wrap_angle}.hpp`, `tools/build.py`,
`patches/citro3d-hardware.patch` (apply to `vendor/citro3d`), and
`pgo/race-profile.tar.gz`. None of the low-poly work's files
(`model_variant.hpp`, `model_detail.hpp`, `Model::load`, rival loading,
`cloneModel`, skin compaction calls) were modified.
