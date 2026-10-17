"""GMDL course-mesh reader for the PVS tool: the geometry Model::load ends up
with, including an Old 3DS O3RM sidecar (models/o3ds/<name>.gmdl) applied the
way include/mkw/model_variant.hpp applies it (no validation here: a sidecar
the game rejects leaves the original, whose fingerprint then differs)."""
import struct

import numpy as np

VERTEX = np.dtype([('p', '<f4', 3), ('n', '<f4', 3), ('uv', '<f4', 2), ('uv1', '<f4', 2), ('c', 'u1', 4)])
assert VERTEX.itemsize == 44
SUBMESH_V5 = 92


def _record(version):
    return 92 if version == 5 else 56 if version == 4 else 44


def read(path, variant=None):
    data = open(path, 'rb').read()
    magic, version, nv, ni, ns, nt = struct.unpack_from('<4s5I', data, 0)
    assert magic == b'GMDL' and version in (3, 4, 5), path
    rec = _record(version)
    at = 24
    subs = []
    for _ in range(ns):
        first, count, texture, flags = struct.unpack_from('<IIiI', data, at)
        subs.append(dict(first=first, count=count, texture=texture, flags=flags))
        at += rec
    at += 64 * nt
    vertices = np.frombuffer(data, VERTEX, nv, at); at += nv * 44
    indices = np.frombuffer(data, '<u2', ni, at); at += ni * 2
    out = dict(version=version, vertices=vertices, indices=indices, submeshes=subs, variant=False)
    if variant:
        try:
            cdata = open(variant, 'rb').read()
        except FileNotFoundError:
            return out
        cmagic, cversion, cnv, cni, cns, cnt = struct.unpack_from('<4s5I', cdata, 0)
        if cmagic != b'GMDL' or cversion != version or cns != ns or cnt != nt:
            return out
        at = 24
        spans = []
        for i in range(ns):
            first, count = struct.unpack_from('<II', cdata, at)
            spans.append(dict(subs[i], first=first, count=count))
            at += rec
        at += 64 * nt
        if cnv != nv:
            cverts = np.frombuffer(cdata, VERTEX, cnv, at); at += cnv * 44
        else:
            cverts = vertices; at += nv * 44
        cidx = np.frombuffer(cdata, '<u2', cni, at)
        out = dict(version=version, vertices=cverts, indices=cidx, submeshes=spans, variant=True)
    return out


def fnv1a(buf, h=0x811C9DC5):
    """32-bit FNV-1a over 32-bit words (fast in numpy-free loops on the 3DS too)."""
    words = np.frombuffer(buf, '<u4')
    h = np.uint32(h)
    prime = np.uint32(0x01000193)
    with np.errstate(over='ignore'):
        for w in words:  # small inputs only; see fingerprint()
            h = np.uint32((h ^ w) * prime)
    return int(h)


def fingerprint(model):
    """Runtime-checkable identity of the loaded geometry (src/course_pvs.cpp):
    FNV-1a over [vertexCount, indexCount, submeshCount, (first,count)*,
    every index (as u16 pairs packed in u32 words, zero padded) and every
    position float bit pattern]."""
    v = model['vertices']; i = model['indices']; s = model['submeshes']
    head = [len(v), len(i), len(s)] + [x for sm in s for x in (sm['first'], sm['count'])]
    idx = np.asarray(i, '<u2')
    if len(idx) % 2:
        idx = np.concatenate([idx, np.zeros(1, '<u2')])
    pos = np.ascontiguousarray(v['p']).astype('<f4')
    words = np.concatenate([np.asarray(head, '<u4'), idx.view('<u4'), pos.reshape(-1).view('<u4')])
    # Vectorised FNV-1a is sequential; do it in C-speed chunks via python ints.
    h = 0x811C9DC5
    for w in words.tolist():
        h = ((h ^ w) * 0x01000193) & 0xFFFFFFFF
    return h
