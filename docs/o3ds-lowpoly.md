# Old 3DS low-poly overlay

Old 3DS consoles load reduced, DS-style versions of the Wii models; New 3DS and
unknown hardware always keep the originals. Everything is derived from the
user's own Mario Kart Wii data. Mario Kart DS served only as a visual style
reference; no Mario Kart DS or Mario Kart 7 geometry,
UVs or textures are used.

## What the game loads

| Path | Used for | Format |
| --- | --- | --- |
| `models/o3ds/<name>.gmdl` | any model, Old 3DS | O3RM v1–v3 sidecar |
| `models/o3ds/rival/<name>.gmdl` | CPU opponents' drivers | O3RM v1–v3 sidecar |
| `textures/o3ds/<name>_o3<tag>.t3x` | re-baked textures of a sidecar | ETC1 / ETC1A4 |

Gate: `include/mkw/model_detail.hpp`. On a console identified as Old 3DS the
sidecars load in QA and release builds. QA builds can force the originals with
`full-quality.txt` or `o3ds-models-off.txt` on the SD card (A/B testing).
Any sidecar that fails validation falls back to the original model.

Sidecar formats (`include/mkw/model_variant.hpp`, tests in
`tools/test_model_variant.cpp`, host validator `tools/validate_model_variants.cpp`):

- v1: a subset of the authored vertex records, strictly increasing source map.
- v2: as v1, UV0 may be re-baked, a source vertex may repeat (UV seams), a
  texture slot may name its own re-baked sibling `<original>_o3<hex tag>`.
- v3: as v2, positions may move; an `O3SP` block gives every vertex its
  bone-local skin position. Each vertex keeps its authored bone, so animation
  banks and clips are unchanged (`applySkinPositions`).

## Cooks

1. **Characters** — `tools/cook_o3ds_lowpoly_characters.py` (main set; `--variant rival` for
   opponents; `--landmark` for track statues/creatures/vehicles). Welded by
   position + bone, symmetric paired collapses across x = 0, volume-preserving
   placement with exact re-binding, per-bone shape limits (head tighter),
   eye overlays and the surface beneath them, decals (emblems), pointed tips and
   open borders between rigid parts locked. Texture: authored UVs kept wherever
   the colours match the original surface; otherwise re-baked into free atlas
   space from the faces each triangle replaced; otherwise that area keeps
   authored detail.
2. **Levels** — `tools/o3ds_course_roads.py` (KCL drivable surfaces) →
   `tools/o3ds_level_topology_locks.py` (roads + open borders + non-manifold
   edges) → `node tools/cook_o3ds_geometry.mjs --listing build/o3ds-level-listing.json
   --road-locks build/o3ds-level-locks.json --seamless true --profile aggressive`
   `--absolute-error 600` → `tools/o3ds_piece_guard.py` (re-run the cook until
   every authored piece survives at ≥85% of its size). Course skies keep their
   authored geometry. Landmarks (statues, cars, creatures, trees and their
   `_low` LODs, `build/o3ds-landmark-names.txt`) use the character cook with
   `--landmark` instead.
3. **Vehicles** — `tools/cook_o3ds_vehicles.py` cooks three candidate sets
   (fine: bodies/handlebars at error 0.02; careful: every part at 0.04; lean:
   every part at 0.06), each with the piece guard so no steering wheel,
   exhaust block or hub cap vanishes. `tools/select_o3ds_vehicles.py` renders
   every part from behind, the rear quarter, the side and the front next to the
   original and picks the cheapest candidate (the earlier refined set
   included) within the limits: strict for the player's own vehicle
   (`models/o3ds/`), looser for CPU opponents (`models/o3ds/rival/`, which
   `src/racer_visuals.cpp` loads first). A part with no passing candidate keeps
   its original for the player.
4. **Everything else** (items, menus) — the earlier refined O3RM set
   (`build/o3ds-models-refined`). Course objects with their own collision
   (bridges, escalators, bounce mushrooms, volcano pieces, battle blocks)
   always keep authored geometry.

## Coconut Mall keeps its original course mesh (2026-10-16)

Seen through the outdoor "THANK YOU!" arch, the reduced `course_05_000` drew a
brown slab where the original shows shop fronts (texture smeared across
collapsed triangles behind the arch; positions were within tolerance, so the
surface audits missed it). Locking the arches and the mall exit sign did not
cure it; the reduction only saved 7% of this mesh (19,517 -> 18,143), so
`build/o3ds-levels-abs600b` simply has no `course_05_000` sidecar and the course
loads its authored mesh (every other Coconut Mall model stays reduced).
Verified in-game: intro frame 540 matches the original. Its track and object
visibility files are rebuilt for the authored mesh.

## Player's own character (2026-10-16)

The player's driver loads its original model: `stage_o3ds_lowpoly.py` leaves
every `*_driver.gmdl` out of `models/o3ds/` (the refined set too), so only CPU
racers and other players, which load `models/o3ds/rival/` first, draw reduced
drivers. The player's vehicle keeps its careful reduced set.
`--decimate-player-driver` restores the old behaviour; it needs a main driver
cook (`--characters`, default `build/o3ds-lowpoly-main2`). Without one, the
player's drivers are every `*_driver.gmdl` in `--originals`. QA logs list the
player's driver under `o3ds-fallbacks.txt`, as intended.

## Texture mipmaps (merged 2026-10-16)

From branch `o3ds-texture-mips` (docs/o3ds-texture-cache.md):
`textures/o3ds/mips.pak` adds mip chains to 1,159 course textures for Old 3DS;
stage it with `--texture-mips build/o3ds-texmips3/textures/o3ds/mips.pak`.
All 32 courses: 0 fallbacks; with bloom back on, Toad's Factory keeps 13
textures in FCRAM (they no longer fit in VRAM), every other course fits.

## Track sectioning, visibility and vertex-cache order (merged 2026-10-16)

Merged from branches `o3ds-pvs` (docs/o3ds-pvs-log.md) and `o3ds-asset-gpu`
(docs/o3ds-asset-gpu.md) as uncommitted changes in this checkout.

- `src/course_pvs.cpp` (Old 3DS only): applies the per-course triangle
  permutation from `models/pvs/<part>.pvs`, then skips blocks the camera's
  column cannot see. A sidecar whose geometry fingerprint differs from the
  loaded course (original + O3RM overlay) is ignored. QA markers:
  `o3ds-pvs-off.txt`, `o3ds-pvs-mask-off.txt`.
- `tools/optimize_o3ds_vertex_cache.mjs`: reorders whole triangles inside draw
  ranges for the post-transform cache (index order only), via
  `stage_o3ds_lowpoly.py --vertex-cache`.

Order matters, because the visibility fingerprint covers the index order:

1. `stage_o3ds_lowpoly.py --levels build/o3ds-levels-abs600b --landmarks build/o3ds-landmarks4 --vertex-cache`
   (package only).
2. `build_o3ds_pvs.py --models build/o3ds-pvs-models --out build/o3ds-pvs/models/pvs`, where
   `build/o3ds-pvs-models` holds links to the loose course files of an SD
   `models/` folder plus `o3ds -> build/o3ds-lowpoly-package/models/o3ds`
   (about 45 minutes for all 32 courses).
3. Step 1 again with `--dest <SD mkw-3ds> --backup <dir>`: the stage copies
   only visibility sidecars whose fingerprint matches the staged course
   overlay and reports any stale ones.

Any re-cook of a course model, or of the vertex-cache order, needs step 2 again.

## Reproducible cook (2026-10-16)

`tools/cook_o3ds_overlay.py` cooks the whole overlay from a builder's own
cooked assets. It reads `sd-card/3ds/mkw-3ds` through
`cook_o3ds_textures.Assets` (packed files win, as in the game), the course
KCLs in `assets/`, and this repository's tools. The CIA builder runs it as its
last cook step, `Old 3DS overlay`, after the race archive. Its output is
`build/o3ds-lowpoly-package`, which `tools/package_sd.py` ships. Nothing it
reads or writes is tracked: the only new tracked data is the names-only
`tools/o3ds/landmarks.txt`.

How it runs:

- Each stage writes a stamp in its work folder (default `build/o3ds-overlay`,
  removed after a successful builder step) and a log in `<work>/logs/`.
- A stage is skipped while its stamp still matches its inputs (sizes and times
  of the cooked assets), its tool sources and its settings. Once one stage
  runs, every later stage runs too.
- Usage: `tools/cook_o3ds_overlay.py [--work DIR] [--package DIR] [--jobs N] [--force]`.

The stages, which follow how the shipped overlay was made:

1. **level-sources** gathers every part named in `course_NN.list` and
   `course_NN_sky.list` and every course visual object (`course_NN_visuals.list`),
   708 models in all. It copies them out of the paks, with their `.dskm`/`.dobj`
   banks and the visuals lists, and writes the listing.
2. **levels** builds KCL road locks and topology locks. The piece guard then
   runs to convergence against the relative-error seamless cook (3 rounds lock
   88,787, 10,671 and 504 vertices). The level cook then runs at
   `--absolute-error 600` with those locks. Skies are left out by the stage, and
   `course_05_000` is dropped (`EXCLUDED_LEVELS`).
3. **landmarks** cooks `tools/o3ds/landmarks.txt` with `--landmark --ratio 0.4`.
   The two `dokan_sfc` pipes use `--landmark-cap 0.01`.
4. **drivers** runs the opponents' driver cook (`--variant rival`). The
   player's drivers keep their original models, so no main driver cook runs:
   with no `--characters`, the stage names them from the cooked assets (every
   `*_driver.gmdl` in `models/`, the set the main cook would reduce, 312).
5. **refined** builds `build_o3ds_character_regions.py` and runs
   `cook_o3ds_geometry.mjs --profile aggressive --character-head-mode preserve`.
   Both read the models as packaged (`package_sd.py --list`, through a links
   folder or a listing).
6. **vehicles** runs `cook_o3ds_vehicles.py`, then `select_o3ds_vehicles.py`.
7. **texture-mips** runs `cook_o3ds_course_textures.py --mode pack`.
8. **stage, visibility, final** run `stage_o3ds_lowpoly.py --vertex-cache
   --texture-mips`, then `build_o3ds_pvs.py` and `build_o3ds_object_pvs.py`.
   The two visibility tools read the packaged models with
   `o3ds -> <package>/models/o3ds` linked in. The stage then runs again, which
   folds in the visibility files whose fingerprints match.

Proof run on this Mac (10 cores): `--work build/o3ds-repro --package
build/o3ds-repro/package`, compared with `build/o3ds-lowpoly-package`. The
result is **4,041 of 4,041 shipped files byte-identical**:

| Group | Files | Triangles |
| --- | ---: | ---: |
| Levels | 468 | 520,107 |
| Landmarks | 137 | 40,329 |
| Vehicles (player) | 1,336 | 265,324 |
| Refined (items, menus) | 396 | 83,815 |
| Opponents (`rival/`) | 1,307 | 275,163 |
| Re-baked textures | 339 | |
| Track / object visibility | 32 / 25 | |
| `mips.pak` | 1 | |
| **Total** | **4,041** | **1,184,738** |

There were no differences to explain. Every intermediate also matched its
counterpart in this checkout:

- level names, listing and sources
- lock sets (the file only gains a `freeBorders: false` key)
- all 664 level sidecars
- landmarks
- both driver sets and their textures
- the 3,331 refined sidecars and the character regions' 39 bindings
- all three vehicle candidate sets and every vehicle pick
- `mips.pak`

The cooks, the vertex-cache order and the visibility builds are deterministic
on this machine.

That first run still cooked the main driver set, only to name the player's
drivers. The drivers stage no longer runs it: the stage names them from the
cooked assets instead. A second proof run (`--work build/o3ds-repro2 --package
build/o3ds-repro2/package`) again gave **4,041 of 4,041 shipped files
byte-identical**. Its receipt matched too: the same counts
(`playerDriversOriginal` 312), excluded list and per-model hashes.

Time per stage:

| Stage | First run | Without the main driver cook |
| --- | ---: | ---: |
| Package listing | 2–3.5 min | 3.7 min |
| Levels | 32 s | 33 s |
| Landmarks | 2 min | 2 min |
| Drivers | 25 min | 13.8 min (opponents only) |
| Refined, vehicles, mips, stage ×2 | 3.3 min | 3.5 min |
| Visibility | 72 min | 76 min |
| **Total** | **1 h 47 min** | **1 h 39 min** |

The drivers stage is 11 min shorter. The visibility tools did not change, yet
that stage ran 4 min longer on the second run, so the total fell by 8 min.

The other 42 cook steps took 31 min on this machine. The step adds 92.2 MiB
(4,041 files) to the SD folder; the CIA (code only) is unchanged by it.

Not reproduced on purpose: a piece-guard round against the absolute-error
cook itself. Run on today's levels, it would lock pieces in 65 models. Eleven of
them ship as level sidecars (`course_21_000`, `obj06_heyho2` ×4, the
`group_enemy` crowds, `obj09_pylon01`), so they and course 21's visibility
would change. That round would be a quality change to review in game, not a
reproduction.

Builder changes in `tools/release/build_cia.py`:

- The step's fingerprint includes every tool in the orchestrator's
  `COOK_SOURCES` table.
- The previous overlay package is removed before the menu and race archive
  steps run, because those steps pack `package_sd.py --list` and packed sidecars
  win over loose ones at runtime (`src/renderer.cpp`).
- The content stamp changes, so players need the full CIA.

## Checks

- `tools/verify_o3ds_course_roads.py` — every visible drivable triangle byte-exact.
- `tools/check_o3ds_seams.py` — no new open edges (cracks) or UV tears in levels;
  no new open edges in characters/landmarks.
- `tools/check_o3ds_surface.py` — distance of reduced triangles from the
  authored surface (facing-aware, per model and per piece).
- `tools/stage_o3ds_lowpoly.py` — assembles the overlay, runs the runtime
  validator on every sidecar and the seam gate, then stages to an SD folder
  (backing up the previous overlay).
- Previews: `tools/preview_o3ds_lowpoly.py`; in-game object review with the QA
  `objects-test.txt` marker; course tour `tools/o3ds_course_tour.py`.

## Measured (Azahar, Old 3DS mode, CPU-driven autoplay races)

Average triangles drawn per rendered frame over a 1,500-step VS race (Mario,
11 CPU racers, player CPU-driven) on every course, 2026-10-16. Original =
`o3ds-models-off.txt`; Overlay = low-poly models with the careful player kart
and guarded opponent karts; + sectioning = track sectioning, precomputed
visibility and vertex-cache order merged (bloom on, which adds about 44
triangles of screen quads). Tours: `build/o3ds-tour/all-baseline`, `all-final`,
`all-merged`.

| Course | Original | Overlay | Overlay + sectioning | Reduction |
| --- | ---: | ---: | ---: | ---: |
| Luigi Circuit | 23,016 | 15,718 (31.7%) | 13,122 | 43.0% |
| Moo Moo Meadows | 26,219 | 18,057 (31.1%) | 15,794 | 39.8% |
| Mushroom Gorge | 19,967 | 15,261 (23.6%) | 14,016 | 29.8% |
| Toad's Factory | 23,181 | 17,398 (24.9%) | 14,481 | 37.5% |
| Mario Circuit | 26,988 | 20,234 (25.0%) | 17,777 | 34.1% |
| Coconut Mall | 28,434 | 22,130 (22.2%) | 20,577 | 27.6% |
| DK Summit | 19,213 | 14,087 (26.7%) | 11,836 | 38.4% |
| Wario's Gold Mine | 21,131 | 17,110 (19.0%) | 13,032 | 38.3% |
| Daisy Circuit | 31,722 | 23,524 (25.8%) | 21,894 | 31.0% |
| Koopa Cape | 19,290 | 14,589 (24.4%) | 12,837 | 33.5% |
| Maple Treeway | 21,009 | 15,597 (25.8%) | 15,265 | 27.3% |
| Grumble Volcano | 20,524 | 16,238 (20.9%) | 13,253 | 35.4% |
| Dry Dry Ruins | 16,970 | 12,898 (24.0%) | 12,426 | 26.8% |
| Moonview Highway | 23,384 | 17,723 (24.2%) | 16,663 | 28.7% |
| Bowser's Castle | 30,302 | 25,006 (17.5%) | 18,013 | 40.6% |
| Rainbow Road | 19,092 | 15,623 (18.2%) | 12,990 | 32.0% |
| GCN Peach Beach | 25,721 | 18,837 (26.8%) | 16,097 | 37.4% |
| DS Yoshi Falls | 16,196 | 11,304 (30.2%) | 10,264 | 36.6% |
| SNES Ghost Valley 2 | 10,901 | 6,867 (37.0%) | 6,211 | 43.0% |
| N64 Mario Raceway | 14,822 | 9,730 (34.4%) | 8,446 | 43.0% |
| N64 Sherbet Land | 14,182 | 8,477 (40.2%) | 7,293 | 48.6% |
| GBA Shy Guy Beach | 13,428 | 9,613 (28.4%) | 8,696 | 35.2% |
| DS Delfino Square | 24,338 | 17,591 (27.7%) | 16,429 | 32.5% |
| GCN Waluigi Stadium | 19,028 | 15,077 (20.8%) | 12,618 | 33.7% |
| DS Desert Hills | 15,668 | 11,104 (29.1%) | 9,544 | 39.1% |
| GBA Bowser Castle 3 | 10,718 | 6,951 (35.2%) | 6,735 | 37.2% |
| N64 DK's Jungle Parkway | 21,508 | 16,886 (21.5%) | 15,571 | 27.6% |
| GCN Mario Circuit | 20,467 | 14,886 (27.3%) | 11,911 | 41.8% |
| SNES Mario Circuit 3 | 13,305 | 8,562 (35.6%) | 7,665 | 42.4% |
| DS Peach Gardens | 16,516 | 11,576 (29.9%) | 10,684 | 35.3% |
| GCN DK Mountain | 16,270 | 11,906 (26.8%) | 10,519 | 35.3% |
| N64 Bowser's Castle | 21,060 | 13,595 (35.4%) | 12,375 | 41.2% |
| **All 32 (sum)** | 644,568 | 474,156 (26.4%) | 415,036 | **35.6%** |

With the old vehicle set (missing steering wheels, emblems and exhaust blocks)
the overlay alone measured 31.5%; the vehicle fix costs about 5 points.
Visibility on vs off (`o3ds-pvs-off.txt`), Luigi Circuit, Dry Dry Ruins and
Bowser's Castle: 48 of 48 captures pixel-identical.

Course meshes dominate the frame; their remaining triangles are mostly locked
roads, alpha cutout cards and open piece borders. Tried and rejected: sliding
free borders (−3% only), near/far error split (no gain), simplifying off-road
(hills moved by hundreds of units), very aggressive rival karts (parts vanish),
the character cook for karts (alpha-tested kart materials are ineligible),
piece guard with error 0.12 (squeezes the unlocked parts flat).
Emulator timing is not hardware timing; no physical Old 3DS FPS has been measured.
