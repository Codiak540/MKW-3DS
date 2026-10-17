"""Minimal Mario Kart Wii KCL reader (collision prisms -> triangles + surface type).

Used only to keep drivable course surfaces exact in the Old 3DS course cook.
"""
import struct

import numpy as np

# KCL attribute base types (attribute & 0x1f).
DRIVABLE = {
    0x00,  # road
    0x01,  # slippery road (sand/dirt)
    0x02,  # weak off-road
    0x03,  # off-road
    0x04,  # heavy off-road
    0x05,  # slippery road 2
    0x06,  # boost panel
    0x07,  # boost ramp
    0x08,  # jump pad
    0x09,  # item road
    0x13,  # half-pipe ramp
    0x15,  # moving road
    0x16,  # sticky road
    0x17,  # road 2
    0x1D,  # rotating road
}
ROAD = DRIVABLE - {0x02, 0x03, 0x04}


def read_kcl(path):
    data = open(path, 'rb').read()
    pos_off, nrm_off, tri_off, idx_off = struct.unpack_from('>4I', data, 0)
    count = (idx_off - (tri_off + 0x10)) // 0x10
    prisms = np.frombuffer(data, dtype='>u2', count=count * 8, offset=tri_off + 0x10).reshape(count, 8)
    height = np.frombuffer(data, dtype='>f4', count=count * 4, offset=tri_off + 0x10).reshape(count, 4)[:, 0]
    n_pos = (nrm_off - pos_off) // 12
    positions = np.frombuffer(data, dtype='>f4', count=n_pos * 3, offset=pos_off).reshape(-1, 3).astype(np.float64)
    n_nrm = (tri_off + 0x10 - nrm_off) // 12
    normals = np.frombuffer(data, dtype='>f4', count=n_nrm * 3, offset=nrm_off).reshape(-1, 3).astype(np.float64)
    pi, fi, e1, e2, e3 = (prisms[:, k].astype(np.int64) for k in range(2, 7))
    attr = prisms[:, 7].astype(np.int64)
    fn, en1, en2, en3 = normals[fi], normals[e1], normals[e2], normals[e3]
    ca, cb = np.cross(en1, fn), np.cross(en2, fn)
    v1 = positions[pi]
    with np.errstate(divide='ignore', invalid='ignore'):
        v2 = v1 + cb * (height / (cb * en3).sum(1))[:, None]
        v3 = v1 + ca * (height / (ca * en3).sum(1))[:, None]
    ok = np.isfinite(v2).all(1) & np.isfinite(v3).all(1)
    return dict(a=v1[ok], b=v2[ok], c=v3[ok], normal=fn[ok], attribute=attr[ok], type=attr[ok] & 0x1F)
