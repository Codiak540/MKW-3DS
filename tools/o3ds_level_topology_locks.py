#!/usr/bin/env python3
"""Merge topology locks into the level lock file: every vertex on an edge that
is not shared by exactly two triangles across the whole model (open borders,
non-manifold fins, joins between materials) stays put, so reduction can never
open a crack. Usage: o3ds_level_topology_locks.py LISTING.json ROADS.json OUT.json"""
import json
import sys

import numpy as np
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from o3ds_lowpoly_io import read_gmdl  # noqa: E402


def topology_locks(path):
    g = read_gmdl(path)
    pos = [g.records[i, 0:12].tobytes() for i in range(len(g.records))]
    tri = g.indices.reshape(-1, 3)
    use = defaultdict(int)
    for a, b, c in tri:
        if len({pos[a], pos[b], pos[c]}) < 3:
            continue
        for x, y in ((a, b), (b, c), (c, a)):
            use[(min(pos[x], pos[y]), max(pos[x], pos[y]))] += 1
    fixed = {p for e, n in use.items() if n != 2 for p in e}
    if FREE_BORDERS:
        # Borders of solid (non-alpha) geometry that touch nothing may slide.
        from o3ds_free_borders import free_border_records
        tri = g.indices.reshape(-1, 3)
        alpha = np.zeros(len(pos), bool)
        for m in g.submeshes:
            if m.flags & 3:
                alpha[g.indices[m.first:m.first + m.count]] = True
        free = {pos[i] for i in free_border_records(g)}
        free -= {pos[i] for i in np.nonzero(alpha)[0]}
        nonmanifold = {p for e, n in use.items() if n > 2 for p in e}
        fixed = (fixed - free) | nonmanifold
    return [i for i, p in enumerate(pos) if p in fixed]


FREE_BORDERS = '--free-borders' in sys.argv


def lossless_flat(path, plane_eps=0.02, attr_eps=1e-4, colour_eps=1.0):
    """Vertices whose removal cannot change the picture: every triangle around
    them (whole model, welded by position) is in one material and one plane,
    with UVs, vertex colours and normals affine across that plane."""
    g = read_gmdl(path)
    rec = g.records
    f = rec[:, :40].copy().view('<f4').reshape(-1, 10).astype(np.float64)
    P, N, UV = f[:, 0:3], f[:, 3:6], f[:, 6:10]
    C = rec[:, 40:44].astype(np.float64)
    tri = g.indices.reshape(-1, 3)
    sub = np.zeros(len(tri), int)
    for s_, m in enumerate(g.submeshes):
        sub[m.first // 3:(m.first + m.count) // 3] = s_
    key = [rec[i, 0:12].tobytes() for i in range(len(rec))]
    ring = defaultdict(list)
    for t, rs in enumerate(tri):
        if len({key[r] for r in rs}) < 3:
            continue
        for r in rs:
            ring[key[r]].append(t)
    ok = set()
    for k, ts in ring.items():
        if len(ts) < 3 or len({sub[t] for t in ts}) != 1:
            continue
        rs = np.unique(tri[ts].ravel())
        pts = P[rs]
        n = np.cross(P[tri[ts[0], 1]] - P[tri[ts[0], 0]], P[tri[ts[0], 2]] - P[tri[ts[0], 0]])
        if np.linalg.norm(n) < 1e-9:
            continue
        n /= np.linalg.norm(n)
        if np.abs((pts - pts[0]) @ n).max() > plane_eps:
            continue
        # The same orientation everywhere (no folded-over fan).
        tn = np.cross(P[tri[ts, 1]] - P[tri[ts, 0]], P[tri[ts, 2]] - P[tri[ts, 0]])
        if (tn @ n <= 0).any():
            continue
        e1 = pts[1] - pts[0] if np.linalg.norm(pts[1] - pts[0]) > 1e-9 else pts[2] - pts[0]
        e1 = e1 - n * (e1 @ n)
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(n, e1)
        X = np.c_[(pts - pts[0]) @ e1, (pts - pts[0]) @ e2, np.ones(len(pts))]
        good = True
        for attr, eps in ((UV[rs], attr_eps), (C[rs], colour_eps), (N[rs], attr_eps)):
            coef, *_ = np.linalg.lstsq(X, attr, rcond=None)
            if np.abs(X @ coef - attr).max() > eps:
                good = False
                break
        if good:
            ok.add(k)
    return {i for i, k in enumerate(key) if k in ok}


def main():
    argv = [x for x in sys.argv if x != '--free-borders']
    listing = json.loads(Path(argv[1]).read_text())
    roads = json.loads(Path(argv[2]).read_text())
    # Every drivable surface (road and off-road) stays exact. Freeing off-road
    # was measured: few triangles saved, hills moved up to hundreds of units.
    locks = roads['locks']
    total = 0
    freed = 0
    for rel, src in listing.items():
        if not rel.endswith('.gmdl'):
            continue
        extra = set(topology_locks(src))
        total += len(extra)
        # Drivable surfaces stay byte-exact. (lossless_flat() finds flat, affinely
        # mapped road interiors; measured at under 100 vertices in all levels, so
        # it is not used and every road triangle stays verifiable as authored.)
        road = set(locks.get(rel, []))
        locks[rel] = sorted(road | extra)
    roads['topologyLocks'] = True
    roads['freeBorders'] = FREE_BORDERS
    Path(argv[3]).write_text(json.dumps(roads))
    print('topology-locked vertices', total, 'models', len(locks))


if __name__ == '__main__':
    main()
