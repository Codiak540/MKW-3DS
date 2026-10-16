#!/usr/bin/env python3
"""Surface audit for Old 3DS sidecars: how far reduced triangles stray from the
authored surface (spikes, fins, bridged gaps). Samples 7 points per reduced
triangle and measures the distance to the nearest authored triangle of the
same material. Reports the worst distance relative to the model's size."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from o3ds_lowpoly_io import read_gmdl, read_variant  # noqa: E402
from cook_o3ds_lowpoly_characters import closest_point_triangles  # noqa: E402

BARY = np.array([[1 / 3, 1 / 3, 1 / 3], [0.6, 0.2, 0.2], [0.2, 0.6, 0.2], [0.2, 0.2, 0.6],
                 [0.45, 0.45, 0.1], [0.1, 0.45, 0.45], [0.45, 0.1, 0.45]])


def tris_by_sub(model):
    out = {}
    for s, m in enumerate(model.submeshes):
        out[s] = model.indices[m.first:m.first + m.count].reshape(-1, 3)
    return out


def piece_sizes(model):
    """Bounding-box diagonal of the connected piece each record belongs to."""
    key = [model.records[i, 0:12].tobytes() for i in range(len(model.records))]
    pid = {}
    ids = np.array([pid.setdefault(k, len(pid)) for k in key])
    parent = list(range(len(pid)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b, c in ids[model.indices.reshape(-1, 3)]:
        parent[find(a)] = find(b)
        parent[find(c)] = find(b)
    root = np.array([find(i) for i in range(len(pid))])[ids]
    size = np.zeros(len(model.records))
    P = model.positions
    for r in np.unique(root):
        m = root == r
        size[m] = np.linalg.norm(P[m].max(0) - P[m].min(0))
    return size


def audit(orig, cand, remap=None, chunk=512):
    P0, P1 = orig.positions, cand.positions
    diag = float(np.linalg.norm(P0.max(0) - P0.min(0)))
    # Distances also judged against the authored piece each reduced triangle came
    # from: a pipe folded into a fin is small for the level, large for the pipe.
    piece = piece_sizes(orig)[remap] if remap is not None else None
    worst_piece = 0.0
    o, c = tris_by_sub(orig), tris_by_sub(cand)
    worst = 0.0
    for s, T in c.items():
        if len(T) == 0:
            continue
        To = o[s]
        A, B, C = P0[To[:, 0]], P0[To[:, 1]], P0[To[:, 2]]
        On = np.cross(B - A, C - A)
        On /= np.maximum(np.linalg.norm(On, axis=1, keepdims=True), 1e-12)
        Cn = np.cross(P1[T[:, 1]] - P1[T[:, 0]], P1[T[:, 2]] - P1[T[:, 0]])
        keep = np.linalg.norm(Cn, axis=1) > 1e-9
        T, Cn = T[keep], Cn[keep] / np.linalg.norm(Cn[keep], axis=1, keepdims=True)
        pts = np.einsum('kj,tjd->tkd', BARY, P1[T]).reshape(-1, 3)
        pn = np.repeat(Cn, len(BARY), axis=0)
        psize = np.repeat(piece[T].max(1), len(BARY)) if piece is not None else None
        for i in range(0, len(pts), chunk):
            q, qn = pts[i:i + chunk], pn[i:i + chunk]
            lo, hi = q.min(0) - diag * 0.05, q.max(0) + diag * 0.05
            near = ((np.minimum(np.minimum(A, B), C) <= hi) & (np.maximum(np.maximum(A, B), C) >= lo)).all(1)
            if not near.any():
                return float('inf'), diag
            d2, _ = closest_point_triangles(q, A[near], B[near], C[near])
            # Only authored surfaces facing the same way (either winding) count:
            # a fin passes near other parts but matches none of their orientations.
            facing = np.abs(qn @ On[near].T) > 0.5
            d2 = np.where(facing, d2, np.inf)
            d = np.sqrt(d2.min(1))
            worst = max(worst, float(d.max()))
            if psize is not None:
                worst_piece = max(worst_piece, float((d / np.maximum(psize[i:i + chunk], 1e-6)).max()))
    return worst, diag, worst_piece


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('originals', type=Path)
    ap.add_argument('sidecars', type=Path)
    ap.add_argument('--limit', type=float, default=0.02, help='worst distance / model size allowed')
    ap.add_argument('--piece-limit', type=float, default=0.15, help='worst distance / size of its own piece')
    ap.add_argument('--names', type=Path)
    ap.add_argument('--json', type=Path)
    a = ap.parse_args()
    names = set(a.names.read_text().split()) if a.names else None
    report, bad = {}, []
    for side in sorted(a.sidecars.glob('*.gmdl')):
        if names is not None and side.name not in names:
            continue
        orig = a.originals / side.name
        if not orig.exists():
            continue
        v, remap, _ = read_variant(side)
        worst, diag, piece = audit(read_gmdl(orig), v, remap)
        rel = worst / diag if diag else 0
        report[side.name] = {'worst': worst, 'relative': rel, 'pieceRelative': piece}
        if rel > a.limit or piece > a.piece_limit:
            bad.append(side.name)
            print(f'STRAY {side.name}: {worst:.1f} units ({rel:.1%} of model, {piece:.0%} of its piece)')
    print(f'audited {len(report)} models; {len(bad)} over {a.limit:.1%} of model or {a.piece_limit:.0%} of a piece')
    if a.json:
        a.json.write_text(json.dumps(report, indent=1))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
