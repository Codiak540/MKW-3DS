"""Readers/writers for the authored GMDL/DSKM files used by the Old 3DS low-poly cook.

Only staged Wii-derived assets are read. Nothing here modifies an input file.
"""
import struct
from dataclasses import dataclass, field

import numpy as np

RECORD = {3: 44, 4: 56, 5: 92}
VERTEX = 44


@dataclass
class Submesh:
    first: int
    count: int
    texture: int
    flags: int
    secondary: int
    anim: int
    raw: bytes


@dataclass
class Gmdl:
    version: int
    submeshes: list
    names: list
    records: np.ndarray  # (n,44) uint8
    indices: np.ndarray  # (k,) int64
    header: tuple
    path: str = ''

    @property
    def floats(self):
        return self.records[:, :40].copy().view('<f4').reshape(-1, 10)

    @property
    def positions(self):
        return self.floats[:, 0:3].astype(np.float64)

    @property
    def normals(self):
        return self.floats[:, 3:6].astype(np.float64)

    @property
    def uv0(self):
        return self.floats[:, 6:8].astype(np.float64)


def read_gmdl(path):
    data = open(path, 'rb').read()
    magic, version, nv, ni, ns, nt = struct.unpack_from('<4s5I', data)
    if magic != b'GMDL' or version not in RECORD:
        raise ValueError(f'{path}: not a supported GMDL')
    size = RECORD[version]
    offset = 24
    submeshes = []
    for _ in range(ns):
        first, count, texture, flags, secondary = struct.unpack_from('<IIiIi', data, offset)
        anim = struct.unpack_from('<I', data, offset + 40)[0]
        submeshes.append(Submesh(first, count, texture, flags, secondary, anim, data[offset:offset + size]))
        offset += size
    names = []
    for i in range(nt):
        raw = data[offset + 64 * i: offset + 64 * (i + 1)]
        names.append(raw.split(b'\0')[0].decode())
    offset += 64 * nt
    records = np.frombuffer(data, dtype=np.uint8, count=nv * VERTEX, offset=offset).reshape(nv, VERTEX).copy()
    offset += nv * VERTEX
    indices = np.frombuffer(data, dtype='<u2', count=ni, offset=offset).astype(np.int64)
    offset += ni * 2
    if offset != len(data):
        raise ValueError(f'{path}: trailing bytes (already a variant?)')
    return Gmdl(version, submeshes, names, records, indices, (version, nv, ni, ns, nt), str(path))


@dataclass
class Dskm:
    version: int
    vertices: int
    matrices: int
    shapes: int
    source: np.ndarray  # (n,7) float32 p,n + matrix as uint32 view
    matrix: np.ndarray  # (n,) uint32
    ranges: np.ndarray  # (shapes,2)
    pat_shapes: set = field(default_factory=set)
    pat_textures: set = field(default_factory=set)
    uvan_shapes: set = field(default_factory=set)


def read_dskm(path):
    data = open(path, 'rb').read()
    u32 = lambda o: struct.unpack_from('<I', data, o)[0]
    if data[:4] != b'DSKM':
        raise ValueError(f'{path}: not DSKM')
    version, nv, matrices, clips, shapes, faces = (u32(4), u32(8), u32(12), u32(16), u32(20), u32(24))
    cursor = 32 if version == 5 else 28
    src = np.frombuffer(data, dtype='<f4', count=nv * 7, offset=cursor).reshape(nv, 7)
    matrix = np.frombuffer(data, dtype='<u4', count=nv * 7, offset=cursor).reshape(nv, 7)[:, 6].copy()
    cursor += nv * 28
    ranges = np.frombuffer(data, dtype='<u4', count=shapes * 2, offset=cursor).reshape(shapes, 2).copy()
    cursor += shapes * 8
    out = Dskm(version, nv, matrices, shapes, src.copy(), matrix, ranges)

    def clip():
        nonlocal cursor
        count = u32(cursor + 40)
        cursor += 44
        return count

    for _ in range(clips):
        count = clip()
        cursor += count * (matrices * 84 + shapes * 4)
    for _ in range(faces):
        count = clip()
        block = np.frombuffer(data, dtype='<i4', count=count * shapes, offset=cursor).reshape(count, shapes)
        for s in range(shapes):
            if (block[:, s] >= 0).any():
                out.pat_shapes.add(s)
                out.pat_textures.update(int(x) for x in block[:, s] if x >= 0)
        cursor += count * shapes * 4
    if version >= 3:
        nodes, hair = u32(cursor + 4), u32(cursor + 8)
        cursor += 12 + nodes * 8
        for _ in range(hair):
            count = clip()
            cursor += nodes * 84 + count * nodes * 84
    if version >= 4:
        count_uv = u32(cursor + 4)
        cursor += 8
        identity = np.array([1, 0, 0, 0, 1, 0], np.float32)
        for _ in range(count_uv):
            count = clip()
            block = np.frombuffer(data, dtype='<f4', count=count * shapes * 6, offset=cursor).reshape(count, shapes, 6)
            for s in range(shapes):
                if np.abs(block[:, s] - identity).max() > 1e-5:
                    out.uvan_shapes.add(s)
            cursor += count * shapes * 24
    return out


def write_variant(path, model, vertex_records, remap, spans, indices, names, version=2, skin=None):
    """Write an O3RM sidecar. version 1: exact retained records, strictly
    increasing remap. version 2 (UV re-bake): UV0 may differ, remap is
    nondecreasing and texture names may carry the `_o3` re-bake suffix.
    version 3: as 2, positions may move; `skin` holds every vertex's bone-local
    position (O3SP block)."""
    header = model.header
    nv = len(vertex_records)
    with open(path, 'wb') as f:
        f.write(struct.pack('<4s5I', b'GMDL', header[0], nv, len(indices), header[3], header[4]))
        for sub, (first, count) in zip(model.submeshes, spans):
            f.write(struct.pack('<II', first, count) + sub.raw[8:])
        for name in names:
            raw = name.encode()
            if len(raw) >= 64:
                raise ValueError('texture name too long')
            f.write(raw.ljust(64, b'\0'))
        f.write(np.ascontiguousarray(vertex_records, dtype=np.uint8).tobytes())
        f.write(np.asarray(indices, dtype='<u2').tobytes())
        f.write(struct.pack('<4sIII', b'O3RM', version, header[1], nv))
        f.write(np.asarray(remap, dtype='<u4').tobytes())
        if version == 3:
            # O3SP: bone-local skin position of every compact vertex.
            f.write(struct.pack('<4sI', b'O3SP', nv))
            f.write(np.ascontiguousarray(skin, dtype='<f4').tobytes())


def read_variant(path):
    """Read an O3RM sidecar -> (Gmdl-like with records/indices/names, remap)."""
    data = open(path, 'rb').read()
    magic, version, nv, ni, ns, nt = struct.unpack_from('<4s5I', data)
    size = RECORD[version]
    offset = 24
    submeshes = []
    for _ in range(ns):
        first, count, texture, flags, secondary = struct.unpack_from('<IIiIi', data, offset)
        anim = struct.unpack_from('<I', data, offset + 40)[0]
        submeshes.append(Submesh(first, count, texture, flags, secondary, anim, data[offset:offset + size]))
        offset += size
    names = [data[offset + 64 * i: offset + 64 * (i + 1)].split(b'\0')[0].decode() for i in range(nt)]
    offset += 64 * nt
    records = np.frombuffer(data, dtype=np.uint8, count=nv * VERTEX, offset=offset).reshape(nv, VERTEX).copy()
    offset += nv * VERTEX
    indices = np.frombuffer(data, dtype='<u2', count=ni, offset=offset).astype(np.int64)
    offset += ni * 2
    tag, rversion, original, count = struct.unpack_from('<4sIII', data, offset)
    offset += 16
    remap = np.frombuffer(data, dtype='<u4', count=count, offset=offset).astype(np.int64)
    return Gmdl(version, submeshes, names, records, indices, (version, nv, ni, ns, nt), str(path)), remap, rversion


def read_dobj(path):
    """Course-object bank (src/object_bank.cpp layout) as a Dskm-like record.
    Shapes with texture-pattern, UV-transform or colour tracks count as animated."""
    data = open(path, 'rb').read()
    u32 = lambda o: struct.unpack_from('<I', data, o)[0]
    if data[:4] != b'DOBJ' or u32(4) != 1:
        raise ValueError(f'{path}: not DOBJ v1')
    nv, matrices, shapes, nodes, chr_n, pat_n, srt_n, clr_n = (u32(8 + 4 * i) for i in range(8))
    at = 40
    src = np.frombuffer(data, dtype='<f4', count=nv * 7, offset=at).reshape(nv, 7).copy()
    matrix = np.frombuffer(data, dtype='<u4', count=nv * 7, offset=at).reshape(nv, 7)[:, 6].copy()
    at += nv * 28
    ranges = np.frombuffer(data, dtype='<u4', count=shapes * 2, offset=at).reshape(shapes, 2).copy()
    at += shapes * 8 + matrices * 48
    out = Dskm(1, nv, matrices, shapes, src, matrix, ranges)
    node_size = 32 + 4 * 3 + 48 * 2  # name, parent, slot, envelope, local[12], world[12]
    at += nodes * node_size
    def samples(frames, step):
        return 1 if frames <= 1 else (frames - 1 + step - 1) // step + 1
    for _ in range(chr_n):
        frames, loop, step, count = (u32(at + 32 + 4 * i) for i in range(4))
        at += 48 + count * 4 + matrices * 48 + samples(frames, step) * count * 48
    for _ in range(pat_n):
        frames = u32(at + 32)
        at += 40
        block = np.frombuffer(data, dtype='<i4', count=frames * shapes * 2, offset=at).reshape(frames, shapes, 2)
        for s in range(shapes):
            if (block[:, s, :] >= 0).any():
                out.pat_shapes.add(s)
                out.pat_textures.update(int(x) for x in block[:, s, :].ravel() if x >= 0)
        at += frames * shapes * 8
    out.trailing = at
    out.srt = srt_n
    out.clr = clr_n
    # SRT/CLR/MORF are not walked: any of them marks the whole object animated
    # beyond skinning, and such objects keep the generic level cook.
    out.extra_animation = bool(srt_n or clr_n)
    out.morph = b'MORF' in data[at:]
    return out


def static_bank(gmdl):
    """A one-bone stand-in bank for an unanimated model (identity skin)."""
    nv = gmdl.header[1]
    src = np.zeros((nv, 7), np.float32)
    src[:, 0:6] = gmdl.floats[:, 0:6]
    return Dskm(0, nv, 1, 0, src, np.zeros(nv, np.uint32), np.zeros((0, 2), np.uint32))
