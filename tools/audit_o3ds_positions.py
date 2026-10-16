#!/usr/bin/env python3
"""Screen moved-position prototypes for newly opened geometric boundaries."""
import argparse
import json
from pathlib import Path

import numpy as np

from audit_o3ds_character_geometry import boundary_edges
from preview_o3ds_geometry import read_model
from preview_o3ds_positions import validate


def inspect(metadata, original, candidate):
    validate(metadata, original, candidate)
    epsilon = max(1e-6, float(np.ptp(original.position, axis=0).max()) * 1e-6)
    _, original_points = np.unique(np.rint(original.position / epsilon).astype(np.int64), axis=0, return_inverse=True)
    _, candidate_points = np.unique(np.rint(candidate.position / epsilon).astype(np.int64), axis=0, return_inverse=True)
    original_edges = boundary_edges(original_points[original.indices].reshape(-1, 3))
    candidate_edges = boundary_edges(candidate_points[candidate.indices].reshape(-1, 3))
    original_open = {point for edge in original_edges for point in edge}
    candidate_open = {point for edge in candidate_edges for point in edge}
    source_ids = np.asarray(metadata['sourceVertexIds'], dtype=np.int64)
    exposed = []
    for point in candidate_open:
        copies = np.flatnonzero(candidate_points == point)
        if any(int(original_points[source_ids[i]]) not in original_open for i in copies):
            exposed.append(int(point))
    return dict(originalBoundaryEdges=len(original_edges), candidateBoundaryEdges=len(candidate_edges),
                newlyExposedInteriorPositions=len(exposed),
                positionsModified=not np.array_equal(candidate.position, original.position[source_ids]), visualApproval=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('metadata', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text())
    report = inspect(metadata, read_model(metadata['inputs']['model']), read_model(args.metadata.with_suffix('.gmdl')))
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    if report['newlyExposedInteriorPositions']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
