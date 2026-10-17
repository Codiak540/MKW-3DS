#!/usr/bin/env python3
"""Check reduced character meshes for newly open geometric boundaries.

This supplements textured and animated review. Coincident source positions
are welded only for measurement; model and animation files are never changed.
Original open surfaces (for example facial cards) are allowed. An entirely
removed component or a boundary bridging two old boundary points still needs
visual review; this test is not an aesthetic approval.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

import numpy as np

from preview_o3ds_geometry import read_model, validate_pair


def boundary_edges(triangles):
    edges = Counter()
    for triangle in triangles:
        if len(set(triangle)) != 3:
            continue
        for a, b in zip(triangle, np.roll(triangle, -1)):
            edges[tuple(sorted((int(a), int(b))))] += 1
    return {edge for edge, count in edges.items() if count == 1}


def inspect(original, candidate):
    validate_pair(original, candidate)
    extent = float(np.ptp(original.position, axis=0).max())
    tolerance = max(1e-6, extent * 1e-6)
    positions = np.rint(original.position / tolerance).astype(np.int64)
    _, point_ids = np.unique(positions, axis=0, return_inverse=True)
    mapped = candidate.indices
    if candidate.source_indices is not None:
        mapped = candidate.source_indices[mapped]
    old = boundary_edges(point_ids[original.indices].reshape(-1, 3))
    new = boundary_edges(point_ids[mapped].reshape(-1, 3))
    old_points = {point for edge in old for point in edge}
    exposed = sorted({point for edge in new for point in edge} - old_points)
    return {
        'originalTriangles': original.triangle_count,
        'candidateTriangles': candidate.triangle_count,
        'originalBoundaryEdges': len(old),
        'candidateBoundaryEdges': len(new),
        'newlyExposedInteriorPoints': len(exposed),
        'status': 'new-open-boundary' if exposed else 'pass-boundary-screen',
        'visualApproval': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--candidates', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = {}
    for bank in sorted((args.assets / 'models').glob('*.dskm')):
        original = bank.with_suffix('.gmdl')
        candidate = args.candidates / 'models/o3ds' / original.name
        if original.is_file() and candidate.is_file():
            rows[original.name] = inspect(read_model(original), read_model(candidate))
    report = {'models': rows, 'checked': len(rows),
              'newOpenBoundaryModels': sum(row['newlyExposedInteriorPoints'] > 0 for row in rows.values()),
              'method': 'coincident-position edge incidence; original open boundaries allowed',
              'visualApproval': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'models'}))
    if report['newOpenBoundaryModels']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
