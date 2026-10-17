# Old 3DS course textures and the PICA200 texture cache

Old 3DS only. New 3DS, unknown hardware and `full-quality.txt` runs load and
place every texture exactly as before. Physics, collision and model data are
untouched; game data comes only from the user's Mario Kart Wii dump.

Status (2026-10-16, branch `o3ds-texture-mips`): runtime path,
cook tool, validation, tests and emulator A/B done. 1,159 of 1,329 course
textures get mip chains (23 also become ETC1), every course's textures fit in
VRAM with the chains, and the emulator A/B on 8 courses shows every changed
frame calmer, the bottom screen identical and 0 fallbacks. **Not yet known:**
the frame-time effect, because the emulator does not model the texture cache;
section 5 is the hardware A/B to run. Nothing is staged for release.

## 1. Research

### 1.1 What the hardware has

| Fact | Source |
|---|---|
| PICA200: four texture units (three sample 2D textures, the fourth is procedural); each has **256 bytes of L1** texture cache and all share **8 KB of L2** | [Copetti, Nintendo 3DS Architecture](https://www.copetti.org/writings/consoles/nintendo-3ds/) (GPU section) |
| 6 MB VRAM inside the SoC, "expected to be used to store data that needs instant access (commands, buffers and recurrent textures)"; the rest lives in FCRAM; a DMA unit copies between them | Copetti (above); [3dbrew Hardware](https://www.3dbrew.org/wiki/Hardware) |
| FCRAM (128 MB on Old 3DS) is the main memory; the ARM11 cores run from it and LINEAR (GPU-visible) allocations live in it | 3dbrew Hardware; libctru `linearAlloc` |
| `GPUREG_TEXUNIT_CONFIG` bit 16 clears the texture cache | [3dbrew GPU/Internal Registers](https://www.3dbrew.org/wiki/GPU/Internal_Registers) |
| `GPUREG_TEXUNITi_PARAM` bit 1/2 mag/min filter, bit 24 mipmap filter (nearest/linear); `GPUREG_TEXUNITi_LOD` bits 0-12 LOD bias (fixed 1.4.8), 16-19 max level, 24-27 min level; level data follows level 0 directly; texels are in 8x8 Z-order tiles | 3dbrew GPU/Internal Registers |
| citro3d re-sends the texture unit config with the cache-clear bit whenever a texture binding changes, so every texture switch starts with a cold cache | `vendor/citro3d/source/base.c` (C3Di_UpdateContext, lines 237-266); upstream [citro3d](https://github.com/devkitPro/citro3d) |
| citro3d allocates a texture's whole chain contiguously (`C3D_TexCalcTotalSize(size, maxLevel)`), in VRAM (`vramAlloc`) or LINEAR FCRAM (`linearAlloc`); `C3D_TexSetLodBias` writes the bias field | `vendor/citro3d/source/texture.c`, `include/c3d/texture.h` |
| Wii (Hollywood) has 1 MB of embedded texture memory used as a texture cache | [Wikipedia: Hollywood (graphics chip)](https://en.wikipedia.org/wiki/Hollywood_(graphics_chip)) |

No public source gives the PICA200's cache line size, associativity, miss
latency or VRAM/FCRAM bandwidth (searched 3dbrew, devkitPro forum, citro3d,
DMP material, emulator sources). Azahar does not model texture-cache timing
(its hardware renderer samples host GPU textures), so the cost model below is
derived from the cache sizes and must be confirmed on a console (section 5).

### 1.2 Why Mario Kart Wii's courses hit this

Mario Kart Wii was authored for a 1 MB texture cache: only 3 of the 1,329
course textures carry mip levels (`docs/o3ds-asset-gpu.md` section 2). On the
PICA200 the shared L2 is 8 KB, 128 times smaller.

Bytes per 8x8 tile (the Z-order unit the cache works on):

| Format | bits/texel | 8x8 tile | tiles in one L1 (256 B) | texels in L2 (8 KB) |
|---|---:|---:|---:|---:|
| ETC1 | 4 | 32 B | 8 | 16,384 (128x128) |
| ETC1A4 | 8 | 64 B | 4 | 8,192 |

Let *k* be the minification (texels stepped per screen pixel along an axis).
At *k* <= 1 a tile serves dozens of pixels. At *k* >= 8 neighbouring pixels
land in different tiles and every bilinear footprint is a miss: one tile
(32 B ETC1, 64 B ETC1A4) per pixel, versus about 0.5-2 B per pixel when the
sampled level matches the screen (a mip chain keeps *k* near 1 at every
distance). That is a 16-64x difference in texture traffic for distant road,
walls and scenery, which is most of a racing game's frame. The same surfaces
also alias (shimmer), which is the visible symptom of the same sampling.

Consequences:

* **Mip chains are the main lever.** They cap the traffic per pixel
  independently of distance. Cost: +33% texture memory.
* **VRAM placement matters more for misses.** Misses from FCRAM compete with
  the ARM11 cores for main memory (the engine work runs from it), while VRAM
  is on the SoC. The engine already moves single-level course textures into
  VRAM; mip chains must follow them or part of the lever is lost.
* **ETC1A4 costs twice ETC1 per miss.** 23 course textures are ETC1A4 with
  every alpha nibble 0xF: as ETC1 they sample identical texels at half the
  bytes.
* **Alpha-tested ETC1A4 with real alpha is a separate case.** Box-filtered
  alpha drops coverage under an alpha test (foliage and fences thin out with
  distance), so those textures keep level 0 only here. A coverage-preserving
  alpha chain (scale each level's alpha so the share of texels passing the
  material's alpha reference matches level 0) would make them eligible; see
  section 6.
* **Trilinear vs nearest-level.** The mipmap filter (PARAM bit 24) linear
  blends two levels per sample. Whether that halves the PICA200's sampling rate
  is undocumented, so the QA build has a nearest-level switch for the
  hardware A/B.
* **LOD bias** could trade sharpness for traffic (positive bias picks smaller
  levels sooner). Not used: it would make mid-distance surfaces blurrier, not
  just calmer.

### 1.3 Other ports

Common low-spec practice (and what Copetti describes for the 3DS) is
compressed formats, mip chains and VRAM for the textures drawn every frame.
This port already had the first and the last (ETC1/ETC1A4 for every course
texture; `Renderer::moveToVram` since the Old 3DS reduced-quality work); the
mip chains were missing because the source game never needed them. Mario Kart
7 was not inspected: the user's MK7 dump is a 7z with no extractor on this
machine and unpacking 1 GiB was not justified on a nearly full disk; it would
be a technique reference only anyway.

## 2. Runtime (Old 3DS only)

Files: `include/mkw/texture_mips.hpp` (policy, OMIP and MPK1 parsing, pure and
host-tested), `src/texture_mips.cpp` (archive I/O, C3D texture), small hooks
in `src/renderer.cpp`, `include/mkw/renderer.hpp`, `src/main.cpp`,
`include/mkw/runtime_profile.hpp`.

**Data.** `textures/o3ds/mips.pak` is an MPK1 archive (the menu.pak/race.pak
layout) keyed by the original texture name. Each entry is OMIP v1: a 32-byte
header (`"OMIP"`, version, the original T3X's byte count and CRC32, result
format, level count, flags, tail byte count and CRC32, zero) and then levels
1..n in PICA tile order. Level 0 is never stored: it always comes from the
original T3X the game already loads, so near and 1:1 surfaces sample the very
same texels. Flag bit 0 turns an ETC1A4 whose alpha is all 0xF into ETC1 (the
8-byte colour block of every 16-byte ETC1A4 block, verbatim).

**Load path.** `Texture::load` (any model, any thread) reads the original as
before; when `textureMips().enabled`, `loadTextureMips` looks the name up in the
archive (opened once per session; one SD open instead of one per texture).
No entry: the original path, silently. An entry must pass `readMipVariant`:
original size and CRC32 equal, the original a single full-size uncompressed
ETC texture without levels, result format equal (or the flagged ETC1A4 ->
ETC1 with every alpha nibble verified 0xF), 1..7 levels with every level at
least 8x8, tail size exact, tail CRC32 equal. Any mismatch counts a fallback
and the original loads unchanged, exactly as the O3RM sidecars fall back.
Accepted chains get a trilinear filter (mip filter linear), or nearest-level
with the QA marker.

**Gate.** `textureMips().configure(modelDetail().candidate, off, nearest)`:
only a console identified as Old 3DS, not `full-quality.txt`, not
`o3ds-models-off.txt` (the mips ride on the Old 3DS overlay), and in QA builds
not `o3ds-texmips-off.txt`. QA `o3ds-texmips-nearest.txt` selects nearest-level
sampling for a hardware A/B. New 3DS never opens the archive. Release builds
ignore the markers; the data's presence decides.

**VRAM residency.** `Renderer::moveToVram` used to skip any texture with
levels. It now copies the whole chain (`C3D_TexCalcTotalSize(size, maxLevel)`,
`C3D_TexInitWithParams(..., maxLevel, ..., onVram=true)`; levels are contiguous
in both allocations). The budget is unchanged: a texture moves only while
`vramReserve` (1 MiB) stays free; the existing order (track, sky, objects)
gives the road and walls first claim. `Renderer::vramMoves` counts moved,
mip-chained and kept (did not fit) textures and bytes for the QA log. Only
reduced quality (Old 3DS) moves course textures at all, so New 3DS is
unaffected; on Old 3DS the 3 natively mipmapped course textures now move too.

**QA output.** `runtime.log`: `texture_mips=1 nearest=0` at start-up, and per
course `course textures in VRAM moved=.. mipmapped=.. bytes=.. kept=..
kept_bytes=.. vram_free=..` and `texture_mips loaded=.. fallbacks=..
converted=.. level_bytes=.. alpha_bytes_dropped=..` (cumulative).
`performance.csv` gains `texture_mips_enabled, texture_mips_nearest,
texture_mips_loaded, texture_mips_fallbacks, texture_mips_level_bytes`
(appended after the existing columns).

### Changes in shared files (for the engine branch, `src/renderer.cpp` overlap)

* `src/renderer.cpp`: one include; two lines in `Texture::load` (after the
  OTEX block: `if(textureMips().enabled&&loadTextureMips(path,file,*this))return true;`);
  `Renderer::moveToVram` body (whole-chain size and `C3D_TexInitWithParams`
  with `maxLevel`, 2D-only check, `vramMoves` counters).
* `include/mkw/renderer.hpp`: `moveToVram` comment and the `VramMoves vramMoves` member.
* `src/main.cpp`: one include, `textureMips().configure` + log after
  `textureDetail().configure`, two log lines in `moveCourseTexturesToVram`.
* `include/mkw/runtime_profile.hpp`: one include, three row fields, five
  trailing CSV columns.
* New: `include/mkw/texture_mips.hpp`, `src/texture_mips.cpp`.

## 3. Cook (`tools/cook_o3ds_course_textures.py --mode pack`)

```sh
.venv/bin/python tools/cook_o3ds_course_textures.py --mode pack --out build/o3ds-texmips3 --jobs 6
c++ -std=c++17 -O2 -Iinclude tools/validate_texture_mips.cpp -o build/validate_texture_mips
build/validate_texture_mips build/o3ds-texmips3/textures/o3ds/mips.pak sd-card/3ds/mkw-3ds
```

Input: the T3X files exactly as the runtime loads them (menu.pak/race.pak
first, then loose files) from the user's converted Wii data. Course textures:
everything the track lists, sky lists and `objNN_*` / `coconut_*` objects of
the 32 courses reference (the audit's definition). Material state for every
texture comes from every model's GMDL submesh records (the state
`src/renderer.cpp` applies). Per texture:

| Case | Result |
|---|---|
| opaque ETC1, both sides > 8 | chain to 8 texels; each level a Lanczos-2 2:1 downsample of the previous one (decoded level 0 first) with the material's S/T wrap at the borders, ETC1 high quality |
| ETC1A4, every alpha nibble 0xF | ETC1 (colour blocks verbatim, half the bytes) + the same chain |
| ETC1A4, alpha used, all materials alpha-tested alike (GREATER/GEQUAL, one reference) | ETC1A4 chain: alpha-weighted colour; per level, exactly the share of texels passing the test that level 0 has (alpha scaled, then ranked with an ordered-dither tie-break), so distant fences and foliage keep their density |
| ETC1A4, alpha used, blended with SRC_ALPHA | ETC1A4 chain: alpha-weighted colour (equals the premultiplied average), mean alpha |
| ETC1A4, alpha used, neither tested nor blended | plain box chain |
| used as a secondary (light/environment) map anywhere | no chain (alpha-free ETC1A4 still -> ETC1) |
| materials disagree, already mipmapped, not one full uncompressed ETC image, smaller than 16 on a side | original |

Why these filters:

* Box (tex3ds `-m box`, the first version) blurred thin features on oblique
  surfaces too much (isotropic mip selection uses the most minified axis;
  the PICA200 has no anisotropic filtering): the orange curb at Luigi
  Circuit's first bend nearly vanished. Lanczos-2 keeps more of their
  contrast at no runtime cost. tex3ds's own Lanczos filters clamp at the
  borders (ImageMagick), which would seam tiled road textures, hence the
  in-tool, wrap-aware filter and one tex3ds encode per level.
* tex3ds truncates alpha to 4 bits, so the cook quantizes (round to nibble x17)
  first and coverage is computed on what the GPU will see.
* Box-filtered alpha under an alpha test thins out with distance (a measured
  example: 34% coverage -> 63%, 0%, 0% for levels 1-3); the coverage-preserving
  chain keeps 34% at every level.

Totals (receipt `docs/receipts/o3ds-course-textures-pack-summary.json`):

| | textures | GPU bytes before | after |
|---|---:|---:|---:|
| ETC1 chains | 635 | 8.18 MiB | 10.90 MiB |
| ETC1A4 -> ETC1, no chain (all are 32x32 secondary maps) | 23 | 23 KiB | 11.5 KiB |
| ETC1A4 alpha-tested chains | 256 | 5.38 MiB | 7.16 MiB |
| ETC1A4 blended chains | 202 | 2.76 MiB | 3.68 MiB |
| ETC1A4 other-alpha chains | 43 | 0.72 MiB | 0.96 MiB |
| **all entries** | **1,159** | **17.07 MiB** | **22.71 MiB (+33%)** |
| kept (secondary maps 52, materials disagree 31, too small 84, already mipmapped 3) | 170 | 1.64 MiB | unchanged |

Archive: 5.7 MiB on the SD card (levels only). Per course, all course
textures together grow from 0.15-1.05 MiB to 0.19-1.39 MiB (Grumble Volcano
the largest), well inside the ~2.6 MB of VRAM above the 1 MiB reserve that the
engine leaves for them (section 4).

Staging: `tools/stage_o3ds_lowpoly.py --texture-mips <mips.pak>` validates the
archive with the runtime parser and adds it to the overlay package's
`textures/o3ds/`; without the flag nothing changes. Any re-conversion of the
Wii data that changes a texture's bytes makes that entry fall back (CRC), so
re-run the cook after a re-cook of textures.

## 4. Emulator A/B and measurements

Setup: a private Azahar copy (Old 3DS mode, `build/azahar-o3ds-auto` in this
worktree), one 3DSX per comparison, `tools/o3ds_texmips_ab.py run` = two
`tools/o3ds_perf_run.py` races per course: VS, Mario, Standard Kart M,
1,500 CPU-driven steps after the course intro, lockstep captures every 60
steps (26 top + 26 bottom frames), `performance.csv` and `runtime.log`.
Arm "off" = `o3ds-texmips-off.txt`, arm "on" = the final archive.
`tools/o3ds_texmips_ab.py compare` counts identical frames, changed pixels
and the mean |Laplacian| of luma over the changed pixels in each arm (a
high-frequency/aliasing measure: lower is calmer). Receipt:
`docs/receipts/o3ds-texmips-emulator-ab.json`. Side-by-side PNGs (off | on |
diff x4) are in `build/texmips-ab/ab5/` (game-derived, not committed).

Identity checks:

* The new binary with `o3ds-texmips-off.txt` is pixel-identical to the
  baseline binary (this branch before the change): 52/52 frames, Luigi
  Circuit. The off path changes nothing.
* The QA build (with the extra CSV columns) renders exactly as the build used
  for the A/B: 52/52 frames.

Final archive, LOD bias -0.5 (the shipped default):

| Course | top frames changed | bottom identical | changed px / frame | \|lap\| changed px, off -> on | calmer frames | chains loaded / fallbacks | course textures moved to VRAM | VRAM free after | render thread ms off / on |
|---|---|---|---:|---|---|---|---|---|---|
| Luigi Circuit | 26/26 | 26/26 | 22,521 | 60.1 -> 39.7 (-34%) | 26/26 | 39 / 0 | 0.77 MB, kept 0 | 2.84 MB | 3.90 / 3.90 |
| Moo Moo Meadows | 26/26 | 26/26 | 31,996 | 47.8 -> 25.5 (-47%) | 26/26 | 42 / 0 | 1.03 MB, kept 0 | 2.59 MB | 3.76 / 3.76 |
| Coconut Mall | 26/26 | 26/26 | 35,041 | 44.0 -> 29.5 (-33%) | 26/26 | 51 / 0 | 1.05 MB, kept 0 | 2.57 MB | 5.58 / 5.57 |
| DK Summit | 26/26 | 26/26 | 27,532 | 40.4 -> 31.0 (-23%) | 26/26 | 45 / 0 | 1.12 MB, kept 0 | 2.78 MB | 3.77 / 3.76 |
| Daisy Circuit | 26/26 | 26/26 | 33,395 | 40.5 -> 24.8 (-39%) | 26/26 | 47 / 0 | 0.95 MB, kept 0 | 2.67 MB | 4.83 / 4.82 |
| Rainbow Road | 26/26 | 26/26 | 20,310 | 59.7 -> 47.7 (-20%) | 26/26 | 27 / 0 | 1.08 MB, kept 0 | 2.54 MB | 4.05 / 4.07 |
| Grumble Volcano | 26/26 | 26/26 | 30,783 | 26.3 -> 16.8 (-36%) | 26/26 | 51 / 0 | 1.56 MB, kept 0 | 2.34 MB | 4.79 / 4.80 |
| Toad's Factory | 26/26 | 26/26 | 30,749 | 47.0 -> 32.6 (-31%) | 26/26 | 61 / 0 | 1.52 MB, kept 0 | 2.10 MB | 4.05 / 4.06 |

(changed pixels of 96,000 per top frame: 21-36%, all in minified areas; the
bottom screen never changes.) Grumble Volcano and Toad's Factory have the
most course texture bytes of all 32 courses, so every course fits: no course
texture stays in FCRAM, and at least 2.1 MB of VRAM remains free. Before
this change, mip-chained textures could not move at all.

Frame timings: emulated render-thread time and loop interval are unchanged
within 0.5% (the CPU-driven race runs at the capture lockstep, and Azahar does
not model the GPU texture cache, so no FPS claim comes from these runs).
Load cost: "load track" +84 to +167 ms (reading the levels, CRC32 of the
originals), whole race load +0 to +0.22 s (<= 1.2%).

Per step (Luigi Circuit unless noted; |lap| over the pixels each arm changes):

| Arm | changed px / frame | \|lap\| off -> on |
|---|---:|---|
| step 1: ETC1 box chains only (Luigi Circuit) | 22,557 | 43.4 -> 29.2 (-33%) |
| step 1: ETC1 box chains only (Moo Moo Meadows) | 27,496 | 36.5 -> 18.3 (-50%) |
| step 2: + alpha-aware ETC1A4 chains, box colour | 27,042 | 52.9 -> 33.1 (-37%) |
| step 3: Lanczos-2 colour, bias 0 | 27,096 | 53.0 -> 32.7 (-38%) |
| nearest level instead of trilinear, bias 0 | 25,259 | 55.0 -> 33.7 (-39%) |
| **step 4: bias -0.5 (final)** | 22,521 | 60.1 -> 39.7 (-34%) |
| bias -1.0 | 18,509 | 67.8 -> 47.9 (-29%) |

Visual review of the crops (`build/texmips-ab/`): distant grass, sand, crowd
stands, snow and water lose their sparkle; tiled road shows no seams; fences
and trees keep their density at distance (DK Summit trees, Moo Moo Meadows
fences: the coverage-preserving alpha chains do their job); colours of large
areas are unchanged. The one visible loss: narrow painted features on
surfaces seen at a grazing angle soften (Luigi Circuit's orange curb at the
first bend turns into a brown blur beyond mid-distance with bias 0; bias -0.5
and -1.0 recover a little of it, `curb-bias.png`). That is the isotropic mip
selection of the hardware, not the filter. If a specific texture looks worse
on a console, `--exclude` keeps it at level 0.

## 5. Hardware test checklist

The emulator renders the chains but does not model texture-cache or VRAM/FCRAM
timing, so the frame-time effect can only be measured on a console. Prepare
the package on the Mac:

```sh
.venv/bin/python tools/build.py --external-assets --jobs 4
.venv/bin/python tools/prepare_o3ds_texmips_test.py   # -> build/o3ds-texmips-qa/
```

`build/o3ds-texmips-qa/` (private: the archive is game-derived) holds
`3ds/mkw-3ds/mkw-3ds-texmips-qa.3dsx`, `3ds/mkw-3ds/textures/o3ds/mips.pak`,
`3ds/mkw-3ds/performance.txt`, `autoplay/*.txt`, this checklist as
`HARDWARE-TEST.txt`, a manifest template and `receipt.json` (hashes).

On the Old 3DS (or 2DS; not a New 3DS: it never loads the chains):

1. The SD card must already hold the game files this archive was cooked from
   (`sdmc:/3ds/mkw-3ds/` with `menu.pak` / `race.pak` from the same Wii
   conversion). Each chain is bound to its original texture's CRC32; a
   different conversion just falls back (step 5 shows it).
2. Copy the package's `3ds` folder onto the SD root (merge). Make sure
   `sdmc:/3ds/mkw-3ds/` has no `full-quality.txt`, `o3ds-models-off.txt`,
   `o3ds-texmips-nearest.txt`, `o3ds-texmips-bias.txt` or `race-capture.txt`.
3. Pick a scripted scene: copy one of `autoplay/*.txt` to
   `sdmc:/3ds/mkw-3ds/menu-autoplay.txt` (Grumble Volcano and Toad's Factory
   have the most course texture bytes; DK Summit and Luigi Circuit are the
   familiar ones). The script picks VS, Mario, Standard Kart M and the course,
   CPU-drives 75 s of race (`auto:4500`) and quits; the same script gives the
   same race in both arms.
4. **Arm "off"**: create `sdmc:/3ds/mkw-3ds/o3ds-texmips-off.txt` (any
   content). Launch `mkw-3ds-texmips-qa` from the Homebrew Launcher, let the
   script finish (it returns to the menu; `menu-autoplay-result.json`
   appears), exit. Copy `performance.csv` to the Mac as `off-1.csv` and keep
   `runtime.log`.
5. **Arm "on"**: delete `o3ds-texmips-off.txt`, run again, copy
   `performance.csv` as `on-1.csv`. `runtime.log` must show
   `texture_mips=1 nearest=0 bias=-0.50`, `texture_mips loaded=N fallbacks=0`
   with N > 0, and `course textures in VRAM ... kept=0`. Fallbacks > 0 mean the
   SD's game files differ from the cooked ones; kept > 0 means some textures
   stayed in FCRAM (note `kept_bytes`).
6. Repeat 4-5 until there are at least 3 pairs (alternate the order: off, on,
   on, off, off, on), delete `performance.csv` between runs.
7. Optional arms, each against "on": `o3ds-texmips-nearest.txt` (nearest
   level instead of trilinear: is trilinear costing fill rate?) and
   `o3ds-texmips-bias.txt` containing `0` or `-1` (sharper vs cheaper).
8. Compare: fill `texmips-manifest-template.json` (one pair per run: CSV
   names and SHA-256, the same `assets_sha256` for both arms, `replay_sha256` =
   SHA-256 of the autoplay script used, `settings_sha256` of your SD settings)
   and run
   `python3 tools/compare_o3ds_performance.py --manifest texmips.json --output texmips-report.json`.
   It rejects mixed or mislabelled captures (wrong marker, no chains loaded,
   different assets) and reports sustained FPS, p95/p99 frame time and the
   gain per pair. For a nearest-level comparison set `"mip_filter": "nearest"`
   and use the nearest captures as the candidate.
9. Look while it plays (both arms), and note anything worse in "on":
   * distant road, grass, crowd stands and water: less crawling/sparkle;
   * thin road features (curbs, lane and start lines, arrows) at a grazing
     angle: still readable (they soften; with bias 0 they wash out sooner);
   * fences, trees and foliage far away: about the same density, not thinning
     or vanishing (DK Summit trees, Moo Moo Meadows fences, Maple Treeway);
   * tiled road and walls in the distance: no seams or colour shifts;
   * near and mid-distance surfaces, the bottom screen and the sky: unchanged.
10. Report: the compare tool's JSON, the `runtime.log` lines from step 5 and
    any visual notes. Keep the chains if FPS is equal or better and the
    visuals pass; if FPS regresses, check `kept_bytes` and try the nearest
    and bias arms before deciding.

## 6. Research passes after each step, and what is left

1. **After the ETC1 chains + VRAM residency** (step 1, 635 textures): the
   largest untouched group was ETC1A4 with real alpha, 541 textures, mostly
   alpha-tested (265, 5.4 MiB, 3.8 MiB of it on track models) and blended
   (209, 2.8 MiB). At 8 bpp they cost twice ETC1 per miss and box-filtered
   alpha would thin alpha-tested foliage, so the next lever was alpha-aware
   chains with coverage preservation (step 2, 501 textures).
2. **After step 2**, the first emulator crops showed the remaining visual cost:
   isotropic level selection softens thin painted features on surfaces seen
   at a grazing angle (Luigi Circuit's orange curb). Lanczos-2 levels (step 3)
   did not change that measurably (|lap| 32.7 box vs 32.2); a -0.5 LOD bias
   (step 4) keeps a little more of it and is the default; with no anisotropic
   filtering on the PICA200 this is the platform's usual trade-off and the
   hardware checklist asks for it explicitly (bias 0 / -1 markers).
3. **What is left**, by size:
   * Racer textures (drivers, karts, rival sets) stay in FCRAM: only course
     textures move to VRAM, and that happens before the opponents load. After
     the course chains, 2.1-2.8 MB of VRAM is still free above the 1 MiB
     reserve on every measured course; a second `moveToVram` pass after
     `opponents.load()` would place most racer textures in VRAM with no pixel
     change. Racer textures are also minified at distance (rivals), so they
     are the next mip candidates (same OMIP path; the 11 CPU racers are drawn
     small most of the time).
   * 52 secondary (light/environment) maps, 1.0 MiB, keep level 0: their UVs
     come from normals and change quickly across facets, where per-quad LOD can
     step visibly; worth a separate visual test before including them.
   * 31 alpha textures whose materials disagree (0.6 MiB): would need a
     per-material split; small.
   * Coverage-preserving chains assume the alpha test happens on the
     bilinear-filtered alpha (as on the PICA200); hardware review should
     confirm foliage density on a console.

## 7. Merge notes

* Branch `o3ds-texture-mips` = `517bdee` (snapshot of the main
  checkout's uncommitted work, 2026-10-16) + the texture commits after it.
* Runtime overlap with the engine branch (`src/renderer.cpp`, `src/main.cpp`,
  `include/mkw/renderer.hpp`, `include/mkw/runtime_profile.hpp`) is listed in
  section 2; everything else is new files.
* Data is not committed (game-derived): rebuild with section 3's commands.
  Emulator runs used `build/o3ds-texmips3/textures/o3ds/mips.pak`
  (sha256 in `docs/receipts/o3ds-course-textures-pack-summary.json`).
* Model A/B captures taken with `mips.pak` installed must add
  `o3ds-texmips-off.txt` (the compare tool now rejects captures in which mip
  chains loaded outside a `texture_mips` experiment).
* Release: nothing ships until `mips.pak` is staged (`stage_o3ds_lowpoly.py
  --texture-mips`, or copied to `textures/o3ds/`); the packagers
  (`package_sd.py`, `build_cia.py`) were not changed. Decide after the
  hardware A/B.
