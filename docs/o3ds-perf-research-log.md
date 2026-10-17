# Old 3DS engine performance: research log

Dated findings from the engine work (branch `o3ds-engine-perf`).
Each block lists sources, what it means for this engine, and the backlog it
feeds. Findings are applied only after the feature in progress is verified.

## 2026-10-15 ~23:15 BST: where Old 3DS frame time goes

### Sources

- 3dbrew, [APT:SetApplicationCpuTimeLimit](https://www.3dbrew.org/wiki/APT:SetApplicationCpuTimeLimit):
  the limit ranges 5–89% (maximum set by the ExHeader), and "setting a value
  higher than 30% does not seem to improve performance on Old 3DS, however it
  definitely does on New 3DS".
- 3dbrew, [Multi-threading](https://www.3dbrew.org/wiki/Multi-threading): no
  time-slice scheduling; a thread is preempted only when a higher-priority
  thread wakes. Core 1 needs a time limit before an application may use it.
- Rodrigo Copetti, [Nintendo 3DS Architecture](https://www.copetti.org/writings/consoles/nintendo-3ds/):
  "syscore can lend 30% of its execution time to user applications"; Old 3DS
  has 268 MHz MP11 cores with 16 KiB I + 16 KiB D L1 each and **no L2** (the
  2 MiB L2 is New 3DS only); PICA200 has four vertex processors, a small
  post-transform cache, 256 B L1 per texture unit and 8 KiB texture L2;
  vertex data in a VRAM buffer is "the most efficient".
- GBAtemp wiki, [3DS Homebrew](https://wiki.gbatemp.net/wiki/3DS_Homebrew):
  homebrew is "usually limited to ... about 30% of the system core".
- Upstream project [MiniBandit/MKW-3DS PR #15](https://github.com/MiniBandit/MKW-3DS/pull/15)
  (merged 2026-10-11): render-thread starvation on Old 3DS core 1 by the job
  worker's priority; it was fixed by equal priorities and timed waits, with no
  hardware FPS figures.

### What the emulator shows (baseline, Luigi Circuit, 12 racers, lockstep)

Azahar counts emulated ARM11 time per guest instruction; it does not model
cache misses, VFP latency or the core-1 time cap. Per presented frame:

| Work | Core | Emulated ms |
| --- | --- | --- |
| Simulation, 2 ticks (Kinoko + AI + items + per-tick presentation) | 0 | 4.19 |
| Recording the frame (culling, posing waits, command copies) | 0 | 1.47 |
| Render thread replay (citro3d command building) | 1 | 4.19 |
| Job worker (opponent and player posing) | 1 | 0.26 |
| Audio mixing (0.14 ms per 4.9 ms DSP frame) | 1 | ~0.95 |

### Interpretation

Real hardware runs about 23 FPS (43 ms). Let R be hardware time divided by
emulated time for the same code (CPI and memory stalls; plausibly 2–5 on an
L2-less ARM11). If core 0 limited the frame, R would have to be about 7.6.
If the system core's ~30% share limited it, core-1 work of 5.4 emulated ms
needs only R ≈ 2.4 to take 43 ms of wall time. The second case fits a
realistic CPI, the published 30% figure and the upstream starvation report.
Moving the replay to core 0 gives about 9.5 emulated ms there, or 23–29 ms at
R = 2.4–3, inside the 33.3 ms budget.

### Backlog (ranked)

1. **Old 3DS: build race GPU commands on core 0** (immediate mode), leaving
   core 1 to audio and small posing jobs. Largest expected gain.
2. Split the simulation profile column into Kinoko, AI/items and per-tick
   presentation, so later core-0 work targets the real hot spots.
3. Cut citro3d per-draw CPU cost: redundant projection/modelview/palette
   uniform uploads, texenv rebuilds and per-draw attribute setup (82 draws per
   frame).
4. Per-tick presentation work (particles, kart effects, 11 opponent
   controllers) at 60 Hz: check for redundant work without changing visuals.
5. Static course geometry in VRAM (vertex fetch) and smaller vertex formats.
6. Command-list reuse for static draws (track, sky).

## 2026-10-15 ~23:50 BST: after moving work off core 1

### Sources

- N. Burgess, [Design of the ARM VFP11 Divide and Square Root Synthesisable Macrocell](https://www.lirmm.fr/arith18/papers/burgessn-divider.pdf)
  and [slides](https://www.lirmm.fr/arith18/slides/burgessn-divider.pdf):
  VFP11 divide/sqrt latency 15 cycles (single), FMAC issue every cycle with
  8-cycle result latency; divide/sqrt run in parallel with other VFP work.
- FFmpeg developers, [ARM VFP notes](https://ffmpeg.org/pipermail/ffmpeg-devel/2008-April/040786.html):
  4-cycle VFP loads, 8-cycle arithmetic; scheduling around stalls matters.
- Fan documentation on the series ([Supper Mario Broth](https://www.suppermariobroth.com/post/811191047543734272/modelsspriters-resourcecomgamecubemkdoubledash/amp)):
  Mario Kart games swap racers for low-quality models at distance. No primary
  source for Mario Kart DS specifics was found.

### Consequences

- Azahar charges one cycle per instruction, so dependent VFP chains (Kinoko
  physics, animation, matrix code) are underestimated by up to 8×, and
  divide/sqrt by 15×. R (hardware ÷ emulated time) may exceed 3 for core 0.
- Every (core-1 share, R) pair that reproduces the measured 43 ms before
  change 1 makes core 1 the bottleneck: core-1 work (5.4 emulated ms) was as
  large as core-0 work (5.7), and core 1 gives an application at most 89%
  of its time, probably about 30%. So changes 1–3 remove the old bottleneck
  in every consistent scenario.
- After change 3, core 0 (8.36 emulated ms) sets the frame: 20 ms at R = 2.4,
  33 ms at R = 4. To hold 30 FPS across the plausible range, core-0 work
  should drop to about 7 emulated ms or less.
- GCC's `vsqrt` NaN check (`vcmpe; vmrs; bmi sqrtf`) appears at 399 sites;
  `vmrs` waits for the 15-cycle sqrt. `-fno-math-errno` removes the check
  without changing results (default-NaN mode is on).
- The GDB-stub PC sampler lands on block entry points (samples pile onto
  function entries such as a 4-instruction accessor), so it points at
  neighbourhoods; the in-game emulated-time profile stays the measure.

### Backlog (re-ranked)

1. Opponent presentation LOD on Old 3DS (half-rate posing/effects for far or
   small racers, as the series' distant-racer models do); opponents cost
   about 1.5 emulated ms per frame in total.
2. `-fno-math-errno` (safe, small).
3. Lower per-draw citro3d CPU cost (uniform scan, texture rebinds, attribute
   and buffer rebinds when unchanged).
4. Bottom-screen map at half rate on Old 3DS.
5. Kinoko object collision (`ObjectCollisionConvexHull::transform` from
   `KartCollide::calcObjectCollision`): look for exact-result savings.
6. Course-object animation at reduced rate for distant objects.

## 2026-10-16 ~00:30 BST: inside Kinoko's step (temporary probes, not committed)

Kinoko's `calcEngines` is ~99% `KartObjectManager::calc`: 1.1–1.46 emulated
ms per tick for 12 karts. Per tick, all karts (probe overhead included):

| Kart step | ms |
| --- | --- |
| Wheel sphere collision (`suspensionPhysics::calcCollision`, KCL) | 0.33 |
| `calcSuspension` | 0.30 |
| Body KCL collision (`findCollision`) | 0.25 |
| `KartMove::calc` | 0.18 |
| App item pass hook (`RaceItems::nativeKartPass`: snapshot + contacts) | 0.135 |
| `KartPhysics::calc` | 0.10 |
| Narrow-scope KCL prism cache (`checkCourseColNarrScLocal`) | 0.09 |
| Kart-kart collision | 0.07 |
| Hitboxes | 0.05 |
| Object collision | 0.04 |

KCL prism tests reach a vertex and four normals through separate indices
(`m_vertices[prism.pos_i]`, `m_nrms[...]`), which is a scattered FCRAM access per
field on an L2-less Old 3DS. A packed per-prism record would keep the same
arithmetic, but Azahar cannot show the cache saving and it costs memory, so it
stays in the backlog. The app's item snapshot (`RaceItems::snapshot`) runs 36
times per tick and calls out-of-line Kinoko accessors; app objects are not
built with LTO, so none of these accessors inline into app code.

## 2026-10-16 ~00:55 BST: cache maintenance and GPU state

### Sources

- 3dbrew, [SVC access control](https://www.3dbrew.org/w/index.php?diff=4671) and
  [GSPGPU:FlushDataCache](https://www.3dbrew.org/wiki/GSPGPU:FlushDataCache):
  applications normally reach only SVCs up to 0x3D; calling a disabled SVC
  is a kernel panic. `svcFlushProcessDataCache` is 0x54.
- 3dbrew GPU register notes and Citra's register handling
  ([commit](https://git.openpunk.com/OpenPunk/citra/commit/046daac427f937fdb570ae267c339ad25f006d68)):
  bit 16 of the texture unit config clears the texture cache.

### Consequences

- The ~5 `GSPGPU_FlushDataCache` IPCs per frame stay: a direct
  `svcFlushProcessDataCache` would avoid the hop to GSP on the system core,
  but 0x54 is outside the usual application SVC mask and would crash.
- citro3d sets the texture-cache clear bit whenever a texture unit is
  rebound (every non-repeated submesh). Skipping it when bindings are
  unchanged could save GPU time, but nothing here can measure PICA texture
  cache behaviour, and a stale cache after render-to-texture would be a
  visible bug. Not attempted.
- Per-draw citro3d CPU cost after change 8: about 8.5 emulated µs per draw
  (95 per frame). Remaining draw-call reductions need asset-side material
  merges (the low-poly work's area).

## 2026-10-16 ~01:30 BST: series techniques and live trig counts

### Sources

- Iwata Asks, [Mario Kart 7](https://iwataasks.nintendo.com/interviews/3ds/mario-kart-7/1/3);
  [Siliconera](https://siliconera.com/?p=165444): Mario Kart 7 held 60 FPS by
  budgeting eight racers first and adding model detail afterwards; three
  images per frame (two eyes and the lower screen) were the main load. No
  published specifics on its LOD or CPU techniques.
- No public material on PICA command-list reuse (`GPUREG_CMDBUF_JUMP`) for
  homebrew beyond 3dbrew's register pages.

### Measurements (temporary linker wraps, not committed)

Per presented frame: ~140 `sinf`, ~140 `cosf`, ~70 `atan2f`, 96 `remainderf`
(wheel roll, 4 per kart per tick), ~13 `powf`. `VehicleAnimation` accounts for
most of the sin/cos/atan2 (≈24 sin + 24 cos + 12 atan2 per visible kart).
Several of its angles are per-vehicle constants (arm X/Y rotations) or shared
(both front wheels' steering angle), so ~30% of its trig can be reused with
identical results. A faster approximate sin/cos for presentation would save
more on hardware but changes low bits, so captures would no longer match
pixel for pixel; not attempted.

### Ideas parked

- A cached command list for the static course draw (replayed by
  `GPUREG_CMDBUF_JUMP`) could save ~0.5 emulated ms, but it would carry
  view-dependent light uniforms and fog constants, and would bypass
  citro3d's state shadows. Too risky without hardware.
- Profile-guided optimization (devkitARM ships `libgcov` for armv6k/fpu):
  instrumented runs in Azahar can produce `.gcda` profiles. Semantics
  unchanged, but the profile must be regenerated as code changes.

## 2026-10-16 ~03:35 BST: other Old 3DS ports; profile-guided build

- [VGC](https://www.videogameschronicle.com/news/mario-64-3ds-port/) and
  [GBAtemp](https://gbatemp.net/threads/super-mario-64-port.564431/post-9207228):
  the Super Mario 64 3DS port reached ~30 FPS partly by moving audio
  processing to the second CPU; Old 3DS users are pointed to its 400 px build.
  This matches the layout here (audio alone on core 1).
- Profile-guided optimization (change 12) was the largest remaining exact
  CPU win: Kinoko −8.6% in emulated instructions, and 11% less code, which
  the emulator cannot credit but an L2-less 16 KB I-cache will.
