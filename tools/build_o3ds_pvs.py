#!/usr/bin/env python3
"""Old 3DS precomputed visibility (PVS) for course meshes.

Track sectioning: within every submesh whose triangle order cannot change
the image (opaque or alpha-tested, depth tested and depth writing, not
blended), triangles are reordered by a k-d tree whose leaves are runs of
BLOCK triangles, so the renderer's static-culling blocks (consecutive BLOCK
triangles of a submesh, mkw::buildVisibility) become compact spatial chunks.
Vertices, submeshes, materials and the order of blended/non-depth-writing
submeshes are unchanged. Authored index order spreads a 128-triangle block
over half a course, which defeats frustum culling as well as a PVS.

Visibility: the course camera space is split into vertical columns (CELL x
CELL world units in XZ, each with one sampled height interval) around every
drivable KCL surface; each column records which blocks are potentially
visible from any camera position inside it. The runtime (src/course_pvs.cpp,
Old 3DS only) applies the permutation at load, then skips blocks outside the
camera column's set before frustum culling; outside every column, or for
geometry whose fingerprint differs from the one recorded here, it draws
everything.

Visibility per sample camera (tools/pvs/pvs_raster.cpp): six faces of a
widened cube map; depth of the opaque occluders (depth-writing, not alpha
tested, not blended, back faces culled as the game culls them), eroded by a
pixel; then every triangle, conservatively rasterised and without back-face
culling, is tested against it. Samples fill each column's volume on a lattice;
each column's set is then padded with its eight neighbours' sets.

Sidecar models/pvs/<part>.pvs (little endian):
  "MPVS" u32 version=1, u32 fingerprint, u32 vertexCount, u32 indexCount,
  u32 submeshCount, u32 blockTriangles, u32 blockCount,
  f32 originX, f32 originZ, f32 cell, u32 columnsX, u32 columnsZ,
  u32 setCount, u32 setBytes,
  indexCount/3 * u16: per submesh, the authored local triangle drawn at each
    local position (identity for submeshes that keep their order),
  columnsX*columnsZ * {f32 yMin, f32 yMax, u32 set (0xffffffff: no data)},
  setCount * setBytes (bit b = block b in submesh order), zero padded to 4.
"""
import argparse
import json
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tools/pvs'))
import gmdl  # noqa: E402
from host_cxx import host_cxx  # noqa: E402
from o3ds_kcl import DRIVABLE, read_kcl  # noqa: E402

NONE = 0xFFFFFFFF


def material(flags):
    """(occluder, single_sided) exactly as Renderer::drawNow sets course state."""
    source = bool(flags >> 31 & 1)
    blended = bool(flags & 2)
    depth_test = (not source) or bool(flags & (1 << 23))
    depth_write = bool(flags & (1 << 24)) if source else not blended
    occluder = depth_test and depth_write and not (flags & 1) and not blended
    return occluder, not (flags & 4)


def kd_order(centroids, tris, block, out):
    n = len(tris)
    if n <= block:
        out.extend(tris.tolist())
        return
    c = centroids[tris]
    axis = int(np.argmax(c.max(0) - c.min(0)))
    order = tris[np.argsort(c[:, axis], kind='stable')]
    k = min(max(block, int(round(n / 2 / block)) * block), (n - 1) // block * block)
    # Leaves keep their authored relative order (vertex cache locality).
    kd_order(centroids, np.sort(order[:k]), block, out)
    kd_order(centroids, np.sort(order[k:]), block, out)


def sectioned(model, block):
    """Model with spatially reordered triangles and the local permutation."""
    tri = model['indices'].astype(np.int64).reshape(-1, 3)
    centroids = model['vertices']['p'].astype(np.float64)[tri].mean(1)
    perm = np.arange(len(tri))
    local = np.zeros(len(tri), np.uint16)
    for s in model['submeshes']:
        t0, n = s['first'] // 3, s['count'] // 3
        assert s['first'] % 3 == 0 and n <= 65536
        local[t0:t0 + n] = np.arange(n)
        if n <= block or not material(s['flags'])[0]:
            continue
        out = []
        kd_order(centroids, np.arange(t0, t0 + n), block, out)
        perm[t0:t0 + n] = out
        local[t0:t0 + n] = np.asarray(out) - t0
    return dict(model, indices=tri[perm].reshape(-1).astype('<u2')), local


def blocks_of(model, block):
    """Triangle -> global block id, in the renderer's block order."""
    ids = np.zeros(len(model['indices']) // 3, np.uint32)
    flags = np.zeros(len(ids), np.uint32)
    base = 0
    for s in model['submeshes']:
        tris = s['count'] // 3
        t0 = s['first'] // 3
        ids[t0:t0 + tris] = base + np.arange(tris) // block
        occ, single = material(s['flags'])
        flags[t0:t0 + tris] = (1 if occ else 0) | (2 if single else 0)
        base += (tris + block - 1) // block
    return ids, flags, base


def columns(kcl, cell, margin, below, above):
    drive = np.isin(kcl['type'], list(DRIVABLE))
    a, b, c = kcl['a'][drive], kcl['b'][drive], kcl['c'][drive]
    pts = np.stack([a, b, c], 1)
    lo, hi = pts.min(1), pts.max(1)
    origin = np.floor((lo[:, [0, 2]].min(0) - margin) / cell) * cell
    extent = hi[:, [0, 2]].max(0) + margin - origin
    nx, nz = (np.ceil(extent / cell)).astype(int)
    ymin = np.full((nz, nx), np.inf)
    ymax = np.full((nz, nx), -np.inf)
    x0 = np.floor((lo[:, 0] - margin - origin[0]) / cell).astype(int)
    x1 = np.floor((hi[:, 0] + margin - origin[0]) / cell).astype(int)
    z0 = np.floor((lo[:, 2] - margin - origin[1]) / cell).astype(int)
    z1 = np.floor((hi[:, 2] + margin - origin[1]) / cell).astype(int)
    for i in range(len(pts)):
        sl = (slice(max(z0[i], 0), min(z1[i], nz - 1) + 1), slice(max(x0[i], 0), min(x1[i], nx - 1) + 1))
        ymin[sl] = np.minimum(ymin[sl], lo[i, 1])
        ymax[sl] = np.maximum(ymax[sl], hi[i, 1])
    have = np.isfinite(ymin)
    ymin[have] -= below
    ymax[have] += above
    return origin, cell, nx, nz, have, ymin, ymax


def samples_for(origin, cell, have, ymin, ymax, step, vstep):
    per = max(1, int(round(cell / step)))
    offs = (np.arange(per) + .5) * cell / per
    eyes, owner = [], []
    for iz, ix in zip(*np.nonzero(have)):
        nv = max(2, int(np.ceil((ymax[iz, ix] - ymin[iz, ix]) / vstep)) + 1)
        ys = np.linspace(ymin[iz, ix], ymax[iz, ix], nv)
        X, Z, Y = np.meshgrid(origin[0] + ix * cell + offs, origin[1] + iz * cell + offs, ys, indexing='ij')
        e = np.stack([X.ravel(), Y.ravel(), Z.ravel()], 1)
        eyes.append(e)
        owner.append(np.full(len(e), iz * have.shape[1] + ix))
    return np.concatenate(eyes).astype(np.float32), np.concatenate(owner)


def run_raster(exe, tris, ids, flags, nblocks, eyes, res, near, fov, work):
    inp, out = work / 'in.bin', work / 'out.bin'
    rec = np.zeros(len(ids), np.dtype([('v', '<f4', 9), ('b', '<u4'), ('f', '<u4')]))
    rec['v'] = tris.reshape(len(ids), 9)
    rec['b'] = ids
    rec['f'] = flags
    with open(inp, 'wb') as f:
        f.write(b'PVSI' + struct.pack('<5I', 1, len(ids), nblocks, len(eyes), res) + struct.pack('<ffi', near, fov, -1))
        f.write(rec.tobytes())
        f.write(np.ascontiguousarray(eyes, '<f4').tobytes())
    subprocess.run([str(exe), str(inp), str(out)], check=True)
    stride = (nblocks + 7) // 8
    return np.unpackbits(np.fromfile(out, np.uint8).reshape(len(eyes), stride), axis=1, bitorder='little')[:, :nblocks]


def build_part(name, models, kcl_path, exe, out_dir, work, a):
    loaded = gmdl.read(models / f'{name}.gmdl', models / f'o3ds/{name}.gmdl')
    model, local = sectioned(loaded, a.block)
    ids, flags, nblocks = blocks_of(model, a.block)
    p = model['vertices']['p'].astype(np.float32)
    tris = p[model['indices'].astype(np.int64)]
    kcl = read_kcl(kcl_path)
    origin, cell, nx, nz, have, ymin, ymax = columns(kcl, a.cell, a.margin, a.below, a.above)
    eyes, owner = samples_for(origin, cell, have, ymin, ymax, a.step, a.vstep)
    t = time.time()
    vis = run_raster(exe, tris, ids, flags, nblocks, eyes, a.res, a.near, a.fov, work)
    raster_s = time.time() - t
    raw = np.zeros((nz * nx, nblocks), bool)
    np.logical_or.at(raw, owner, vis.astype(bool))
    grid = raw.reshape(nz, nx, nblocks)
    padded = np.zeros_like(grid)
    for dz in (-1, 0, 1):
        for dx in (-1, 0, 1):
            src = np.zeros_like(grid)
            zs, xs = slice(max(dz, 0), nz + min(dz, 0)), slice(max(dx, 0), nx + min(dx, 0))
            zd, xd = slice(max(-dz, 0), nz + min(-dz, 0)), slice(max(-dx, 0), nx + min(-dx, 0))
            src[zd, xd] = grid[zs, xs]
            padded |= src
    padded &= have[:, :, None]
    # Deduplicated sets.
    sets, index = {}, np.full(nz * nx, NONE, np.uint32)
    flat = padded.reshape(nz * nx, nblocks)
    for k in np.nonzero(have.ravel())[0]:
        key = np.packbits(flat[k], bitorder='little').tobytes()
        index[k] = sets.setdefault(key, len(sets))
    set_bytes = (nblocks + 7) // 8
    fp = gmdl.fingerprint(loaded)
    head = struct.pack('<4s7I3f4I', b'MPVS', 1, fp, len(model['vertices']), len(model['indices']),
                       len(model['submeshes']), a.block, nblocks, float(origin[0]), float(origin[1]),
                       float(cell), nx, nz, len(sets), set_bytes)
    cols = np.zeros(nz * nx, np.dtype([('lo', '<f4'), ('hi', '<f4'), ('set', '<u4')]))
    cols['lo'] = np.where(have.ravel(), ymin.ravel(), 0)
    cols['hi'] = np.where(have.ravel(), ymax.ravel(), 0)
    cols['set'] = index
    body = b''.join(sets.keys())
    body += bytes(-len(body) % 4)
    out = out_dir / f'{name}.pvs'
    out.write_bytes(head + local.astype('<u2').tobytes() + bytes(len(local) % 2 * 2) + cols.tobytes() + body)
    # Triangle-weighted statistics over the columns.
    tri_per_block = np.bincount(ids, minlength=nblocks)
    total = tri_per_block.sum()
    frac_raw = (raw[have.ravel()] @ tri_per_block) / total
    frac_pad = (flat[have.ravel()] @ tri_per_block) / total
    # Block extents (diagonal), authored order vs sectioned.
    def diag(m):
        i, _, nb = blocks_of(m, a.block)
        t = m['vertices']['p'].astype(np.float64)[m['indices'].astype(np.int64)].reshape(-1, 3, 3)
        lo = np.full((nb, 3), np.inf); hi = np.full((nb, 3), -np.inf)
        np.minimum.at(lo, i, t.min(1)); np.maximum.at(hi, i, t.max(1))
        return float(np.median(np.linalg.norm(hi - lo, axis=1)))
    return dict(part=name, variant=model['variant'], block=a.block,
                block_diag_median_authored=round(diag(loaded)), block_diag_median_sectioned=round(diag(model)), fingerprint=f'{fp:08x}', triangles=int(total), blocks=int(nblocks),
                occluder_triangles=int((flags & 1).astype(bool).sum()), columns=int(have.sum()), grid=[int(nx), int(nz)],
                samples=int(len(eyes)), sets=len(sets), bytes=out.stat().st_size, raster_seconds=round(raster_s, 1),
                visible_raw_mean=round(float(frac_raw.mean()), 4), visible_padded_mean=round(float(frac_pad.mean()), 4),
                visible_padded_min=round(float(frac_pad.min()), 4), visible_padded_max=round(float(frac_pad.max()), 4))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--models', type=Path, default=ROOT / 'build/azahar-o3ds/user/sdmc/3ds/mkw-3ds/models',
                    help='staged models/ folder (course lists, gmdl, o3ds/ sidecars, course-catalog.json)')
    ap.add_argument('--assets', type=Path, default=ROOT / 'assets/courses')
    ap.add_argument('--out', type=Path, default=ROOT / 'build/o3ds-pvs/models/pvs')
    ap.add_argument('--courses', default='', help='comma separated course ids (default: every race course)')
    ap.add_argument('--block', type=int, default=128, help='triangles per static-culling block')
    ap.add_argument('--cell', type=float, default=1000.)
    ap.add_argument('--margin', type=float, default=1200., help='horizontal camera reach beyond drivable surfaces')
    ap.add_argument('--below', type=float, default=400.)
    ap.add_argument('--above', type=float, default=1200.)
    ap.add_argument('--step', type=float, default=500.)
    ap.add_argument('--vstep', type=float, default=333.)
    ap.add_argument('--res', type=int, default=256)
    ap.add_argument('--near', type=float, default=10.)
    ap.add_argument('--fov', type=float, default=96.)
    a = ap.parse_args()
    exe = ROOT / 'build/o3ds-pvs/pvs_raster'
    src = ROOT / 'tools/pvs/pvs_raster.cpp'
    if not exe.exists() or exe.stat().st_mtime < src.stat().st_mtime:
        exe.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([*host_cxx(), '-O3', '-std=c++17', '-pthread', '-o', str(exe), str(src)], check=True)
    a.out.mkdir(parents=True, exist_ok=True)
    work = a.out.parent / 'work'
    work.mkdir(exist_ok=True)
    catalog = json.loads((a.models / 'course-catalog.json').read_text())['courses']
    want = {int(x) for x in a.courses.split(',') if x}
    report = []
    for course in catalog:
        if course.get('kind') != 'race' or (want and course['id'] not in want):
            continue
        lst = a.models / f"course_{course['id']:02d}.list"
        kcl = a.assets / course['key'] / 'course.kcl'
        for name in lst.read_text().split():
            t = time.time()
            r = build_part(name, a.models, kcl, exe, a.out, work, a)
            r.update(course=course['name'], id=course['id'], seconds=round(time.time() - t, 1))
            report.append(r)
            print(json.dumps(r), flush=True)
            (a.out.parent / 'report.json').write_text(json.dumps(dict(
                params={k: (str(v) if isinstance(v, Path) else v) for k, v in vars(a).items()}, parts=report),
                indent=1) + '\n')


if __name__ == '__main__':
    main()
