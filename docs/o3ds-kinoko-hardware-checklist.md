# Old 3DS hardware checklist: Kinoko physics branch

Everything on branch `o3ds-kinoko-arm11` was verified in Azahar
(Old 3DS mode) and on the host harness; no console was available. These runs
replace the model's assumptions with measurements. Each takes one race.

## Build

    .venv/bin/python tools/build.py --external-assets

Copy `build/mkw-3ds.3dsx` to `sdmc:/3ds/mkw-3ds/` (or run it over 3dslink).
Every file below is a marker in `sdmc:/3ds/mkw-3ds/`; an empty file is enough.

## 1. Does the two-core kart step help? (10 minutes)

1. Put `performance.txt` on the card. Play (or autoplay with
   `menu-autoplay.txt`, as `tools/o3ds_perf_run.py` does) one VS race on
   Luigi Circuit with 12 racers, Old 3DS.
2. `runtime.log` ends with one line starting `sim worker:`. Read:
   - `calib_phased_ms` and `calib_parallel_ms`: the main-core time of one
     simulation tick in each mode, measured during the countdown (median of 6
     settled ticks, 3 rounds each). `mode=` is what the race then used.
   - `worker_karts` vs `main_karts`: how many kart phases core 1 actually ran.
     On a console where the system core lends little time this stays small
     and the calibration keeps the phased (main-core) step.
   - `wait_ms` / `max_wait_us`: time the main core spent waiting for a kart
     the worker had claimed but not finished (preemption of core 1 mid-kart).
     If `max_wait_us` is in the thousands, the system core is taking the
     worker away mid-kart; `docs/o3ds-kinoko-arm11.md` section 7 explains the
     2 ms × limit bursts.
3. `performance.csv`: `simulation_ms` per presented frame against the same
   race with `o3ds-kinoko-reference.txt` on the card (retail order, no
   worker). The difference is the hardware gain of this branch's kart step.
4. Repeat with `o3ds-kinoko-phased.txt` (reordered step, main core only) to
   separate the reorder's cost from the worker's gain.

## 2. Is it bit-identical on hardware? (5 minutes)

1. Put `sim-hash.txt` and `cpu-log.txt` (content `30`) next to
   `menu-autoplay.txt` (the scripted race), run once with
   `o3ds-kinoko-reference.txt` and once without it.
2. `sim-hash.bin` (8 bytes per simulation tick) must be identical between the
   two runs, and identical to the emulator's run of the same scripted race
   (`build/runs/<label>/sim-hash.bin`). `runtime.log`'s `QA cpu` lines must
   also match line for line.
3. `replay-test.txt` + `lc.rkg` (the Funky Kong ghost): `replay-result.json`
   must say `"pass": true` with `physics_steps` 9064 in both modes.

## 3. Hardware numbers the cost model needs (2 minutes)

Put `o3ds-memtest.txt` on the card and start the app. Before the menu it
runs, on the main core, and writes `memtest-result.json`:

- `chase_ns_<size>`: pointer-chase latency through a random cycle inside a
  buffer of 8 KB (L1 hit), 64 KB, 1 MB and 8 MB (FCRAM). The 8 MB number is
  the L1-miss cost the model's `FCRAM_LATENCY` should use.
- `fmacs_dep_cycles`, `fmacs_indep_cycles`: cycles per `fmacs` in a dependent
  chain and in eight independent chains (expected about 8 and 1).
- `fdivs_cycles`, `fsqrts_cycles`: expected about 19.
- `fcmp_branch_cycles`: compare of a fresh result, `fmstat`, branch.
- `vector_fmacs_cycles`: one `fmacs` with FPSCR.LEN = 4 (short vectors), to
  learn whether VFP11 vector mode runs faster than four scalar ops.
- `tick_call_cycles`: cost of one `svcGetSystemTick` (the profiler's unit).

Send `memtest-result.json` back; `tools/o3ds_vfp11_model.py --hardware
memtest-result.json` rescales the modelled times.

## 4. Feel and visuals

Nothing in this branch changes rendering, timing or input; if anything looks
or feels different between `o3ds-kinoko-reference.txt` and the default, that
is a bug: note the course and the step from `runtime.log`.
