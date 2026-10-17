#!/usr/bin/env python3
"""Old 3DS precomputed visibility for course objects and racers.

The track PVS (tools/build_o3ds_pvs.py) hides blocks of course triangles; a
block set cannot hide an object, which may stand above a hidden floor. This
tool records, for the same camera columns, which *region boxes* are
potentially visible. A box is hidden from a column when the course's opaque
geometry covers it from every sampled camera position in the column and its
eight neighbours. At run time (src/object_pvs.cpp, Old 3DS only) an object or
racer is skipped only when its current world bounds lie wholly inside a box
that is hidden from the camera's column, so anything that moves outside the
boxes, or a camera outside the columns, simply draws as before.

Region boxes (no object semantics needed; containment keeps it safe):
  - drivable chunks: KCL drivable triangles split by a k-d tree until each
    leaf's centroids span at most --chunk (x, z) and --chunk-height (y); box =
    the leaf triangles' bounds, widened by --chunk-side, lowered by
    --chunk-below and raised by --chunk-above (karts, walkers, route movers);
  - placements: each KMP GOBJ placement present in races gets boxes of
    --object-radii around its position (times its largest scale above 1).

Occluders: every course part of the course list as Model::load produces it on
Old 3DS (models/o3ds overlay applied), opaque depth-writing submeshes only,
back faces culled as the game does. Sampling, erosion and padding are the
track PVS's (tools/pvs/pvs_raster.cpp, input version 2 adds the boxes).

Sidecar models/pvs/course_NN.opvs (little endian, 4-byte aligned; the
runtime's include/mkw/object_pvs.hpp):
  "MOPV" u32 version=1, u32 fingerprint (object_fingerprint over the parts),
  u32 partCount, f32 originX, f32 originZ, f32 cell, u32 columnsX, u32 columnsZ,
  f32 lookupCell, u32 lookupX, u32 lookupZ,
  u32 regionCount, u32 setCount, u32 setBytes, u32 entryCount,
  columnsX*columnsZ * {u16 set (0xffff: no data), i16 yLow, i16 yHigh}
    (camera height interval in units of 8, rounded inward), zero padded to 4,
  regionCount * f32 {min xyz, max xyz},
  (lookupX*lookupZ + 1) * u16 offsets into the entries, zero padded to 4
    (lookup cell: regions whose XZ box overlaps it, smallest box first),
  entryCount * u16 region index, zero padded to 4,
  setCount * setBytes (bit r = region r potentially visible), zero padded to 4.

The fingerprint ignores triangle order inside a submesh (the track PVS and the
vertex-cache pass both reorder whole triangles), so it matches whether or not
the track PVS has been applied; any change to the triangle set, submesh spans
or positions rejects the sidecar and objects draw exactly as before.
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
from host_cxx import compile_command, exe as executable, host_cxx  # noqa: E402
from build_o3ds_pvs import columns, material, samples_for  # noqa: E402
from o3ds_kcl import DRIVABLE, read_kcl  # noqa: E402

NONE = 0xFFFFFFFF
FNV_BASIS, FNV_PRIME = 0x811C9DC5, 0x01000193
HEADER = struct.Struct('<4s3I3f2If6I')
assert HEADER.size == 64


def fnv_words(h, words):
    for w in words:
        h = ((h ^ int(w)) * FNV_PRIME) & 0xFFFFFFFF
    return h


def triangle_hashes(indices):
    """Per triangle fnv(fnv(fnv(basis, i0), i1), i2), vectorised."""
    t = np.asarray(indices, np.uint64).reshape(-1, 3)
    h = np.full(len(t), FNV_BASIS, np.uint64)
    for k in range(3):
        h = ((h ^ t[:, k]) * np.uint64(FNV_PRIME)) & np.uint64(0xFFFFFFFF)
    return h


def object_fingerprint(parts):
    """include/mkw/object_pvs.hpp objectFingerprint(): per part, in list
    order: vertex, index and submesh counts; per submesh its triangle count
    and the 32-bit sum of its triangle hashes (order independent); every
    position float bit pattern."""
    h = fnv_words(FNV_BASIS, [len(parts)])
    for m in parts:
        h = fnv_words(h, [len(m['vertices']), len(m['indices']), len(m['submeshes'])])
        for s in m['submeshes']:
            tris = triangle_hashes(m['indices'][s['first']:s['first'] + s['count']])
            h = fnv_words(h, [s['count'] // 3, int(tris.sum()) & 0xFFFFFFFF])
        pos = np.ascontiguousarray(m['vertices']['p']).astype('<f4').reshape(-1).view('<u4')
        h = fnv_words(h, pos.tolist())
    return h


def occluders(parts):
    """World triangles of every part; flags 1 occluder, 2 single sided."""
    tris, flags = [], []
    for m in parts:
        p = m['vertices']['p'].astype(np.float32)
        for s in m['submeshes']:
            occ, single = material(s['flags'])
            if not occ or not s['count']:
                continue
            idx = m['indices'][s['first']:s['first'] + s['count']].astype(np.int64)
            tris.append(p[idx].reshape(-1, 9))
            flags.append(np.full(len(idx) // 3, 1 | (2 if single else 0), np.uint32))
    return np.concatenate(tris), np.concatenate(flags)


def kmp_sections(data):
    n, header = struct.unpack_from('>HH', data, 8)
    out = {}
    for offset in struct.unpack_from(f'>{n}I', data, 0x10):
        at = header + offset
        out[data[at:at + 4].decode('latin1')] = at
    return out


def placements(kmp):
    """(position, scale) of every GOBJ placement present in races."""
    data = Path(kmp).read_bytes()
    at = kmp_sections(data).get('GOBJ')
    if at is None:
        return []
    out = []
    for i in range(struct.unpack_from('>H', data, at + 4)[0]):
        e = at + 8 + i * 0x3C
        pos = np.array(struct.unpack_from('>3f', data, e + 4), np.float64)
        scale = np.array(struct.unpack_from('>3f', data, e + 0x1C), np.float64)
        if struct.unpack_from('>H', data, e + 0x3A)[0] & 1:
            out.append((pos, scale))
    return out


def chunks(kcl, a):
    """Boxes over k-d leaves of the drivable KCL triangles."""
    drive = np.isin(kcl['type'], list(DRIVABLE))
    tri = np.stack([kcl['a'][drive], kcl['b'][drive], kcl['c'][drive]], 1)
    lo, hi, cen = tri.min(1), tri.max(1), tri.mean(1)
    limit = np.array([a.chunk, a.chunk_height, a.chunk])
    boxes = []

    def split(ids):
        c = cen[ids]
        span = c.max(0) - c.min(0)
        over = span / limit
        if len(ids) == 1 or over.max() <= 1:
            boxes.append((lo[ids].min(0), hi[ids].max(0)))
            return
        axis = int(np.argmax(over))
        order = ids[np.argsort(c[:, axis], kind='stable')]
        mid = len(order) // 2
        split(order[:mid])
        split(order[mid:])

    split(np.arange(len(tri)))
    grow_lo = np.array([a.chunk_side, a.chunk_below, a.chunk_side])
    grow_hi = np.array([a.chunk_side, a.chunk_above, a.chunk_side])
    return [(b0 - grow_lo, b1 + grow_hi) for b0, b1 in boxes]


def regions_for(kcl, kmp, a):
    out = [('chunk', lo, hi) for lo, hi in chunks(kcl, a)]
    radii = [float(r) for r in a.object_radii.split(',') if r]
    for pos, scale in placements(kmp):
        k = max(1.0, float(np.abs(scale).max()))
        for r in radii:
            out.append(('object', pos - r * k, pos + r * k))
    return out


def merge_zones(flat, have, limit):
    """Unions the sets of adjacent columns until at most `limit` distinct
    sets remain (a union only adds visible regions, so it stays
    conservative). Greedy: always the adjacent pair of zones whose merge adds
    the fewest (visible region, column) pairs."""
    import heapq
    nz, nx = have.shape
    cols = [int(k) for k in np.nonzero(have.ravel())[0]]
    bits = {k: int.from_bytes(np.packbits(flat[k], bitorder='little').tobytes(), 'little') for k in cols}
    # Columns with identical sets start as one zone even when apart.
    zone_of, zones = {}, {}
    by_set = {}
    for k in cols:
        z = by_set.setdefault(bits[k], len(by_set))
        zone_of[k] = z
        zones.setdefault(z, dict(bits=bits[k], cols=[]))['cols'].append(k)
    if len(zones) <= limit:
        return flat
    adj = {z: set() for z in zones}
    for k in cols:
        z, x = divmod(k, nx)
        for dz, dx in ((0, 1), (1, 0)):
            zz, xx = z + dz, x + dx
            if zz < nz and xx < nx and have[zz, xx]:
                a_, b_ = zone_of[k], zone_of[zz * nx + xx]
                if a_ != b_:
                    adj[a_].add(b_)
                    adj[b_].add(a_)

    def cost(a_, b_):
        A, B = zones[a_], zones[b_]
        u = (A['bits'] | B['bits']).bit_count()
        return u * (len(A['cols']) + len(B['cols'])) - A['bits'].bit_count() * len(A['cols']) - B['bits'].bit_count() * len(B['cols'])

    version = {z: 0 for z in zones}
    heap = [(cost(a_, b_), a_, b_, 0, 0) for a_ in adj for b_ in adj[a_] if a_ < b_]
    heapq.heapify(heap)
    while len(zones) > limit and heap:
        c, a_, b_, va, vb = heapq.heappop(heap)
        if a_ not in zones or b_ not in zones or version[a_] != va or version[b_] != vb:
            continue
        A, B = zones[a_], zones.pop(b_)
        A['bits'] |= B['bits']
        A['cols'] += B['cols']
        version[a_] += 1
        for n in adj.pop(b_):
            adj[n].discard(b_)
            if n != a_:
                adj[n].add(a_)
                adj[a_].add(n)
        adj[a_].discard(a_)
        for n in adj[a_]:
            lo_, hi_ = min(a_, n), max(a_, n)
            heapq.heappush(heap, (cost(lo_, hi_), lo_, hi_, version[lo_], version[hi_]))
    out = flat.copy()
    nbytes = (flat.shape[1] + 7) // 8
    for z in zones.values():
        row = np.unpackbits(np.frombuffer(z['bits'].to_bytes(nbytes, 'little'), np.uint8), bitorder='little')[:flat.shape[1]].astype(bool)
        out[z['cols']] = row
    return out


def encode(fp, nparts, origin, cell, nx, nz, have, ymin, ymax, index, set_rows, boxes, lookup):
    """The sidecar bytes (see the module docstring) and its lookup entry count.
    index: per column (z * nx + x) its set number or NONE; set_rows: packed
    bit rows; boxes: (regions, 6) min/max, smallest first."""
    boxes = np.asarray(boxes, np.float32).reshape(-1, 6)
    nr = len(boxes)
    lcell = cell * lookup
    lx, lz = (nx + lookup - 1) // lookup, (nz + lookup - 1) // lookup
    lists = [[] for _ in range(lz * lx)]
    for r, b in enumerate(boxes):
        x0 = max(0, int(np.floor((b[0] - origin[0]) / lcell)))
        x1 = min(lx - 1, int(np.floor((b[3] - origin[0]) / lcell)))
        z0 = max(0, int(np.floor((b[2] - origin[1]) / lcell)))
        z1 = min(lz - 1, int(np.floor((b[5] - origin[1]) / lcell)))
        for z in range(z0, z1 + 1):
            for x in range(x0, x1 + 1):
                lists[z * lx + x].append(r)
    offsets = np.zeros(lz * lx + 1, np.int64)
    offsets[1:] = np.cumsum([len(x) for x in lists])
    entries = np.array([r for x in lists for r in x], '<u2')
    assert offsets[-1] <= 0xFFFF, 'too many lookup entries: raise --lookup or shrink regions'
    assert 0 < len(set_rows) < 0xFFFF and 0 < nr <= 0xFFFF
    set_bytes = (nr + 7) // 8
    assert all(len(r) == set_bytes for r in set_rows)
    head = HEADER.pack(b'MOPV', 1, fp, nparts, float(origin[0]), float(origin[1]), float(cell), nx, nz,
                       float(lcell), lx, lz, nr, len(set_rows), set_bytes, len(entries))
    have = np.asarray(have, bool).ravel()
    cols = np.zeros(nz * nx, np.dtype([('set', '<u2'), ('lo', '<i2'), ('hi', '<i2')]))
    lo_q = np.ceil(np.where(have, np.asarray(ymin, np.float64).ravel(), 0) / 8)
    hi_q = np.floor(np.where(have, np.asarray(ymax, np.float64).ravel(), 0) / 8)
    fits = have & (np.asarray(index) != NONE) & (lo_q >= -32768) & (hi_q <= 32767) & (lo_q <= hi_q)
    cols['set'] = np.where(fits, index, 0xFFFF)
    cols['lo'] = np.where(fits, lo_q, 0)
    cols['hi'] = np.where(fits, hi_q, 0)

    def pad(b):
        return b + bytes(-len(b) % 4)
    data = (head + pad(cols.tobytes()) + boxes.astype('<f4').tobytes() + pad(offsets.astype('<u2').tobytes())
            + pad(entries.tobytes()) + pad(b''.join(set_rows)))
    return data, int(len(entries))


def run_raster(exe, tris, flags, eyes, boxes, a, work):
    inp, out = work / 'in.bin', work / 'out.bin'
    rec = np.zeros(len(tris), np.dtype([('v', '<f4', 9), ('b', '<u4'), ('f', '<u4')]))
    rec['v'] = tris
    rec['b'] = 0
    rec['f'] = flags
    box = np.asarray(boxes, '<f4').reshape(-1, 6)
    with open(inp, 'wb') as f:
        f.write(b'PVSI' + struct.pack('<5I', 2, len(tris), 1, len(eyes), a.res) + struct.pack('<ffi', a.near, a.fov, -1))
        f.write(rec.tobytes())
        f.write(np.ascontiguousarray(eyes, '<f4').tobytes())
        f.write(struct.pack('<I', len(box)) + box.tobytes())
    subprocess.run([str(exe), str(inp), str(out)], check=True)
    stride = 1 + (len(box) + 7) // 8
    rows = np.fromfile(out, np.uint8).reshape(len(eyes), stride)[:, 1:]
    return np.unpackbits(rows, axis=1, bitorder='little')[:, :len(box)].astype(bool)


def build_course(course, a, exe, work):
    names = (a.models / f"course_{course['id']:02d}.list").read_text().split()
    parts = [gmdl.read(a.models / f'{n}.gmdl', a.models / f'o3ds/{n}.gmdl') for n in names]
    tris, flags = occluders(parts)
    kcl = read_kcl(a.assets / course['key'] / 'course.kcl')
    origin, cell, nx, nz, have, ymin, ymax = columns(kcl, a.cell, a.margin, a.below, a.above)
    eyes, owner = samples_for(origin, cell, have, ymin, ymax, a.step, a.vstep)
    regions = regions_for(kcl, a.assets / course['key'] / 'course.kmp', a)
    # Smallest first: the runtime takes the first hidden box that contains.
    regions.sort(key=lambda r: float(np.prod(r[2] - r[1])))
    boxes = np.array([np.concatenate([lo, hi]) for _, lo, hi in regions], np.float32)
    assert len(boxes) <= 0xFFFF
    t = time.time()
    cache = work / f"vis-{course['id']:02d}.npz"
    if a.reuse_raster and cache.exists() and np.array_equal(np.load(cache)['boxes'], boxes):
        vis = np.load(cache)['vis']
    else:
        vis = run_raster(exe, tris, flags, eyes, boxes, a, work)
        np.savez_compressed(cache, vis=vis, boxes=boxes)
    raster_s = time.time() - t
    nr = len(boxes)
    raw = np.zeros((nz * nx, nr), bool)
    np.logical_or.at(raw, owner, vis)
    grid = raw.reshape(nz, nx, nr)
    padded = np.zeros_like(grid)
    for dz in (-1, 0, 1):
        for dx in (-1, 0, 1):
            zs, xs = slice(max(dz, 0), nz + min(dz, 0)), slice(max(dx, 0), nx + min(dx, 0))
            zd, xd = slice(max(-dz, 0), nz + min(-dz, 0)), slice(max(-dx, 0), nx + min(-dx, 0))
            padded[zd, xd] |= grid[zs, xs]
    padded &= have[:, :, None]
    flat = padded.reshape(nz * nx, nr)
    cols_with_data = have.ravel()

    def prune(flat, boxes, kinds):
        # A region no column hides (or too few) only costs bits and lookups.
        hidden_in = (~flat[cols_with_data]).sum(0)
        keep = hidden_in >= max(1, int(np.ceil(a.min_hidden * cols_with_data.sum())))
        return flat[:, keep], boxes[keep], kinds[keep]
    kinds = np.array([k for k, _, _ in regions])
    flat, boxes, kinds = prune(flat, boxes, kinds)
    flat = merge_zones(flat, have, a.max_sets)
    flat, boxes, kinds = prune(flat, boxes, kinds)
    nr = len(boxes)
    out = a.out / f"course_{course['id']:02d}.opvs"
    if nr < a.min_regions:
        # Too little this course's geometry can hide to pay for the column
        # table: no sidecar, objects draw as before.
        out.unlink(missing_ok=True)
        return dict(course=course['name'], id=course['id'], parts=names, regions=int(nr), sidecar=False, bytes=0,
                    raster_seconds=round(raster_s, 1))
    sets, index = {}, np.full(nz * nx, NONE, np.uint32)
    for k in np.nonzero(have.ravel())[0]:
        key = np.packbits(flat[k], bitorder='little').tobytes()
        index[k] = sets.setdefault(key, len(sets))
    set_bytes = (nr + 7) // 8
    fp = object_fingerprint(parts)
    data, entry_count = encode(fp, len(parts), origin, cell, nx, nz, have, ymin, ymax, index, list(sets.keys()),
                               boxes, a.lookup)
    out.write_bytes(data)
    rows = flat[have.ravel()]
    def hidden_share(kind):
        sel = kinds == kind
        return round(float(1 - rows[:, sel].mean()), 4) if sel.any() and len(rows) else 0.0
    return dict(course=course['name'], id=course['id'], parts=names, fingerprint=f'{fp:08x}',
                occluder_triangles=int(len(tris)), columns=int(have.sum()), grid=[int(nx), int(nz)],
                samples=int(len(eyes)), regions=int(nr), chunks=int((kinds == 'chunk').sum()),
                object_boxes=int((kinds == 'object').sum()), sets=len(sets), entries=entry_count,
                bytes=out.stat().st_size, raster_seconds=round(raster_s, 1),
                hidden_chunks_mean=hidden_share('chunk'), hidden_object_boxes_mean=hidden_share('object'))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--models', type=Path, default=ROOT / 'build/azahar-o3ds/user/sdmc/3ds/mkw-3ds/models',
                    help='staged models/ folder (course lists, gmdl, o3ds/ sidecars, course-catalog.json)')
    ap.add_argument('--assets', type=Path, default=ROOT / 'assets/courses')
    ap.add_argument('--out', type=Path, default=ROOT / 'build/o3ds-ovis/models/pvs')
    ap.add_argument('--courses', default='', help='comma separated course ids (default: every race course)')
    # Camera columns and sampling: the track PVS's parameters.
    ap.add_argument('--cell', type=float, default=1000.)
    ap.add_argument('--margin', type=float, default=1200.)
    ap.add_argument('--below', type=float, default=400.)
    ap.add_argument('--above', type=float, default=1200.)
    ap.add_argument('--step', type=float, default=500.)
    ap.add_argument('--vstep', type=float, default=333.)
    ap.add_argument('--res', type=int, default=256)
    ap.add_argument('--near', type=float, default=10.)
    ap.add_argument('--fov', type=float, default=96.)
    # Region boxes.
    ap.add_argument('--chunk', type=float, default=2500.)
    ap.add_argument('--chunk-height', type=float, default=1000.)
    ap.add_argument('--chunk-side', type=float, default=500.)
    ap.add_argument('--chunk-below', type=float, default=600.)
    ap.add_argument('--chunk-above', type=float, default=1200.)
    ap.add_argument('--object-radii', default='700,1500,3500')
    ap.add_argument('--lookup', type=int, default=2, help='region lookup cell, in camera cells')
    ap.add_argument('--min-hidden', type=float, default=.01, help='drop regions hidden from fewer columns than this share')
    ap.add_argument('--min-regions', type=int, default=8, help='write no sidecar with fewer regions left')
    ap.add_argument('--max-sets', type=int, default=256, help='distinct column sets after merging adjacent zones')
    ap.add_argument('--reuse-raster', action='store_true', help='reuse work/vis-<id>.npy from an earlier run with the same regions')
    a = ap.parse_args()
    exe = executable(ROOT / 'build/o3ds-ovis/pvs_raster')
    src = ROOT / 'tools/pvs/pvs_raster.cpp'
    if not exe.exists() or exe.stat().st_mtime < src.stat().st_mtime:
        exe.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(compile_command(host_cxx(), '-O3', '-std=c++17', '-o', str(exe), str(src)), check=True)
    a.out.mkdir(parents=True, exist_ok=True)
    work = a.out.parent / 'work'
    work.mkdir(exist_ok=True)
    catalog = json.loads((a.models / 'course-catalog.json').read_text())['courses']
    want = {int(x) for x in a.courses.split(',') if x}
    report_path = a.out.parent / 'report.json'
    report = json.loads(report_path.read_text())['courses'] if report_path.exists() else []
    for course in catalog:
        if course.get('kind') != 'race' or (want and course['id'] not in want):
            continue
        t = time.time()
        r = build_course(course, a, exe, work)
        r['seconds'] = round(time.time() - t, 1)
        report = [x for x in report if x['id'] != r['id']] + [r]
        print(json.dumps(r), flush=True)
        report_path.write_text(json.dumps(dict(
            params={k: (str(v) if isinstance(v, Path) else v) for k, v in vars(a).items()}, courses=report),
            indent=1) + '\n')


if __name__ == '__main__':
    main()
