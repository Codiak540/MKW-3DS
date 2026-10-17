# Old 3DS draw pipeline: GPU frame pipelining and course display lists

Branch `o3ds-draw-pipeline` (worktree), on top of `d6313d8`
(`o3ds-performance`). Two stages that take the CPU cost of drawing off the Old
3DS frame's critical path. Pixel output and physics are unchanged: every
change below was A/B'd in the worktree's Azahar copy (Old 3DS mode) with the
physics replay test, the 12-racer CPU log and lockstep captures.
Emulated time is instruction counts, not hardware time: the emulator's GPU
finishes instantly, so stage 1 cannot show its effect there; the hardware
checklist at the end is the measurement that decides it.

QA markers (QA builds; release builds ignore markers):

| Marker | Effect |
| --- | --- |
| `o3ds-pipeline-off.txt` | stage 1 off: one command buffer, `C3D_FrameBegin` waits for the previous frame |
| `o3ds-displaylists-off.txt` | stage 2 off: the course and sky draw through `drawNow` every frame |
| `o3ds-main-only.txt` | pins the main-core layout, so the pipeline is on for the whole race (the countdown calibration may otherwise choose the render-thread layout, which never pipelines) |
| `o3ds-displaylists-diag.txt` (content 1-5) | stage 2 diagnostics for A/Bs: 1 replay without skipping the materials' CPU-side state calls, 2 re-upload every uniform after a replay, 3 draw every block, 4 sky drawn directly, 5 course drawn directly |

## What was found

- citro3d keeps one command buffer per context (`C3D_Init(0x100000)`) and
  `C3D_FrameBegin` calls `C3Di_WaitAndClearQueue(-1)`: the CPU cannot start
  recording frame N+1 until the GPU has finished every queued command of
  frame N. The profiler records that wait as `render_begin_wait_ms` and the
  previous frame's GPU time as `previous_gpu_queue_ms` (both meaningless in
  the emulator). On Old 3DS the race loop waits for the top-screen refresh,
  simulates the ticks owed and draws; a frame that takes over 33.3 ms owes
  three ticks the next time round (a 50 ms slot, 20 fps). Nothing lies
  between 30 and 20.
- libctru runs one GX command queue at a time (`gxqueue.c`: a single
  `curQueue`/`isRunning`), so two in-flight frames cannot each own a running
  queue; a finished queue's completion callback has to start the next one.
- citro3d chooses the LCD framebuffer a frame transfers into at `FrameEnd`
  (`gfxGetFramebuffer`, the back buffer of libctru's pair). With two frames in
  flight the previous frame's transfer may still be queued when the next
  frame's destination is chosen, so pipelined frames need a third buffer per
  screen and their own presentation.
- A race frame uses about 4% of the 1 MB command buffer (`command_usage`,
  max 8%), and ~113 draws per frame, two thirds of them the course's 128-
  triangle blocks, are rebuilt by `drawNow` every frame.

## Stage 1: frame pipelining

### citro3d (`patches/citro3d-hardware.patch`, `renderqueue.c`)

Two *slots*, each a command buffer and a GX queue: slot 0 is the context's
own pair, slot 1 is allocated by `C3D_PipelineInit(size)`. With
`C3D_PipelineEnable(true)`:

- `C3D_FrameBegin` alternates slots and waits (a per-slot one-shot
  `LightEvent`) for the slot's previous frame, i.e. frame N-1, not frame N.
  Its queue is cleared and bound; `ctx->cmdBuf` points at the slot's buffer.
- `C3D_FrameEnd` queues the frame's transfers (destination from the
  application's `framebuffer` hook, see below) and *submits* the slot: if no
  slot is running it starts the queue, otherwise the running slot's
  completion callback starts it (`C3Di_PipeFinish`, GX completion thread,
  under `pipeLock`). The callback also presents the finished frame through
  the `present` hook (or `gfxScreenSwapBuffers` without hooks), stops the
  drained queue so libctru forgets it, and signals the slot's event.
- Everything that used to wait for "the queue" waits for both slots
  (`C3Di_WaitAndClearQueue` → `C3Di_PipeWaitAll`): sync transfers outside
  frames (`captureTop`, VRAM moves), target deletion, the APT suspend hook,
  and the command-space continuation (which runs the recording slot's queue
  without presenting). `C3D_PipelineWaitIdle()` is the explicit version.
- `C3D_GetDrawingTime()` returns the GPU time of the most recently finished
  frame (measured from the moment its queue started, so it excludes time
  spent queued behind the previous frame).
- An empty frame (nothing drawn, no transfer) completes immediately. The
  unpipelined path is unchanged, so New 3DS, menus and the render-thread
  layout behave exactly as before.

### Renderer (`src/renderer.cpp`, `render_thread.cpp`, `renderer_retail.cpp`)

`Renderer::setPipelined(on)` (Old 3DS, immediate race frames only; menus
and deferred frames never pipeline, `setDeferred(true)` turns it off):

- **LCD framebuffers**: three per screen, libctru's pair plus one of ours
  (LINEAR, like `gfxInitDefault`'s), rotated per frame that transfers to that
  screen. `lcdFramebufferHook` picks the destination at `FrameEnd`;
  `lcdPresentHook` presents from the completion thread with
  `gspPresentBuffer`, the same mode word libctru builds (2D/3D top mode
  recorded per frame, since `gfxSet3D` may already have moved on). Leaving
  the mode re-presents the shown buffer through gfx when gfx's back buffer
  is on screen, so the next unpipelined frame never draws into the displayed
  buffer. Enabling waits one VBlank after the last unpipelined frame; so
  does disabling.
  Why three: with two frames in flight the previous frame's transfer may be
  queued when the next destination is chosen; with three the destination is
  neither shown nor pending unless two consecutive GPU frames finish within
  one refresh, which a race frame cannot.
- **Per-frame scratch**: two sets of the overlay, sprite, text (with the
  bottom screen's quads), spark, NW4R effect vertex buffers, the effect
  colour-lookup texture and bloom's quad vertices (`FrameScratch`), and two
  of the retail light-texture pairs and fog ramps. `pipelineBeginFrame`
  selects the set of frame parity: frame N-1's set is free once `FrameBegin`
  has waited for it.
- **Models written per frame** (`Model::beginWrite`): CPU skinning
  (`DriverAnimation::write`, `ObjectBank::skin`), the GPU-skinned path's
  shape hiding and face-UV writes, and Coconut Mall's escalator treads write
  the model's *other* buffer (allocated on first use as a copy, so colours,
  UV1 and untouched positions stay valid; a full rewrite follows a switch on
  the GPU-skinned path). The switch happens once per pipelined frame per
  model, keyed on `pipelineWriteFrame`; the buffer last drawn in frame N is
  never written in frame N+1. If the copy cannot be allocated the writer
  writes in place, `pipelineWriteMisses` counts it and the renderer leaves
  pipelining at the end of that frame (`runtime.log` "write_misses").
- Resources `retire()`d during a pipelined frame are released two frames
  later. `waitForGpu()` waits for both slots. Stereo frames are recorded and
  drawn locally as before (the slot switch happens in `beginRecorded`).

`performance.csv` gains `gpu_pipeline` (0/1). `runtime.log` logs
`gpu pipeline on|off linear_free=...` at the race start and the state at the
countdown calibration's decision.

### Memory (Luigi Circuit, `linear_free` during the race)

| | bytes free | cost |
| --- | --- | --- |
| baseline | 9,095,680 | |
| pipeline on (race start) | 6,119,680 | second 1 MB command buffer, second scratch set (1.13 MB), extra LCD buffers (576,000 + 230,400) |
| pipeline on (during the race, min) | 5,725,184 | model second buffers as they are first written |

The second command buffer is the same 1 MB as the first (a race frame uses
≤ 8%; `C3D_PipelineInit`'s size is the knob).

## Stage 2: course display lists

`Renderer::drawStatic(list, model, transform, backdrop)` replaces
`renderer.draw` for the course parts and sky models in the race loop.

- **Recording** (the first frame that draws the model, and again whenever
  the retail fog state changes): the frame-level state (framebuffer,
  viewport, scissor, program, shader constants: `C3D_UpdateContextFrameState`)
  and every pending uniform (`C3D_UpdateUniforms`) go to the frame's own
  list first, so the list's first draw uploads exactly what `drawNow`
  itself dirties, whichever objects drew before it (without this flush the
  replay's upload pattern depended on the frame and re-recorded almost every
  frame); then `GPUCMD_SetBuffer` points citro3d at
  the list's LINEAR memory, every register shadow is forgotten and every
  material state group marked dirty (`C3D_InvalidateGpuState`), so the
  list's first material emits its whole state and the list never depends on
  the GPU state the frame happens to be in when it is replayed (without
  this, state equal to the preceding object's at recording time, such as
  `white_` already bound on unit 1, was left out and 7 of 52 Luigi Circuit
  captures differed wherever a different object preceded the list), and the
  ordinary `drawNow` records the model with
  every culling block visible, one `C3D_DrawElements` per block (one per
  submesh for the sky), while citro3d (a) uploads every dirty float uniform
  regardless of its register shadow and logs where each run's data landed
  (`C3D_UniformRecordBegin`), and (b) the renderer notes each draw's trigger
  word, each submesh's upload runs and its bool-uniform state. The list ends
  with `GPUCMD_Split`'s finalize (`C3D_RecordListEnd`); neither the seam
  before a list nor its end flushes or invalidates the framebuffer (the list
  keeps drawing into the frame's target; target changes still flush through
  the frame-state path). After recording, citro3d's CPU-side state
  is exactly the GPU's (the real calls ran), so no resync is needed.
- **Replaying**: `drawNow` runs in replay mode: every material's TEV, texture,
  blend, depth, cull and attribute calls are skipped (the list has them); the
  uniform calls run as before (projection, model-view, UV scroll, the retail
  light uniforms and environment rows, bools). At each submesh's first draw
  the dirty uniform runs are consumed (`C3D_UniformsConsume`: shadows take
  the values, nothing is emitted) and must equal the recorded runs; their
  values are patched into the list (`C3D_UniformPatchRun`), and the draws
  are patched to exactly the merged ranges `drawNow` would issue
  (`selectVisible`, then `VisibleRanges`' merging): each range's first
  block gets the range's index count in its `GPUREG_NUMVERTICES` word and
  a live trigger (`GPUREG_DRAWELEMENTS` header), every other block's trigger
  becomes a mask-0 write of register 0x0000, which touches no byte. The
  recording frame is patched the same way before it is queued.
  Then `C3D_InvalidateGpuState()` marks every state group a list may carry
  dirty and forgets the register shadows, the frame's list so far is split
  off, and the list is queued as its own GX command list
  (`GX_ProcessCommandList`, `GX_CMDLIST_FLUSH`, so the GSP flushes its cache).
  Any mismatch (a run or bool state the recording did not have) re-records
  the list that frame after re-dirtying every uniform; a material the list
  cannot carry (a light-texture material whose fog constant follows the eye
  depth) marks the list unsupported and the model draws directly from then on.
- One copy per frame slot (`DisplayList::Copy`): while frames pipeline, the
  GPU may still read the previous frame's copy, so each frame patches its
  own. The copies are owned with the course models (`trackLists`,
  `skyLists` in `main.cpp`) and freed with them, always after `waitForGpu`.
- Culled submeshes still execute their state commands on the GPU (and the
  disabled draws' 14 harmless register writes); the draws that run are
  `drawNow`'s own merged ranges, so the GPU does the same work as without
  the list.
- Stereo frames, deferred (render-thread) frames and frames short of GX
  queue entries draw as before (`fallbacks` in the log).

Why GX command lists rather than `GPUREG_CMDBUF_JUMP` chains: a jump is a
goto without return, so a chain's last segment would have to jump back into
the frame's buffer with the continuation's size written in advance, which is
unknown while the frame is still being recorded (an oversized size has the
GPU fetch past the real end after FINALIZE, as libctru's own lists rely on
exact sizes). Queuing the list between two halves of the frame's list uses
only documented GX behaviour, costs one P3D round trip per list (bloom
already splits the frame the same way), and per frame the CPU writes only
the patched uniform words and trigger headers.

`runtime.log` at each race release: `display lists records=.. replays=..
mismatches=.. fallbacks=.. unsupported=.. bytes=..`. `performance.csv`
gains `list_record_ms/_calls` and `list_replay_ms/_calls`; `draws` counts
the same merged-range draws as before.

## Verification (emulator, Luigi Circuit, QA build, Old 3DS mode)

All runs: the worktree's Azahar copy in Old 3DS mode, the same QA 3DSX with
and without markers, `tools/o3ds_perf_verify.py` / `tools/o3ds_perf_run.py`
(main-only layout pinned so the pipeline is on for the whole race), compared
with `tools/pvs/compare_runs.py`. The baseline is the merged tree at
`d6313d8` built in this worktree; a baseline-vs-baseline run is 52/52 identical.

| Build | Replay test | CPU racer log | Captures vs baseline | Captures vs the same build with the stage's marker |
| --- | --- | --- | --- | --- |
| Stage 1 (pipeline on) | pass, heap 7029484/7029484 | 920/920 identical | 52/52 | 52/52 (`o3ds-pipeline-off.txt`), perf captures 12/12 |
| Stage 1 + 2 (pipeline + lists) | pass, heap identical | 920/920 identical | 52/52 Luigi Circuit, 52/52 Coconut Mall | 52/52 (`o3ds-displaylists-off.txt`), perf captures 12/12 |
| Stage 1 + 2, Koopa Cape (pose LOD off on both sides) | | identical | 52/52 (47/52 before the merged-range draws, see below) | |

Replay test with both stages: pass, heap identical, pipeline on (the QA console on the lower screen turns libctru's double buffering off there, so that screen keeps gfx's own buffer and swap), lists: 4 recordings (two lists, two slots), 18,124 replays, 0 mismatches.

Two defects found and fixed on the way (both caught by the captures): the
replay's upload pattern depended on what preceded the list (now every
pending uniform is flushed before a list, see Stage 2), and a recorded list
omitted state equal to the preceding draw's at recording time (now the
recording starts from invalidated shadows). Two further changes were made
while chasing the Koopa Cape residual below and kept because they are right
on their own: the invalidation no longer marks the lighting-enable
registers dirty (the application never writes them; the lists wrote them),
and lists are split off and ended without `C3D_FrameSplit`'s framebuffer
flush and invalidate (the list keeps drawing into the frame's target).

A third defect showed only on Koopa Cape (five captures with 1 to 30
pixels one to six units off, in fogged sky-coloured pixels at the far
horizon): the list issued one GPU draw per 128-triangle block where
`drawNow` issues one per merged range. Same triangles in the same order,
but the emulator renders the split draws with one-unit differences at far
depth ties (it never showed on Luigi Circuit or Coconut Mall). The list
now issues exactly `drawNow`'s draws: each range's first block is patched
to the whole range's index count and the range's other blocks are disabled,
in recording and replaying frames alike. Isolation on the way there (stage
1 alone and the sky list alone were identical; re-recording every frame,
pose LOD off, no seam flushes and no lighting-enable writes changed nothing)
is what pointed at the draw splitting.

Final build (merged-range draws): Luigi Circuit replay pass with identical
heap, CPU log 920/920 identical, 52/52 captures against the baseline and
against the lists-off run, perf captures 12/12, pipeline on in every run
(lists: 4 recordings, 18,124 replays, 0 mismatches); Koopa Cape 52/52 with
the CPU log identical (pose LOD off on both sides); Coconut Mall 52/52 with
the CPU log identical.

Emulated main-core time per race frame, Luigi Circuit, 1137 race frames
(`rendered=1`, two ticks), the same main-only layout:

| | lists off | lists on | |
| --- | --- | --- | --- |
| `course_ms` (sky + course draw on the main core) | 0.765 | 0.556 | -27% |
| `draw_elements_ms` (every draw's state flush and draw commands) | 0.819 | 0.588 | -28% |
| `draw_elements_calls` | 68.5 | 44.1 | the course's draws no longer go through it |
| `list_replay_ms` | | 0.39 | culling, uniforms, patches for the two lists |
| `draws` | 113.6 | 113.6 | the same draws as drawNow's merged ranges |
| `triangles` | 13,422 | 13,422 | the same geometry |
| `interval_ms` | 18.44 | 18.51 | the emulator is simulation-bound |

The pipeline itself cannot show a saving in the emulator (`render_begin_wait_ms`
is ~0 there with or without it; the emulated GPU finishes instantly).
Display lists: records=2 per list copy (Luigi Circuit: 60,800 bytes for the
sky and course lists), replays every frame after that, 0 mismatches, 0
unsupported materials on Luigi Circuit, Koopa Cape and Coconut Mall.

Memory with both stages, Luigi Circuit: `linear_free` 5.6 MB at its lowest
during the race (baseline 9.1 MB).


## How to confirm on a real Old 3DS

1. Build the QA 3DSX (`tools/build.py --external-assets`), put
   `performance.txt` and `o3ds-main-only.txt` in `sdmc:/3ds/mkw-3ds/`, play
   or autoplay a race; repeat with `o3ds-pipeline-off.txt` added, then with
   `o3ds-displaylists-off.txt` added instead. `runtime.log` confirms the
   state (`gpu pipeline on|off`, `display lists records=..`).
2. `performance.csv`, race frames (`rendered=1`, `physics_ticks=2`):
   - `interval_ms`: 33.3 = 30 fps. Frames at 50 are the 20 fps slot.
   - `render_begin_wait_ms`: with the pipeline off, the wait for the previous
     frame's GPU work (the cost stage 1 removes); on, the wait for the frame
     before it (should be ~0).
   - `previous_gpu_queue_ms`: the GPU's time per frame. This is the number
     that sizes stage 1: if it is under the sim+record time of a frame the
     pipeline gains nothing, if it is near 33 ms the frame is GPU-bound and
     the display lists' extra command processing matters.
   - `course_ms`, `draw_elements_ms`, `list_replay_ms`: the CPU cost of the
     course draw, before and after stage 2.
   - `linear_free`: must stay well above 0 on the largest courses (Coconut
     Mall, Bowser's Castle) with both stages on.
3. Watch for tearing at the top of the screen during races (the three-buffer
   presentation) and after returning to the menu (the gfx hand-back), and for
   any visual difference between the marker runs. `tools/compare_o3ds_performance.py`
   compares paired hardware captures.
4. If a course shows `unsupported=1` or `mismatches>0` in `runtime.log`,
   report the course: its materials need a look.
