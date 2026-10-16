# Old 3DS performance experiments

Research snapshot: 15 October 2026. This is an experiment plan backed by source and binary inspection, not a hardware performance result. No real Old 3DS capture has established a 5–10 FPS improvement. New 3DS keeps its existing assets and behavior.

## What is already implemented

The existing Old 3DS path already makes substantial presentation compromises. `include/mkw/render_quality.hpp` disables bloom and stencil shadow volumes, limits particles to 16, uses reduced kart effects, selects authored course-object low LOD beyond 150 world units, and skips sufficiently small parts. Some crowd/item animation is sampled every five simulation ticks. Do not count these existing savings as a new model-decimation improvement.

`src/main.cpp` and `include/mkw/presentation_cadence.hpp` keep simulation/input at 60 Hz while presenting Old 3DS races every two source ticks, approximately 30 FPS. Missed presentation slots coalesce. New 3DS uses one tick in 2D and two in stereo. An extra frame-skip option could stabilize a slower target but cannot itself satisfy “5 FPS more”; it reduces presentation frequency. Preserve simulation cadence, item behavior, collision and AI.

`src/racer_visuals.cpp` already rejects an opponent's entire visual before posing when its generous envelope is outside the view in reduced quality. It also has a 25,000-unit distance cutoff and individual body/driver/wheel/accessory visibility checks. Animation state still advances, permitting correct re-entry. The new distance experiment reduces the whole-racer threshold independently; it must leave race simulation, ranking, collision, item targeting and audio intact. Culling only after `apply()` would miss much of the potential CPU saving.

## Order of experiments

| Experiment | Possible benefit | Required evidence before promotion |
| --- | --- | --- |
| Original geometry versus cooked Old 3DS variants | Less submitted triangle/vertex work; aggressive variants also compact source vertices and animation bindings | At least three matched physical-console captures per scene; triangle and CPU/GPU skin counts; deformation/material/silhouette review |
| Lower color-texture resolution | Less texture allocation and potential bandwidth/cache pressure | Independent texture-only A/B; character faces and scene readability; no assumption that smaller assets raise FPS |
| Whole distant opponent visual culling | Fewer pose evaluations, transforms, skin writes and draw submissions | Independent distance-only A/B with identical assets; positive cull-counter delta; no unacceptable pop-in or missing nearby rivals |
| Palette compaction and additional GPU skinning | Move remaining per-vertex work from ARM11 to PICA | CPU-skinned model census, fallback reasons, skin CPU time, GPU time and batch-count change |
| Squared projected-size tests / invariant hoisting | Reduce repeated bounds arithmetic without changing content | Visibility boundary equivalence, finite/degenerate transform cases, camera/stereo captures and measured CPU-stage improvement |
| Lower scene resolution | Reduce rasterization, texture and framebuffer work | A GPU/fill bottleneck; compare net time including composite/clear and inspect 200×120 scene readability |
| More presentation skipping | More processing time between visible frames | Separate pacing objective; never report skipped frames as an FPS gain |
| Math/library changes in measured hot functions | Remove confirmed redundant conversions, divides or transcendentals | Disassembly, current timing evidence and appropriate replay/presentation fidelity checks |

Keep the model and distance experiments isolated first. A combined setting can be benchmarked later, but its total gain cannot establish that decimation itself met the user's threshold. The comparison tool rejects distance culling during a model-only experiment, even if both captures enable it.

## Separate texture experiment

`tools/cook_o3ds_textures.py` creates private `textures/o3ds/*.otex` sidecars
from verified Wii PNG/T3X pairs. It halves eligible primary color textures,
caps them at 128 pixels, and preserves the original ETC format, alpha precision
and mip policy. Textures already at 32 pixels or smaller, ordinary UI/HUD and
secondary light/environment maps remain unchanged. Model textures used in
character-selection menus are eligible. No MK7 or other replacement texture
pixels are used.

The first full library cook produced 4,127 candidates, with aggregate GPU
texture allocation falling from 76,184,064 to 19,046,400 bytes. These totals
include textures never resident together; they are neither concurrent VRAM
usage nor an FPS result. All candidates pass the native format validator.

Enable only in a QA build on confirmed Old 3DS with
`o3ds-textures-test.txt`. Runtime validates original and candidate checksums,
dimensions and format, falling back to the original on failure. New 3DS,
unknown hardware, full-quality mode and release builds keep original textures.
`prepare_o3ds_test.py --with-texture-candidates` stages this experiment disabled.
Leave its marker absent for geometry-only measurements.

## Resolution reduction is not a free switch

The normal top target is created as rotated `240×400` RGBA8 plus depth/stencil in `src/renderer.cpp`; the bottom target is `240×320`. The current race letterbox uses 320×240 of the top display. `GX_TRANSFER_SCALE_*` supports downscaling, not enlarging a small scene to the LCD. Therefore changing target dimensions alone is insufficient. [3dbrew transfer-engine research](https://www.3dbrew.org/wiki/GPU/External_Registers)

A concrete first prototype would allocate a `128×256` RGBA8 texture and depth target (256 KiB combined), render into a rotated `120×200` viewport, and draw an upscale quad into the native target. That represents a 200×120 scene, with the existing race letterbox leaving 160×120 effective scene pixels: one quarter of today's rasterized scene area. Use a proportionally scaled scissor `(0,20,120,180)`. Composite at the existing `applyBloom` boundary before native-resolution HUD; handle the `endEye`/`beginBottom` fallback paths. Copy intent into the deferred `BeginCommand`, reserve before course textures occupy VRAM, and fall back to native rendering on allocation failure.

This would retain the native target and display transfer, vertex work, much CPU work, and add a full-resolution quad plus clear. It can regress a CPU/vertex-bound scene. Menus, HUD and touch-map readability should remain native in the first controlled experiment. Compare both filters visually before selecting one. The proposed prototype remains unimplemented pending bottleneck evidence.

## Using spare GPU time

The useful destination for additional GPU work is presentation geometry already headed to the GPU. PICA's documented programmable stages are vertex/geometry processing; its shader interface is not a general compute API for arbitrary engine state. In particular, relative addressing accesses a small constant-register space, with valid float constants ending at index 95. [3dbrew shader ISA research](https://3dbrew.org/wiki/Shader_Instruction_Set)

That makes GPU skinning and vertex transforms plausible, while moving collision, AI, gameplay timers or simulation to the GPU would require a different algorithm and potentially synchronization/readback. The extra dependency is an engineering risk rather than a measured benefit. Keep authoritative Wii simulation on the CPU.

The project already does the main offload:

- `RacerVisuals::load()` and player loading call `DriverAnimation::prepareLitSkinning()`.
- `src/mkw.v.pica` allocates `bones[57]`: 19 affine matrices, matching `Renderer::MaxSkinBones=19`. It transforms positions and lit normals, including nonuniform-scale handling, without another normal-matrix palette.
- `DriverAnimation::gpuSkin()` falls back when its bank is unready, the vertex count differs, the palette exceeds 19 matrices, or the extra lit bone-index buffer cannot allocate. Merely calling it again will not solve those cases.
- Course objects first try rigid per-part drawing. Weighted unlit objects can use a shared GPU-skinned mesh within the palette limit; weighted lit objects currently take CPU skinning. Menu rigid partitions already avoid CPU transforming each vertex when possible.

First inventory real cooked banks: total matrices, matrices actually referenced by drawn vertices, CPU/GPU path, vertex count, pose evaluation cost and buffer flush bytes. Compaction can map referenced matrices into fewer GPU slots without changing deformation. For more than 19 required matrices, partition triangles into material-preserving batches with local palettes and remap their matrix indices. This trades CPU skinning for extra palette uploads/draw calls and possibly duplicate vertices; it requires visibility, UV/face animation, rigid part and skin-sidecar consistency checks. Do not simply increase the shader constant array beyond available uniforms.

Lit weighted course-object skinning is a separate experiment: reuse the lit attribute/normal path, verify authored nonuniform scales and singular poses, then compare CPU savings against shader work. Reducing scene pixels creates raster headroom, but may not create spare vertex-shader capacity. Measure both instead of assuming one interchangeable GPU budget.

At the initial audit, `skinned_vertices` counted CPU and GPU paths together (`DriverAnimation::apply` and `CourseObjects`). New `cpu_skinned_vertices` and `gpu_skin_vertex_references` instrumentation makes this distinction visible. The comparator reports those columns when available; total skin work is not evidence of CPU cost by itself. GPU index references are a submission count, not a count of unique vertex-shader executions after hardware reuse.

The read-only `tools/audit_skinning.py --assets build/playable/3ds/mkw-3ds
--output build/skin-census.json` inventories final model/bank pairs, checks
DSKM2–5 and DOBJ1 layouts, and reports total versus referenced palettes,
rigidity and conditional runtime paths. A static opportunity does not prove
that a model is visible or expensive in a captured scene; correlate it with
the CPU/GPU skin counters before implementing offload.

The first real course census exposed valid 24,000-frame SRT water animations
that exceeded the loader's old 20,000-frame limit. SRT loading now validates
sampled payload size with checked arithmetic and an exact-float period bound,
accepting the authored 1,501 samples without relaxing CHR/PAT/CLR limits.
A host sanitizer regression covers this case and malformed sizes. This is
an asset compatibility fix, not an optimization or measured FPS improvement.

The completed package census (`build/skin-census.json`) contains 849 valid
model/bank pairs and zero structural errors. It identifies 54 weighted lit
course-object pairs taking CPU skinning, of which 53 fit the existing
19-matrix shader palette. Five weighted unlit object pairs exceed that limit;
none of the oversized palettes can be reduced to 19 simply by dropping
unreferenced matrix slots. Partitioning would be a separate change.

The full racer overlay census contains 800 pairs (400 distinct model/bank
hash pairs after aliases), all already within 19 matrices. Standard Mario's
driver uses 15; player and rival code already prepares its GPU path. His
Standard Kart body is static. Remaining DSKM palette-limit cases in the
finished package are menu models, whose caller paths need separate analysis.
The lit weighted course-object offload is now implemented as a separate QA
experiment. Create `o3ds-object-gpu-test.txt` and restart on Old 3DS to enable
it. Models exceeding 19 matrices or failing allocation retain their CPU path.
The shader preserves the DOBJ CPU normal transform (normalized `B*n`), while
racer DSKM keeps its existing inverse-transpose treatment. Nonuniform and
singular affine cases have host contract tests; actual PICA output still needs
visual/hardware validation. New 3DS, unknown hardware and release builds keep
the previous path. Leave this marker absent for geometry-only measurements.

The CSV records `object_gpu_enabled`, converted model/fallback counts and
actual object GPU vertex references. The comparator's `object_gpu_skin`
experiment requires original model profiles and identical assets in both
runs, actual GPU submissions, and reduced CPU skin work. No hardware FPS gain
has been measured for this change.

## ARM11 findings, verified locally

The linked binary inspected during this research had SHA256 `05d79b5435c8484e763f50f3eb86c39de58987e93cba0565c16b3b2f3a5b0a2c`. Source edits and later builds invalidate these static counts. The compiler was devkitARM GCC 16.1.0. `tools/build.py` uses ARMv6K, MPCore tuning, hard-float linkage, software TLS-pointer access, `-O3`, `-ffp-contract=off` and Kinoko LTO. Compiler macros reported `__ARM_FP=12`; the application `animation.o` declared ARMv6K/VFPv2 with VFP argument registers. The merged ELF attributes also contained ARMv7/Thumb-2 from linked objects; use application-object attributes and actual call paths rather than that merged label alone to infer app instruction selection.

GCC documents that `-mtp=soft` concerns the thread-pointer access mechanism, while `-mfloat-abi=hard` controls floating-point calling convention/code generation. These switches solve different problems. `-ffast-math` permits numerical transformations that can invalidate exact floating-point assumptions; `-fno-math-errno` is a narrower setting affecting math-library errno semantics, not the switch enabling hardware division. [GCC ARM options](https://gcc.gnu.org/onlinedocs/gcc/ARM-Options.html), [GCC optimization options](https://gcc.gnu.org/onlinedocs/gcc/Optimize-Options.html)

ARM's manual documents hardware single and double precision, including divide and square root. Transfer, conversion and comparison latencies differ; there is no universal 20-cycle “float crossing” penalty. The VFP table distinguishes issue throughput from latency and warns that dependencies and memory behavior matter. FZ/DN change numerical semantics and should not be toggled casually. [ARM11 MPCore TRM, chapters 16–20, especially table 19-16](https://documentation-service.arm.com/static/5e8e1cd9fd977155116a4a7a)

Concrete observations from this binary:

- `DriverAnimation::write()` already emitted `vsqrt.f32`/`vdiv.f32` in its normalization path, with no math-library calls in that function. `ObjectBank::skin()` also used hardware sqrt/divide. A blanket libm replacement would not optimize those loops.
- `RacerVisuals::Impl::prepareRacer()` contained 25 static `vsqrt.f32`, 6 `vdiv.f32` and 155 VFP comparison sites after inlining, plus one `sinf` call site. These are static instruction-site counts, not execution counts or measured time.
- Whole-binary direct call-site counts included 85 `sinf`, 67 `cosf`, 21 `floorf`, 16 `atan2f`, 177 `__aeabi_uidivmod`, 74 `__udivsi3` and 49 `__divsi3`. Many are cold, libraries or correctness-sensitive simulation. They form a screening list only.
- The linked `initThreadVars` wrote FPSCR `0x03000000` (FZ+DN); the project main selects nearest rounding/FZ and the job/render threads copy its state. Adding another FZ setter is not a demonstrated new optimization. Upstream `threadCreate` initializes thread variables before entering user code. [libctru thread implementation](https://raw.githubusercontent.com/devkitPro/libctru/master/libctru/source/thread.c)

The strongest source-level math candidate is `RenderQuality::screenRatio()`: repeated per-part calls calculate three axis lengths, bounding radius, camera distance and division. Hoist known vehicle scale and compare squared quantities where the algebra and rounding boundary are justified. Keep conservative padding around visibility thresholds so a rounding difference cannot hide a significant object. A source grep alone cannot establish a gain. Never replace Kinoko's Wii arithmetic, reciprocal-square-root behavior or collision predicates purely to reduce a call count; replay fidelity is the oracle.

## External reference reviewed

[T2K](https://github.com/colbyshores/t2k)'s ARM11 notes were read as reference material. Their useful transferable approach is to identify current hot functions, inspect emitted ARM/VFP instructions, record hardware/clock/cache/stereo settings, and preserve gameplay-sensitive arithmetic. It distinguishes hardware capabilities from its own stricter project policies, including restrictions on conversions, control flow and library math. Its historical optimizations and measured gains belong to T2K and do not certify MKW-3DS. We have independently checked the relevant compiler, VFP and thread-initialization claims above; its naming, language and release rules are not adopted.

## Capture protocol and decision

Use `python3 tools/compare_o3ds_performance.py --template` for the manifest format. Three distinct matched pairs per scene, five seconds warmup and at least sixty measured seconds per capture are required. Set `experiment` to `model_decimation` (default), or `racer_distance` with a `candidate_distance` between 1,000 and 25,000. Distance-only pairs use original model profiles and identical asset hashes; the candidate must actually cull racers in the measured window. Model-only pairs must submit reduced geometry and lower mean submitted triangles.

Record the same executable, console, replay, race settings, stereo state, quality state and 30 FPS cap. Attest physical Old 3DS explicitly; emulator, synthetic and unknown captures remain ineligible. Bind each CSV to a SHA256 and record binary/replay/settings/asset identities. Compute elapsed time from all rows, including `rendered=0`; derive consecutive rendered intervals by accumulating intervening skipped rows. GPU/render-thread samples may lag, and threaded CPU times overlap, so do not sum them into frame latency.

Every designated demanding pair must gain at least 5 sustained FPS, no pair may lose sustained FPS, and p95/p99 must not regress beyond the tool's 5% allowance. A 30 FPS cap means a baseline already above 25 FPS cannot clear a +5 FPS requirement there. Choose genuinely slow representative scenes before running the comparison. Passing the numerical screen means only **eligible for visual review**; it does not activate any model, distance, resolution or engine change.
