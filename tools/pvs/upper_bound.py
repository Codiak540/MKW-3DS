#!/usr/bin/env python3
"""How much can occlusion culling remove beyond frustum culling? Simulated
chase cameras along the KMP enemy route (ENPT): per camera, triangles in
frustum-visible blocks (a), those also visible from the exact eye (b, cube-map
sample, all directions), and those in the eye's column PVS (c)."""
import struct, sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'tools/pvs')]
import build_o3ds_pvs as B, gmdl  # noqa: E402


def enpt(path):
    d = Path(path).read_bytes()
    n, hl = struct.unpack_from('>HH', d, 8)
    for o in struct.unpack_from(f'>{n}I', d, 0x10):
        at = hl + o
        if d[at:at + 4] == b'ENPT':
            c = struct.unpack_from('>H', d, at + 4)[0]
            return np.array([struct.unpack_from('>3f', d, at + 8 + i * 0x14) for i in range(c)])


def frustum_planes(eye, target, fovy, aspect, near=10., far=100000.):
    f = target - eye; f /= np.linalg.norm(f)
    r = np.cross(f, [0, 1, 0]); r /= np.linalg.norm(r); u = np.cross(r, f)
    ty = np.tan(np.radians(fovy) / 2); tx = ty * aspect
    planes = [(f, -np.dot(f, eye) - near), (-f, np.dot(f, eye) + far)]
    for s in (1, -1):
        n = f * tx - s * r; n /= np.linalg.norm(n); planes.append((n, -np.dot(n, eye)))
        n = f * ty - s * u; n /= np.linalg.norm(n); planes.append((n, -np.dot(n, eye)))
    return planes


def in_frustum(lo, hi, planes):
    ok = np.ones(len(lo), bool)
    for n, d in planes:
        pmax = np.where(n >= 0, hi, lo)
        ok &= pmax @ n + d >= 0
    return ok


def main(cid, key, models=ROOT / 'build/azahar-o3ds/user/sdmc/3ds/mkw-3ds/models', block=128):
    name = f'course_{cid:02d}_000'
    m, _ = B.sectioned(gmdl.read(models / f'{name}.gmdl', models / f'o3ds/{name}.gmdl'), block)
    ids, flags, nb = B.blocks_of(m, block)
    tris = m['vertices']['p'].astype(np.float32)[m['indices'].astype(np.int64)]
    T = tris.reshape(-1, 3, 3).astype(np.float64)
    lo = np.full((nb, 3), np.inf); hi = np.full((nb, 3), -np.inf)
    np.minimum.at(lo, ids, T.min(1)); np.maximum.at(hi, ids, T.max(1))
    tpb = np.bincount(ids, minlength=nb)
    pts = enpt(ROOT / f'assets/courses/{key}/course.kmp')
    eyes, targets = [], []
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        for t in (0, .5):
            p = a + (b - a) * t; f = b - a; f[1] = 0; f /= np.linalg.norm(f) + 1e-9
            eyes.append(p - f * 650 + [0, 280, 0]); targets.append(p + f * 300 + [0, 80, 0])
    eyes = np.array(eyes, np.float32)
    vis = B.run_raster(ROOT / 'build/o3ds-pvs/pvs_raster', tris, ids, flags, nb, eyes, 256, 10., 96.,
                       ROOT / 'build/o3ds-pvs/work').astype(bool)
    pv = (ROOT / f'build/o3ds-pvs/models/pvs/{name}.pvs').read_bytes()
    h = struct.unpack_from('<4s7I3f4I', pv, 0)
    ox, oz, cell, nx, nz, nsets, sb = h[8], h[9], h[10], h[11], h[12], h[13], h[14]
    at = 60 + ((len(m['indices']) // 3) * 2 + 3 & ~3)
    cols = np.frombuffer(pv, np.dtype([('lo', '<f4'), ('hi', '<f4'), ('set', '<u4')]), nx * nz, at)
    sets = np.unpackbits(np.frombuffer(pv, np.uint8, nsets * sb, at + nx * nz * 12).reshape(nsets, sb), axis=1,
                         bitorder='little')[:, :nb].astype(bool)
    A = Bv = C = 0; miss = 0
    for e, t, v in zip(eyes.astype(np.float64), np.array(targets), vis):
        fr = in_frustum(lo, hi, frustum_planes(e, t, 62., 400 / 240))
        ix, iz = int((e[0] - ox) // cell), int((e[2] - oz) // cell)
        col = cols[iz * nx + ix] if 0 <= ix < nx and 0 <= iz < nz else None
        s = sets[col['set']] if col is not None and col['set'] != 0xFFFFFFFF and col['lo'] <= e[1] <= col['hi'] else None
        A += tpb[fr].sum(); Bv += tpb[fr & v].sum()
        if s is None: miss += 1; C += tpb[fr].sum()
        else: C += tpb[fr & s].sum()
        assert s is None or not (v & ~s).any(), 'PVS misses a block visible from a route camera'
    n = len(eyes)
    print(f'{name}: cameras {n} (outside columns {miss}); triangles per camera: frustum {A / n:.0f}, '
          f'+exact occlusion {Bv / n:.0f} ({(Bv - A) / A * 100:+.1f}%), +column PVS {C / n:.0f} ({(C - A) / A * 100:+.1f}%)')


if __name__ == '__main__':
    main(int(sys.argv[1]), sys.argv[2])
