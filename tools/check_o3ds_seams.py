#!/usr/bin/env python3
"""Seam audit for Old 3DS level sidecars.

For every models/o3ds/<name>.gmdl with an original, counts edges that the reduced
mesh introduces and the authored mesh does not have:
  open  - an edge used by one triangle only (a crack or hole you can see through)
  uv    - a point where two triangles sharing an edge disagree on UVs although
          the authored mesh mapped that point continuously (a texture tear)
Edges are compared by exact authored positions, so a clean result means every
open border and texture seam in the reduced level already existed in the Wii level.
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from o3ds_lowpoly_io import read_gmdl, read_variant  # noqa: E402


def edges(model):
    rec = model.records
    pos = [rec[i, 0:12].tobytes() for i in range(len(rec))]
    uv = [rec[i, 24:32].tobytes() for i in range(len(rec))]
    tri = model.indices.reshape(-1, 3)
    sub = np.zeros(len(tri), int)
    for s, m in enumerate(model.submeshes):
        sub[m.first // 3:(m.first + m.count) // 3] = s
    use = defaultdict(list)
    for t, (a, b, c) in enumerate(tri):
        if len({pos[a], pos[b], pos[c]}) < 3:
            continue  # zero-area triangles draw nothing
        for x, y in ((a, b), (b, c), (c, a)):
            key = (min(pos[x], pos[y]), max(pos[x], pos[y]))
            use[key].append((sub[t], {pos[x]: uv[x], pos[y]: uv[y]}))
    open_edges = {k for k, v in use.items() if len(v) == 1}
    # Distinct UVs each position has inside each material.
    uvs = defaultdict(set)
    for t, rs in enumerate(tri):
        for r in rs:
            uvs[(sub[t], pos[r])].add(uv[r])
    # (material, position) points where two triangles sharing an edge disagree on UV.
    seams = set()
    for k, v in use.items():
        if len(v) == 2 and v[0][0] == v[1][0]:
            for p in k:
                if v[0][1][p] != v[1][1][p]:
                    seams.add((v[0][0], p))
    return open_edges, seams, uvs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('originals', type=Path, help='directory with original .gmdl files')
    ap.add_argument('sidecars', type=Path, help='models/o3ds directory')
    ap.add_argument('--match', default='', help='only names containing this text')
    ap.add_argument('--names', type=Path, help='file listing the .gmdl names to audit')
    ap.add_argument('--json', type=Path)
    ap.add_argument('--allow-free-borders', action='store_true',
                    help='accept changed open edges whose ends are free (touch-nothing) borders')
    a = ap.parse_args()
    names = set(a.names.read_text().split()) if a.names else None
    report = {}
    bad = 0
    for side in sorted(a.sidecars.glob('*.gmdl')):
        if a.match not in side.name or (names is not None and side.name not in names):
            continue
        orig = a.originals / side.name
        if not orig.exists():
            continue
        og = read_gmdl(orig)
        o_open, o_seam, o_uvs = edges(og)
        v, _, _ = read_variant(side)
        c_open, c_seam, _ = edges(v)
        opened = c_open - o_open
        if a.allow_free_borders and opened:
            # A border that touches nothing may slide along itself: no gap can show.
            from o3ds_free_borders import free_border_records
            alpha = set()
            for m in og.submeshes:
                if m.flags & 3:
                    alpha.update(int(i) for i in og.indices[m.first:m.first + m.count])
            free = {og.records[i, 0:12].tobytes() for i in free_border_records(og) if i not in alpha}
            opened = {e for e in opened if not (e[0] in free and e[1] in free)}
        new_open = len(opened)
        # A tear: a UV break at a point the authored level mapped continuously
        # (simplifying an authored seam along itself is not a tear).
        new_seam = sum(1 for point in c_seam - o_seam if len(o_uvs[point]) < 2)
        report[side.name] = {'newOpenEdges': new_open, 'newUvSeams': new_seam}
        if new_open or new_seam:
            bad += 1
            print(f'SEAM {side.name}: {new_open} new open edges, {new_seam} new UV seams')
    print(f'audited {len(report)} models; {bad} with new seams')
    if a.json:
        a.json.write_text(json.dumps(report, indent=1))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
