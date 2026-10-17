# Old 3DS scenery and opponent LOD

Branch `o3ds-scenery-lod`.
Goal: cut the drawing-side cost of course objects (scenery) and opponent racers on
Old 3DS with no change to physics, collision, object logic or race outcomes, and no
visible change in normal play. New 3DS and unknown hardware are unchanged.

All numbers are Azahar (Old 3DS mode, unthrottled) emulated ARM11 time, measured with
`tools/o3ds_perf_run.py` in this worktree's own emulator copy, same 3DSX with and without
QA markers. Emulated time counts instructions, not VFP/memory latency; no physical Old 3DS
was available.

## Summary

| Change (Old 3DS only) | Effect | Image | QA marker to turn it off |
| --- | --- | --- | --- |
| Exact early culling of course-object CPU work | scenery_ms −15% to −49% on every course with objects (mean 0.42 → 0.29 ms); job-worker time −16% | pixel-identical, 32/32 courses | `o3ds-objcull-off.txt` |
| Object/racer visibility per camera column (`models/pvs/course_NN.opvs`) | draws −0.7% to −4.0% and render thread up to −5.7% where the course hides things (Grumble Volcano, Coconut Mall, N64 Bowser's Castle, Bowser's Castle); ~0 on open courses | pixel-identical | `o3ds-objpvs-off.txt` |
| Opponent pose LOD (small racers re-pose on alternate frames) | pose evaluations −10% to −15%, skinned vertices −7% to −15%; opponents_ms within noise | 7–30 px per affected frame, on ~15 px racers | `o3ds-pose-lod-off.txt` |

Physics, collision, object logic and race outcomes are unchanged: on all 32 VS courses the
per-racer CPU log (position, speed, rank, lap, item, AI state every 30 steps; 912 lines) is
identical with and without the changes, and the physics replay QA test passes with the
engine baseline's result (9,064 steps, heap 7,029,484 / 7,029,484).

## Research (first pass)

### How low-spec racers handled distant racers and scenery

- **Mario Kart 64** drew the racers as pre-rendered sprites over polygon tracks, for
  speed ([Nintendo Life](https://nintendolife.com/reviews/n64/mario_kart_64),
  [Super Mario Wiki](https://mariowiki.com/Mario_Kart_64)); each frame is said to be one of
  many renders of a 3D model chosen by viewing angle (unsourced in the
  [Wikipedia mirror](https://wikipedia2007.classicistranieri.com/m/a/r/Mario_Kart_64_0fbf.html)).
- **Later Mario Kart games** swap racers for low-quality models when far from the camera
  (Double Dash, Wii, 7, Tour; fan documentation by
  [Supper Mario Broth](https://www.suppermariobroth.com/post/811191047543734272/modelsspriters-resourcecomgamecubemkdoubledash/amp),
  [Mario Kart Tour post](https://www.suppermariobroth.com/post/808824167068827648/in-mario-kart-tour-the-low-quality-models-used/amp)).
  Tour's distant models are automatic simplifications with textures 10–12x smaller.
  Mario Kart Arcade GP 2 has unused, extremely low-poly models for very far racers; the
  shipped game does not draw racers at those distances at all. No primary source covers
  Mario Kart DS's distant racers. The Wii data itself ships authored `-low` LODs for
  course objects, which this port already uses on every console.
- **F-Zero X** reached 60 FPS with 30 machines by cutting background, texture and vehicle
  polygon detail, reducing vehicle detail as they pass, and using fog to hide the horizon
  ([Wikipedia](https://en.wikipedia.org/wiki/F-Zero_X)).
- **Mario Kart 7** (the 3DS precedent) budgeted eight racers first and added detail
  afterwards; three images per frame were the main load
  ([Iwata Asks](https://iwataasks.nintendo.com/interviews/3ds/mario-kart-7/1/3)).

### Occlusion and precomputed visibility in racers

- **Gran Turismo** renders each course offline from thousands of camera positions along
  the track, stores which *objects* are visible, and merges the sample points into zones by
  Voronoi partitioning; a zone keeps every object visible from any of its points
  (conservative), at the cost of coarse culling and hard zone boundaries
  ([GTPlanet on Polyphony's NeuralPVS talk](https://gtplanet.net/inside-the-ai-powered-rendering-tech-polyphony-digital-is-building-for-gran-turismos-future)).
  This is the closest published match to what is built here: per camera column, which
  regions the course can hide, with adjacent columns merged conservatively.
- **Crash Bandicoot** precomputed visibility (and polygon order) offline per camera position
  along a constrained path, distributed across the artists' workstations
  ([Andy Gavin's blog](https://all-things-andy-gavin.com/tag/video-games/page/2/)).
- **Surveys**: from-region visibility and conservative potentially-visible sets are covered
  by Cohen-Or et al., *A survey of visibility for walkthrough applications*, IEEE TVCG 2003
  ([PDF](https://www.cs.princeton.edu/courses/archive/fall16/cos526/papers/cohenor03.pdf));
  Plantinga defines conservative region visibility as a superset of what any point in the
  region sees ([GI 1993](https://graphicsinterface.org/wp-content/uploads/gi1993-25.pdf)).
  Unreal's precomputed visibility targets the same case: mostly static worlds with
  restricted, roughly 2D movement ([UDK docs](https://docs.unrealengine.com/udk/Three/PrecomputedVisibility.html)).

### Animation update-rate LOD

- Unreal's **Animation Budget Allocator** ranks skeletal meshes by significance and, under
  a game-thread budget (1 ms default), ticks less significant ones at a lower rate (rate 5
  = every fifth frame; max tick rate 10), optionally interpolating
  ([Epic docs](https://dev.epicgames.com/documentation/en-us/unreal-engine/animation-budget-allocator-in-unreal-engine)).
  **Update Rate Optimization** does the same per mesh by LOD or screen size, with the
  known "stop-motion" look when overused
  ([Fab listing](https://fab.com/listings/67102b1e-b955-4ad3-927e-240b4c2b8101)).

### Impostors

Billboards and impostors replace distant geometry with images; they lose parallax and
pop when the view moves off-axis, which octahedral and depth impostors mitigate at a
memory cost ([Eurographics 2005](https://diglib.eg.org/items/114bac30-ab84-4b5e-9147-268808f6ba51),
[TU Wien report](https://www.cg.tuwien.ac.at/research/publications/2002/TR-186-2-02-04/),
[Unreal impostor baker](https://dev.epicgames.com/documentation/unreal-engine/impostor-baker-plugin-in-unreal-engine),
[GameDev.net](https://gamedev.net/forums/topic/708639-depth-impostors-does-anybody-use-them/5433661/)).

### What fits PICA200 + ARM11

- The PICA200 has three vertex processors (a fourth when the geometry unit is off), runs at
  268 MHz like the Old 3DS's two ARM11 MPCore cores, one of which belongs to the system
  ([3dbrew](https://www.3dbrew.org/wiki/Hardware)). There are no occlusion queries or
  hierarchical-Z the CPU could read back, so occlusion has to be precomputed.
- Per-draw CPU cost dominates small draws (~8.5 emulated µs per draw after the engine
  session's citro3d work, docs on branch `o3ds-engine-perf`), so removing
  whole draws and the CPU work that prepares them pays more than removing triangles.
- Old 3DS already runs "reduced detail" (`render_quality.hpp`): objects under ~4 px are not
  drawn, crowds and item models animate at 12 Hz, objects always use their `-low` LOD,
  shadow volumes and bloom are off, racers wholly outside a 1,000-unit envelope are not
  posed. Whatever is added here has to beat that baseline.

## Where the time goes (measured before changing anything)

Baseline = the main checkout's working tree (commit 9fdb940), 1,500-step VS races,
Mario, 11 CPU racers, player CPU-driven (`tools/o3ds_perf_run.py --course N`).

| Course | scenery_ms | opponents_ms | draws | triangles | pose_evaluations | pose_reuses | skinned_vertices | cpu_skinned_vertices | render_thread_ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Luigi Circuit | 0.21 | 0.73 | 90.1 | 15805 | 2.67 | 0.41 | 1356 | 115 | 3.90 |
| Mushroom Gorge | 0.34 | 0.70 | 83.8 | 15172 | 2.71 | 0.50 | 1392 | 133 | 3.93 |
| Toad's Factory | 0.38 | 0.73 | 96.7 | 17609 | 2.92 | 0.20 | 1654 | 201 | 4.05 |
| Coconut Mall | 0.51 | 0.67 | 106 | 22035 | 2.51 | 0.46 | 1728 | 585 | 5.58 |
| DK Summit | 0.44 | 0.71 | 82.9 | 14540 | 3.06 | 0.29 | 1587 | 157 | 3.77 |
| Wario's Gold Mine | 0.29 | 0.70 | 84.0 | 17106 | 2.86 | 0.30 | 1586 | 181 | 3.80 |
| Daisy Circuit | 0.53 | 0.64 | 88.9 | 23255 | 2.73 | 0.11 | 2626 | 930 | 4.82 |
| Koopa Cape | 0.32 | 0.73 | 87.4 | 14443 | 3.01 | 0.20 | 1619 | 136 | 3.87 |
| Maple Treeway | 0.54 | 0.69 | 73.6 | 15512 | 2.75 | 0.43 | 1663 | 324 | 3.40 |
| Grumble Volcano | 0.26 | 0.72 | 118 | 16225 | 2.69 | 0.35 | 1374 | 106 | 4.79 |
| Dry Dry Ruins | 0.29 | 0.68 | 70.1 | 13061 | 2.39 | 0.14 | 1213 | 106 | 3.21 |
| Moonview Highway | 0.52 | 0.68 | 92.5 | 17503 | 2.77 | 0.11 | 1432 | 108 | 4.28 |
| Bowser's Castle | 0.76 | 0.59 | 84.9 | 24844 | 2.95 | 0.57 | 2398 | 529 | 4.22 |

Temporary probes (not committed) split this further, 3,000-step races:

- **Opponents draw very little.** On Bowser's Castle 4.5 racers per frame survive the
  existing culls, but only 5.3 racer parts are drawn per frame (body 2.5, driver 1.5,
  wheels 1.4); Coconut Mall 4.0, Daisy Circuit 4.1. The existing 4 px cut already drops far
  parts, and only ~3 skeletal poses are evaluated per frame in total (player included).
  A leaner far model or an impostor would save almost nothing, and 15 Hz posing can save
  at most a few poses per frame.
- **Scenery cost is presenter CPU, mostly for objects that are not drawn.** Course objects
  record only 4–42 draws per frame, but each placement's presenter runs every frame
  (emulated µs per frame, job worker; probe overhead included):

  | Course | Object | µs | calls | draws/frame |
  | --- | --- | --- | --- | --- |
  | Bowser's Castle | koopaFirebar (3 bars, ~36 fireballs) | 222 | 3 | 9.3 |
  | Bowser's Castle | TwistedWay (procedural road) | 218 | 1 | 0.41 |
  | Bowser's Castle | WLfireringGC | 96 | 4 | 3.5 |
  | Bowser's Castle | FlamePole | 85 | 14 | 1.1 |
  | Coconut Mall | CarB (parked cars) | 318 | 51 | 5.0 |
  | Daisy Circuit | pylon01 | 141 | 25 | 0.74 |
  | Daisy Circuit | seagull | 81 | 3 | 2.0 |
  | Luigi Circuit | FlagB2 | 56 | 10 | 1.9 |
  | Luigi Circuit | dokan_sfc | 33 | 5 | 0.02 |

  Every `ObjectDraw::submit` built a model-to-clip matrix (two 4×4 products) for its
  frustum test, the firebar did that twice per fireball, and the twisted road looked up
  each of its 18 joints by `snprintf` + name search and evaluated 54 trig calls every
  frame, drawn or not.

So the order of levers became: (1) exact early culling of scenery CPU work, (2) object
visibility for what survives it, (3) opponent pose LOD, with distant racer models and
impostors rejected (below).


## What was built, step by step

Each step was chosen from a short research/measurement pass after the previous one.

### Step 1: object visibility per camera column (the task's first lever)

`tools/build_o3ds_object_pvs.py` reuses the track PVS's camera columns (1000 × 1000 units
around every drivable KCL surface, one height interval each), sample lattice, cube-map
depth of the opaque course (all course parts as Old 3DS loads them, O3RM overlay applied)
and one-pixel erosion. `tools/pvs/pvs_raster.cpp` gained input version 2: after the
occluder depth, each *region box* is tested by its clipped projection's bounding rectangle
against a min-depth pyramid; a box is visible when any pixel's eroded occluder depth is at
or behind the box's nearest point (if any interior point is visible, the ray's entry point
into the box is too). Version 1 output is unchanged (test).

A dense voxel grid was measured first and rejected: 5,000–70,000 voxels per course, 0.5–9 KB
per column set, megabytes per course. Regions instead are boxes that need no object
knowledge: k-d leaves of the drivable KCL triangles (≤2,500 × 1,000 units, widened 500,
600 below, 1,200 above, for karts, walkers and route movers) and boxes of radius
700/1,500/3,500 × scale around every KMP placement. At run time (`include/mkw/object_pvs.hpp`,
`src/object_pvs.cpp`) an object or racer is skipped only when its current world bounds lie
wholly inside a box its camera column's set does not contain, so anything that moves out
of the boxes, or a camera outside the columns (intros, cannons, falls), draws as before.

Size control: column sets padded with their 8 neighbours, then adjacent columns merged
greedily (the pair adding the fewest visible (region, column) pairs, as GT's zones do)
to at most 256 distinct sets; regions hidden from under 1% of columns dropped; no sidecar
with fewer than 8 regions left. Sidecar layout and validation are in the header comment of
`object_pvs.hpp` and the builder's docstring; any mismatch (magic, version, an
order-independent fingerprint of the loaded course triangles and positions, part count,
sizes, every index, every box) keeps it empty.

First results: Bowser's Castle draws −1.8%, captures identical; Coconut Mall 1% of tested
objects hidden and +0.1 ms of test cost (long region lists). Fix: `set()` rebuilds, when the
camera changes column, per-lookup-cell lists of only the hidden regions, so a test usually
reads zero or one box (randomised host test: same answers as the full scan). Cost then
measured +0.004 ms on Coconut Mall.

### Step 2 (after a timing pass): exact early culling of scenery CPU work

Per-object timing showed the scenery time is presenter work for every placement, drawn or
not (table above). Changes, all skipping only what a later exact test rejects:

- `include/mkw/world_cull.hpp`: the six clip planes normalised in world space once per
  frame; `ObjectDraw::submit` rejects an object whose world AABB is outside one plane by a
  margin (1 unit + 1e-4 relative, far above the exact test's 1e-5) before building the
  model-to-clip matrix. Host test: 200,000 random boxes/transforms/frusta, never rejects
  what `ClipFrustum` keeps, catches 99.7% of what it rejects.
- Firebars: one box around all fireballs (ball reach × scale) before any per-ball matrix.
- Procedural roads, nets and bridges (Bowser's Castle twisting road, Rainbow Road aurora,
  Maple Treeway net, DK Mountain rope bridge): joint nodes looked up once instead of
  `snprintf` + name search per joint per frame, and posing skipped when an envelope of
  *every possible pose* is off screen or hidden. The envelope is exact by construction: a
  joint rolled about an axis keeps each vertex's coordinate along it and its distance from
  it (cylinder), a joint translated along a bounded range sweeps the hull of its two
  extreme placements; unposed slots keep their bind placement. Presenter state (pre-race
  clocks via `stateAge`) still advances every frame.
- Coconut Mall palms: the early reject before their size test.

### Step 3: opponent pose LOD

`include/mkw/pose_lod.hpp`, `DriverAnimation::keepPose`: an opponent whose model radius
is under 5% of its distance from the camera (about 13 px of radius) re-evaluates its
driver/body/accessory poses on alternate frames (half such racers per frame) and draws the
pose it has otherwise, like engines' animation update-rate LOD. Reuse is allowed only for a
pose applied within the last 3 simulation ticks, so a racer or part that was culled never
shows a stale pose; the kart body and wheels still move every frame.

### Not built (and why)

- **Leaner far models / impostors for opponents.** Opponents already draw only ~4–5 parts
  per frame (the 4 px cut removes far parts) and ~3 poses per frame are evaluated in total.
  Rival sidecars are already what every opponent loads on Old 3DS. A third, far LOD or an
  MK64-style sprite would save almost nothing and risks the look.
- **Lower update rate for animated course objects.** Clip poses are a cheap
  interpolation; CPU-skinned crowds already step at 12 Hz in reduced detail; the cost was
  the presenters' fixed work, which step 2 removes without any visual change.
- **Coarser camera cells for the object PVS** (memory): 2,000-unit cells would quarter the
  column table but make every set 6,000 units wide after padding.
- **Fast approximate trig** in presenters: would change low bits, so captures could no
  longer prove "no change".

## Results: all 32 VS courses

Same 3DSX, 1,500-step VS races (Mario, 11 CPU racers, player CPU-driven, captures every 60
steps), markers `o3ds-objcull-off.txt o3ds-objpvs-off.txt o3ds-pose-lod-off.txt` (off) vs
`o3ds-pose-lod-off.txt` (exact changes on). `tools/o3ds_lod_compare.py --row`.

| Course | CPU log | Captures identical | draws | triangles | scenery_ms | opponents_ms | pose_evaluations | pose_reuses | skinned_vertices | render_thread_ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Luigi Circuit | 912 identical | 52/52 | 90.1 → 90.1 (-0.0%) | 15806 → 15805 (-0.0%) | 0.24 → 0.18 (-26.2%) | 0.70 → 0.72 (+2.2%) | 2.67 → 2.67 (+0.0%) | 0.41 → 0.41 (+0.0%) | 1356 → 1356 (+0.0%) | 3.90 → 3.89 (-0.1%) |
| Moo Moo Meadows | 912 identical | 52/52 | 76.7 → 76.7 (+0.0%) | 18000 → 18000 (+0.0%) | 0.20 → 0.15 (-23.7%) | 0.76 → 0.76 (-0.7%) | 2.71 → 2.71 (+0.0%) | 0.41 → 0.41 (+0.0%) | 2057 → 2057 (+0.0%) | 3.76 → 3.75 (-0.2%) |
| Mushroom Gorge | 912 identical | 52/52 | 83.8 → 82.6 (-1.3%) | 15172 → 15066 (-0.7%) | 0.32 → 0.26 (-19.8%) | 0.71 → 0.73 (+2.7%) | 2.71 → 2.71 (+0.0%) | 0.50 → 0.50 (+0.0%) | 1392 → 1392 (+0.0%) | 3.92 → 3.89 (-0.7%) |
| Toad's Factory | 912 identical | 52/52 | 96.7 → 96.7 (+0.0%) | 17608 → 17607 (-0.0%) | 0.37 → 0.28 (-23.8%) | 0.74 → 0.77 (+4.0%) | 2.92 → 2.92 (+0.0%) | 0.20 → 0.20 (+0.0%) | 1654 → 1654 (+0.0%) | 4.05 → 4.05 (+0.0%) |
| Mario Circuit | 912 identical | 52/52 | 105 → 105 (-0.0%) | 20050 → 20043 (-0.0%) | 0.56 → 0.48 (-14.8%) | 0.68 → 0.70 (+2.0%) | 2.95 → 2.95 (+0.0%) | 0.48 → 0.48 (+0.0%) | 1896 → 1896 (+0.0%) | 4.92 → 4.92 (-0.0%) |
| Coconut Mall | 912 identical | 52/52 | 106 → 105 (-0.8%) | 22033 → 21969 (-0.3%) | 0.54 → 0.36 (-32.6%) | 0.64 → 0.68 (+7.4%) | 2.51 → 2.51 (+0.0%) | 0.46 → 0.46 (+0.0%) | 1728 → 1728 (+0.0%) | 5.57 → 5.25 (-5.7%) |
| DK Summit | 912 identical | 52/52 | 82.9 → 82.9 (+0.0%) | 14538 → 14542 (+0.0%) | 0.43 → 0.29 (-32.6%) | 0.68 → 0.72 (+5.5%) | 3.06 → 3.06 (+0.0%) | 0.29 → 0.29 (+0.0%) | 1587 → 1587 (+0.0%) | 3.77 → 3.77 (+0.0%) |
| Wario's Gold Mine | 912 identical | 52/52 | 84.0 → 83.8 (-0.2%) | 17108 → 17076 (-0.2%) | 0.32 → 0.24 (-22.5%) | 0.71 → 0.68 (-3.3%) | 2.86 → 2.86 (+0.0%) | 0.30 → 0.30 (+0.0%) | 1586 → 1586 (+0.0%) | 3.81 → 3.77 (-0.9%) |
| Daisy Circuit | 912 identical | 52/52 | 88.9 → 88.9 (+0.0%) | 23253 → 23252 (-0.0%) | 0.52 → 0.43 (-17.7%) | 0.65 → 0.68 (+3.9%) | 2.73 → 2.73 (+0.0%) | 0.11 → 0.11 (+0.0%) | 2626 → 2626 (+0.0%) | 4.82 → 4.82 (-0.1%) |
| Koopa Cape | 912 identical | 52/52 | 87.3 → 86.3 (-1.2%) | 14439 → 14382 (-0.4%) | 0.34 → 0.28 (-19.6%) | 0.72 → 0.75 (+3.7%) | 3.01 → 3.01 (+0.0%) | 0.20 → 0.20 (+0.0%) | 1619 → 1619 (+0.0%) | 3.87 → 3.84 (-0.6%) |
| Maple Treeway | 912 identical | 52/52 | 73.6 → 73.5 (-0.0%) | 15512 → 15511 (-0.0%) | 0.39 → 0.20 (-49.3%) | 0.70 → 0.78 (+12.5%) | 2.75 → 2.75 (+0.0%) | 0.43 → 0.43 (+0.0%) | 1663 → 1663 (+0.0%) | 3.40 → 3.39 (-0.3%) |
| Grumble Volcano | 912 identical | 52/52 | 118 → 113 (-4.0%) | 16224 → 16076 (-0.9%) | 0.28 → 0.23 (-17.7%) | 0.76 → 0.78 (+2.7%) | 2.69 → 2.69 (+0.0%) | 0.35 → 0.35 (+0.0%) | 1374 → 1374 (+0.0%) | 4.80 → 4.62 (-3.7%) |
| Dry Dry Ruins | 912 identical | 52/52 | 70.0 → 70.0 (+0.0%) | 13061 → 13061 (+0.0%) | 0.29 → 0.24 (-19.1%) | 0.69 → 0.67 (-2.5%) | 2.39 → 2.39 (+0.0%) | 0.14 → 0.14 (+0.0%) | 1213 → 1213 (+0.0%) | 3.21 → 3.22 (+0.1%) |
| Moonview Highway | 912 identical | 52/52 | 92.5 → 91.8 (-0.7%) | 17498 → 17444 (-0.3%) | 0.56 → 0.39 (-30.7%) | 0.67 → 0.70 (+5.3%) | 2.77 → 2.77 (+0.0%) | 0.11 → 0.11 (+0.0%) | 1432 → 1432 (+0.0%) | 4.28 → 4.24 (-1.0%) |
| Bowser's Castle | 912 identical | 52/52 | 84.8 → 84.8 (-0.0%) | 24844 → 24844 (-0.0%) | 0.65 → 0.34 (-47.4%) | 0.61 → 0.72 (+19.1%) | 2.95 → 2.95 (+0.0%) | 0.57 → 0.57 (+0.0%) | 2398 → 2398 (+0.0%) | 4.21 → 4.21 (-0.0%) |
| Rainbow Road | 912 identical | 52/52 | 75.3 → 75.4 (+0.0%) | 15550 → 15550 (+0.0%) | 0.28 → 0.15 (-47.2%) | 0.73 → 0.74 (+2.4%) | 2.34 → 2.34 (+0.0%) | 0.46 → 0.46 (+0.0%) | 1220 → 1220 (+0.0%) | 4.04 → 4.03 (-0.3%) |
| GCN Peach Beach | 912 identical | 52/52 | 88.7 → 88.6 (-0.2%) | 18821 → 18772 (-0.3%) | 0.49 → 0.41 (-16.7%) | 0.68 → 0.71 (+5.3%) | 2.88 → 2.88 (+0.0%) | 0.56 → 0.56 (+0.0%) | 2150 → 2150 (+0.0%) | 4.59 → 4.58 (-0.1%) |
| DS Yoshi Falls | 912 identical | 52/52 | 74.0 → 74.0 (-0.0%) | 11297 → 11297 (-0.0%) | 0.01 → 0.00 (-34.6%) | 0.83 → 0.82 (-1.2%) | 3.34 → 3.34 (+0.0%) | 0.23 → 0.23 (+0.0%) | 1722 → 1722 (+0.0%) | 3.24 → 3.23 (-0.1%) |
| SNES Ghost Valley 2 | 912 identical | 52/52 | 122 → 122 (-0.0%) | 6815 → 6814 (-0.0%) | 2.73 → 1.62 (-40.6%) | 0.10 → 0.26 (+166.1%) | 2.91 → 2.91 (+0.0%) | 0.20 → 0.20 (+0.0%) | 1467 → 1467 (+0.0%) | 6.85 → 6.76 (-1.2%) |
| N64 Mario Raceway | 912 identical | 52/52 | 69.9 → 69.9 (+0.0%) | 9744 → 9744 (+0.0%) | 0.14 → 0.08 (-40.8%) | 0.81 → 0.82 (+1.2%) | 3.19 → 3.19 (+0.0%) | 0.15 → 0.15 (+0.0%) | 1773 → 1773 (+0.0%) | 3.23 → 3.23 (-0.1%) |
| N64 Sherbet Land | 912 identical | 52/52 | 60.7 → 60.7 (+0.0%) | 8460 → 8460 (+0.0%) | 0.15 → 0.11 (-26.9%) | 0.77 → 0.76 (-0.5%) | 3.55 → 3.55 (+0.0%) | 0.05 → 0.05 (+0.0%) | 1953 → 1953 (+0.0%) | 3.05 → 3.04 (-0.3%) |
| GBA Shy Guy Beach | 912 identical | 52/52 | 67.3 → 67.3 (-0.0%) | 9611 → 9611 (-0.0%) | 0.24 → 0.18 (-26.7%) | 0.76 → 0.79 (+3.1%) | 3.16 → 3.16 (+0.0%) | 0.51 → 0.51 (+0.0%) | 1706 → 1706 (+0.0%) | 3.24 → 3.24 (-0.1%) |
| DS Delfino Square | 912 identical | 52/52 | 76.4 → 76.4 (-0.0%) | 17280 → 17280 (-0.0%) | 0.39 → 0.27 (-31.3%) | 0.67 → 0.73 (+8.5%) | 2.43 → 2.43 (+0.0%) | 0.44 → 0.44 (+0.0%) | 1681 → 1681 (+0.0%) | 3.82 → 3.81 (-0.2%) |
| GCN Waluigi Stadium | 912 identical | 52/52 | 96.4 → 95.7 (-0.7%) | 15038 → 15025 (-0.1%) | 0.69 → 0.38 (-44.8%) | 0.64 → 0.71 (+11.0%) | 2.98 → 2.98 (+0.0%) | 0.30 → 0.30 (+0.0%) | 1539 → 1539 (+0.0%) | 4.53 → 4.48 (-1.1%) |
| DS Desert Hills | 912 identical | 52/52 | 67.9 → 67.9 (-0.0%) | 10926 → 10921 (-0.0%) | 0.04 → 0.04 (+9.3%) | 0.80 → 0.77 (-2.8%) | 2.30 → 2.30 (+0.0%) | 0.60 → 0.60 (+0.0%) | 1169 → 1169 (+0.0%) | 2.94 → 2.93 (-0.3%) |
| GBA Bowser Castle 3 | 912 identical | 52/52 | 67.8 → 67.8 (+0.0%) | 6900 → 6900 (+0.0%) | 0.15 → 0.09 (-39.1%) | 0.75 → 0.76 (+1.7%) | 2.48 → 2.48 (+0.0%) | 0.23 → 0.23 (+0.0%) | 1261 → 1261 (+0.0%) | 2.98 → 2.98 (+0.1%) |
| N64 DK's Jungle Parkway | 912 identical | 52/52 | 88.4 → 88.3 (-0.0%) | 16591 → 16589 (-0.0%) | 0.40 → 0.33 (-17.6%) | 0.76 → 0.76 (+0.9%) | 3.37 → 3.37 (+0.0%) | 0.14 → 0.14 (+0.0%) | 1750 → 1750 (+0.0%) | 5.07 → 5.08 (+0.1%) |
| GCN Mario Circuit | 912 identical | 52/52 | 79.3 → 79.3 (-0.0%) | 14997 → 14997 (+0.0%) | 0.43 → 0.33 (-21.4%) | 0.73 → 0.71 (-2.9%) | 3.66 → 3.66 (+0.0%) | 0.42 → 0.42 (+0.0%) | 2085 → 2085 (+0.0%) | 3.86 → 3.85 (-0.2%) |
| SNES Mario Circuit 3 | 912 identical | 52/52 | 69.7 → 69.7 (-0.0%) | 8554 → 8554 (-0.0%) | 0.15 → 0.10 (-38.0%) | 0.81 → 0.78 (-3.7%) | 3.71 → 3.71 (+0.0%) | 0.16 → 0.16 (+0.0%) | 1898 → 1898 (+0.0%) | 3.10 → 3.10 (-0.1%) |
| DS Peach Gardens | 912 identical | 52/52 | 80.1 → 80.1 (-0.0%) | 11470 → 11469 (-0.0%) | 0.23 → 0.16 (-31.2%) | 0.73 → 0.75 (+3.3%) | 2.69 → 2.69 (+0.0%) | 0.31 → 0.31 (+0.0%) | 1343 → 1343 (+0.0%) | 3.51 → 3.53 (+0.4%) |
| GCN DK Mountain | 912 identical | 52/52 | 83.8 → 83.8 (+0.0%) | 11781 → 11780 (-0.0%) | 0.67 → 0.44 (-34.2%) | 0.62 → 0.67 (+7.4%) | 2.80 → 2.80 (+0.0%) | 0.31 → 0.31 (+0.0%) | 1428 → 1428 (+0.0%) | 3.81 → 3.79 (-0.3%) |
| N64 Bowser's Castle | 912 identical | 52/52 | 76.3 → 75.1 (-1.6%) | 13357 → 13155 (-1.5%) | 0.27 → 0.18 (-33.0%) | 0.73 → 0.78 (+7.5%) | 2.85 → 2.85 (+0.0%) | 0.55 → 0.55 (+0.0%) | 1468 → 1468 (+0.0%) | 3.40 → 3.30 (-3.0%) |

`opponents_ms` rises on most courses although the racer work is unchanged (same pose
evaluations and skinned vertices): in this (main checkout) layout the course-object
recording and the racer posing share the core-1 job worker; with the object job done
sooner the worker takes racer jobs the main thread used to run itself, and the main thread
waits for them. Net main-thread drawing time (course + scenery + opponents + items +
player + effects + HUD + bottom screen) still drops on 30 courses (−0.02 to −0.94 ms per
frame; Bowser's Castle −0.18, GCN Waluigi Stadium −0.24, GCN DK Mountain −0.20, SNES Ghost
Valley 2 −0.94) and is flat on Grumble Volcano (+0.008) and DS Desert Hills (+0.005); worker
time drops on every course (mean 0.318 → 0.267 ms). With the engine branch's main-core
layout the saved work simply disappears from core 0.

Object/racer visibility in those runs (QA counters, tests = objects/racers that passed the
frustum and size tests with the camera inside a column):

| Course | Objects hidden / tested | Racers hidden / tested |
| --- | --- | --- |
| Grumble Volcano | 1432 / 6323 (23%) | 0 / 4700 |
| N64 Bowser's Castle | 1031 / 6377 (16%) | 172 / 4878 (4%) |
| GCN Waluigi Stadium | 784 / 15511 (5%) | 0 / 4049 |
| Wario's Gold Mine | 728 / 8763 (8%) | 0 / 3794 |
| Moonview Highway | 561 / 15483 (4%) | 0 / 5442 |
| Mushroom Gorge | 171 / 9615 (2%) | 556 / 4156 (13%) |
| Koopa Cape | 41 / 4998 (1%) | 796 / 4926 (16%) |
| GCN Peach Beach | 99 / 19953 | 0 / 6290 |
| Bowser's Castle, 3,000 steps | 3013 / 16599 (18%) | 0 / 6589 |
| other courses | ≤ 20 | 0 |

Longer runs, Bowser's Castle 3,000 steps, cull-only vs cull + object PVS: draws 92.4 → 89.5
(−3.1%), triangles −0.5%, render thread 4.50 → 4.35 ms (−3.3%), 102/102 captures identical.

### Sidecars

Built from this worktree's staged Old 3DS models (`build/o3ds-ovis/models/pvs`, 25 files,
1.8 MB; about 50 minutes on this Mac shared with emulator runs):

| Course | Regions (chunks + object boxes) | Sets | KB | Hidden chunks / boxes per column | Raster s |
| --- | --- | --- | --- | --- | --- |
| Luigi Circuit | 32 (2 + 30) | 67 | 26 | 1% / 3% | 35 |
| Moo Moo Meadows | 0 (no sidecar) | – | – | – | 38.3 |
| Mushroom Gorge | 608 (458 + 150) | 256 | 82 | 20% / 17% | 72 |
| Toad's Factory | 674 (422 + 252) | 256 | 77 | 22% / 20% | 51 |
| Mario Circuit | 177 (126 + 51) | 256 | 38 | 5% / 5% | 29 |
| Coconut Mall | 1169 (885 + 284) | 256 | 165 | 14% / 7% | 358 |
| DK Summit | 524 (413 + 111) | 256 | 77 | 12% / 9% | 57 |
| Wario's Gold Mine | 475 (322 + 153) | 256 | 71 | 41% / 42% | 92 |
| Daisy Circuit | 149 (95 + 54) | 252 | 32 | 7% / 6% | 21 |
| Koopa Cape | 931 (735 + 196) | 256 | 124 | 22% / 21% | 100 |
| Maple Treeway | 765 (569 + 196) | 256 | 157 | 5% / 4% | 330 |
| Grumble Volcano | 894 (471 + 423) | 256 | 91 | 16% / 14% | 47 |
| Dry Dry Ruins | 1013 (817 + 196) | 256 | 111 | 3% / 3% | 79 |
| Moonview Highway | 724 (520 + 204) | 256 | 89 | 22% / 14% | 62 |
| Bowser's Castle | 920 (650 + 270) | 256 | 104 | 21% / 25% | 250 |
| Rainbow Road | 3 left (no sidecar: under 8) | – | – | – | 49 |
| GCN Peach Beach | 173 (116 + 57) | 241 | 53 | 2% / 3% | 85 |
| DS Yoshi Falls | 7 left (no sidecar: under 8) | – | – | – | 126 |
| SNES Ghost Valley 2 | 0 (no sidecar) | – | – | – | 6.2 |
| N64 Mario Raceway | 268 (250 + 18) | 255 | 56 | 4% / 3% | 80 |
| N64 Sherbet Land | 297 (214 + 83) | 256 | 63 | 37% / 28% | 39 |
| GBA Shy Guy Beach | 0 (no sidecar) | – | – | – | 16.2 |
| DS Delfino Square | 106 (80 + 26) | 249 | 56 | 4% / 6% | 38 |
| GCN Waluigi Stadium | 71 (57 + 14) | 256 | 25 | 7% / 14% | 37 |
| DS Desert Hills | 270 (206 + 64) | 255 | 46 | 6% / 6% | 43 |
| GBA Bowser Castle 3 | 0 (no sidecar) | – | – | – | 17.7 |
| N64 DK's Jungle Parkway | 95 (46 + 49) | 119 | 41 | 1% / 1% | 26 |
| GCN Mario Circuit | 543 (424 + 119) | 256 | 67 | 4% / 5% | 41 |
| SNES Mario Circuit 3 | 0 (no sidecar) | – | – | – | 15.2 |
| DS Peach Gardens | 56 (56 + 0) | 247 | 81 | 2% / 0% | 100 |
| GCN DK Mountain | 389 (307 + 82) | 256 | 79 | 6% / 6% | 89 |
| N64 Bowser's Castle | 558 (400 + 158) | 256 | 76 | 32% / 31% | 44 |

### Pose LOD (same binary, `o3ds-pose-lod-off.txt` vs on; exact culls on in both)

| Course | CPU log | Captures identical | pose_evaluations | pose_reuses | skinned_vertices | opponents_ms |
| --- | --- | --- | --- | --- | --- | --- |
| Luigi Circuit | 912 identical | 45/52 (max 28 px) | 2.67 → 2.29 (-14.3%) | 0.41 → 0.80 (+93.6%) | 1356 → 1153 (-15.0%) | 0.738 → 0.745 (+0.9%) |
| Mario Circuit | 912 identical | 49/52 (max 27 px) | 2.95 → 2.50 (-15.2%) | 0.48 → 0.93 (+94.6%) | 1896 → 1660 (-12.5%) | 0.721 → 0.700 (-2.8%) |
| Bowser's Castle | 912 identical | 46/52 (max 30 px) | 2.95 → 2.65 (-10.3%) | 0.57 → 0.88 (+53.6%) | 2398 → 2243 (-6.5%) | 0.681 → 0.681 (+0.1%) |

The differing pixels are a one-frame pose lag on drivers about 15 px tall (arms, heads),
mostly at the race start when the pack sits small on the grid (review crop:
`build/o3ds-scenery-lod-ab/sheets/crop-intro-05-0720.png`). The saving is small in emulated
time; on hardware pose evaluation is VFP-latency bound and worth more. If even this is
unwanted, `PoseLod::ratio` (0.05) can be lowered or the LOD disabled with one line in
`main.cpp`; nothing else depends on it.

## A/B frames

`tools/o3ds_course_tour.py` on Bowser's Castle, Coconut Mall, Maple Treeway and Rainbow
Road (9 intro fly-over frames + 7 race frames each), three passes with the same 3DSX:
everything off, exact culls only (`o3ds-pose-lod-off.txt`), everything on. Sheets
(OFF | ON | changed pixels in red) via `tools/o3ds_ab_sheet.py`, kept out of git with the
other game-derived images:

- `build/o3ds-scenery-lod-ab/sheets-exact/` — exact culls vs off: **64/64 frames identical**.
- `build/o3ds-scenery-lod-ab/sheets/` — everything on vs off: 10 of 64 frames differ, by
  7–29 pixels, all on small opponents' drivers (pose LOD).

Together with the campaign above (1,664 race captures, all identical with the exact
changes), the scenery changes are invisible by construction and by measurement.

## Files

New: `include/mkw/object_pvs.hpp`, `src/object_pvs.cpp` (visibility runtime),
`include/mkw/world_cull.hpp` (early reject), `include/mkw/pose_lod.hpp`,
`tools/build_o3ds_object_pvs.py` (sidecar builder), `tools/o3ds_lod_compare.py` (A/B: CPU
log, captures, means), `tools/o3ds_ab_sheet.py` (review sheets), tests
`tools/test_object_pvs.cpp`, `tools/test_world_cull.cpp`, `tests/test_object_pvs.py`.

Changed, kept small because other branches edit the same files:

- `src/main.cpp` (engine branch also edits it): `#include`s; `poseLod().configure` and
  `worldCullEnabled()` next to the `racerDistance().configure` call; `objectPvsOn` next to
  `coursePvs`; `attachObjectPvs` after each of the two `courseSections.attach` calls; a QA
  log line and `objectPvs().clear()` in `releaseRace`.
- `src/racer_visuals.cpp` (engine branch also edits it): per-racer `pvsRadius`/`reach` at
  load; in `prepareRacer` the object-PVS test after the envelope test and `keepPose` in the
  two `apply` calls; `frame.pvs`/`frame.count` in `openFrame`.
- `src/renderer.cpp`: not changed.
- `src/course_objects.cpp`, `include/mkw/course_object_kinds.hpp`, `src/course_object_kinds.cpp`:
  `ObjectDraw::world/pvs/culled`, the early reject in `submit`, palms, firebar group test,
  joint cache, pose envelopes.
- `include/mkw/animation.hpp`, `src/animation.cpp`: `keepPose`, `poseVisible` refactor.
- `tools/pvs/pvs_raster.cpp`: input version 2 (boxes); version 1 unchanged.
- `tools/o3ds_perf_run.py`: `--emulator DIR`, launch with `open -n -g -a`.

QA markers (QA builds only; release has no markers): `o3ds-objcull-off.txt`,
`o3ds-objpvs-off.txt`, `o3ds-pose-lod-off.txt`, `o3ds-pose-lod.txt` (another ratio).
`full-quality.txt` turns all three off, like the other Old 3DS reductions.

## Overlap with the engine branch

The engine branch (branch `o3ds-engine-perf`)
lists "opponent presentation LOD" and "course-object animation at reduced rate" in its
backlog; both are covered here, so it can drop them. Its `VehicleAnimation` laziness (only
visible racers build wheel matrices) composes with the racer visibility test, which runs
before posing. Its main-core layout removes the worker/main wait shift seen above. No
other overlap: this work does not touch Kinoko, citro3d, the render thread or `renderer.cpp`.
Racer distance culling (`racer_distance.hpp`, QA experiment) is untouched.

## Follow-ups

- **Packaging**: `models/pvs/course_NN.opvs` must ship next to the track PVS's
  `models/pvs/*.pvs` (`tools/package_sd.py`, `tools/stage_o3ds_lowpoly.py`,
  `tools/release/build_cia.py`). Rebuild after any course overlay re-cook:
  `.venv/bin/python tools/build_o3ds_object_pvs.py --models <SD>/3ds/mkw-3ds/models --out <SD>/3ds/mkw-3ds/models/pvs`
  (about 50 minutes for all courses). A stale sidecar is rejected and the course draws as
  before. The fingerprint ignores triangle order, so the track PVS and vertex-cache passes
  do not invalidate it.
- Memory: the sidecar plus its hidden lists stay resident during a race (up to 190 KB,
  Coconut Mall), next to the track PVS's. The column table is most of it on large courses.
- Racers are hidden only on a few courses (chunks are large); smaller chunks would hide
  more at more bits per set.
- Physical Old 3DS timing is unmeasured; emulated time counts instructions, so the saved
  matrix products, trig and lookups are probably worth more on hardware (VFP latency).
