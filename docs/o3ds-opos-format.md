# OPOS v1: preparatory modified-position sidecar

Status: host-only preparation. There are no runtime consumers, markers, asset
installation steps, or release approvals. Wii-derived model prototypes are the
only inputs. A structurally accepted sidecar does not establish visual quality,
animation quality, or a physical Old 3DS frame-rate improvement.

The existing O3RM contract remains unchanged: its mapped vertices are byte-exact
copies. OPOS is a separate format for modified positions and normals. Version 1
requires **unique original source IDs and byte-exact original UVs/colours**. UV
projection, appended texture atlases, material overrides, and duplicated source
IDs are unsupported and rejected. A writer may reorder unique vertices to sort
their source IDs, remapping indices without changing triangle/material order.

## File layout

All integers are unsigned 32-bit little-endian. The header is 64 bytes:

| Offset | Field |
| --- | --- |
| 0 | `OPOS` magic |
| 4 | Version 1 |
| 8 | Header size 64 |
| 12,16 | Full original GMDL byte size, CRC32 |
| 20,24 | Full original DSKM byte size, CRC32 |
| 28 | Candidate raw GMDL size |
| 32 | Candidate vertex count |
| 36,40 | Original primary clip index and sampled neutral frame |
| 44 | Protected triangle mapping count |
| 48,52 | Payload byte size, CRC32 |
| 56 | Flags 1: position/normal edits only |
| 60 | Reserved 0 |

The payload concatenates:

1. Raw candidate GMDL, without O3RM/trailing data.
2. One sorted, strictly increasing original source vertex ID per candidate vertex.
3. One 28-byte rebound DSKM `SourceVertex` per candidate vertex: local position 3f,
   local normal 3f, unchanged original matrix ID u32.
4. Protected mappings `(originalGlobalTriangle, candidateGlobalTriangle)` as
   pairs of u32, sorted by the original triangle ID.

The wrapper has no animation tail. Full original DSKM identity binds its clips,
PAT, hair, UV animation and root data. The writer uses the offline rebinder to
prove that a compact diagnostic bank would retain that tail byte-for-byte, then
stores only the rebound source records in OPOS. CRC32 detects corruption/stale
inputs; it is not a cryptographic signature. The receipt also records SHA-256.

## Validation and protected detail policy

`include/mkw/position_variant.hpp` is SDK-independent and reads fields without
unaligned native casts. It validates full identities and payload integrity,
bounded raw GMDL and DSKM layouts, material/texture identity, nonempty original
spans, counts/reduction, finite data, strict source IDs, original material and
shape membership, unchanged matrix IDs, and both original and rebound neutral
pose roundtrips against an actual sampled original palette. Position tolerance
is `max(1e-5, max(originalExtent,1)*1e-6)`; normalized normal error is at most 1e-4.
Selected neutral transforms must be invertible with bounded condition estimates.

Alpha-material triangles are always protected. The caller supplies independently
expected eye/cap triangle IDs and hard-locked source IDs in `opos::Policy`.
Protected triangles preserve oriented 44-byte vertex records and 28-byte local
bindings, allowing cyclic rotation and equivalent welded copies with identical
shape membership. Every hard-locked source vertex needs an unchanged equivalent
candidate witness. Declared cap mappings alone cannot establish which cap
triangles ought to be protected. A future loader must obtain an expected policy
from trusted authored conversion metadata or decline OPOS. It must not trust a
candidate's own list as the sole policy. The diagnostic receipt/text policy is a
review artifact; it does not by itself provide runtime authorization.

Untrusted lengths are bounded before checksum work or allocation: original
GMDL 10 MiB, DSKM 12 MiB, wrapper 16 MiB; vertices 65535, indices 3 million, driver
material spans 64, textures 512, matrices 128, shapes 64, primary clips 64 and
frames 2000 per clip. Scratch vectors are bounded by these validated dimensions.
The current host implementation uses `std::vector`; a production integration
must add an explicit allocation-failure strategy suitable for the runtime's
`-fno-exceptions` build. Bounded counts alone do not guarantee free heap space.

## Future atomic fallback contract

Missing, stale, invalid, unsupported or policy-failing OPOS must leave the
original model and bank usable. `opos::validate` returns views only after every
check passes and leaves its output unchanged on rejection. Inputs remain
read-only and must outlive the returned views. This is not a runtime commit API.

Any later `DriverAnimation::load` integration must:

1. Fully load and validate the original model/bank, retaining them until the
   complete candidate transaction has succeeded.
2. Validate OPOS plus the independent expected policy, prepare all required
   replacement storage, then commit model and rebound sources together.
3. Preserve **original authoredRanges and original model submesh first offsets**
   for PAT lookup. Compacted indexed GMDL offsets no longer identify original
   authored shape ranges. Compact only source-write ranges using lower bounds
   over sorted source IDs; retain original ranges separately.
4. Retain original clips/PAT/hair/UV/ROOT data. Never substitute a compact DSKM
   as though its compacted ranges were original authored PAT ranges.
5. Preserve existing Old 3DS experiment/release gates. Do not enable a candidate
   without visual/animation review and measured Old 3DS improvement. New 3DS
   full-quality behavior remains outside this format's scope.

The writer's animation proof checks finite/bounded integer sampled primary
frames with applicable secondary hair composition. Fractional transitions and
independent hair phases still require review. Structural validation cannot
replace textured animation inspection or real-hardware timing.

## Host workflow

```sh
.venv/bin/python tools/package_o3ds_positions.py \
  --candidate build/example/candidate.gmdl \
  --output build/example/package/candidate.opos
c++ -std=c++17 -Wall -Wextra -Werror -pedantic -Iinclude \
  tools/validate_position_variant.cpp -o build/validate_position_variant
build/validate_position_variant original.gmdl original.dskm \
  build/example/package/candidate.opos build/example/package/candidate.policy.txt
.venv/bin/python -m unittest discover -s tests -p 'test_o3ds_position*.py' -v
```

The writer emits `.opos`, canonically ordered raw `.gmdl`, reordered metadata
`.json`, `.receipt.json`, and `.policy.txt` beside the selected output. It does
not overwrite source inputs or install any of these files. Receipt and harness
responses always report `runtimeReady:false` and `visualApproval:false`.
