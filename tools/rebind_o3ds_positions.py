#!/usr/bin/env python3
"""Rebind a private Wii-derived geometry prototype to its original DSKM.

This is an offline experiment. It does not install assets or authorize runtime
loading. Original sampled palettes are preferred over fitted transforms. The
entire clip/PAT/hair/UV/ROOT payload is copied byte for byte after the compacted
source vertices and shape ranges. No DS assets are read by this tool.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct

import numpy as np

from audit_skinning import parse_bank
from build_o3ds_character_regions import binding_fingerprint
from preview_o3ds_geometry import VERTEX, read_model

SOURCE = np.dtype([('p', '<f4', 3), ('n', '<f4', 3), ('matrix', '<u4')])


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normalize(vectors):
    lengths = np.linalg.norm(vectors, axis=-1, keepdims=True)
    require(np.isfinite(lengths).all() and (lengths > 1e-12).all(), 'Zero/nonfinite normal')
    return vectors / lengths


def bank_layout(data):
    """Validate the complete existing format, then expose bounded frame views."""
    summary = parse_bank(data)
    require(summary['format'] == 'DSKM', 'Only authored driver banks are supported')
    vertices, matrices, shapes = (summary[k] for k in ('vertices', 'matrices', 'shapes'))
    start = 32 if summary['version'] == 5 else 28
    sources = np.frombuffer(data, dtype=SOURCE, count=vertices, offset=start)
    require(np.isfinite(sources['p']).all() and np.isfinite(sources['n']).all(), 'Nonfinite source vertex')
    ranges_at = start + vertices * SOURCE.itemsize
    ranges = np.frombuffer(data, dtype='<u4', count=shapes * 2, offset=ranges_at).reshape(-1, 2)
    tail = ranges_at + shapes * 8
    cursor = tail

    def clip(stride, kind):
        nonlocal cursor
        name = data[cursor:cursor + 32].split(b'\0')[0].decode('ascii')
        duration, loop, count = struct.unpack_from('<3I', data, cursor + 32)
        cursor += 44
        item = {'name': name, 'duration': duration, 'loop': loop, 'count': count}
        if kind == 'primary':
            item['frames'] = np.ndarray((count, matrices, 21), '<f4', data,
                                       offset=cursor, strides=(stride, 84, 4))
            require(np.isfinite(item['frames']).all(), 'Nonfinite authored pose matrix')
        elif kind == 'uv':
            require(np.isfinite(np.frombuffer(data, '<f4', count=count * shapes * 6,
                                             offset=cursor)).all(), 'Nonfinite authored UV animation')
        cursor += count * stride
        return item

    clips = [clip(matrices * 84 + shapes * 4, 'primary') for _ in range(summary['clips'])]
    faces = struct.unpack_from('<I', data, 24)[0]
    for _ in range(faces):
        clip(shapes * 4, 'pat')
    hair_nodes, hair_clips = [], []
    if summary['version'] >= 3:
        count, clip_count = struct.unpack_from('<2I', data, cursor + 4)
        cursor += 12
        hair_nodes = [struct.unpack_from('<2I', data, cursor + i * 8) for i in range(count)]
        cursor += count * 8
        for _ in range(clip_count):
            name = data[cursor:cursor + 32].split(b'\0')[0].decode('ascii')
            duration, loop, frames = struct.unpack_from('<3I', data, cursor + 32)
            cursor += 44
            values = np.frombuffer(data, '<f4', count=(frames + 1) * count * 21,
                                   offset=cursor).reshape(frames + 1, count, 21)
            require(np.isfinite(values).all(), 'Nonfinite authored secondary hair matrix')
            hair_clips.append({'name': name, 'loop': loop, 'count': frames,
                               'inverseRest': values[0], 'frames': values[1:]})
            cursor += values.nbytes
    if summary['version'] >= 4:
        count = struct.unpack_from('<I', data, cursor + 4)[0]
        cursor += 8
        for _ in range(count):
            clip(shapes * 24, 'uv')
    if summary['version'] == 5 and summary['root_slot'] == -1:
        cursor += 8
        count = sum(c['count'] for c in clips) * 3
        require(np.isfinite(np.frombuffer(data, '<f4', count=count, offset=cursor)).all(),
                'Nonfinite authored root track')
        cursor += count * 4
    require(cursor == len(data), 'Unconsumed animation bytes')
    return {'summary': summary, 'start': start, 'sources': sources, 'ranges': ranges,
            'tail': tail, 'clips': clips, 'hairNodes': hair_nodes, 'hairClips': hair_clips}


def apply_pose(source, palette):
    selected = np.asarray(palette, dtype=np.float64)[source['matrix']]
    affine = selected[:, :12].reshape(-1, 3, 4)
    position = np.einsum('nij,nj->ni', affine[:, :, :3], source['p']) + affine[:, :, 3]
    normal = normalize(np.einsum('nij,nj->ni', selected[:, 12:].reshape(-1, 3, 3), source['n']))
    return position, normal


def residuals(source, target_p, target_n, palette):
    p, n = apply_pose(source, palette)
    return np.linalg.norm(p - target_p, axis=1), np.linalg.norm(n - normalize(target_n), axis=1)


def fit_palette(source, target_p, target_n, matrices, position_tolerance, normal_tolerance):
    """Explicit opt-in fallback: full-rank affine or strictly validated rigid fit."""
    out = np.zeros((matrices, 21), dtype=np.float64)
    methods = []
    for matrix in range(matrices):
        mask = source['matrix'] == matrix
        require(mask.any(), 'Cannot fit an unused matrix without an authored pose')
        p, q = source['p'][mask].astype(float), target_p[mask]
        pn, qn = normalize(source['n'][mask]), normalize(target_n[mask])
        centered_p, centered_q = p - p.mean(0), q - q.mean(0)
        extent = max(float(np.ptp(p, axis=0).max()), 1.0)
        covariance = centered_p.T @ centered_q + (extent * .1) ** 2 * pn.T @ qn
        u, _, vt = np.linalg.svd(covariance)
        rotation = vt.T @ u.T
        if np.linalg.det(rotation) < 0:
            vt[-1] *= -1
            rotation = vt.T @ u.T
        candidates = [('rigid-procrustes', rotation, q.mean(0) - rotation @ p.mean(0))]
        homogeneous = np.column_stack((p, np.ones(len(p))))
        affine, _, rank, _ = np.linalg.lstsq(homogeneous, q, rcond=None)
        if rank == 4:
            candidates.append(('full-rank-affine', affine[:3].T, affine[3]))
        selected = None
        for method, linear, translation in candidates:
            if not np.isfinite(linear).all() or np.linalg.cond(linear) > 1e5:
                continue
            normal = np.linalg.inv(linear).T
            pe = np.linalg.norm(p @ linear.T + translation - q, axis=1).max()
            ne = np.linalg.norm(normalize(pn @ normal.T) - qn, axis=1).max()
            if pe <= position_tolerance and ne <= normal_tolerance:
                selected = (method, linear, translation, normal)
                break
        require(selected is not None, f'No strictly valid transform fit for matrix {matrix}')
        method, linear, translation, normal = selected
        out[matrix, :12] = np.column_stack((linear, translation)).reshape(-1)
        out[matrix, 12:] = normal.reshape(-1)
        methods.append(method)
    return out, {'method': 'validated-fit', 'matrixMethods': methods}


def find_neutral_pose(layout, target_p, target_n, position_tolerance, normal_tolerance, allow_fit):
    sources, clips = layout['sources'], layout['clips']
    preferred = [('drive', 15), ('sel_wait', 0), ('wait', 0)]
    order, seen = [], set()
    for name, frame in preferred:
        for i, clip in enumerate(clips):
            if clip['name'] == name and frame < clip['count']:
                order.append((i, frame)); seen.add((i, frame))
    order.extend((i, 0) for i in range(len(clips)) if (i, 0) not in seen)
    seen.update(order)
    order.extend((i, frame) for i, clip in enumerate(clips) for frame in range(clip['count'])
                 if (i, frame) not in seen)
    # Bounded samples cheaply reject other poses. Accepted matrices still need
    # every original source vertex and normal to meet the strict residual gate.
    sample = np.unique(np.concatenate([np.flatnonzero(sources['matrix'] == m)[::max(1, int(np.sum(sources['matrix'] == m)) // 8)]
                                       for m in range(layout['summary']['matrices'])]))
    best = float('inf')
    for index, frame in order:
        palette = clips[index]['frames'][frame]
        pe, ne = residuals(sources[sample], target_p[sample], target_n[sample], palette)
        best = min(best, float(pe.max()))
        if pe.max() > position_tolerance or ne.max() > normal_tolerance:
            continue
        pe, ne = residuals(sources, target_p, target_n, palette)
        if pe.max() <= position_tolerance and ne.max() <= normal_tolerance:
            return palette.astype(float), {'method': 'original-authored-palette',
                                           'clip': clips[index]['name'], 'frame': frame}
    require(allow_fit, f'No original pose matches GMDL within tolerance (best sampled position error {best:g}); fitting requires --allow-fit')
    return fit_palette(sources, target_p, target_n, layout['summary']['matrices'], position_tolerance, normal_tolerance)


def pair_multiply(a, b):
    aa, ba = a[:12].reshape(3, 4), b[:12].reshape(3, 4)
    out = np.empty(21)
    out[:12] = np.column_stack((aa[:, :3] @ ba[:, :3], aa[:, :3] @ ba[:, 3] + aa[:, 3])).reshape(-1)
    out[12:] = (a[12:].reshape(3, 3) @ b[12:].reshape(3, 3)).reshape(-1)
    return out


def pair_inverse(pair):
    affine = pair[:12].reshape(3, 4)
    inverse = np.linalg.inv(affine[:, :3])
    return np.concatenate((np.column_stack((inverse, -inverse @ affine[:, 3])).reshape(-1),
                           np.linalg.inv(pair[12:].reshape(3, 3)).reshape(-1)))


def with_secondary_hair(layout, palette, clip_name, tick):
    waiting = clip_name == 'wait'
    if not waiting and clip_name not in ('drive', 'back', 'dash', 'drift_l', 'drift_r'):
        return palette, False
    name = 'pc_hair_wait' if waiting else 'pc_hair_drive'
    secondary = next((x for x in layout['hairClips'] if x['name'] == name), None)
    if secondary is None:
        return palette, False
    out = palette.copy()
    frame = tick % secondary['count'] if secondary['loop'] else min(tick, secondary['count'] - 1)
    for i, (matrix, parent) in enumerate(layout['hairNodes']):
        current = secondary['frames'][frame, i].astype(float)
        if waiting:
            out[matrix] = pair_multiply(out[parent], current)
        else:
            local = pair_multiply(pair_inverse(palette[parent]), palette[matrix])
            delta = pair_multiply(secondary['inverseRest'][i].astype(float), current)
            out[matrix] = pair_multiply(out[parent], pair_multiply(local, delta))
    require(np.isfinite(out).all(), 'Nonfinite composed secondary hair pose')
    return out, True


def animation_proof(layout, rebound, source_ids):
    original = layout['sources']
    results = []
    for clip in layout['clips']:
        bounds = np.array([[np.inf] * 3, [-np.inf] * 3])
        original_bounds = bounds.copy()
        largest_delta = 0.0
        composed = 0
        for frame in range(clip['count']):
            palette, used_hair = with_secondary_hair(layout, clip['frames'][frame].astype(float), clip['name'], frame)
            composed += used_hair
            old, _ = apply_pose(original, palette)
            new, _ = apply_pose(rebound, palette)
            require(np.isfinite(new).all(), 'Nonfinite animated candidate position')
            bounds[0] = np.minimum(bounds[0], new.min(0)); bounds[1] = np.maximum(bounds[1], new.max(0))
            original_bounds[0] = np.minimum(original_bounds[0], old.min(0)); original_bounds[1] = np.maximum(original_bounds[1], old.max(0))
            largest_delta = max(largest_delta, float(np.linalg.norm(new - old[source_ids], axis=1).max()))
        extent = max(float(np.max(original_bounds[1] - original_bounds[0])), 1.0)
        expansion = float(np.max(np.maximum(original_bounds[0] - bounds[0], bounds[1] - original_bounds[1])))
        require(expansion <= extent and largest_delta <= extent * 2,
                f'Gross animated bounds regression in {clip["name"]}')
        results.append({'clip': clip['name'], 'frames': clip['count'], 'secondaryHairFrames': composed,
                        'originalBounds': original_bounds.tolist(), 'candidateBounds': bounds.tolist(),
                        'maximumRetainedVertexDisplacement': largest_delta,
                        'maximumBoundsExpansion': max(0.0, expansion), 'allFinite': True})
    return {'clips': results, 'framesChecked': sum(c['frames'] for c in results),
            'fractionalFrameAndTransitionReview': 'not performed; requires animated visual review',
            'independentSecondaryHairPhases': 'not exhaustively sampled; hair tick equals primary frame',
            'boundsGate': 'reject expansion beyond original maximum extent or displacement above twice that extent',
            'visualApproval': False}


def rebind(original_path, bank_path, candidate_path, metadata, allow_fit=False, allow_split_vertices=False):
    original, candidate = read_model(original_path), read_model(candidate_path)
    bank_data = Path(bank_path).read_bytes()
    layout = bank_layout(bank_data)
    inputs = metadata.get('inputs', {})
    for key, actual in [('modelSha256', sha(original.data)), ('bankSha256', sha(bank_data))]:
        require(inputs.get(key) == actual, f'Input provenance mismatch: {key}')
    require(metadata.get('candidateSha256') == sha(candidate.data), 'Candidate provenance mismatch')
    require(inputs.get('bindingFingerprint') == binding_fingerprint(bank_data), 'Binding fingerprint mismatch')
    require(original.source_indices is None, 'Original GMDL must be the unmodified authored model')
    require(candidate.source_indices is None, 'Position prototypes must be standalone GMDL, without O3RM')
    require(layout['summary']['vertices'] == len(original.position), 'Original model/bank vertex mismatch')
    require(candidate.version == original.version and candidate.texture_bytes == original.texture_bytes
            and candidate.materials == original.materials, 'Candidate changed material/texture layout')
    require(len(candidate.position) <= len(original.position) and len(candidate.indices) <= len(original.indices)
            and (len(candidate.position) < len(original.position) or len(candidate.indices) < len(original.indices)),
            'Candidate must reduce vertex or triangle count without increasing the other')
    source_ids = np.asarray(metadata.get('sourceVertexIds', []))
    require(source_ids.dtype.kind in 'iu' and source_ids.ndim == 1 and len(source_ids) == len(candidate.position), 'Invalid source vertex mapping')
    require(len(source_ids) > 0 and source_ids.min() >= 0 and source_ids.max() < len(original.position),
            'Source IDs must be in range')
    require(allow_split_vertices or np.all(np.diff(source_ids.astype(np.int64)) > 0),
            'Source IDs must be strictly increasing; split/reordered offline prototypes require --allow-split-vertices')
    source_ids = source_ids.astype(np.int64)
    sources = layout['sources']
    require(np.array_equal(metadata.get('sourceMatrixIds'), sources['matrix'][source_ids]), 'Candidate changed palette binding')
    groups = metadata.get('bindingGroups')
    expected_groups = [[i for i, (first, count) in enumerate(layout['ranges']) if first <= source < first + count]
                       for source in source_ids]
    require(groups == expected_groups, 'Candidate changed shape binding membership')
    require(all(groups), 'Candidate vertex has no authored shape membership')
    for (first, count), (new_first, new_count) in zip(original.submeshes, candidate.submeshes):
        require(not count or new_count > 0, 'Candidate removed an entire material span')
        require(new_count <= count and np.isin(source_ids[candidate.indices[new_first:new_first + new_count]],
                                               original.indices[first:first + count]).all(), 'Candidate crossed material membership')
    original_vertices = np.frombuffer(original.vertex_bytes, dtype=VERTEX)
    candidate_vertices = np.frombuffer(candidate.vertex_bytes, dtype=VERTEX)
    protected_shapes = metadata.get('faceShapes', [])
    require(isinstance(protected_shapes, list) and all(type(i) is int and 0 <= i < len(layout['ranges'])
                                                     for i in protected_shapes), 'Invalid protected facial shape IDs')
    protected_vertices = np.zeros(len(original.position), dtype=bool)
    for shape in protected_shapes:
        first, count = layout['ranges'][shape]
        protected_vertices[int(first):int(first) + int(count)] = True

    def protected_triangles(model, vertices, mapped):
        result = Counter()
        for material_index, ((first, count), material) in enumerate(zip(model.submeshes, model.materials)):
            alpha = bool(struct.unpack_from('<I', material, 4)[0] & 3)
            for triangle in model.indices[first:first + count].reshape(-1, 3):
                ids = triangle if mapped is None else mapped[triangle]
                if not alpha and not protected_vertices[ids].any():
                    continue
                values = tuple(vertices[i].tobytes() for i in triangle)
                # Cyclic reordering preserves the same oriented triangle.
                key = min(values, values[1:] + values[:1], values[2:] + values[:2])
                result[(material_index, key)] += 1
        return result

    old_protected = protected_triangles(original, original_vertices, None)
    new_protected = protected_triangles(candidate, candidate_vertices, source_ids)
    require(old_protected == new_protected,
            'Candidate changed protected facial/alpha triangles or their vertex attributes')
    extent = max(float(np.ptp(original.position, axis=0).max()), 1.0)
    position_tolerance, normal_tolerance = max(1e-5, extent * 1e-6), 1e-4
    palette, pose_info = find_neutral_pose(layout, original.position, original_vertices['n'].astype(float),
                                         position_tolerance, normal_tolerance, allow_fit)
    pe, ne = residuals(sources, original.position, original_vertices['n'].astype(float), palette)
    rebound = sources[source_ids].copy()
    changed_p = np.any(candidate_vertices['p'] != original_vertices['p'][source_ids], axis=1)
    changed_n = np.any(candidate_vertices['n'] != original_vertices['n'][source_ids], axis=1)
    bone_proofs = []
    for matrix in range(layout['summary']['matrices']):
        original_mask = sources['matrix'] == matrix
        mask = rebound['matrix'] == matrix
        affine = palette[matrix, :12].reshape(3, 4)
        normal = palette[matrix, 12:].reshape(3, 3)
        require(np.linalg.cond(affine[:, :3]) < 1e5 and np.linalg.cond(normal) < 1e5, 'Singular/ill-conditioned binding transform')
        pmask, nmask = mask & changed_p, mask & changed_n
        if pmask.any():
            rebound['p'][pmask] = (candidate.position[pmask] - affine[:, 3]) @ np.linalg.inv(affine[:, :3]).T
        if nmask.any():
            rebound['n'][nmask] = normalize(candidate_vertices['n'][nmask].astype(float) @ np.linalg.inv(normal).T)
        bone_proofs.append({'matrix': matrix, 'originalVertices': int(original_mask.sum()),
                            'candidateVertices': int(mask.sum()), 'maxPositionResidual': float(pe[original_mask].max()) if original_mask.any() else 0,
                            'maxNormalResidual': float(ne[original_mask].max()) if original_mask.any() else 0,
                            'positionCondition': float(np.linalg.cond(affine[:, :3]))})
    rpe, rne = residuals(rebound, candidate.position, candidate_vertices['n'].astype(float), palette)
    require(rpe.max() <= position_tolerance and rne.max() <= normal_tolerance, 'Float32 rebound failed round-trip residual gate')
    new_ranges = []
    for first, count in layout['ranges']:
        if allow_split_vertices:
            members = np.flatnonzero((source_ids >= first) & (source_ids < int(first) + int(count)))
            require(not len(members) or int(members[-1]) - int(members[0]) + 1 == len(members),
                    'Reordered/split vertices interleave authored shape ranges')
            new_ranges.append((int(members[0]) if len(members) else 0, len(members)))
        else:
            start, end = np.searchsorted(source_ids, [int(first), int(first) + int(count)])
            new_ranges.append((int(start), int(end - start)))
    header = bytearray(bank_data[:layout['start']])
    struct.pack_into('<I', header, 8, len(rebound))
    tail = bank_data[layout['tail']:]
    output = bytes(header) + rebound.tobytes() + b''.join(struct.pack('<2I', *r) for r in new_ranges) + tail
    checked = bank_layout(output)
    require(output[checked['tail']:] == tail, 'Animation payload changed')
    require(checked['summary']['vertices'] == len(candidate.position), 'Compacted bank/model count mismatch')
    animation = animation_proof(layout, rebound, source_ids)
    proof = {'version': 1, 'experiment': 'Wii-only modified-position offline rebind', 'runtimeReady': False,
             'originalModelSha256': sha(original.data), 'originalBankSha256': sha(bank_data),
             'candidateModelSha256': sha(candidate.data), 'candidateBankSha256': sha(output),
             'sourceBindingFingerprint': inputs['bindingFingerprint'], 'pose': pose_info,
             'positionTolerance': position_tolerance, 'normalTolerance': normal_tolerance,
             'matrixResiduals': bone_proofs, 'roundTripMaxPositionResidual': float(rpe.max()),
             'roundTripMaxNormalResidual': float(rne.max()), 'originalVertices': len(sources),
             'candidateVertices': len(rebound), 'modifiedPositions': int(changed_p.sum()),
             'allowSplitVertices': allow_split_vertices, 'duplicateSourceBindings': len(source_ids) - len(np.unique(source_ids)),
             'sourceIdsStrictlyIncreasing': bool(np.all(np.diff(source_ids) > 0)),
             'modifiedNormals': int(changed_n.sum()), 'originalShapeRanges': layout['ranges'].tolist(),
             'candidateShapeRanges': new_ranges, 'preservedPayloadSha256': sha(tail),
             'protectedFacialShapes': protected_shapes, 'protectedFacialAndAlphaTriangles': sum(old_protected.values()),
             'clipPatHairUvRootPayloadByteExact': True, 'animation': animation, 'visualApproval': False}
    return output, proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, help='Defaults to candidate path with .json suffix')
    parser.add_argument('--original', type=Path, help='Defaults to metadata inputs.model')
    parser.add_argument('--bank', type=Path, help='Defaults to metadata inputs.bank')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--proof', type=Path, help='Defaults to output path with .rebind.json suffix')
    parser.add_argument('--allow-fit', action='store_true', help='Permit strictly verified rigid/affine fit if no authored sampled pose matches')
    parser.add_argument('--allow-split-vertices', action='store_true',
                        help='Offline atlas previews: permit duplicate/reordered source bindings only with contiguous original shape membership')
    args = parser.parse_args()
    metadata_path = args.metadata or args.candidate.with_suffix('.json')
    metadata = json.loads(metadata_path.read_text())
    original = args.original or Path(metadata['inputs']['model'])
    bank = args.bank or Path(metadata['inputs']['bank'])
    proof_path = args.proof or args.output.with_suffix('.rebind.json')
    protected = {p.resolve() for p in (original, bank, args.candidate, metadata_path)}
    require(args.output.resolve() not in protected and proof_path.resolve() not in protected,
            'Output must not overwrite original inputs or prototype geometry')
    require(args.output.resolve() != proof_path.resolve(), 'Bank/proof outputs must differ')
    output, proof = rebind(original, bank, args.candidate, metadata, args.allow_fit, args.allow_split_vertices)
    for path in (args.output, proof_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)
    proof_path.write_text(json.dumps(proof, indent=2) + '\n')
    print(json.dumps({'bank': str(args.output), 'proof': str(proof_path), 'vertices': proof['candidateVertices'],
                      'pose': proof['pose'], 'framesChecked': proof['animation']['framesChecked'], 'runtimeReady': False}))


if __name__ == '__main__':
    main()
