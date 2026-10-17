# Old 3DS geometry experiment

Status: experimental, no measured hardware FPS improvement yet. Automatic
release selection must remain disabled until the acceptance gate below passes.

The local implementation includes the sidecar cooker, validated runtime loader,
hardware/release gates, a test-package wrapper and paired-capture analysis.
Synthetic fixtures exercise geometry, animation bindings, corrupt sidecars and
frame accounting. The completed local disc has now passed the full authored
cook and QA build. Physical-console performance and final visual approval
are still outstanding. Textured inspection rejected the first aggressive
character pass: lost facial detail and distorted head shapes are not acceptable.
That pass's counts below are historical measurements, not an approved art result.

The revised candidate preserves character heads and uses a 0.06 body error
limit, while keeping strong scenery reduction. It produces 3,331 sidecars;
all pass exact source-record/remap validation. All 449 animated model pairs
pass the new open-boundary screen. Eight kart-body aliases retain their
original triangles plus vertex compaction because reduction opened a boundary.
The earlier aggressive candidate booted in Azahar with confirmed
`hardware=old3ds` and `model_profile=o3ds_candidate`; that boot is not validation
of the revised candidate or a hardware FPS result.

The refined fallback has since passed the built-in 13-page menu capture test
and reached normal startup in Azahar Old 3DS mode with the regular QA binary
(`8fbab2e9…`). Its local session receipt is `build/azahar-refined-session.json`.
The subsequent race capture contains 196.8 seconds, 208 loaded variants and
zero loading fallbacks, with texture reduction disabled. The guest reports
29.64 presented frames per second; this is an emulator smoke test, not a
physical-console benchmark or a baseline/candidate improvement. Its read-only
summary is `build/azahar-refined-race-smoke-summary.json`.
An inactive Azahar Vulkan window returned black captures; explicitly raising
the native window revealed the rendered game. Temporary startup diagnostics
were removed; no game startup fix was required.

## Prepared hardware package — 15 October 2026

Copy `build/playable/3ds` to the SD card root and launch the included
`mkw-3ds.3dsx` with Homebrew Launcher. Profiling is enabled. The currently
staged aggressive package includes its
Old 3DS activation marker. Its identity and A/B asset hashes are recorded in
`build/o3ds-test-receipt.json`.

1. Remove the staged `o3ds-models-test.txt` for the baseline. Run on Old 3DS with stereo off and no `full-quality.txt`,
   `o3ds-racer-distance.txt` or `o3ds-models-test.txt` marker. Keep the existing
   30 FPS presentation cap and ordinary Old 3DS settings.
2. Save `/3ds/mkw-3ds/performance.csv` before the next launch overwrites it.
3. Create an empty `/3ds/mkw-3ds/o3ds-models-test.txt`, restart the same build,
   repeat the identical scene and save the candidate CSV. Remove the marker
   to return to the baseline. Collect the repeated pairs described below.

The staged candidate uses target ratio 0.3 and general error 0.12, with verified
character heads preserved and character body error capped at 0.06. It
permits faceted surfaces and simplifies normal/UV seams with attribute error
costs, while retaining material joins, alpha outlines, small details and
mixed-joint animation boundaries. Retained vertex and binding bytes are
unchanged; sorted O3RM source maps allow the runtime to compact DSKM/DOBJ
bindings and shape ranges without changing animation clips. The original
model is retained if a candidate fails validation or allocation.

`build/o3ds-models-refined/receipt.json` records the staged assets and hashes.
The earlier conservative comparisons remain in `build/o3ds-models` and
`build/o3ds-models-error002`. Optional `--protect-features true` retains more
acute tips and tighter shape detail; it is not the current strong candidate.

| Error setting | Models reduced / examined | Library triangles removed |
| --- | --- | --- |
| 0.005 | 435 / 3,414 | 131,091 (6.4%) |
| Conservative 0.02, previous | 1,341 / 3,414 | 220,646 (10.8%) |
| Aggressive 0.12, rejected character quality | 3,331 / 3,414 | 1,245,542 (61.0%) |
| Refined heads, 0.06 character bodies, boundary fallback | 3,331 / 3,414 | 1,127,718 (55.23%) |

These are static library totals, including assets that may never appear in a
particular frame. They do not measure FPS. The revised library goes from
3,237,288 to 1,096,958 vertices (66.11% fewer). Character triangle savings are
16.22%, with 73.64% fewer stored character vertices. Mario is now 666 to 592
triangles and Bowser 855 to 785. The earlier 312/385-triangle results failed
textured review. The separate actual-DS-Mario texture-transfer preview under
`build/mkds-mario-preview` is private reference work and is not in this package.
The corrected eye preview is `eye-fixed-full-before-after.png` (and the head
close-up is `eye-fixed-before-after.png`). It gives the original Wii eye
overlay priority over skin during texture projection. Its receipt verifies
that all 162 DS triangles, the nose geometry and texture, and every non-eye
texture island are unchanged. This fixes the private reference bake; it is
not a change to Wii runtime eyes or a completed roster conversion.
Original texture pixels
and materials remain in the geometry-only comparison. CPU skinning iterates
compacted bindings, unlike the earlier index-only experiment; GPU vertex
processing savings depend on submitted indices and cache reuse.

### Wii-only position-update research

The user reaffirmed Wii-only input after reviewing the corrected private DS
reference. `tools/prototype_o3ds_positions.mjs` therefore reads only authored
Wii GMDL/DSKM assets and binding-verified semantic metadata. Its raw outputs
are deliberately incompatible with the O3RM retained-vertex runtime contract.
`tools/rebind_o3ds_positions.py` verifies the exact original neutral palette
(Mario: `drive`, frame 15), converts updated vertices back into their original
bone spaces, and preserves every animation payload byte. The pose viewer and
boundary auditor are offline diagnostics, not runtime acceptance.

Early 448-triangle and 334-triangle Mario trials damaged silhouettes. A
502-triangle regional trial passed the static boundary screen and all 613
authored animation-frame bounds checks, but its texture projection failed
visual review. Rebaking every triangle expanded 538 vertices to 1,506, losing
much of the compaction benefit. The later 450-triangle/472-vertex joint trial
retains all 31 eye-overlay triangles and eight cap-emblem triangles exactly;
35 triangles still cross original UV charts, causing cheek/neck artifacts.
Hard original UV-chart constraints remove those crossings but retain 576
triangles and 590 vertices. This produces coherent face colours at only
13.5% triangle reduction, so it does not meet the intended aggressive art
target. The next isolated trial relaxes unnecessary sharp-fold locks on the
torso and limbs while retaining joints and terminal supports, and adds
posterior-cap and ear silhouette supports. Selective Wii-texture repair is
being tested separately; it must not conceal a damaged silhouette.
These are rejected or pending research outputs, not the staged roster and
not evidence of an FPS gain. Source UV-chart constraints and local texture
repairs must pass textured and posed review before runtime integration.

Reproduce the private QA package from the completed authored cook with:

```sh
.venv/bin/python tools/prepare_o3ds_test.py --reuse-cooked-assets --profile aggressive --activate-candidate
```

Omit `--reuse-cooked-assets` to run the authored extraction/cook validation
first. New 3DS and release builds always select the original geometry until
the physical-hardware acceptance gate is met.

## Goal and constraints

Use this repository's authored Wii conversion pipeline and the user's local
USA RMCE01 disc to make a separately testable Old 3DS model set. Preserve New
3DS models. Ship the reduced set only if real Old 3DS testing demonstrates at
least 5 FPS improvement (target 5–10) in demanding scenes, with acceptable
appearance and no material regressions. Do not change physics or collision.

There is no universal 160k-polygon scene limit. At 30 FPS, 160k submitted
triangles per frame means 4.8 million submissions per second, before extra
passes. Polygon count alone does not determine runtime speed. This renderer
already has Old 3DS quality settings, authored scenery LODs, frustum culling,
a 30 FPS presentation cadence, CPU skinning and frame profiling. Compare
against that existing Old 3DS profile; do not disable its other optimizations
for the baseline. A solid 30 FPS scene cannot gain another 5 FPS under this cap.

## Implementation

1. Keep disc verification, BRRES parsing, original animations, material and
   lighting conversion, textures, and final package overlays in the existing
   `tools/release/build_cia.py` pipeline. Use the final staged package as the
   reduction input, so a stale pre-overlay model cannot become the candidate.
2. Generate `models/o3ds/*.gmdl` sidecars with the pinned meshoptimizer tool.
   Aggressive mode uses a 30% remaining-triangle target and bounded error;
   it is a target, not permission to exceed the error limit. Record hashes,
   original/candidate counts, skipped meshes and reasons in the receipt.
3. Simplify independently within materials, respecting borders, normals,
   both UV sets, vertex colours and animation bindings. Preserve retained
   vertex bytes and source ordering with a strictly increasing O3RM mapping.
   Remap animation bindings/ranges together, preserving original shape offsets
   for facial textures. Keep material joins and transparent/cutout outlines.
   Do not invent simplified collision or remove animation data.
4. Select sidecars only in a test build, after successful Old 3DS hardware
   detection and an explicit `o3ds-models-test.txt` marker. New 3DS, unknown
   hardware and release builds keep original models. `full-quality.txt`
   disables the candidate path, but must be absent in normal A/B comparisons.
   Missing or incompatible candidates fall back to the original model.
5. Keep the production packaging allowlist unchanged. A separate test wrapper
   adds candidates after ordinary staging. Running the ordinary packager
   again removes test-only files. No production release is enabled by a
   benchmark script, filename, synthetic test or reduced triangle count.

The conservative mode reduces triangle work and index-buffer size only.
Aggressive mode also compacts vertices and their animation bindings. Its
receipt distinguishes those structural savings from measured CPU/GPU time.
If submission dominates, investigate batching and visibility rather than
assuming that fewer triangles necessarily produce a proportional FPS gain.

## Art direction

The production geometry cook requires only Mario Kart Wii source assets.
Locally supplied DS/3DS models may inform private visual and triangle-budget
research, but their geometry, UVs, animation data and textures must not be
copied into candidates or required by the build. Generate reduced geometry
from the Wii meshes, retaining Wii texture and rig provenance. A DS-like
result is an art target, not a claim of reproducing an exact DS mesh or a legal
clean-room certification.

Use the supplied Mario comparison as an aesthetic reference, not a triangle
budget or evidence of performance. Preserve recognisable silhouettes, eyes,
moustaches, noses, caps, hands, wheels and kart proportions. Keep large clean
colour areas and the original texture/material treatment. Prioritise smooth
silhouettes over hidden or nearly planar interior edges. Preserve roads,
ramps and visible boundaries; road rendering must still align with collision.

Review side by side at the actual 400×240 top-screen size, then inspect close
captures. Check front/side/back views; idle, steering, drift, wheelie, trick,
item and hit animations; seams, alpha edges, water, sky and moving scenery.
Any visible break overrides a percentage target. Review the player and menu
characters especially carefully. Per-model exceptions are preferable to a
uniform damaging decimation ratio.

Verified rig metadata from `tools/build_o3ds_character_regions.py` associates
bone semantics with an exact source-binding hash. The aggressive cooker uses
it to protect facial geometry, head shapes and seams; unknown signatures
preserve character topology. `tools/audit_o3ds_character_geometry.py` screens
for newly exposed interior vertices on open boundaries. This structural test
cannot detect every removed component, distorted face or bad animation, so
textured and in-game review remain required.

## Preparation and testing

After the transfer is complete, unpack it into ignored project storage and
retain the source archive. Validate the archive and disc identity; never use
a `.download`, `.part` or `.crdownload` as a completed input. RVZ/NKit needs
supported conversion before the pipeline. The normal disc extractor validates
sector H0 hashes and the build checks the expected USA source-file hashes.
Check free disk space before unpacking and cooking; a disc plus intermediate
assets can need substantial additional space.

From the project virtual environment:

```sh
.venv/bin/python tools/prepare_o3ds_test.py "/absolute/path/Mario Kart Wii.iso" --jobs 4 --profile aggressive --activate-candidate
```

This runs the authored pipeline, builds a nonrelease external-assets 3DSX,
stages the normal package, generates candidates, and adds them to
`build/playable/3ds/mkw-3ds`. The activation option starts reduced geometry on
Old 3DS; omit it to start with originals. Copy the generated `3ds` folder to SD
and launch the test 3DSX. Remove `/3ds/mkw-3ds/o3ds-models-test.txt` for the paired
baseline, then restore it for the candidate and restart each time. Do not
change the executable or other quality settings between runs. Preserve each
performance CSV (which includes hardware/build/profile metadata) before restarting; each launch
overwrites the current capture. The private test package contains game assets
and stays local.

For an initial geometry check, run `tools/preview_o3ds_geometry.py` with an
original `.gmdl`, its matching `models/o3ds/` sidecar, and `--output` pointing
to an ignored build PNG. It renders identical front, side and oblique cameras
at 400×240 per panel; `--mode silhouette` removes vertex colours and synthetic
lighting. Use `--textured` to resolve verified Wii source PNGs matching the
staged T3X assets and inspect UVs, alpha and facial detail. These neutral-pose
software diagnostics still cannot approve deformation or gameplay appearance.
The PNG records
both source hashes so it can be tied to the geometry receipt.

First profile representative tracks to identify the slow scenes. Candidate
stress cases include busy 12-racer starts, Coconut Mall scenery, crowds and
alpha-heavy scenes; these are test candidates, not claims about which track
is currently slowest. Use an identical recorded route/controller script,
camera, character/vehicle lineup, item conditions and 2D setting per pair.
The existing QA autoplay supports `auto:N` route driving. Verify its captured
course/racer state and reproducibility before trusting it as a benchmark.
Do not compare different races or use synthetic accelerated replay mode as
normal paced gameplay evidence.

Collect at least three matched baseline/candidate pairs per selected scene,
alternating order, after warmup, with at least 60 seconds of measured gameplay
per run. Count presented frames over total elapsed time, including skipped
presentation iterations. Keep p95/p99 presented-frame time, GPU queue time,
simulation/render/wait costs, triangles, draw calls, skinned vertices and
memory. GPU queue timing includes more than triangle rasterization; it is
diagnostic, not a replacement for sustained FPS.

Use `tools/compare_o3ds_performance.py --template` to create a capture manifest,
then `--manifest captures.json --output comparison.json` to check the timing
gate. Copy the binary and baseline/candidate asset hashes from
`build/o3ds-test-receipt.json`, hash each CSV and the fixed controller script,
and record the actual device and settings. The template's `pairs` list needs
three independent pairs per scene. Real hardware provenance and visual review remain
human evidence. Neither emulator FPS nor host synthetic fixtures establish
Old 3DS speed. Keep the current 30 FPS cap and 60 Hz simulation unchanged.

## Acceptance and follow-up

- Confirm the same test executable and base assets, confirmed Old 3DS
  hardware, ordinary reduced quality in both runs, and candidates actually
  loaded only in the candidate capture.
- Require at least +5 sustained FPS in repeated matched demanding scenes;
  aim for +10 where the 30 FPS cap allows. Frame-time headroom at 30 FPS does
  not satisfy the user's requested FPS increase by itself.
- Require no sustained FPS or significant p95/p99 regression in other scenes,
  no gameplay/collision/animation defects, and an approved visual comparison.
- Confirm on New 3DS that candidate markers/assets do not select reduced
  models and that its visual output and performance remain unchanged.
- Only after the evidence passes, prepare a separate release-enablement
  change with an approved asset manifest, source/content fingerprints and
  per-model exceptions. If it fails, retain original models and report the
  observed bottleneck rather than enable a cosmetic downgrade.

## Subsequent experiments

The user subsequently requested aggressive consistent resolution cuts,
distant driver/kart disappearance, frame skipping, engine research, and use
of freed GPU capacity for CPU work. The detailed sequence and source audit are
in [o3ds-engine-research.md](o3ds-engine-research.md).

The first separately testable engine option is `o3ds-racer-distance.txt` on SD.
Its only content is a distance between 1000 and 25000 game units (for example
`8000`, an experimental setting, not a measured recommendation). It is read at
startup only in a QA build on confirmed Old 3DS, with full-quality mode off.
Absent/invalid files retain the existing 25000-unit behavior. It hides the
entire opponent driver/kart/wheels/accessories before pose work; the local
player, physics, controller clocks and collision stay active. An opponent
returns at 90% of the cutoff to avoid boundary flicker, sampling its current
animation instead of blending from a stale hidden pose. Items and particle
systems retain their existing independent visibility rules and need visual
review for detached effects at the cutoff.

Measure this option **without** `o3ds-models-test.txt` first, using the same
original model assets in both runs. Record the enabled state, distance and
increasing `racer_distance_culled` counter. The comparison tool's
`racer_distance` experiment keeps this separate from `model_decimation`; a
combined experiment is only justified after isolated evidence. Culling stays
disabled in release builds until tested. No chosen cutoff is production-ready.

The profiler also separates actual CPU position transforms
(`cpu_skinned_vertices`) from indexed GPU skinning submissions
(`gpu_skin_vertex_references`). The latter counts submitted vertex references,
not unique shader executions, because GPU vertex caching is unknown. The
older `skinned_vertices` column mixes paths and must not be treated as CPU
cost. These measurements guide palette compaction/batching work; physics is
not moved to the GPU.

A fixed 200×120 scene with a sharp native HUD is the next resolution candidate,
not yet implemented. It requires a separate texture target, upscale composite,
scaled scissor, deferred-frame state and allocation fallback. Existing 30 FPS
presentation already skips alternate 60 Hz simulation ticks. More skipping
would lower visual FPS and cannot be presented as achieving a +5 FPS gain.

## Download continuation

The completed archive was found on 15 October 2026, unpacked successfully
without modifying it, and passed the USA revision, 53 source-file hashes and
game-code validation. The authored preparation pipeline has completed. The
15-minute “Watch for Mario Kart Wii transfer” monitor is now stopped.
Archive/disc observations are in ignored `build/wii-download-watch.json` and
`build/wii-transfer-validation.json`; progress is in
`build/o3ds-preparation.log`. The supervisor stops cooking if free disk space
falls below 2 GiB. Completed export intermediates are pruned, and macOS
package staging uses independent copy-on-write files where supported.
Real hardware performance and visual approval remain outstanding.

## Hardware references

- [DMP: Nintendo 3DS uses PICA200](https://www.dmprof.com/en/news/dmp-3d-graphics-ip-core-pica200-is-adopted-by-nintendo-3ds/)
- [3dbrew hardware research](https://www.3dbrew.org/wiki/Hardware)
- [libctru hardware detection implementation](https://github.com/devkitPro/libctru/blob/master/libctru/source/services/apt.c)
- [Citro3D timing implementation](https://github.com/devkitPro/citro3d/blob/master/source/renderqueue.c)
