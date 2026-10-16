#!/usr/bin/env python3
"""Find the course model vertices that lie on drivable collision (Old 3DS course cook).

Every visual triangle that sits on a drivable KCL surface (road, off-road, boost,
ramps, half-pipes, moving/rotating roads) keeps all of its vertices locked, so the
reduced course draws exactly the authored road you drive on. Physics never reads
these models; this protects how driving looks and feels.

Writes --output JSON: {"locks": {"models/<course>.gmdl": [vertex ids]}, "courses": {...stats}}.
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from o3ds_kcl import read_kcl, DRIVABLE, ROAD  # noqa: E402
from o3ds_lowpoly_io import read_gmdl  # noqa: E402
from cook_o3ds_lowpoly_characters import closest_point_triangles  # noqa: E402

CELL = 1500.0


class Grid:
    def __init__(self, a, b, c):
        self.a, self.b, self.c = a, b, c
        lo = np.minimum(np.minimum(a, b), c)
        hi = np.maximum(np.maximum(a, b), c)
        self.cells = defaultdict(list)
        for i, (l, h) in enumerate(zip(np.floor(lo / CELL).astype(int), np.floor(hi / CELL).astype(int))):
            for x in range(l[0], h[0] + 1):
                for y in range(l[1], h[1] + 1):
                    for z in range(l[2], h[2] + 1):
                        self.cells[(x, y, z)].append(i)

    def nearest(self, points):
        """Distance and triangle index of the nearest drivable triangle (within a cell)."""
        dist = np.full(len(points), np.inf)
        best = np.full(len(points), -1)
        keys = np.floor(points / CELL).astype(int)
        groups = defaultdict(list)
        for i, k in enumerate(map(tuple, keys)):
            groups[k].append(i)
        for k, idx in groups.items():
            cand = set()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        cand.update(self.cells.get((k[0] + dx, k[1] + dy, k[2] + dz), ()))
            if not cand:
                continue
            cand = np.array(sorted(cand))
            idx = np.array(idx)
            for s in range(0, len(idx), 2048):
                part = idx[s:s + 2048]
                d2, _ = closest_point_triangles(points[part], self.a[cand], self.b[cand], self.c[cand])
                j = d2.argmin(1)
                dist[part] = np.sqrt(d2[np.arange(len(part)), j])
                best[part] = cand[j]
        return dist, best


def road_locks(gmdl_path, kcl, tolerance, normal_dot, types=DRIVABLE):
    g = read_gmdl(gmdl_path)
    pos = g.positions
    tri = g.indices.reshape(-1, 3)
    keep = np.isin(kcl['type'], list(types))
    if not keep.any():
        return np.zeros(0, np.int64), 0, len(tri)
    grid = Grid(kcl['a'][keep], kcl['b'][keep], kcl['c'][keep])
    knormal = kcl['normal'][keep]
    # Query unique positions once.
    uniq, inverse = np.unique(pos, axis=0, return_inverse=True)
    dist, which = grid.nearest(uniq)
    vd, vw = dist[inverse.ravel()], which[inverse.ravel()]
    on_road = np.zeros(len(tri), bool)
    for t, (a, b, c) in enumerate(tri):
        if max(vd[a], vd[b], vd[c]) > tolerance:
            continue
        n = np.cross(pos[b] - pos[a], pos[c] - pos[a])
        length = np.linalg.norm(n)
        if length < 1e-9:
            continue
        n /= length
        # Visual and collision surfaces face the same way (either winding).
        agree = max(abs(n @ knormal[vw[x]]) for x in (a, b, c) if vw[x] >= 0)
        if agree >= normal_dot:
            on_road[t] = True
    locked = np.unique(tri[on_road].ravel())
    return locked, int(on_road.sum()), len(tri)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--assets', required=True, type=Path, help='staged package root (models/, course-catalog)')
    ap.add_argument('--sources', required=True, type=Path, help='authored assets/ root with course KCLs')
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--tolerance', type=float, default=80.0, help='max visual-to-collision distance (units)')
    ap.add_argument('--normal-dot', type=float, default=0.7)
    a = ap.parse_args()
    catalog = json.loads((a.assets / 'models' / 'course-catalog.json').read_text())
    out = {'version': 1, 'tolerance': a.tolerance, 'normalDot': a.normal_dot, 'locks': {}, 'roadOnly': {}, 'courses': {}}
    for course in catalog['courses']:
        kcl_path = a.sources / 'courses' / course['key'] / 'course.kcl'
        if not kcl_path.exists():
            out['courses'][course['key']] = {'skipped': 'no course.kcl'}
            continue
        kcl = read_kcl(kcl_path)
        main_list = a.assets / course['model_lists'][0]
        stats = {'id': course['id'], 'name': course['name'], 'models': {}}
        for name in main_list.read_text().split():
            rel = f'models/{name}.gmdl'
            locked, road, total = road_locks(a.assets / rel, kcl, a.tolerance, a.normal_dot)
            out['locks'][rel] = locked.tolist()
            # Road-type surfaces only (asphalt, boost, ramps, half-pipes): always exact.
            hard, hard_n, _ = road_locks(a.assets / rel, kcl, a.tolerance, a.normal_dot, ROAD)
            out['roadOnly'][rel] = hard.tolist()
            stats['models'][rel] = {'roadTriangles': road, 'roadTypeTriangles': hard_n, 'triangles': total,
                                    'lockedVertices': len(locked)}
        out['courses'][course['key']] = stats
        tot = sum(m['triangles'] for m in stats['models'].values())
        road = sum(m['roadTriangles'] for m in stats['models'].values())
        print(f"{course['id']:>3} {course['name']:<26} road {road:>6} / {tot:>6} triangles ({road / max(tot, 1):.0%})", flush=True)
    a.output.write_text(json.dumps(out))


if __name__ == '__main__':
    main()
