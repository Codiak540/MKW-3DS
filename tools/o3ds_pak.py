#!/usr/bin/env python3
"""Old 3DS overlay archive (o3ds.pak), the MPK1 layout of menu.pak/race.pak.

The overlay is thousands of small sidecars (models/o3ds, models/o3ds/rival,
textures/o3ds re-baked textures). Loose on a FAT32 SD card, every model a race
loads cost extra file opens in folders of thousands of entries, which roughly
doubled race loading. Packed, each lookup is a binary search in memory and one
seek (src/asset_pack.cpp opens o3ds.pak as its third archive). mips.pak and
the course visibility files stay loose: each is opened once per session or
twice per course.
"""
import struct
from pathlib import Path

ALIGN = 32


def packed(rel):
    """True for overlay files that belong in o3ds.pak."""
    return (rel.startswith('models/o3ds/') and rel.endswith('.gmdl')) or \
        (rel.startswith('textures/o3ds/') and rel.endswith('.t3x'))


def write(files, output):
    """files: {archive path: source Path}. Writes output; returns (count, bytes)."""
    names = sorted(files)                      # byte order = strcmp order (ASCII paths)
    blob = b''.join(n.encode() + b'\0' for n in names)
    header = 12 + 12 * len(names) + len(blob)
    offset = (header + ALIGN - 1) & ~(ALIGN - 1)
    sizes = [Path(files[n]).stat().st_size for n in names]
    entries, name_offset = [], 0
    for n, size in zip(names, sizes):
        entries.append((name_offset, offset, size))
        name_offset += len(n.encode()) + 1
        offset += (size + ALIGN - 1) & ~(ALIGN - 1)
    if offset >= 1 << 31:
        raise SystemExit('o3ds.pak exceeds the 2 GiB seek range')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('wb') as out:
        out.write(b'MPK1' + struct.pack('<II', len(names), len(blob)))
        for entry in entries:
            out.write(struct.pack('<III', *entry))
        out.write(blob)
        out.write(b'\0' * (((header + ALIGN - 1) & ~(ALIGN - 1)) - header))
        for n, size in zip(names, sizes):
            out.write(Path(files[n]).read_bytes() + b'\0' * (((size + ALIGN - 1) & ~(ALIGN - 1)) - size))
    return len(names), output.stat().st_size
