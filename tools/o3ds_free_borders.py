#!/usr/bin/env python3
"""Border vertices that touch nothing else (Old 3DS level cook).

An open border vertex whose position is farther than --gap units from every
triangle of the model that is not around it can slide along its own border
without opening a visible gap. Everything else on a border stays locked.
"""
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from o3ds_lowpoly_io import read_gmdl  # noqa: E402
from cook_o3ds_lowpoly_characters import closest_point_triangles  # noqa: E402


def border_vertices(g):
    key = [g.records[i, 0:12].tobytes() for i in range(len(g.records))]
    pid = {}
    ids = np.array([pid.setdefault(k, len(pid)) for k in key])
    tri = ids[g.indices.reshape(-1, 3)]
    use = defaultdict(int)
    for a, b, c in tri:
        if len({a, b, c}) < 3:
            continue
        for x, y in ((a, b), (b, c), (c, a)):
            use[(min(x, y), max(x, y))] += 1
    border = {p for e, n in use.items() if n == 1 for p in e}
    nonmanifold = {p for e, n in use.items() if n > 2 for p in e}
    return ids, tri, border, nonmanifold


def free_border_records(g, gap=4.0, chunk=256):
    ids, tri, border, nonmanifold = border_vertices(g)
    P = np.zeros((ids.max() + 1, 3))
    P[ids] = g.positions
    candidates = sorted(border - nonmanifold)
    if not candidates:
        return set()
    vt = defaultdict(set)
    for t, (a, b, c) in enumerate(tri):
        for x in (a, b, c):
            vt[x].add(t)
    A, B, C = P[tri[:, 0]], P[tri[:, 1]], P[tri[:, 2]]
    lo = np.minimum(np.minimum(A, B), C) - gap
    hi = np.maximum(np.maximum(A, B), C) + gap
    free = set()
    for s in range(0, len(candidates), chunk):
        vs = candidates[s:s + chunk]
        q = P[vs]
        near = ((lo[None] <= q[:, None]) & (hi[None] >= q[:, None])).all(2)
        for i, v in enumerate(vs):
            ts = np.nonzero(near[i])[0]
            # Ignore the triangles of the vertex's own fan and their neighbours.
            own = set()
            for t in vt[v]:
                own.update(vt[tri[t, 0]] | vt[tri[t, 1]] | vt[tri[t, 2]])
            ts = np.array([t for t in ts if t not in own], np.int64)
            if len(ts) == 0:
                free.add(v)
                continue
            d2, _ = closest_point_triangles(q[i:i + 1], A[ts], B[ts], C[ts])
            if np.sqrt(d2.min()) > gap:
                free.add(v)
    return {i for i, p in enumerate(ids) if p in free}


if __name__ == '__main__':
    for path in sys.argv[1:]:
        g = read_gmdl(path)
        ids, tri, border, nm = border_vertices(g)
        free = free_border_records(g)
        fp = {ids[i] for i in free}
        print(Path(path).name, 'border positions', len(border), 'free', len(fp))
