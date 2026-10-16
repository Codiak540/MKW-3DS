# Old 3DS combined build (2026-10-16)

Every Old 3DS performance branch, merged into this checkout as uncommitted
changes (nothing committed or pushed). Each part's own doc has the details.

| Part | Source | Doc |
| --- | --- | --- |
| Low-poly overlay (characters for CPUs, karts, landmarks, levels) | this checkout | docs/o3ds-lowpoly.md |
| Track sectioning and course visibility (`models/pvs/*.pvs`) | branch `o3ds-pvs` | docs/o3ds-pvs-log.md |
| Vertex-cache triangle order | branch `o3ds-asset-gpu` | docs/o3ds-asset-gpu.md |
| Course texture mipmaps (`textures/o3ds/mips.pak`) | branch `o3ds-texture-mips` | docs/o3ds-texture-cache.md |
| Engine/CPU: core layout calibration, LTO, PGO, animation, citro3d | branch `o3ds-engine-perf` | docs/o3ds-engine-perf-summary.md |
| Bit-exact Kinoko batching and two-core kart step | branch `o3ds-kinoko-arm11` | docs/o3ds-kinoko-arm11.md |
| Scenery/racer LOD and object visibility (`models/pvs/*.opvs`) | branch `o3ds-scenery-lod` | docs/o3ds-scenery-lod.md |
| GPU frame pipelining and course display lists | branch `o3ds-draw-pipeline` (fast-forwarded into `o3ds-performance`) | docs/o3ds-draw-pipeline.md |

Also: bloom back on in Old 3DS mode (one blur round), and the player's own
driver at its original model (CPUs and other players stay reduced).

## How it was merged

Per file, three-way: the branch's own baseline snapshot as base, this working
tree as ours, the branch head as theirs. Conflicts resolved by hand:
`include/mkw/runtime_profile.hpp` (engine `sim_*` columns and texture-mip
columns both kept, header and rows in the same order), `tools/o3ds_perf_run.py`
(engine runner + `--course` + `--emu`/`--emulator`), `tools/build.py` (engine
flags, LTO and PGO + Kinoko's `MKW_OPT`), `src/main.cpp` (Kinoko worker
begin/end inside the engine's profiler laps), `src/racer_visuals.cpp` (includes).

`vendor/citro3d` and `vendor/kinoko` carry their patches as working-tree
edits: both were checked to be exactly upstream + the old patch, then moved to
upstream + the merged `patches/citro3d-hardware.patch` / `patches/kinoko-arm11.patch`.

QA tools here default to `build/azahar-o3ds-auto`; `build/azahar-o3ds` is the
user's own play copy.

## Verification

- Host tests: core calibration, wrap angle, texture mips, model variant,
  model detail: pass. Python tests (texture mips, compare tool): 54 OK.
- Kinoko determinism harness, all 32 courses x 2 seeds x 6,000 ticks:
  reference vs phased 64/64 identical; reference vs parallel (worker thread,
  50 us jitter) 64/64 identical, karts split across both threads.
- Emulator (`tools/o3ds_perf_verify.py merged --reference premerge`, pre-merge
  build as reference): physics replay pass (9,064 steps, same heap), CPU racer
  log 920/920 lines identical. Lockstep captures 45/52 identical; the 7 others
  differ by 1-423 pixels on CPU racers only, from the scenery branch's
  opponent pose LOD (small racers re-pose on alternate frames). With
  `o3ds-pose-lod-off.txt`: 52/52 identical.
- PGO: `pgo/race-profile.tar.gz` re-recorded for the merged code
  (`tools/o3ds_pgo_profile.py`, 240 objects; the engine branch's profile
  predates the Kinoko and texture changes). Rebuilt binary re-verified: replay
  pass, CPU racer log 920/920 identical, the same 45/52 captures (pose LOD only).
- Staging: 32 track and 25 object visibility sidecars match the overlay
  geometry (fingerprints), 1,159 mip chains validate, every sidecar validates.

Emulator time is not hardware time. Hardware checklists:
docs/o3ds-engine-perf-summary.md ("How to confirm on a real Old 3DS"),
docs/o3ds-texture-cache.md section 5, docs/o3ds-kinoko-hardware-checklist.md.

## Pushing to GitHub later (checked 2026-10-16, nothing pushed)

Committed locally on 2026-10-16 as `d698e02` on branch `o3ds-performance`
(from `af17857`; `main` is untouched; nothing pushed). New worktrees branch
from this commit. The "skip the main driver cook" change to
`tools/cook_o3ds_overlay.py` / `tools/stage_o3ds_lowpoly.py` is in it while
its proof run was still finishing; a follow-up commit records that result.

- Nothing game-derived is tracked: `build/`, `data/`, `assets/`, `sd-card/`,
  `vendor/` and `.toolchain/` are ignored. The overlay, visibility files and
  mip chains are cooked on each builder's machine from their own disc; the
  vendored Kinoko/citro3d changes travel as `patches/*.patch`.
- The work is 174 source/doc/test files on top of `af17857` (plus
  `pgo/race-profile.tar.gz`, compiler counts only, and
  `licenses/meshoptimizer-LICENSE.txt`).
- Since `af17857`, the fork (`Codiak540/MKW-3DS`, `origin`) gained 7d84ac5 and
  `MiniBandit/MKW-3DS` gained 14 commits (builder hardening, triage). A trial
  merge of this work showed conflicts only in: fork — `object_bank.hpp/.cpp`,
  `render_profile.hpp`, `runtime_profile.hpp`, `course_objects.cpp`,
  `tools/build.py` (8 hunks); MiniBandit — `src/main.cpp` (24 hunks),
  `tools/build.py` (2 hunks).
- Suggested order: commit this work on a branch from `af17857`, merge
  `origin/main`, then MiniBandit `main`; rebuild, then run
  `tools/o3ds_perf_verify.py` (replay, CPU racer log, captures) and the Kinoko
  harness (`tools/sim_determinism/run.py --paths reference parallel`) before
  pushing the branch and opening a pull request.
- `tools/package_sd.py` changed, so the content fingerprint changes: players
  need the full CIA (or a fresh builder run), not only a code update.

## Release CIA (0.6.1, 2026-10-16)

Built with the builder's own stages: `tools/build.py --release`,
`tools/package_sd.py --no-zip` (now ships the Old 3DS overlay from
`build/o3ds-lowpoly-package`, every file checked against its staging receipt),
`tools/release/package_cia.py`, then `build/release/sd-card/3ds/mkw-3ds` with
its `content.txt`. In this fork the CIA is code only (commit 51d2fe9 dropped
`-DAPP_ROMFS`), so players install `build/release/mkw-3ds.cia` and copy
`build/release/sd-card/3ds` to the SD card root; the overlay lives there
(2,337 main + 1,307 opponent sidecars, 339 re-baked textures, `mips.pak`,
32 track + 25 object visibility files). The release build was booted in the
emulator (reports 0.6.1); release builds ignore QA markers, so races were
verified with the QA build of the same code.

## Release CIA 0.6.2 (2026-10-16): drawing pipeline added

`o3ds-performance` fast-forwarded to the drawing-pipeline branch (`abeb635`);
`vendor/citro3d` moved to upstream + the merged `patches/citro3d-hardware.patch`.
QA build checked against the 0.6.1-era build (`tools/o3ds_perf_verify.py dl
--reference perf0`): physics replay pass (9,064 steps), CPU racer log 920/920
identical, 52/52 lockstep captures identical; emulated course drawing -23.5%,
draw-element time -10%. Release built with the same stages as 0.6.1:
`build/release/mkw-3ds.cia` (code) + `build/release/sd-card/3ds/mkw-3ds`
(assets and the Old 3DS overlay, with `content.txt`).

## Race loading: overlay in one archive (2026-10-16)

On real hardware the course-to-race load felt very slow. Emulator race loads
had roughly doubled with the overlay (mean 7.8 s originals vs 16.3 s), because
every model load opened extra loose files in folders of thousands of entries
(`models/o3ds` 2,337, `models/o3ds/rival` 1,307). The sidecars and re-baked
textures now ship as `o3ds.pak` (MPK1, `tools/o3ds_pak.py`; the runtime's third
archive in `src/asset_pack.cpp`); `mips.pak` and the visibility files stay loose.
Same QA binary, loose vs pak: Luigi Circuit 16.9 -> 9.4 s, Coconut Mall
18.3 -> 10.4 s, Bowser's Castle 17.6 -> 10.1 s; identical sidecar counts and
33/33 identical frames.

## Merged with the fork's `main` (2026-10-16)

`origin/main` (`Codiak540/MKW-3DS`: 7d84ac5, e96a01b, then d268788) merged
into `o3ds-performance`. Conflicts in 8 files (then 3), resolved by hand:

- Flush batching (`include/mkw/flush_batch.hpp`) kept as written. It defers a
  frame's cache flushes to `FrameEnd`. That is safe with frame pipelining:
  a recording slot's queue only runs at its own `FrameEnd`, and the
  command-space continuation flushes the whole linear heap first. The direct
  `svcFlushProcessDataCache` (SVC 0x54) is allowed in `tools/release/mkw-3ds.rsf`.
- Lit GPU skinning of course objects is on for every console (the fork's
  choice; here it was an Old 3DS QA marker). Both branches had written it.
  Now one `ObjectBank::gpuSkin(model, litNormals)`, whose lit path keeps
  DOBJ's direct normal transform (`skinnedNormalsDirect`, matching the CPU
  path's `normalize(B*n)`). `objectGpuSkin()` now only counts loads and
  fallbacks; its marker no longer changes anything.
- Escalators: the fork's batched write, merged flush and off-screen skip,
  with each mesh writing its pipelined buffer (`Model::beginWrite`).
- Profiler: both sets of slots and CSV columns.
- FPS counter. In e96a01b it never counted frames and updated in the menu
  loop, so it always read 00. d268788 counted every race-loop pass, which at
  30 FPS also counts the passes that only step the simulation. Now
  `GameState::frameDone()` runs once per drawn race frame and keeps
  d268788's static members and mean frame time; the window restarts after
  a race load. FPS (green) and ms per frame (light blue) are timer digits on
  a dark backing in the bottom-right corner of the map (the fork's spot
  overlapped the timer's TIME label).
- d268788 also makes Old 3DS present races every tick (`raceTicksPerFrame`,
  aiming at 60 FPS) instead of every other tick (30 FPS). Kept: the team
  chose the 60 FPS target (2026-10-16).
- `tools/build.py`: the fork's `-Wno-error` is kept. A failed compile still
  raises: the fork's change linked a stale object instead, and never wrote
  the object signatures.

Verification (`tools/o3ds_perf_verify.py merge --reference dl`, the drawing
pipeline build as reference): physics replay pass (9,064 steps, same heap),
CPU racer log 920/920 lines identical, all 26 top-screen captures identical.
The bottom-screen captures differ only inside the FPS readout's box
(x 240-317, y 217-236). After d268788 the same again (`merge4`: replay pass,
920/920, 26/26 top captures). QA runs step two ticks per drawn frame, so
they do not exercise d268788's 60 FPS presentation on Old 3DS. Emulated
Luigi Circuit: render thread -10.4%, effect upload -71%. The fork's flush
gain is a hardware effect (a GSP flush call is cheap in the emulator).
