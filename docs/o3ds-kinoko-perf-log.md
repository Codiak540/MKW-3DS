# Old 3DS Kinoko physics: change log

Branch `o3ds-kinoko-arm11`. Research and the dependency audit
that this work rests on: `docs/o3ds-kinoko-arm11.md`. Hardware checklist:
`docs/o3ds-kinoko-hardware-checklist.md`.

Gates applied to every change:

- Physics replay test in the worktree's Azahar copy (Funky Kong ghost, Luigi
  Circuit, race timer and three lap splits, two finishes, identical heap).
- Host determinism harness (`tools/sim_determinism/`): full 12-racer races,
  CPU-driven with seeded input jitter on the local racer, items on, every VS
  course, the complete simulation state hashed after every tick
  (`vendor/kinoko/source/game/SimHash.hh`); the reference path and the new
  path must produce identical hash streams.
- The same per-tick hash in the emulator (`sim-hash.txt` marker) for the
  reference and new builds of the scripted race.

Emulated ms are Azahar's guest-instruction counts (Old 3DS mode); modelled
hardware ms come from `tools/o3ds_vfp11_model.py` with the model of
`docs/o3ds-kinoko-arm11.md` section 3.

| # | Change | Why | Measurement | Determinism | Commit |
| --- | --- | --- | --- | --- | --- |
| 1 | Research document and dependency audit (`docs/o3ds-kinoko-arm11.md`). | Every later change is bit-exact only if the per-kart phases really are independent; the audit shows which are, and which shared scratch state must become per-core. | – | – | (this commit) |
| 2 | Host determinism harness: `tools/sim_determinism/{gen_sim_hash.py,harness.cpp,build_host.py,run.py}`, generated `SimHashFields.cc` (46 classes, 479 fields) and `SimHash::race`. Kinoko and the app's race logic build on macOS with clang. | The bit-identity gate the task requires; it also found the first real divergence (change 3). | 12 karts × 9000 ticks run in ~1.6 s per race on the host; 3.37 M kart-ticks per 32-course sweep. | reference vs reference identical (trivially) | (this commit) |
| 3 | Per-lane collision scratch (`CollisionLane`, `CollisionDirector`, `CourseColMgr`, `KColData::Query`, `BoxColManager::Search`, Ghost Valley block shapes) and the phased kart step (`KartObjectManager::calcPhased`, `KartSub::calcPass1Serial/Local`, `KartObject::calcSerial/Local`): pass 0 in slipstream-safe groups, pass 1A serial, pass 1B per kart, with `PhaseRunner` for a second core. Drivable course objects: `collisionIsFresh()` / `prepareParallel()`, serial fallback for a tick when a stale moving object is near a kart. | Prerequisite for both levers; same arithmetic, same order within each kart. The first version pre-updated every object's collision matrices and diverged on DS Delfino Square (tick 3321, the drawbridge: retail's first-touching kart computes its narrow scope with last tick's matrix); the fallback reproduces that order exactly. | No emulated change intended (same instructions per kart; a few hundred extra per tick for the lane lookups and the freshness pass). | Host sweeps (`build/sim-determinism/runs/sweep-chaos*`): phased vs reference 96/96 races identical (32 courses × 3 seeds × 9000 ticks, 10.2 M kart-ticks); two-thread parallel vs reference 96/96 (worker ran 2.86 M kart phases) and, with 0–3 µs random worker delays, 64/64 (32 courses × 2 seeds). Serial-fallback ticks (stale moving collision objects near a kart): 0 on 24 courses; Toad's Factory 58%, Coconut Mall 46%, Grumble Volcano 87%, Dry Dry Ruins 70%, GCN Waluigi Stadium 27%, Bowser's Castle 8%, DS Delfino Square 6%, Mushroom Gorge 3%. | (this commit) |
| 4 | `KColData::checkCollision` / `checkPointCollision`: the four prism distances (three edge normals and the face normal) are computed before the first early exit instead of one per exit. Same expressions, same operation order inside each; nothing else changed. | Each distance is a 5-link dependent chain on VFP11 (paired-singles emulation in doubles, ~45 cycles); serialized by the compares they cost ~180 cycles of latency per tested prism, interleaved about 60. Azahar counts the same instructions. | Emulated: no measurable change (the extra loads of early-rejected prisms add a few instructions). Modelled hardware: see the trace rows below. | Host sweep (`sweep-hoist`): phased and parallel vs reference 128/128 identical (32 courses × 2 seeds × 9000 ticks, 6.8 M kart-ticks). | (this commit) |
| 5 | Batched body collision: `KartCollide::calcBodyCollisionBatched` computes every hitbox's sphere first, runs all their course prism tests in one pass over the kart's narrow-scope cache (`KColData::checkSpheresCached`), then consumes each hitbox's hits in retail order through `CollisionDirector::checkSphereCachedFullPushHits` (the retail fold, entries, object queries and post-processing unchanged). Off in reference mode. | One pass over the cached prisms per kart body instead of one per hitbox (4–9), each prism's data loaded once, and up to nine independent VFP chains per prism instead of one. The hitbox spheres do not depend on the processing of earlier hitboxes (audit in the code comment), so the hit lists equal the retail sequence per hitbox. | Emulated: see the emulator rows below. Modelled hardware: trace rows below. | Host sweep (`sweep-bodybatch`): see below. | (this commit) |
| 6 | Batched wheel collision: `KartSuspensionPhysics::prepareCollision/finishCollision` and `WheelPhysics::prepareCollision/finishCollision` split `calcCollision`/`updateCollision` around the wheel's course query, `KartCollide::prepareWheelCollision/finishWheelCollision` split `calcWheelCollision`; `KartSub::calcPass1Local` computes the four wheel spheres, runs their prism tests in one pass and finishes each wheel in order. Off in reference mode. | The wheel query is the largest single item of the kart step (0.33 emulated ms per tick); the four wheels share the narrow-scope cache and their spheres do not depend on each other's processing. | Emulated: see the emulator rows below. | Host sweep (`sweep-wheels-phased`): phased vs reference 64/64 identical (32 courses × 2 seeds × 9000 ticks). Parallel: `sweep-wheels-parallel`, below. | (this commit) |
| 7 | `ObjectCollisionBase::s_dotProductCache` (the GJK simplex cache) is per collision lane. | The `sweep-bodybatch` parallel run diverged once on SNES Ghost Valley 2 (tick 4746, not reproducible: a data race). The Ghost Valley block manager tests kart spheres against block boxes with GJK, and GJK's simplex dot products were in one static 4×4 table shared by both cores. Also added to the audit table in `docs/o3ds-kinoko-arm11.md`. | – | Ghost Valley 2 parallel ×8 and the sweeps below. | (this commit) |

## Emulator gates (Azahar, Old 3DS mode, binary `phased-v1`: changes 1–3)

| Run | Result |
| --- | --- |
| Physics replay, default policy (calibration chose two-core in the emulator) | pass, 9064 steps, heap identical; `calib_phased_ms` 0.134 vs `calib_parallel_ms` 0.104 emulated main-core ms per tick |
| Physics replay, `o3ds-kinoko-reference.txt` | pass, 9064 steps, heap identical |
| Scripted 12-racer race, `sim-hash.txt`: reference vs phased | 2272-tick hash streams identical; 912 `QA cpu` log lines identical |
| Scripted 12-racer race, `sim-hash.txt`: reference vs parallel (core-1 worker) | 2272-tick hash streams identical; 912 `QA cpu` log lines identical; the worker ran 8081 of 52256 kart phases |

Azahar runs the two ARM11 cores in alternating host time slices, so its
core-1 timing is not the console's: the parallel run above spent 0.32
emulated ms per tick waiting for the worker's claimed kart. Only the
console's countdown calibration (`sim worker:` line in `runtime.log`) can
say whether the two-core step pays on hardware; see the checklist.

Host sweeps after changes 4–7 (`sweep-wheels-parallel`): reference vs phased
and reference vs two-thread parallel, 32 courses × 2 seeds × 9000 ticks, 128/128
comparisons identical (6.7 M kart-ticks per path); Ghost Valley 2 in parallel
mode eight times, all identical to the reference; with 0–2 µs random worker
delays before every claim (`sweep-wheels-chaos`, 32 courses × 1 seed) 32/32.

## Emulator gates and timing, final binary (`batched-v4`: changes 1–7)

| Run | Result |
| --- | --- |
| Physics replay, default policy | pass, 9064 steps, heap identical; calibration chose two-core (0.080 vs 0.149 emulated ms per tick during the countdown) |
| Physics replay, `o3ds-kinoko-reference.txt` | pass, 9064 steps, heap identical |
| Scripted race, `sim-hash.txt`: reference vs parallel | 2272-tick hash streams identical; 912 `QA cpu` lines identical |
| Scripted race: final-binary reference stream vs first-binary reference stream | identical (the hoist and the batched queries changed no bit on ARM either) |

Emulated main-core time per simulation tick of the scripted 12-racer race
(`sim worker:` `tick_ms`, 2272 ticks, Azahar counts instructions only):

| Mode | ms per tick | Note |
| --- | --- | --- |
| reference (retail order, scalar queries) | 1.415 | |
| phased (reordered step, batched queries, main core only) | 1.449 | +2.4 % instructions: lane lookups, batch bookkeeping, the hoisted loads of early-rejected prisms. The latency this buys back is invisible to the emulator. |
| parallel (core-1 worker) | 1.555 | includes 0.34 ms per tick waiting for the worker's claimed kart: Azahar time-slices the two cores on one host thread, so this is not a hardware number |

With the default policy the 12-racer race's countdown calibration measured
1.278 (phased) vs 1.289 (parallel) emulated ms per tick and kept the phased
step, as designed: where the second core does not pay, nothing is lost but the
calibration's 48 ticks. (The one-kart replay chose two-core: 0.080 vs 0.149.)

The emulator therefore shows no gain and a small instruction-count cost; the
expected hardware gain rests on the cost model below and must be confirmed by
the checklist runs.

## Cost model (tools/o3ds_vfp11_trace.py + tools/o3ds_vfp11_model.py)

Azahar's GDB stub single-steps at about 60 instructions per second, so traces
are short windows, not whole ticks. The model charges VFP11 result latencies
(8 FMAC, 4 simple, 19 divide), `fmstat` stalls, a 3-cycle load-use penalty, a
bimodal branch predictor (6-cycle mispredict) and 16 KB 4-way 32-byte-line L1
instruction and data caches with a 40-cycle miss (an assumption; the checklist's
pointer chase replaces it). `--warm` replays the window twice and reports the
second pass. R = modelled cycles ÷ instructions, i.e. the factor by which the
emulator's time understates the hardware for that code.

| Window (binary `batched-v4`) | instructions | modelled cycles cold / warm | R cold / warm | where the cycles go (warm) |
| --- | --- | --- | --- | --- |
| `RaceScene::calcEngines` from its entry, 600 ticks into the scripted race (race manager, box collision, objects, the first karts' pass 0) | 33,846 | 167,497 / 149,013 | 4.95 / 4.40 | `KartSub::calcPass0` 39k cycles (dependency stalls 3.2k, 417 I-cache misses ≈ 16.7k, 319 D-cache misses ≈ 12.8k), `KartMove::calc` 19k (R 8.0: 308 I-cache misses), `calcAcceleration` R 6.2 |

Pass 0 is not latency-bound so much as cache-bound in this model: Kinoko's
per-kart step code is large (`KartMove::calc` and its callees span tens of
kilobytes) and the 16 KB instruction cache without an L2 misses on it every
tick. That is a separate lever (code layout: PGO hot/cold splitting, which the
engine branch's change 12 already applies, and `-Os` for cold paths), not one
this branch pursued.

Local-phase window: breakpoint at `KartSub::calcPass1Local` 1200 kart-phases
into the scripted race, 40,000 single-stepped instructions, `--until-hits 2`
cuts the window at the next kart's entry (one whole kart phase).

| Path | window instructions | modelled cycles (warm) | of which local-phase functions | R (local) | top functions (warm cycles) |
| --- | --- | --- | --- | --- | --- |
| reference (retail order: kart k's local phase, then kart k+1's serial phase) | 19,280 | 58,904 | 16,671 instr / 48,391 cycles | 2.90 | `calcPass1Local` 10.1k (dependency stalls 1.9k, 100 I-misses), `narrowPolygon_EachBlock` 7.4k (prism cache fill: dep 1.3k, fmstat 183), `WheelPhysics::updateCollision` 4.6k, `checkCollision<Plane>` 3.9k (dep 1.1k, fmstat 162 on 1,670 instructions), `findCollision` 3.6k, `checkSphereCollision` 2.1k, `lookupSphereCached` 2.1k, `Matrix34f::makeR` 2.1k |

| batched (phased step: only the local phase in the window) | 18,037 | 49,240 | 17,109 instr / 47,081 cycles | 2.75 | `calcPass1Local` 14.4k (the wheel prepare/finish code is inlined here), `narrowPolygon_EachBlock` 6.5k, `checkCollision<Plane>` 3.9k, `findCollision` 3.2k, `checkSphereCachedFullPushHits` 2.4k, `makeR` 2.1k, `checkSpheresCached` 1.8k, `prepareSphereCached` 1.7k |

Same kart, same tick, one whole local phase each (the batched window holds no
serial-phase functions because the phased step runs every serial phase before
any local one). Local-phase subtotal: instructions +2.6 %, modelled cycles
−2.7 % (48,391 → 47,081); dependency stalls 9,304 → 8,436, `fmstat` stalls
902 → 809, D-cache misses 187 → 71 (each cached prism's data is now loaded
once per batch instead of once per sphere), I-cache misses 492 → 423. The
prism-test latency the batching hides is a small part of the local phase;
most of its cycles are instruction fetch, data misses and the remaining
dependent chains in the suspension and floor-moment code, so the batched
step on the main core alone is about cost-neutral in the model, as the
estimate below assumes.

## Where the local phase's hardware time goes (model, reference, warm)

| Component | cycles of 58,904 | share |
| --- | --- | --- |
| instruction issue (one per instruction) | 19,280 | 33 % |
| instruction-cache misses (≈400 × 40) | ≈16,000 | 27 % |
| VFP dependency stalls | ≈11,200 | 19 % |
| data-cache misses (≈230 × 40) | ≈9,200 | 16 % |
| `fmstat` compare stalls, mispredicts, divides | ≈3,200 | 5 % |

The batching and hoisting changes act on the VFP dependency and `fmstat`
stalls of the prism tests (`checkCollision`, `checkSphereCollision`,
`lookupSphereCached` and the inlined wheel/body query code: about 6–8 k of
the 48 k local-phase cycles). The instruction cache is the larger cost and is
untouched here: the per-kart step executes ~15 KB of code per kart against a
16 KB L1 with no L2, so every kart re-fetches it from FCRAM. Code-layout work
(the engine branch's PGO, hot/cold splitting, `-Os` for cold paths, keeping
the per-kart hot path contiguous) is the next lever for pass 0 and pass 1.

## Estimated Old 3DS gain, with its uncertainty

Per presented frame the kart step runs twice. Reference: 1.415 emulated ms
per tick; with the model's R of 3.1 (local phase, warm) to 4.4 (pass 0) the
hardware kart step is about 3.5–4.5 ms per tick, 7–9 ms per frame, which is
consistent with the engine branch's R of 2.4–4 for core 0 as a whole.

- Reordered step and batched queries on the main core only (what the
  calibration keeps when the second core does not pay): +2.4 % instructions
  (+0.2 ms per frame at R 3) against modelled savings of 3–5 % of the local
  phase (0.2–0.4 ms per frame). Net about zero to a small gain; the
  hardware checklist's A/B between `o3ds-kinoko-reference.txt` and
  `o3ds-kinoko-phased.txt` measures it directly.
- Two-core step: about 90 % of the kart step's instructions are in the
  parallel groups (pass 0 ≈ 21 %, local phase ≈ 70 %); the serial phase
  (racer, object and item contact, ≈ 9 %) stays on core 0, and on five
  courses with continuously moving collision objects 46–87 % of ticks run
  serially. If the system core lends the application the usually reported
  30 % of its time during the step, the worker takes ≈ 23 % of the parallel
  work: 1.5–2 ms per frame saved (5–8 % of a 25 ms frame). If it lends
  80 % (the limit the app requests), up to 3–4 ms per frame. If it lends
  nothing usable, or preempts the worker mid-kart often (`max_wait_us` in
  the `sim worker:` line), the countdown calibration keeps the main-core
  step and nothing is lost beyond the calibration's 48 ticks.
- The uncertainty is dominated by two things no emulator run can settle:
  the system core's real share and burst pattern (section 7 of the research
  document) and the FCRAM miss cost (40 cycles assumed; `o3ds-memtest.txt`
  measures it). Both are the first items on the hardware checklist.

Not done: VFP short vectors (only measurable on hardware); code-layout work
for the instruction cache. Packed per-prism records were added afterwards
(change 9).

## Change 8: mid-race guard, and a note for the merge

| # | Change | Why | Measurement | Determinism | Commit |
| --- | --- | --- | --- | --- | --- |
| 8 | `SimWorker`: with the default (calibrate) policy, once the race runs two-core, three ticks within sixty that each waited more than 0.3 ms for a kart the worker had claimed switch the race to the main-core step for good (`guard_tripped=1` in the `sim worker:` line). | The countdown calibration sees the system core as it is during the countdown; if the system takes it away mid-kart later (audio, DSP, system services), the main core must not keep stalling. Switching modes between ticks is bit-identical. | Replay and scripted-race gates below. | Below. | (this commit) |

Merge note (engine branch `o3ds-engine-perf`): that branch times three core
layouts during the countdown (`CoreCalibration`, change 9 there) and this
branch times two kart-step modes during the same countdown. Run concurrently
they measure each other's trials. When merging, start `SimWorker::beginRace()`'s
calibration only after `CoreCalibration::calibrating()` has turned false (or
pin the sim mode per layout: with the render thread on core 1, the worker
shares that core and the main-core step is the safe default).

Gates for change 8 (binary `guard-v5`): replay test, default policy: pass,
9064 steps, identical heap; in the emulator the guard tripped after 691
two-core ticks (the emulator's core-1 time slicing makes every tick wait, as
noted above) and the rest of the replay ran on the main core. Scripted
12-racer race, default policy: calibration kept the main-core step (1.278 vs
1.303 emulated ms per tick), guard not tripped, 912 `QA cpu` lines identical
to the reference run.

Course KCL sizes (host harness, `SIM_PRINT_KCL=1`): the largest VS course,
Maple Treeway, has 10,428 prisms, 39,198 normals and 2,938 vertices; most
courses have 5–10 k prisms. Packed per-prism records (vertex, four normals,
height, attribute: 72 bytes) would cost at most 0.75 MB per course against
the 7 MB the replay test shows free, so memory is not the obstacle it looked
like from the 1 MB prism-array estimate.

## Change 9: packed per-prism KCL records

| # | Change | Why | Measurement | Determinism | Commit |
| --- | --- | --- | --- | --- | --- |
| 9 | `KColData::PackedPrism` (68 bytes: vertex, three edge normals, face normal, height, attribute, face-normal index), built once at load next to the existing arrays; every prism test (`checkCollision`, `checkPointCollision`, the batched `checkSpheresCached`) reads the record instead of the five indexed arrays. The values are copies, the arithmetic is unchanged. | A tested prism touched up to six scattered 32-byte lines (16-byte prism, 12-byte vertex, four 12-byte normals) on a core with no L2; now two or three sequential lines. Worst-case cost 10,428 × 68 = 0.7 MB (Maple Treeway) against 7 MB free. | Azahar: no change expected (same instructions). Model: the warm-cache windows already showed few data misses in the prism tests (the cold first query per kart is where this pays); not re-traced. | Host sweep (`sweep-packed`): reference vs phased vs parallel, 32 courses × 2 seeds × 9000 ticks, 128/128 identical. Emulator gates below. | (this commit) |

Gates for change 9 (binary `packed-v6`): replay test, default policy: pass,
9064 steps, identical heap. Scripted 12-racer race, `o3ds-kinoko-phased.txt`
with `sim-hash.txt`: the 2272-tick hash stream equals the reference stream of
the previous binary. Emulated main-core time of the phased step fell from
1.449 to 1.416 ms per tick (the five index loads per prism are gone), level
with the retail-order reference's 1.415: the restructured step no longer
costs instructions on the main core, so every latency and cache saving the
model predicts is net.

## Changes 10–11: the instruction cache

Measured from the single-step traces (distinct 32-byte code lines executed per
kart): pass 0 touches 16.9 KB, the local phase 20.1 KB; the L1 instruction
cache is 16 KB with no L2, and twelve karts run the same code in a row. In the
model that is ~530 + ~650 misses per kart, 2 ms per tick, the largest term
in the kart step (`docs/o3ds-kinoko-arm11.md`, section 12).

| # | Change | Why | Measurement | Determinism | Commit |
| --- | --- | --- | --- | --- | --- |
| 10 | Pass 0 phase-major: `KartMove::calcPart1/calcPart2` (split after `calcSlipstream`), `KartSub::calcPass0A/B`, `KartObject::calcSubA/B`, phases `Pass0A`/`Pass0B`; each kart group runs part A for all its karts, then part B. Per-kart statement order is unchanged; the slipstream kart still runs whole, after its group. | Each half is about 8.5 KB of code, so after the first kart of a group the other eleven hit the instruction cache instead of refetching 16.9 KB each; the kart's own data (a few KB) is touched twice instead of once. | Emulated: same instructions per kart (a few more phase dispatches). Model: see the `v8-pass0a` trace row below. | Host sweep (`sweep-pass0split`): reference vs phased vs parallel, 32 courses × 2 seeds × 9000 ticks, 128/128 identical. Emulator gates below. | (this commit) |
| 11 | `MKW_OPT` in `tools/build.py` (default `-O3`): an `-O2` build for the footprint comparison. | Inlining and unrolling do not change results (`-ffp-contract=off`, no fast-math) but multiply hot code; `-O2` cut the executable's text from 4.18 to 3.79 MB. | Traces `o2-p0`, `o2-p1` vs the `-O3` windows: below. | Same binary rules; the harness and hash gates apply to whichever level ships. | (this commit) |

Cost-model windows for the instruction-cache work (one kart each, 1200
kart-phases into the scripted race):

| Window | instructions | I-footprint | modelled cycles cold / warm |
| --- | --- | --- | --- |
| pass 0, `-O3` (binary `batched-v4`) | 5,191 | 16,864 B | 38,308 / 20,913 |
| pass 0, `-O2` (binary `o2-v7`) | 4,920 | 17,504–17,696 B | 39,518 / 20,562 |
| local phase, `-O3` (binary `batched-v4`) | 18,037 | 20,128 B | 68,441 / 49,240 |
| local phase, `-O2` (binary `o2-v7`) | 17,006 | 20,128 B | 66,904 / 48,380 |

`-O2` executes 5–6 % fewer instructions but its hot path is no smaller (pass 0
larger: less inlining spreads the executed code over more functions; the local
phase identical at 20.1 KB), so its cold cost is +3 % / −2 % and its warm cost
−1.7 % / −1.7 %: not a lever worth a different optimisation level. The default stays
`-O3`; `MKW_OPT` remains for experiments. The cold/warm gap of pass 0, about
17 k cycles per kart, is what the phase-major split (change 10) recovers for
eleven of the twelve karts of a group when each half fits the cache: in the
model about 0.7 ms per tick, 1.4 ms per frame, before the extra data-cache
traffic of touching each kart's state twice (a few hundred lines per tick).

Gates for change 10 (binary `split-v8`): replay test, default policy: pass,
9064 steps, identical heap. Scripted 12-racer race with `sim-hash.txt` and
`o3ds-kinoko-parallel.txt`: the 2272-tick hash stream equals the reference
stream of binary `batched-v4` (the worker ran 9,195 of 77,248 kart phases in
the emulator); phased mode 1.411 emulated ms per tick (reference 1.415), so
the phase-major order costs no instructions.

Pass 0 phase-major, measured with the trace tool on binary `split-v8` (the
scripted race, 1200 kart-phases in, `o3ds-kinoko-phased.txt`), one kart in
steady state (the cache holds what the previous kart of the group left):

| Window | instructions | I-footprint | modelled cycles | I-misses | D-misses | VFP dependency stalls |
| --- | --- | --- | --- | --- | --- | --- |
| sequential pass 0, retail order (binary `batched-v4`, mean of 4 consecutive karts) | 5,142 | 16.9 KB | 27,861 | 355 | 133 | 2,573 |
| phase-major part A (mean of 11 karts) | 1,728 | 7.5 KB | 6,176 | 19 | 65 | 1,103 |
| phase-major part B (one kart) | 3,264 | 10.9 KB | 16,818 | 157 | 137 | 1,499 |
| phase-major pass 0 (A + B) | 4,992 | | 22,994 | 176 | 202 | |

The part B row above is the first kart of its group (cold for part B's code
after the whole group's part A). The longer trace `v8-pass0b-long` gives the
steady state over nine consecutive karts:

| Window | instructions | modelled cycles | I-misses | D-misses | VFP dependency stalls |
| --- | --- | --- | --- | --- | --- |
| phase-major part B, steady state (mean of 9 karts) | 3,310 | 8,935 | 20 | 71 | 1,630 |
| phase-major pass 0 (A + B), steady state | 5,038 | 15,111 | 39 | 136 | 2,733 |
| sequential pass 0, retail order | 5,142 | 27,861 | 355 | 133 | 2,573 |

12,750 fewer modelled cycles per kart: 0.57 ms per tick for twelve karts,
1.1 ms per presented frame at the model's 40-cycle miss cost (scale with the
console's measured `chase_ns_8388608`). Data-cache misses are level (each
kart's state is touched twice, but the second half's data is largely the
first's). The hardware-estimate section's ranges should be read with this on
top: main-core-only step about 1 ms per frame better than the retail order in
the model, the two-core step 1.5–2 ms more at a 30 % system-core share.
