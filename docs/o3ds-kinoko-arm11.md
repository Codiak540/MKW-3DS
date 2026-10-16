# Old 3DS: Kinoko physics on the ARM11 (research notes)

Branch `o3ds-kinoko-arm11`. Written 2026-10-16 before the
batching and two-core work; the change log is `docs/o3ds-kinoko-perf-log.md`.
Every number here is either quoted from a source, measured in the worktree's
Azahar copy (Old 3DS mode), or marked as an estimate. No Old 3DS hardware was
available; the hardware checklist (`docs/o3ds-kinoko-hardware-checklist.md`)
says what to run on a console to replace the estimates.

## 1. Where the time goes (from the engine branch's probes)

`docs/o3ds-perf-research-log.md` (branch `o3ds-engine-perf`, 2026-10-16 00:55):
Kinoko's `calcEngines` is ~99% `KartObjectManager::calc`, 1.1–1.46 emulated
ms per tick for 12 karts, two ticks per presented frame. Per tick, all karts:

| Kart step | emulated ms | phase (section 5) |
| --- | --- | --- |
| Wheel sphere collision vs KCL (`WheelPhysics::updateCollision`) | 0.33 | P1B |
| `KartSuspensionPhysics::calcSuspension` | 0.30 | P1B |
| Body KCL collision (`KartCollide::findCollision`) | 0.25 | P1B |
| `KartMove::calc` | 0.18 | P0 |
| App item pass hook (`RaceItems::nativeKartPass`) | 0.135 | P1A |
| `KartPhysics::calc` | 0.10 | P0 |
| Narrow-scope KCL prism cache (`checkCourseColNarrScLocal`) | 0.09 | P1B |
| Kart–kart collision | 0.07 | P1A |
| Hitboxes | 0.05 | P0 |
| Object collision | 0.04 | P1A |

Azahar charges one emulated cycle per guest instruction. It models neither
VFP11 result latency nor cache misses, so the real cost of this code on a
268 MHz ARM11 without L2 is higher than the emulated time by a factor that
depends on how latency-bound and how cache-missing it is. Section 3 gives the
model used to estimate that factor; the GDB-trace cost model
(`tools/o3ds_vfp11_model.py`) measures it per function.

## 2. The processor: ARM11 MPCore with VFP11

Sources:

- ARM, *ARM1176JZF-S Technical Reference Manual* (DDI 0301) and *ARM11
  MPCore Technical Reference Manual* (DDI 0360): in-order, single-issue,
  8-stage integer pipeline; VFP11 coprocessor; 3DS cores have 16 KB L1
  instruction and 16 KB L1 data caches with 32-byte lines (4-way); the Old 3DS
  has no L2 (copetti.org, *Nintendo 3DS Architecture*; 3dbrew *Hardware*:
  "Old3DS: ARM11 2x MPCore & 2x VFPv2 Co-Processor 268MHz", the 2 MB L2 is
  New 3DS only).
- N. Burgess, C. Hinds, *Design of the ARM VFP11 Divide and Square Root
  Synthesisable Macrocell* (ARITH-18, paper and slides,
  https://www.lirmm.fr/arith18/papers/burgessn-divider.pdf): the FMAC pipeline
  "launches a new FMAC operation every cycle" with results after 8 cycles (9
  for double); the divide/square-root unit is radix-4 SRT, 15 cycles single,
  29 cycles double, and runs in parallel with the FMAC pipeline.
- GCC's VFP11 scheduling model, `gcc/config/arm/vfp11.md`
  (https://gnu.googlesource.com/gcc/+/2a62b047eca5859142f024c339cd2686635cf5b1/gcc/config/arm/vfp11.md):
  FMAC pipeline 8 stages (7 execute + writeback, forwarding from stage 4 for
  "simple" ops); DS pipeline 5 stages; LS pipeline 4 stages (execute, 2 memory,
  writeback). Reservations: `fcpys/fcmps/ffariths` (abs, neg, compare) latency
  4; `fadds/fmuls/fmacs/fconsts/f_cvt` latency 8; `fmuld/fmacd` 9; `fdivs` 19
  (ds × 15); `fdivd` 33; VFP loads and `fmsr` 4; stores and `fmrs` 4; `fmstat`
  4 and it blocks the whole VFP (the file says fmstat "is not modelled
  properly" and models it as excluding the fmac and ds units). Write-after-read
  hazards are not modelled. This is what `-mtune=mpcore` schedules against.

Consequences for Kinoko's code:

- A dependent single-precision chain costs 8 cycles per link. A 3-component
  dot product written as `x*x' + (y*y' + z*z')` is three links (24 cycles)
  although it issues in 3 cycles; four independent dot products interleave to
  about 11 cycles. Kinoko's prism test (`KColData::checkCollision`) does four
  dot products but early-exits after each one, so GCC cannot interleave them.
- `fcmps` + `fmstat` + conditional branch: `fmstat` waits for the compare,
  which waits for its operands; a compare on a fresh FMAC result costs about
  8 + 4 + branch. Every `if (f <= g)` on a just-computed float is a full
  pipeline drain. The prism test has five such checks per prism.
- `fdivs` 19 and `fsqrts` 19 (DS 15 + handoff) are not pipelined with each
  other; Kinoko's `EGG::Mathf::sqrt` is table-based `frsqrt` (no `fsqrts`),
  and `1.0f / x` appears in normalise and a few scalings.
- Double precision (`EGG::Mathf::fma`, `fms`: f64 multiply then add, used by
  `ps_dot`, `ps_squareMag`, `ps_multVector`, `frsqrt`) runs at half FMAC rate
  (one double op per two cycles) with 9-cycle latency; `fcvtds/fcvtsd` add 8
  cycles each side. The paired-singles emulation is therefore 3–4× the cost of
  the single-precision version on this core, and it is exactly the arithmetic
  the bit-exactness rule forbids changing.
- Memory: a load whose line is not in L1 goes to FCRAM. The latency is not
  published; see section 4.

## 3. Hardware cost model

For a function with `I` instructions, critical-path VFP latency `L` (cycles,
summing 8 per dependent FMAC link, 4 per compare, 4 per VFP↔ARM transfer, 19
per divide), and `M` L1 data misses, the ARM11 time is about

    T ≈ max(I, L) + M × FCRAM_LATENCY + (branch mispredicts × ~6)

because the core is in-order: VFP latency overlaps with independent ARM
instructions only up to the issue count. Azahar reports `I`. The ratio `R =
T / I` is what the engine branch called R (2.4–4). `tools/o3ds_vfp11_model.py`
computes `max(I, L)` per basic block from a single-stepped GDB trace of the
hot functions and counts the distinct 32-byte lines touched by loads, so `R`
can be estimated per function without hardware. FCRAM latency and the
misprediction cost are the two hardware parameters the checklist measures.

## 4. Memory: FCRAM without an L2

Sources: 3dbrew *Hardware* (one Fujitsu MB82M8080-07L FCRAM, LPDDR interface,
128 MB mapped at 0x20000000); 3dbrew *Memory layout*. No public latency or
bandwidth measurement for the Old 3DS was found (searched 2026-10-16). FCRAM
is a DRAM with LPDDR-like timing: a random 32-byte line fill is a row
activate + CAS + burst over the system bus, plausibly 100–200 ns, which at
268 MHz is 27–54 core cycles, before any bus arbitration against the GPU and
DMA. The checklist's pointer-chase test (`o3ds-memtest.txt`) measures it.

What this means for the KCL prism test: `KColData::KCollisionPrism` is 16
bytes holding five indices; the test reads the vertex (`m_vertices[pos_i]`,
12 B) and four normals (`m_nrms[...]`, 12 B each) from three large arrays
whose indices are scattered. For a prism whose five referenced lines are not
in L1 that is up to six misses, i.e. 150–300 cycles of memory time against
about 40 cycles of arithmetic. Course KCLs have 10–60 k prisms, and a kart's
narrow-scope cache (`checkCourseColNarrScLocal`, 250-unit radius) holds the
prisms around it; the same prisms are tested by nine sphere queries per kart
per tick (body hitboxes, four wheels, triggers), so after the first query the
lines are usually in L1 (16 KB = 512 lines) unless twelve karts' working sets
and the kart objects themselves evict them. Packing each prism's vertex and
normals into one 64-byte record (two lines, sequential) and copying the
narrow-scope set into a contiguous per-kart buffer converts the scattered
misses into at most two sequential fills per prism per tick; the arithmetic is
unchanged, so results are bit-identical.

## 5. Which per-kart work is independent (code audit, 2026-10-16)

Reference order (`KartObjectManager::calc`): for every kart, reset kart
collision; for every kart `calcSub()` (pass 0, `KartSub::calcPass0`); for every
kart `calc()` (`KartSub::calcPass1` then `KartModel::calc`); then
`KartAction::CalcPowWindow()`.

Pass 0 (`calcPass0`): state, input, `KartMove::calc`, action, pull path,
`KartPhysics::calc`, reject road, hitboxes. Cross-kart reads: only
`KartMove::calcSlipstream`, and only for the one kart `s = timer % count` on
each tick, which reads every other kart's `pos()`, `move()->dir()` and
`speedRatioCapped()`. In the reference those are post-pass-0 values for karts
below `s` and pre-pass-0 values for karts above `s`. Everything else in pass 0
reads its own kart, read-only globals (`RaceManager`, `CourseMap`, `RaceConfig`,
the POW window statics, `ObjectDirector` for rising water) and the collision
singletons (`CollisionDirector::checkSphereFull[Push]` from
`calcStickyRoad`/`calcSpecialFloor`); writes go to the kart itself and to the
collision singletons' scratch state (section 6).

Pass 1 (`calcPass1`) splits into:

- P1A (serial): `boxColUnit()->search`, `calcKartCollision`,
  `calcObjectCollision`, `calcItemCollision` (the app's item pass hook), then
  `dynamics()->setPos(pos() + tangentOff)`. Kart–kart collision writes both
  karts (tangent offsets, bumps, actions, sound cues) and marks the pair in
  both karts' `m_kartPairMask`, so each pair is resolved exactly once, by the
  lower-indexed kart. Object collision mutates course objects through
  `ObjectDirector`'s shared result arrays; the item hook mutates app item
  state. These stay in index order on core 0.
- P1B (parallel): soft-wall push, side collision, sink depth,
  `checkCourseColNarrScLocal`, `findCollision` (body KCL), floor effects and
  triggers, the four wheels' `calcCollision`, `calcSuspension`, hop physics,
  wheel speed/handling factors, moving object/water velocity, `updatePose`,
  `resetHitboxes`, and `KartModel::calc`. Reads: own kart, course KCL,
  drivable objects (`ObjectDrivableDirector`), read-only globals. Writes: own
  kart and the collision scratch state.

Why running every P1A first (in order) and then all P1B in any order equals
the reference order: P1A of kart j reads kart i (< j) only through
`checkKartCollision`, which returns before touching anything because the pair
is already masked (the exemption tests `KartCollisionExempt` are pure reads),
so it does not depend on P1B of kart i having run; P1A of kart j writes only
karts ≥ j, objects and items, none of which P1B of kart i reads (drivable
objects are a different class hierarchy from collidable objects, and their
transforms are final once `ObjectDirector::calc` ran before the kart step).
P1B of kart i reads nothing another kart writes. The one remaining shared
input of P1B, `ObjectKCL::update(0)`/`calcScale(0)` (per-object matrices
cached by frame number), is made explicit: the scheduler updates every
drivable object once on core 0 before forking, which is the value the lazy
update would have computed anyway.

Pass 0 therefore runs as three deterministic groups: karts `< s` in any order,
kart `s` alone, karts `> s` in any order. P1A runs serially. P1B runs in any
order. `CalcPowWindow` runs last. No random numbers, item queues or sound
event lists are touched inside P0 or P1B: sound cues are per-kart queues
(`KartMove::cueSound`) consumed by the app after the step, in kart order.

## 6. Shared scratch state that must become per-core

All of it is overwritten before it is read within one kart's phase, which is
why the reference order never leaks one kart's scratch into another's (the
harness checks this by poisoning the scratch between karts in the reference
path):

| Singleton | Mutable scratch | Used in |
| --- | --- | --- |
| `Field::CollisionDirector` | `m_entries[64]`, `m_collisionEntryCount`, `m_closestCollisionEntry` | every sphere query, `findClosestCollisionEntry` |
| `Field::CourseColMgr` | `m_kclScale`, `m_noBounceWallInfo`, `m_localMtx` | course and object KCL queries |
| `Field::KColData` (course and one per `ObjColMgr`) | `m_pos/m_prevPos/m_movement/m_radius/m_typeMask/m_prismIter`, prism cache (256 × u16), cached sphere | `narrowScopeLocal`, `lookup*`, `checkSphere*` |
| `Field::BoxColManager` | `m_units[256]`, `m_maxID`, `m_nextObjectID`, `m_nextDrivableID`, `m_cache*` | drivable searches inside queries |
| `Field::ObjectCollisionBase` | `s_dotProductCache` (static 4×4 GJK simplex cache) | the Ghost Valley block manager's sphere-vs-box tests |
| `Field::ObjectObakeManager` | `m_colSphere`, `m_colBox` (query shapes) | Ghost Valley block queries |

The static prism, normal and vertex arrays, the octree, the unit pool and the
spatial index are read-only during the kart step.

## 7. Running on the system core

Sources: 3dbrew *Multi-threading* ("The appcore scheduler is a simple
preemptive algorithm based around multiple thread priority levels", "no
timeslice-based scheduling is performed", core IDs: 0 appcore, 1 syscore on
Old 3DS, −2 = the exheader default); 3dbrew *APT:SetApplicationCpuTimeLimit*
(5–89 %, "Setting a value higher than 30% does not seem to improve performance
on Old 3DS, however it definitely does on New 3DS"); 3dbrew *KResourceLimit*
("Preemption type 0: this is the default, preempts threads on core1 and allow
them to run only 2ms * (timeLimit / 100)"); libctru `thread.h`
(`threadCreate(entry, arg, stack, prio, core_id, detached)`),
`synchronization.h` (`LightEvent`, `LightLock`); Azahar `kernel/thread.cpp`
(`CpuLimiterMulti::DoTimeLimit` for core 1, "real hardware uses a
KPreemptionTimer"; the main thread's FPSCR is 0x03C00010 = default NaN,
flush-to-zero, round toward zero, IXC).

What this means:

- The application's core-1 threads run in bursts of `2 ms × limit/100`
  (0.6 ms at 30 %, 1.6 ms at the 80 % the job worker sets) and are then
  preempted until the next period. A kart phase that takes 0.05–0.3 ms on
  hardware usually completes inside a burst, but a worker preempted in the
  middle of a kart holds that kart for up to the rest of the period (0.4–1.4
  ms). Core 0 cannot redo a half-written kart, so the join waits. The design
  bounds the exposure: the worker claims one kart at a time, from the end of
  the list while core 0 takes from the front, and each race measures the
  two-core tick against the serial tick during the countdown and keeps the
  faster (like the engine branch's layout calibration, change 9).
- Within a priority level the 3DS scheduler is cooperative. A spinning worker
  at the render thread's priority would starve it, so the worker sleeps on a
  `LightEvent` between ticks and spins only inside a tick, with
  `wfe` pauses; core 0 wakes it with `sev`. Both are ARMv6K instructions,
  available on ARM11 MPCore.
- FPSCR is per thread. The app's main thread sets round-to-nearest with
  flush-to-zero (`src/main.cpp`, `fpscr = (fpscr & ~(3<<22)) | (1<<24)`); the
  worker copies the main thread's FPSCR exactly as the job worker does. With
  identical FPSCR, identical code and identical inputs, the two VFP11s produce
  identical bits.
- Memory ordering: ARM11 MPCore is coherent across cores for normal cached
  memory (the SCU keeps L1 data caches coherent), but it reorders loads and
  stores; `std::atomic` with acquire/release (GCC emits `mcr p15, 0, r0, c7,
  c10, 5`, the ARMv6 data memory barrier) is used for every claim and
  completion flag.

## 8. VFP short vectors (LEN/STRIDE)

VFPv2 defines short-vector operation: with FPSCR.LEN ≠ 0 a data-processing
instruction operates on LEN consecutive registers of a bank, element by
element, each element exactly as the scalar instruction would (same rounding
mode, same flush-to-zero behaviour). The results are therefore bit-identical
to scalar code. VFP11 implements it (it was removed from VFPv3 as "deprecated"
and most later cores trap it; see RISC OS VFPSupport notes). GCC never emits
it; using it means inline assembly, and VFP11's FMAC pipeline still produces
one single-precision result per cycle, so vector mode saves ARM issue slots
and instruction-cache space rather than FMAC time. It is a candidate for the
batched wheel code only if the cost model shows the ARM side issue-bound; the
checklist includes a micro-benchmark to measure its throughput on hardware.
Not used by this branch's first versions.

## 9. Deterministic parallel lockstep engines

Sources: G. Fiedler, *Deterministic Lockstep* and *Floating Point
Determinism* (gafferongames.com); Itasca PFC documentation, *Accumulate
Deterministic Quantities* (deterministic mode fixes the reduction order at a
speed and memory cost); forum reports (Unigine, ODE) where multithreading or a
randomised solver order broke determinism and stable ordering restored it.
The rules those systems share and this branch follows: identical binary and
FP environment on every executor; work items that write only their own state;
every reduction (sum, min, "first hit") performed in a fixed order on one
thread; a per-tick state hash to prove it.

## 10. Measurement plan

1. Emulated instruction counts per phase: the existing `performance.txt`
   profile plus new `sim_p0/p1a/p1b_ms` columns.
2. Modelled hardware cycles per hot function: `tools/o3ds_vfp11_model.py`
   over a GDB single-step trace from the worktree's Azahar (`gdbstub`), with
   the latency table of section 2 and a 16 KB 4-way 32-byte-line cache model.
3. Hardware: the QA build logs per-phase `svcGetSystemTick` deltas (one
   kernel call per phase, ~150 cycles each) to `sim-phases.csv`, runs the FCRAM
   pointer chase and the VFP latency micro-benchmarks when `o3ds-memtest.txt`
   exists, and the reference/new A/B is a single marker file
   (`o3ds-kinoko-reference.txt`).

## 11. Results (2026-10-16)

The implementation, its gates and the measured numbers are in
`docs/o3ds-kinoko-perf-log.md`. In short: bit-identical on the host harness
(32 courses, ~17 M kart-ticks per path, two-thread work stealing with random
delays) and in the emulator (replay test and 2272-tick hash streams in every
mode); the kart step reordered into deterministic groups with a core-1
worker, a countdown calibration and a QA marker for the reference; batched
body and wheel prism tests; a GDB-trace cost model putting the local phase at
R ≈ 3 and pass 0 at R ≈ 4.4 (instruction-cache bound). Expected hardware
gain 1.5–2 ms per frame at a 30 % system-core share, 0 to 4 ms over the
plausible range, to be confirmed by `docs/o3ds-kinoko-hardware-checklist.md`.

## 12. Instruction cache and short vectors (follow-up, 2026-10-16)

Code footprint per kart, measured from the single-step traces (distinct
32-byte lines executed): pass 0 executes 5.1 k instructions touching 16.9 KB
of code (`KartSub::calcPass0` with the inlined state and physics steps 4.2 KB,
`KartMove::calc` 2.9 KB plus its callees 5 KB); the local phase executes 18 k
instructions touching 20.1 KB (`calcPass1Local` with the inlined wheel code
7 KB, `findCollision` 1.7 KB, the prism tests 3 KB). Both exceed the 16 KB
L1 instruction cache, and the twelve karts run the same code one after
another, so with no L2 nearly every line is fetched from FCRAM again for
every kart: about 530 lines × 12 karts for pass 0 and 650 × 12 for the local
phase, 2 ms per tick at the model's 40-cycle miss, the largest term in the
kart step.

Exact remedies, in the order tried: (1) pass 0 phase-major: each group of
karts runs the first half of pass 0 for all its karts, then the second half
(`KartSub::calcPass0A/B`, `KartMove::calcPart1/2`); the halves are 7.5 KB and
10.9 KB, so eleven of twelve karts find them in cache. Measured in the model
(perf log, change 10): 15.1 k cycles per kart instead of 27.9 k, 1.1 ms per
frame. Per-kart work is unchanged, each
kart's own order is unchanged, and the slipstream kart still runs after its
group; the harness proves bit-identity. (2) Less inlining (`-O2` instead of
`-O3`, `MKW_OPT` in `tools/build.py`): inlining does not change results with
`-ffp-contract=off` and no fast-math, and a smaller hot path misses less;
measured with the trace tool. The local phase cannot be split the same way:
its narrow-scope prism cache, collision entries and box-collision search are
per-lane scratch that a kart's later queries read, so its two halves would
need per-kart scratch (per-object KCL query state for up to 400 drivable
objects makes that expensive). It stays one phase; smaller code is its lever.

VFP11 short vectors (FPSCR.LEN/STRIDE) were not used. The VFP11 FMAC pipeline
starts one single-precision operation per cycle whether the instruction is
scalar or a vector of four, so vector mode saves ARM issue slots, not
floating-point time; the kart step is bound by instruction fetch, data misses
and dependent chains, not by issue, so the gain would be a few percent of the
prism loops at best. Setting LEN also changes the meaning of every VFP
instruction GCC emits on banks 1–3, so it can only be used inside hand-written
assembly with FPSCR saved and restored, and Azahar's correctness for vector
mode is unverified, which would leave the bit-identity gate without an
emulator. The hardware checklist's `vector_fmacs_cycles` measurement exists
so that a console can show whether VFP11's vector throughput is higher than
one per cycle; only then is the idea worth revisiting.
