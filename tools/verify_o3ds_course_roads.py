#!/usr/bin/env python3
"""Check that every drivable (road-locked) course triangle survives unchanged
in its Old 3DS sidecar: same three authored positions, UVs and material."""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from o3ds_lowpoly_io import read_gmdl, read_variant  # noqa: E402


def keyed(records, tri, sub_of):
    out = set()
    for t, rs in enumerate(tri):
        corners = sorted(records[r, 0:32].tobytes() for r in rs)
        out.add((sub_of[t],) + tuple(corners))
    return out


def main():
    roads = json.loads(Path(sys.argv[1]).read_text())
    assets, sidecars = Path(sys.argv[2]), Path(sys.argv[3])
    bad = 0
    total = 0
    for rel, locks in sorted(roads['locks'].items()):
        side = sidecars / 'models' / 'o3ds' / Path(rel).name
        if not side.exists():
            continue
        g = read_gmdl(assets / rel)
        v, remap, _ = read_variant(side)
        lock = np.zeros(g.header[1], bool)
        lock[locks] = True
        tri = g.indices.reshape(-1, 3)
        sub = np.zeros(len(tri), int)
        for s, m in enumerate(g.submeshes):
            sub[m.first // 3:(m.first + m.count) // 3] = s
        # Zero-area triangles (a repeated vertex) draw nothing; dropping them is lossless.
        road = lock[tri].all(1) & (tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 0] != tri[:, 2])
        want = keyed(g.records, tri[road], sub[road])
        vt = v.indices.reshape(-1, 3)
        vsub = np.zeros(len(vt), int)
        for s, m in enumerate(v.submeshes):
            vsub[m.first // 3:(m.first + m.count) // 3] = s
        have = keyed(v.records, vt, vsub)
        missing = want - have
        total += len(want)
        bad += len(missing)
        print(f'{rel}: road {len(want)} missing {len(missing)}')
    print(f'TOTAL road triangles {total}, missing {bad}')
    if len(sys.argv) > 4:
        # Off-road: every authored off-road vertex within --offroad units of the reduced surface.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from cook_o3ds_lowpoly_characters import closest_point_triangles
        tol = float(sys.argv[4])
        failing = []
        for rel, locks in sorted(roads['locks'].items()):
            side = sidecars / 'models' / 'o3ds' / Path(rel).name
            if not side.exists():
                continue
            off = sorted(set(locks) - set(roads['roadOnly'].get(rel, [])))
            if not off:
                continue
            g = read_gmdl(assets / rel)
            v, _, _ = read_variant(side)
            P = v.positions
            T = v.indices.reshape(-1, 3)
            pts = np.unique(g.positions[off], axis=0)
            worst = 0.0
            for s in range(0, len(pts), 256):
                q = pts[s:s + 256]
                # Candidate triangles near these points only.
                lo, hi = q.min(0) - 200, q.max(0) + 200
                near = ((P[T] >= lo) & (P[T] <= hi)).all(2).any(1)
                if not near.any():
                    worst = np.inf
                    break
                Tn = T[near]
                d2, _ = closest_point_triangles(q, P[Tn[:, 0]], P[Tn[:, 1]], P[Tn[:, 2]])
                worst = max(worst, float(np.sqrt(d2.min(1)).max()))
            status = 'ok' if worst <= tol else 'TOO FAR'
            if worst > tol:
                failing.append(Path(rel).stem)
            print(f'{rel}: off-road vertices {len(pts)}, worst deviation {worst:.1f} ({status})')
        print('OFFROAD_FAILING', ' '.join(failing))
        bad += len(failing)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
