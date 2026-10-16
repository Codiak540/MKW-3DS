#!/usr/bin/env python3
"""Piece-survival guard for Old 3DS level sidecars.

Every connected piece of an authored model (a flag pole, a fence post, a
bunting string) must survive reduction at nearly its full extent. A piece that
vanished or shrank is locked in the lock file, so the next cook keeps it as
authored. Usage:
  o3ds_piece_guard.py LISTING.json SIDECARS_DIR LOCKS.json [--min-extent 0.85] [--min-piece 0]
--min-piece ignores pieces smaller than that share of the whole model (bolts,
rivets on far-away opponents' karts).
Exit code 1 when locks were added (cook again), 0 when every piece survived.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from o3ds_lowpoly_io import read_gmdl, read_variant  # noqa: E402


def pieces(model):
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
    return np.array([find(i) for i in range(len(pid))])[ids]


def failing_pieces(orig, cand, remap, min_extent, min_piece=0.0):
    root = pieces(orig)
    P = orig.positions
    whole = np.linalg.norm(P.max(0) - P.min(0)) if len(P) else 0.0
    used = np.zeros(len(P), bool)
    T = remap[cand.indices.reshape(-1, 3)]
    good = (T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])
    used[T[good].ravel()] = True
    bad = []
    for r in np.unique(root):
        members = np.nonzero(root == r)[0]
        ext = np.linalg.norm(P[members].max(0) - P[members].min(0))
        if ext < 1e-6 or ext < min_piece * whole:
            continue
        kept = members[used[members]]
        kept_ext = np.linalg.norm(P[kept].max(0) - P[kept].min(0)) if len(kept) else 0.0
        if kept_ext < min_extent * ext:
            bad.append(members)
    return np.concatenate(bad) if bad else np.zeros(0, np.int64)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('listing', type=Path)
    ap.add_argument('sidecars', type=Path)
    ap.add_argument('locks', type=Path)
    ap.add_argument('--min-extent', type=float, default=0.85)
    ap.add_argument('--min-piece', type=float, default=0.0)
    a = ap.parse_args()
    listing = json.loads(a.listing.read_text())
    locks_doc = json.loads(a.locks.read_text())
    locks = locks_doc['locks']
    added = 0
    models = 0
    for rel, src in sorted(listing.items()):
        side = a.sidecars / Path(rel).name
        if not rel.endswith('.gmdl') or not side.exists():
            continue
        orig = read_gmdl(src)
        cand, remap, _ = read_variant(side)
        bad = failing_pieces(orig, cand, remap, a.min_extent, a.min_piece)
        if len(bad):
            before = set(locks.get(rel, []))
            after = before | set(int(x) for x in bad)
            if len(after) > len(before):
                locks[rel] = sorted(after)
                added += len(after) - len(before)
                models += 1
                print(f'PIECE {rel}: {len(bad)} vertices of vanished/shrunk pieces locked')
    a.locks.write_text(json.dumps(locks_doc))
    print(f'locked {added} more vertices in {models} models')
    return 1 if added else 0


if __name__ == '__main__':
    sys.exit(main())
