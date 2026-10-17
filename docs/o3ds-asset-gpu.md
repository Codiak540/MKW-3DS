# Old 3DS asset data for the PICA200 (vertex cache, textures, draw calls)

Branch `o3ds-asset-gpu`. Offline data only: no file under `src/` or `include/`
changes, physics and collision are untouched, and every output is an Old-3DS
overlay file (`models/o3ds`, `textures/o3ds`) that the existing loader already
validates. Engine-side follow-ups are listed separately at the end; they belong
to the engine branch.

Inputs measured here: the low-poly package staged at 2026-10-15T23:10:03Z
(`build/o3ds-lowpoly-package`, frozen as `build/o3ds-base-package`) and the
packed originals in `sd-card/3ds/mkw-3ds` (menu.pak/race.pak win, as at runtime).

## 1. Post-transform vertex cache order

Tool: `tools/optimize_o3ds_vertex_cache.mjs` (tests: `node --test
tools/test_o3ds_vertex_cache.mjs`). Opt-in in staging:
`tools/stage_o3ds_lowpoly.py --vertex-cache` (runs before the validator and
seam gate). Receipt: `<package>/o3ds-vertex-cache-receipt.json`; summary in
`docs/receipts/o3ds-vertex-cache-summary.json`.

What changes: only the order of whole triangles inside a draw range. Vertex
records, O3RM maps, O3SP blocks, texture names and submesh spans stay
byte-identical, and every triangle keeps its exact `(a,b,c)` tuple (winding and
provoking vertex unchanged). The host validator accepts every rewritten file
(3210 + 312 sidecars, 0 rejected).

Vertex (fetch) order cannot move: O3RM v1 maps are strictly increasing and
v2/v3 only allow equal source ids to swap, which changes no fetch address. The
originals cannot be reordered either: the loader rejects a sidecar whose vertex
and index counts both equal the original (`readModelVariant`), so an
index-only sidecar for an unreduced model is not loadable; reordering the
shared originals would mean changing the cook recipe, which changes the content
fingerprint (`tools/release/build_cia.py`) and New 3DS data as well.

Why the cache model is a range of sizes: `GPUREG_POST_VERTEX_CACHE_NUM`
(0x22D) is undocumented beyond "usually 0x4 or 0x84" (3dbrew), which may well
mean 4 entries. citro3d clears the cache after every draw (`GPUREG_VTX_FUNC`).
ACMR (vertex transforms per triangle) is reported for FIFO caches of 4, 8, 16
and 32 entries, and a range is rewritten only if it is **no worse at 4 entries**
and better in total. Candidates per range: meshoptimizer `optimizeVertexCache`
and its strip variant (pinned 1.3.0), Tipsify (k = 4, 6, 10, 14, 24) and a greedy
that simulates 4- and 16-entry FIFOs.

Pixel safety (the scene depth test is GEQUAL, so a later equal-depth fragment
wins; order is invisible only where no two triangles of a draw can tie):

* blended, depth-test-off and depth-write-off submeshes keep their order;
* sky models (course `*_sky.list`, sun objects: drawn as backdrop without a
  depth test), `ui_*`, shadow volumes, item boxes and blast models (runtime
  material binders / icon layers that can enable blending) keep their order;
* course-object models with MORF (per-vertex morph) keep their order;
* coplanar-overlapping or interpenetrating triangle pairs (bind pose) keep
  their mutual order (a priority topological sort of those pairs);
* animated models (DSKM driver banks, DOBJ object banks): triangles are
  labelled by the bones of their vertices and only runs of one label are
  reordered, so two parts that move relative to each other never swap;
* static-culling models (course track lists, Coconut Mall palm) are reordered
  inside each 128-triangle `buildVisibility` block, so block membership,
  bounds and culling are unchanged; `makeRigid`'s stable sort by bone keeps
  each bone group's internal order.

Results (1,257,571 overlay triangles; ACMR = transforms per triangle, cache
cleared per range; lower bound = unique vertices per range / triangles):

| category | models | triangles | ACMR @4 | @8 | @16 | @32 | transforms saved @4 / @16 |
|---|---:|---:|---|---|---|---|---|
| racers, karts, items | 2242 | 363,046 | 1.882 -> 1.791 | 1.789 -> 1.615 | 1.704 -> 1.543 | 1.602 -> 1.487 | 4.9% / 9.5% |
| rival drivers | 312 | 133,760 | 1.969 -> 1.941 | 1.894 -> 1.856 | 1.834 -> 1.799 | 1.739 -> 1.722 | 1.4% / 1.9% |
| course objects | 565 | 96,123 | 1.756 -> 1.712 | 1.687 -> 1.602 | 1.616 -> 1.532 | 1.527 -> 1.462 | 2.5% / 5.2% |
| course track | 43 | 482,619 | 1.707 -> 1.654 | 1.648 -> 1.563 | 1.598 -> 1.533 | 1.554 -> 1.514 | 3.1% / 4.1% |
| menu / UI | 360 | 182,023 | unchanged (kept) | | | | 0.1% / 0.2% |
| **all** | 3522 | 1,257,571 | 1.816 -> 1.762 | 1.740 -> 1.646 | 1.675 -> 1.593 | 1.599 -> 1.543 | **2.9% / 4.9%** |

Per course (track @16, objects @16) is in the summary receipt, e.g. Luigi
Circuit 1.738 -> 1.680 / 1.608 -> 1.304, DK Summit 1.760 -> 1.694 / 1.669 ->
1.478, Daisy Circuit 1.681 -> 1.624 / 1.656 -> 1.531.

Why the gains are modest: the converted Wii meshes share few vertices (the
lower bound is 1.29 transforms per triangle overall; track blocks 1.6), the
authored order is GX-strip derived (already near-optimal for a 4-entry cache),
and the safety rules keep 214k triangles in authored order where nothing beat
it at 4 entries. A first version without the bone rule saved 10.5% @16 (rivals
20%) but changed 25-1,205 pixels (median 34) in every top-screen frame on Mario
and the rival drivers in the emulator A/B: posed limbs interpenetrate and their GEQUAL ties flip with order.
Recovering that needs pose-aware constraints (evaluate every DSKM clip frame,
constrain only pairs that meet in some pose); not done.

## 2. Course textures: formats and mipmaps

Audit: `tools/audit_o3ds_textures.py --receipt build/o3ds-texture-audit.json`.

Findings over the 32 courses (track lists, skies, `objNN_*` objects; 1,329
distinct textures):

| | textures | GPU bytes | without mips |
|---|---:|---:|---:|
| track | 708 | 12.55 MiB | 676 (12.55 MiB) |
| course objects | 579 | 5.27 MiB | 515 (5.19 MiB) |
| sky | 42 | 0.89 MiB | 42 |

* Formats are already right: every course texture is ETC1 (762) or ETC1A4
  (565); none is uncompressed. 23 ETC1A4 textures never use their alpha
  (every nibble 0xF) and could be ETC1 at half the bytes, pixel-exact.
* Mipmaps are almost absent (3 of 1,329). Minified track and scenery textures
  are the classic PICA200 texture-cache cost, so this is the largest
  remaining GPU data lever, but it cannot ship as data alone yet:
  1. `Renderer::moveToVram` (reduced quality, Old 3DS) skips any texture with
     `maxLevel > 0`, so a mipmapped course texture would stay in FCRAM instead
     of VRAM, likely a net loss.
  2. The only Old-3DS texture override path for course models is the O3RM v2
     `_o3` rename, and the 20 main track models carry tiled UVs outside the v2
     range (|uv| <= 64), so they cannot use it (the OTEX path in
     `texture_detail.hpp` only accepts smaller, not mip-augmented, textures and
     is QA-only).

Tool: `tools/cook_o3ds_course_textures.py --mode exact|mips` builds
`textures/o3ds/<name>_o3<hash>.t3x` siblings from the packed T3X (its ETC1
decoder is self-checked against tex3ds on every run) and renames the slots in
course sidecars (v1 -> v2 header only when every UV0 is in range):

* `exact`: ETC1A4 without alpha -> ETC1, colour blocks copied verbatim. On the
  current package it reaches only 8 textures (8 KiB -> 4 KiB): the other 15
  belong to models it cannot rename. Not staged (no measurable value).
* `mips`: level 0 byte-identical (near and 1:1 surfaces sample identical
  texels), levels 1..n box-filtered from the decoded level 0. Reaches 350
  object textures in 201 models (3.27 MiB -> 4.36 MiB, +33%), none of the
  track models. Not staged until the engine changes below land.

## 3. Draw calls and static batching

Audit: `tools/audit_o3ds_draw_batches.py --package <package> [--runs <emulator run dirs>]`.

Emulator (same build, Old 3DS profile, reduced quality, 900-tick CPU-driven
race, means over 837 frames):

| course | draws/frame (max) | DrawElements calls | triangles/frame | culled submeshes |
|---|---:|---:|---:|---:|
| Luigi Circuit | 88.7 (151) | 89.8 | 14,811 | 17.3 |
| Coconut Mall | 107.5 (156) | 110.4 | 23,043 | 58.1 |
| Daisy Circuit | 83.1 (153) | 85.1 | 21,413 | 70.9 |

Static audit (`build/o3ds-draw-batches.json`, Old-3DS spans):

* Track models are already one draw per material: 660 draws over 32 courses
  (8-31 per course), zero adjacent identical materials, two non-adjacent
  repeats in total. Nothing to batch.
* Course objects: 1,228 submesh draws per instance over 32 courses, of which
  453 are adjacent submeshes with byte-identical material records. Almost all
  are the Mii spectator stands (Luigi Circuit `miiobj01/02/03`, Daisy Circuit
  `miiobjd01/02/03`, Coconut Mall `shmiiobj01-03`, DK Summit `dk_miiobj00`,
  GCN DK Mountain `dkturibashigc`): e.g. `obj08_miiobj01` is 52 submeshes = 26
  spectators x 2 materials, one bone each, so `makeRigid` draws each spectator
  as its own part. Per-part frustum and tiny-part culling already skip most of
  them (32-75 part-cull tests per frame on these three courses), which is why the measured
  totals are far below the static counts.

No data-only batching is possible today: sidecar spans are 1:1 with the
original submeshes (`readModelVariant`) and rigid parts are split per bone at
load. Merging is pixel-exact only as an engine change (below).

## 4. Emulator A/B

`tools/run_o3ds_asset_ab.py run|compare` stages an overlay into a private
Azahar copy, runs `menu-autoplay.txt` with `race-capture.txt` (lockstep: two
simulation ticks per presented frame) and `performance.txt`, collects
`race-*.ppm` and `performance.csv`, and stops only that copy's process.

Build: this branch's `build/mkw-3ds.3dsx` (`tools/build.py --external-assets
--jobs 4`, no engine changes). Script: title -> VS -> Mario, Standard Kart M ->
course -> `auto:900` (CPU-driven race). Each run: 46 top + 46 bottom frames.
Receipt: `docs/receipts/o3ds-emulator-ab.json`.

| comparison | frames identical | notes |
|---|---:|---|
| baseline vs baseline (Luigi Circuit) | 92 / 92 | the lockstep capture is deterministic |
| baseline vs reorder, Luigi Circuit | 92 / 92 | |
| baseline vs reorder, Coconut Mall | 92 / 92 | |
| baseline vs reorder, Daisy Circuit | 92 / 92 | Mii crowds, many objects |
| baseline vs object mipmaps, Luigi Circuit | 48 / 92 | intended: 44 frames differ by up to 960 px, only on distant objects (skyship, flags); not staged |

All runs: autoplay pass, 1 race, the same 1 model-variant fallback in both
arms. Emulator frame rate says nothing about PICA200 vertex cost (Azahar uses
host-GPU shaders); the transform savings above are the measured quantity, and
a hardware A/B (`tools/compare_o3ds_performance.py`) is still needed to put a
millisecond figure on them.

## Engine follow-ups (for the engine branch; not done here)

1. **Mipmapped textures in VRAM.** `Renderer::moveToVram` returns early for
   `t.maxLevel`. Copying the whole chain is enough: size
   `C3D_TexCalcTotalSize(t.size, t.maxLevel)` and a VRAM texture made with
   `C3D_TexInitWithParams(&moved, nullptr, {t.width, t.height, t.maxLevel,
   t.fmt, GPU_TEX_2D, true})` (levels are contiguous in both allocations).
2. **Course texture mip variants for track models.** Either accept a texture
   variant whose level 0 is byte-identical to the original and which only adds
   levels (an OTEX sibling of today's downscale check, Old 3DS only and
   release-eligible), or widen the O3RM v2 UV range for unchanged UVs. Then
   `tools/cook_o3ds_course_textures.py --mode mips` can cover the 676 track
   textures (12.6 MiB, +33% for the chains) that are the main minification cost.
3. **Draw batching.** A load-time merge of adjacent submeshes with identical
   material records is pixel-exact (same state, same order) but only pays where
   a model is not split into rigid parts. The Mii stands would need GPU skinning
   (`objectGpuSkin`, today a QA marker) plus that merge: 52 -> 2 draws per stand,
   trading away per-spectator culling. Measure before adopting.
4. **Reorder-only sidecars** (same vertex and index counts) are rejected by
   `readModelVariant`. Allowing them would let unreduced models (drivable
   roads, excluded objects) carry cache order too; the measured gain on track
   blocks is about 4% @16, so this is low priority.

## Merge notes

* Branch `o3ds-asset-gpu` = `d5a0abe` (a copy of the main checkout's
  uncommitted work at 2026-10-15 23:23) + the commits after it. Those touch
  only `tools/`, `docs/` and seven lines of `tools/stage_o3ds_lowpoly.py`
  (`--vertex-cache`, off by default). Cherry-pick them onto main's own commit
  of the low-poly work; nothing under `src/` or `include/` changed.
* The reorder is a post-pass: any re-cook or re-stage resets triangle order,
  so run it after staging (`--vertex-cache`) or directly:
  `node tools/optimize_o3ds_vertex_cache.mjs --package build/o3ds-lowpoly-package --assets sd-card/3ds/mkw-3ds --out build/o3ds-lowpoly-package --receipt build/o3ds-lowpoly-package/o3ds-vertex-cache-receipt.json`
  then the normal validator and seam gate.
* Regenerated sidecars for the 23:10 package are in this worktree's
  `build/o3ds-vcache-package` (game-derived, not committed).
* Emulator copies cloned from `build/azahar-o3ds` keep `sdmc_directory` /
  `nand_directory` pointing at the main checkout's SD; repoint them per copy.
  Launch Azahar through the bundle (`open -n -g ... Azahar.app --args <3dsx>`):
  run directly it can stop at a modal "run via Azahar.app" warning.
