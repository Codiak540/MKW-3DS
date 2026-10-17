# Old 3DS track sectioning and precomputed visibility (PVS)

Branch `o3ds-pvs` (a separate worktree; merged into main). Goal: draw only
what can be seen from the current part of the track on Old 3DS, with zero visual
change and zero physics change. New 3DS is unchanged.

## What was found first

The renderer already culls each course submesh in *blocks* of 128 consecutive
triangles (`Model::enableStaticCulling`, `mkw::buildVisibility`), keeping the
authored triangle order. Course meshes are stored per material, and the authored
(and O3RM low-poly) index order is not spatial: the median block spans
**41,000–77,000 world units**, about half a course. Such blocks almost never leave
the view frustum, and from a single camera position almost every block has
*some* visible triangle (Luigi Circuit 97%, Bowser's Castle 70% of course
triangles), even though only 57% / 15% of the individual triangles are visible.
So a PVS over the existing blocks could not cull anything; the course had to be
sectioned first.

## Method

### Track sectioning (the "chunks")
`tools/build_o3ds_pvs.py` reorders the triangles **inside** each submesh whose
triangle order cannot change the image: opaque or alpha-tested, depth tested
and depth writing, not blended (the same state `Renderer::drawNow` derives from
the submesh flags). A k-d tree over triangle centroids, with every split a
multiple of the block size, makes each block (128 triangles, the renderer's
existing size) one compact spatial chunk; triangles inside a leaf keep their
authored relative order.

Unchanged: every vertex byte (roads included), every triangle, submesh order,
materials, and the triangle order of blended / non-depth-writing submeshes.
The only image-relevant effect of reordering opaque triangles would be the
winner of an exactly equal depth tie inside one submesh; every capture below
is pixel-identical.

### Visibility
Camera space = vertical columns of 1000 x 1000 units in XZ around every
drivable KCL surface (road and off-road types, `tools/o3ds_kcl.py`), out to
1200 units beyond them. Each column has one height interval: lowest drivable
height within reach − 400 to highest + 1200 (chase camera height, jumps).
Inside each column the camera is sampled on a lattice: 2 x 2 horizontally
(500 units), every 333 units vertically including both ends.

Per sample (`tools/pvs/pvs_raster.cpp`, multithreaded C++, host clang):
six cube faces widened to 96°, 256² each, near plane 10 (the game's).
Pass 1 rasterises the depth of occluders only (opaque, depth-writing, not
alpha-tested, not blended; back faces culled exactly as the game culls them —
the authored winding is clockwise to the outward side, checked against ground
area: 86–99% of horizontal course area has its geometric normal pointing down).
The depth is eroded by one pixel (farthest of 3x3). Pass 2 tests every
triangle (all materials, **without** back-face culling, conservatively
rasterised: any pixel the triangle touches, nearest depth over the pixel)
against it; one passing pixel marks the block. Column set = union over its
samples, then padded with its eight neighbours' sets. Identical sets are
stored once.

Why camera columns rather than checkpoint regions: visibility depends on the
camera, not the kart, and the camera trails the kart, swings in drifts, and
follows respawns and finish/replay views. A column lookup from the camera
position is O(1), needs no Kinoko state, and has an exact "inside the sampled
volume" test, so any camera outside it (intro fly-overs, cannon flights, falls)
simply draws everything. KMP checkpoints are not needed for correctness; the
KMP enemy route is used offline to estimate the benefit (below).

### Sidecar `models/pvs/<part>.pvs`
Header with an FNV-1a fingerprint of the geometry `Model::load` produced on
Old 3DS (counts, submesh spans, indices, positions — i.e. with the O3RM course
overlay applied), block size and count, column grid; then the per-submesh
triangle permutation (u16), the columns {yMin, yMax, set}, and the sets
(one bit per block). 24–228 KB per course, 3.2 MB for all 32.

### Runtime (Old 3DS only)
- `src/course_pvs.cpp`, `include/mkw/course_pvs.hpp` (new): `CourseSections::attach`
  after a course loads (`loadTrack`, `restoreRaceGraphics`): validates the
  sidecar fully (fingerprint, counts, permutation is a permutation, set indices),
  applies the permutation to the index buffer, flushes it, rebuilds the blocks.
  Anything invalid or missing → the ordinary `enableStaticCulling()`.
- `src/main.cpp`: per frame, per track part, `mask(renderer.cameraPosition())`
  → `Renderer::setBlockMask` around the track draw.
- `include/mkw/visibility.hpp`: a `selectVisible` overload that skips masked-out
  blocks before their frustum test (a submesh with none left skips its own test).
- `src/renderer.cpp` (drawNow cull branch), `src/render_thread.cpp` (`CmdBlockMask`,
  recorded like `CmdOverrides`), `include/mkw/renderer.hpp` (`setBlockMask`).
- Gate: `modelDetail().hardware == ModelDetail::Old3DS`. QA markers:
  `o3ds-pvs-off.txt` (authored order, no masks = previous behaviour),
  `o3ds-pvs-mask-off.txt` (sectioning only).

## Verification

All runs: this worktree's own Azahar copy, Old 3DS mode, unthrottled, the same
3DSX with and without markers, `tools/o3ds_perf_run.py` (engine branch's
runner, plus `--course`), VS race, Mario, 11 CPU racers, player CPU-driven,
lockstep race captures. Compare: `tools/pvs/compare_runs.py`.

Physics: replay QA test (Funky Kong ghost, Luigi Circuit, two finishes)
**passes**, result byte-identical to the engine branch's baseline
(`physics_steps 9064`, heap `7029484/7029484`, same step timings).

Every VS course, 1500 steps, captures every 60 steps, `o3ds-pvs-off.txt` (the
previous behaviour) → sectioning + PVS. Triangles / draws are whole-frame
(course, scenery, karts, items); emulated ms are ARM11 guest time on the
render thread (this baseline build still draws on the core-1 render thread).

| Course | Captures identical | Triangles / frame | Draws / frame | cull_ms | render_thread_ms |
| --- | --- | --- | --- | --- | --- |
| Luigi Circuit | 52/52 | 14439 → 11976 (-17.1%) | 90.1 → 91.3 (+1.3%) | 0.159 → 0.149 (-6.4%) | 3.89 → 3.90 (+0.4%) |
| Moo Moo Meadows | 52/52 | 16530 → 14315 (-13.4%) | 76.7 → 79.3 (+3.3%) | 0.183 → 0.176 (-3.8%) | 3.72 → 3.74 (+0.4%) |
| Mushroom Gorge | 52/52 | 13408 → 12405 (-7.5%) | 83.8 → 83.1 (-0.8%) | 0.143 → 0.143 (+0.3%) | 3.91 → 3.89 (-0.3%) |
| Toad's Factory | 52/52 | 16134 → 13331 (-17.4%) | 95.9 → 93.1 (-2.9%) | 0.159 → 0.141 (-10.9%) | 4.05 → 4.01 (-1.0%) |
| Mario Circuit | 52/52 | 18134 → 15735 (-13.2%) | 105.4 → 106.6 (+1.1%) | 0.171 → 0.164 (-3.9%) | 4.92 → 4.91 (-0.1%) |
| Coconut Mall | 52/52 | 20751 → 19289 (-7.0%) | 105.4 → 105.8 (+0.4%) | 0.273 → 0.277 (+1.5%) | 5.54 → 5.55 (+0.2%) |
| DK Summit | 52/52 | 13043 → 10923 (-16.3%) | 82.9 → 83.3 (+0.4%) | 0.146 → 0.133 (-8.9%) | 3.76 → 3.75 (-0.3%) |
| Wario's Gold Mine | 52/52 | 15427 → 11432 (-25.9%) | 83.6 → 82.5 (-1.4%) | 0.156 → 0.143 (-8.1%) | 3.79 → 3.77 (-0.7%) |
| Daisy Circuit | 52/52 | 22066 → 20281 (-8.1%) | 88.7 → 89.1 (+0.5%) | 0.165 → 0.164 (-0.4%) | 4.82 → 4.82 (-0.0%) |
| Koopa Cape | 52/52 | 12855 → 11181 (-13.0%) | 86.6 → 82.5 (-4.7%) | 0.169 → 0.158 (-6.3%) | 3.85 → 3.80 (-1.2%) |
| Maple Treeway | 52/52 | 12698 → 12371 (-2.6%) | 72.2 → 72.4 (+0.3%) | 0.131 → 0.134 (+2.2%) | 3.32 → 3.31 (-0.3%) |
| Grumble Volcano | 52/52 | 14778 → 11703 (-20.8%) | 118.1 → 117.5 (-0.5%) | 0.173 → 0.160 (-7.9%) | 4.79 → 4.75 (-0.9%) |
| Dry Dry Ruins | 52/52 | 11484 → 11086 (-3.5%) | 70.0 → 70.1 (+0.2%) | 0.128 → 0.133 (+4.4%) | 3.21 → 3.22 (+0.5%) |
| Moonview Highway | 52/52 | 15203 → 14150 (-6.9%) | 92.3 → 91.7 (-0.7%) | 0.172 → 0.173 (+0.5%) | 4.28 → 4.25 (-0.6%) |
| Bowser's Castle | 52/52 | 23758 → 16796 (-29.3%) | 84.9 → 84.6 (-0.3%) | 0.207 → 0.174 (-15.8%) | 4.21 → 4.18 (-0.9%) |
| Rainbow Road | 52/52 | 14035 → 11386 (-18.9%) | 75.2 → 73.6 (-2.2%) | 0.150 → 0.130 (-13.2%) | 4.06 → 4.03 (-0.6%) |
| GCN Peach Beach | 52/52 | 16388 → 13648 (-16.7%) | 88.4 → 89.0 (+0.7%) | 0.110 → 0.097 (-11.4%) | 4.56 → 4.57 (+0.1%) |
| DS Yoshi Falls | 52/52 | 9933 → 8866 (-10.7%) | 73.9 → 73.2 (-0.9%) | 0.121 → 0.117 (-3.2%) | 3.23 → 3.21 (-0.4%) |
| SNES Ghost Valley 2 | 52/52 | 5720 → 5037 (-11.9%) | 122.5 → 122.4 (-0.0%) | 0.042 → 0.035 (-15.4%) | 6.84 → 6.84 (+0.1%) |
| N64 Mario Raceway | 52/52 | 8311 → 6970 (-16.1%) | 69.9 → 69.7 (-0.4%) | 0.105 → 0.097 (-7.6%) | 3.21 → 3.21 (+0.1%) |
| N64 Sherbet Land | 52/52 | 6639 → 5788 (-12.8%) | 60.6 → 61.3 (+1.2%) | 0.065 → 0.059 (-9.4%) | 3.02 → 3.02 (+0.1%) |
| GBA Shy Guy Beach | 52/52 | 8355 → 7388 (-11.6%) | 67.2 → 68.1 (+1.3%) | 0.077 → 0.075 (-2.6%) | 3.22 → 3.23 (+0.4%) |
| DS Delfino Square | 52/52 | 16180 → 15067 (-6.9%) | 76.2 → 76.4 (+0.4%) | 0.139 → 0.135 (-3.2%) | 3.81 → 3.81 (+0.2%) |
| GCN Waluigi Stadium | 52/52 | 13727 → 11317 (-17.6%) | 96.8 → 98.2 (+1.5%) | 0.149 → 0.141 (-5.5%) | 4.53 → 4.54 (+0.2%) |
| DS Desert Hills | 52/52 | 9523 → 7980 (-16.2%) | 67.5 → 68.8 (+1.9%) | 0.093 → 0.085 (-8.5%) | 2.93 → 2.93 (+0.2%) |
| GBA Bowser Castle 3 | 52/52 | 5750 → 5568 (-3.2%) | 67.8 → 67.5 (-0.5%) | 0.079 → 0.079 (-0.4%) | 2.98 → 2.99 (+0.2%) |
| N64 DK's Jungle Parkway | 52/52 | 14870 → 13488 (-9.3%) | 88.3 → 89.9 (+1.7%) | 0.155 → 0.158 (+1.5%) | 5.07 → 5.08 (+0.0%) |
| GCN Mario Circuit | 52/52 | 12965 → 10333 (-20.3%) | 79.2 → 82.5 (+4.1%) | 0.132 → 0.121 (-8.6%) | 3.83 → 3.86 (+0.6%) |
| SNES Mario Circuit 3 | 52/52 | 7330 → 6350 (-13.4%) | 69.7 → 70.5 (+1.2%) | 0.059 → 0.053 (-11.1%) | 3.09 → 3.10 (+0.1%) |
| DS Peach Gardens | 52/52 | 10379 → 9486 (-8.6%) | 80.1 → 80.2 (+0.1%) | 0.148 → 0.147 (-0.7%) | 3.51 → 3.53 (+0.4%) |
| GCN DK Mountain | 52/52 | 9900 → 8613 (-13.0%) | 83.8 → 82.9 (-1.1%) | 0.100 → 0.090 (-9.9%) | 3.80 → 3.78 (-0.6%) |
| N64 Bowser's Castle | 52/52 | 12504 → 11391 (-8.9%) | 75.6 → 75.0 (-0.7%) | 0.130 → 0.119 (-8.3%) | 3.38 → 3.35 (-0.7%) |

**All 32 courses: 1664 / 1664 captures pixel-identical.** Triangles per frame
−2.6% to −29.3%, mean −13.1%. Draws −4.7% to +4.1%; culling time mostly lower
(same block count, but whole submeshes and blocks now leave the frustum);
render-thread time within ±1.2%. Masks were in use on 32,074 of 37,521 frames
(85%, intro fly-overs included in the remainder).

Masks vs sectioning only (same binary, `o3ds-pvs-mask-off.txt`; the first
1500 steps of most courses are open, so a longer run on a closed course):

| Run | Captures identical | Triangles / frame | Draws / frame | cull_ms | render_thread_ms |
| --- | --- | --- | --- | --- | --- |
| Bowser's Castle, 6000 steps | 102/102 | 16279 → 15683 (-3.7%) | 93.9 → 94.2 (+0.4%) | 0.166 → 0.164 (-0.7%) | 4.43 → 4.43 (+0.0%) |

So nearly all of the measured saving is the sectioning (spatial blocks that the
existing frustum culling can now reject); the PVS adds a few percent on
closed courses and nothing on open ones (Luigi Circuit −0.2%).

Block size: 64 was tried first (Luigi Circuit −26% triangles, Bowser's Castle
−34%) but cost +10% draws and +3% render-thread time (twice the blocks to test,
more index ranges per submesh). 128 keeps the renderer's block count, so the
culling work is unchanged and the saving is free on the CPU side. With the
engine branch moving Old 3DS race drawing onto core 0, CPU-neutral matters.

Offline estimate over a whole lap (`tools/pvs/upper_bound.py`: simulated chase
cameras every half KMP enemy point, 650 behind / 280 above, 62° vertical FOV;
course-mesh triangles in frustum-visible blocks):

| Course | Triangles in frustum blocks | + exact per-camera occlusion | + column PVS (shipped) |
| --- | --- | --- | --- |
| Bowser's Castle | 12,700 | 8,853 (−30.3%) | 10,991 (−13.5%) |
| Toad's Factory | 8,487 | 5,750 (−32.2%) | 7,056 (−16.9%) |
| Wario's Gold Mine | 7,850 | 6,014 (−23.4%) | 6,802 (−13.3%) |
| Koopa Cape | 8,744 | 4,074 (−53.4%) | 7,310 (−16.4%) |
| N64 Bowser's Castle | 4,968 | 2,871 (−42.2%) | 4,464 (−10.2%) |
| Luigi Circuit | 8,222 | 8,094 (−1.6%) | 8,212 (−0.1%) |

The script also asserts conservativeness: for all ~1,300 route cameras no block
visible from the exact camera is missing from its column's set. The gap between
the exact and column columns is the price of conservative columns (1000-unit
cells, full height interval, all directions, neighbour padding); tighter cells
or direction buckets would recover part of it at more sampling time.

## Per-course sidecars
All built from the staged Old 3DS overlay (`models/o3ds/course_*_000.gmdl`
applied; every part was a variant). Parameters: block 128, cell 1000, margin
1200, below 400, above 1200, step 500, vstep 333, 256² faces, 96°, near 10.
47 minutes for all 32 on this Mac (10 threads, shared with emulator runs).
"Visible per column" is the triangle-weighted share of course blocks in a
column's set (all directions).

| Course | Part | Triangles | Blocks | Median block span authored → sectioned | Columns | Samples | Sets | KB | Visible per column raw / padded | Build s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Luigi Circuit | course_08_000 | 13,034 | 115 | 61,914 → 37,115 | 2,298 | 77,212 | 28 | 71 | 99.7% / 99.9% | 47 |
| Moo Moo Meadows | course_01_000 | 13,905 | 122 | 59,603 → 39,514 | 1,763 | 56,320 | 2 | 71 | 100.0% / 100.0% | 41 |
| Mushroom Gorge | course_02_000 | 9,084 | 84 | 71,063 → 49,309 | 2,603 | 96,972 | 300 | 92 | 89.5% / 92.3% | 67 |
| Toad's Factory | course_04_000 | 15,608 | 132 | 61,012 → 35,090 | 2,804 | 85,096 | 747 | 95 | 84.7% / 89.2% | 61 |
| Mario Circuit | course_00_000 | 12,297 | 109 | 64,286 → 31,509 | 1,899 | 56,300 | 114 | 73 | 98.0% / 99.2% | 30 |
| Coconut Mall | course_05_000 | 17,927 | 152 | 49,040 → 33,166 | 10,222 | 368,680 | 1510 | 215 | 94.7% / 96.2% | 396 |
| DK Summit | course_06_000 | 12,972 | 113 | 77,570 → 42,581 | 2,201 | 108,404 | 384 | 105 | 92.9% / 95.6% | 111 |
| Wario's Gold Mine | course_07_000 | 15,650 | 132 | 68,713 → 48,609 | 2,584 | 94,532 | 681 | 110 | 69.6% / 72.9% | 145 |
| Daisy Circuit | course_09_000 | 17,168 | 146 | 39,903 → 27,852 | 1,837 | 50,836 | 133 | 77 | 96.9% / 98.6% | 24 |
| Koopa Cape | course_15_000 | 17,687 | 152 | 36,442 → 28,323 | 3,541 | 140,296 | 1080 | 161 | 69.0% / 73.6% | 156 |
| Maple Treeway | course_11_000 | 14,930 | 127 | 55,408 → 48,422 | 9,499 | 880,984 | 615 | 228 | 97.4% / 98.2% | 439 |
| Grumble Volcano | course_03_000 | 12,632 | 118 | 52,195 → 28,456 | 2,498 | 83,912 | 353 | 81 | 89.3% / 91.6% | 50 |
| Dry Dry Ruins | course_14_000 | 13,826 | 118 | 38,959 → 31,381 | 4,160 | 181,244 | 497 | 106 | 95.5% / 98.3% | 81 |
| Moonview Highway | course_10_000 | 16,705 | 146 | 57,755 → 43,865 | 3,003 | 117,264 | 806 | 114 | 87.5% / 91.0% | 76 |
| Bowser's Castle | course_12_000 | 21,947 | 182 | 98,483 → 27,927 | 2,894 | 108,228 | 872 | 131 | 80.7% / 85.0% | 94 |
| Rainbow Road | course_13_000 | 18,906 | 155 | 29,069 → 14,380 | 2,066 | 107,552 | 8 | 111 | 98.0% / 98.0% | 54 |
| GCN Peach Beach | course_16_000 | 10,155 | 86 | 62,396 → 24,217 | 5,033 | 180,188 | 147 | 97 | 97.6% / 98.7% | 85 |
| DS Yoshi Falls | course_20_000 | 8,811 | 80 | 42,416 → 30,803 | 4,683 | 304,176 | 1 | 92 | 100.0% / 100.0% | 184 |
| SNES Ghost Valley 2 | course_25_000 | 2,028 | 22 | 41,580 → 15,108 | 1,052 | 25,248 | 2 | 24 | 99.9% / 99.9% | 6 |
| N64 Mario Raceway | course_26_000 | 5,793 | 58 | 66,998 → 29,829 | 4,100 | 150,012 | 116 | 79 | 97.7% / 98.6% | 54 |
| N64 Sherbet Land | course_27_000 | 4,327 | 41 | 50,823 → 33,458 | 2,826 | 70,520 | 246 | 84 | 75.7% / 80.2% | 33 |
| GBA Shy Guy Beach | course_31_000 | 4,438 | 43 | 68,504 → 44,310 | 2,415 | 58,136 | 1 | 50 | 100.0% / 100.0% | 18 |
| DS Delfino Square | course_23_000 | 13,106 | 113 | 49,773 → 41,602 | 3,614 | 98,536 | 36 | 115 | 99.6% / 99.8% | 62 |
| GCN Waluigi Stadium | course_18_000 | 11,199 | 98 | 58,027 → 31,420 | 2,189 | 84,420 | 25 | 59 | 99.0% / 99.6% | 40 |
| DS Desert Hills | course_21_000 | 7,531 | 64 | 68,154 → 26,685 | 2,583 | 105,296 | 81 | 65 | 97.5% / 99.0% | 46 |
| GBA Bowser Castle 3 | course_30_000 | 3,836 | 42 | 37,925 → 27,059 | 2,091 | 56,924 | 2 | 60 | 100.0% / 100.0% | 17 |
| N64 DK's Jungle Parkway | course_29_000 | 10,597 | 96 | 71,751 → 28,252 | 2,679 | 86,732 | 99 | 87 | 98.7% / 99.3% | 28 |
| GCN Mario Circuit | course_17_000 | 10,708 | 94 | 60,448 → 26,677 | 2,691 | 95,244 | 133 | 72 | 97.0% / 98.2% | 67 |
| SNES Mario Circuit 3 | course_24_000 | 3,529 | 34 | 46,105 → 26,215 | 1,882 | 50,352 | 1 | 29 | 100.0% / 100.0% | 16 |
| DS Peach Gardens | course_22_000 | 10,820 | 99 | 71,103 → 43,399 | 10,357 | 315,532 | 336 | 167 | 91.1% / 92.4% | 111 |
| GCN DK Mountain | course_19_000 | 10,173 | 90 | 63,596 → 23,426 | 2,536 | 150,420 | 112 | 117 | 96.0% / 97.5% | 109 |
| N64 Bowser's Castle | course_28_000 | 9,262 | 87 | 47,488 → 35,436 | 2,898 | 83,384 | 911 | 93 | 77.7% / 81.3% | 50 |

## Notes, limits, follow-ups
- **Regenerate after any course overlay re-cook** (`models/o3ds/course_*`):
  `.venv/bin/python tools/build_o3ds_pvs.py --models <SD>/3ds/mkw-3ds/models --out <SD>/3ds/mkw-3ds/models/pvs`.
  A stale sidecar is rejected by its fingerprint and the course draws exactly
  as before (authored order, ordinary culling), so nothing breaks, the saving
  just disappears. Sidecars are build outputs (derived from game data), not
  committed.
- **Packaging is not wired yet**: `models/pvs/*.pvs` must be shipped next to
  `models/o3ds/` by the SD/CIA packaging (`tools/package_sd.py`,
  `tools/stage_o3ds_lowpoly.py`, `tools/release/build_cia.py`), which the main
  branch owns. Only my emulator copy's SD has them.
- Sidecars match the Old 3DS overlay geometry only. With `full-quality.txt` /
  `o3ds-models-off.txt` (original meshes) the fingerprint differs and the
  course is drawn as before; a second set built from the original meshes would
  cover that case.
- Course objects are not culled by the PVS. A column's block set cannot
  conservatively hide an object (it can stand above a hidden floor), so objects
  need a volumetric test (e.g. per column, visible coarse voxels from the same
  depth maps); worth it mainly on Bowser's Castle (scenery_ms 0.75 > course).
- What the emulator cannot show: GPU time. The saving is vertex/primitive
  work for the culled triangles, plus fragment work for the occluded ones the
  PVS removes (PICA tests depth after shading). Whether that moves Old 3DS FPS
  depends on how GPU-bound a frame is; the CPU side is neutral.
- Runtime overlap: `drawNow`'s cull branch, `render_thread.cpp` (one command),
  `renderer.hpp`, `visibility.hpp`, and the course load/draw lines in
  `main.cpp`. In immediate mode (engine branch's core-0 drawing)
  `setBlockMask` just stores the pointer; masks live as long as the course.
- An early scripted run used the emulator copy's unmodified config, which
  pointed at the main checkout's SD: it overwrote that SD's transient
  `runtime.log`, `performance.csv`, `version-bottom.ppm`, `audio-status.txt`
  once (~23:50). Nothing else was written there; the copy's config is fixed.
- Tools: `tools/build_o3ds_pvs.py`, `tools/pvs/pvs_raster.cpp`, `tools/pvs/gmdl.py`,
  `tools/pvs/compare_runs.py` (captures + performance.csv), `tools/pvs/summary.py`,
  `tools/pvs/upper_bound.py`; `tools/o3ds_perf_run.py --course N` (VS cup order).
