#!/usr/bin/env python3
"""Package a Wii-only position/normal prototype as a diagnostic OPOS sidecar.

No runtime installation, marker, or release approval is performed. OPOS v1 keeps
UVs/colour source-exact and rejects duplicate source IDs. A future runtime must
validate the original bank first, retain original PAT ranges/submesh offsets,
and commit the model and source overrides together only after validation.
"""
import argparse
from collections import defaultdict
import copy
import json
from pathlib import Path
import struct
import tempfile
import zlib

import numpy as np

from rebind_o3ds_positions import SOURCE, bank_layout, rebind, require, residuals, sha
from preview_o3ds_geometry import VERTEX, read_model

MAX_MODEL = 10 * 1024 * 1024
MAX_BANK = 12 * 1024 * 1024
MAX_WRAPPER = 16 * 1024 * 1024


def crc(data):
    return zlib.crc32(data) & 0xffffffff


def bounded_read(path, limit):
    require(Path(path).stat().st_size <= limit, f'Input exceeds {limit} byte limit: {path}')
    with Path(path).open('rb') as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit, 'Input grew beyond limit while reading')
    return data


def canonicalize(candidate_path, metadata):
    """Sort unique source IDs and remap indices; preserve triangle/material order."""
    bounded_read(candidate_path, MAX_MODEL)
    candidate = read_model(candidate_path)
    require(candidate.source_indices is None, 'OPOS input must be raw GMDL without O3RM')
    require(metadata.get('candidateSha256') == sha(candidate.data), 'Candidate provenance mismatch')
    ids = np.asarray(metadata.get('sourceVertexIds', []))
    require(ids.ndim == 1 and ids.dtype.kind in 'iu' and len(ids) == len(candidate.position),
            'Invalid source ID array')
    require(len(np.unique(ids)) == len(ids),
            'Unsupported duplicated source IDs: OPOS v1 requires unique original bindings')
    require(len(ids) > 0 and ids.min() >= 0 and ids.max() < 65535, 'Source ID bounds')
    order = np.argsort(ids, kind='stable')
    inverse = np.empty(len(order), dtype=np.int64)
    inverse[order] = np.arange(len(order))
    vertex_start = len(candidate.data) - len(candidate.indices) * 2 - len(candidate.position) * 44
    vertices = np.frombuffer(candidate.vertex_bytes, VERTEX)
    result = (candidate.data[:vertex_start] + vertices[order].tobytes()
              + inverse[candidate.indices].astype('<u2').tobytes())
    out = copy.deepcopy(metadata)
    for key in ('sourceVertexIds', 'sourceMatrixIds', 'bindingGroups'):
        require(isinstance(out.get(key), list) and len(out[key]) == len(order), 'Invalid binding metadata: ' + key)
        out[key] = [out[key][int(i)] for i in order]
    for item in out.get('changedVertices', []):
        item['compactIndex'] = int(inverse[item['compactIndex']])
    for item in out.get('preservedOpaqueTriangles', []):
        if 'candidateIndices' in item:
            item['candidateIndices'] = [int(inverse[i]) for i in item['candidateIndices']]
    out['candidateSha256'] = sha(result)
    return result, out, [int(i) for i in order]


def lock_policy(original, candidate, original_layout, rebound, metadata):
    """Revalidate declared facial/cap/hard locks from actual records and bindings.

    This policy is authored metadata, not an inference from file contents. It is
    recorded independently in the receipt so the C++ validator never treats a
    candidate's self-declared protected-triangle count as the expected policy.
    """
    old_vertices = np.frombuffer(original.vertex_bytes, VERTEX)
    new_vertices = np.frombuffer(candidate.vertex_bytes, VERTEX)
    ids = metadata['sourceVertexIds']
    sources, ranges = original_layout['sources'], original_layout['ranges']
    groups = [tuple(i for i, (first, count) in enumerate(ranges) if first <= v < int(first) + int(count))
              for v in range(len(sources))]
    old_records = [old_vertices[i].tobytes() + sources[i].tobytes() + repr(groups[i]).encode()
                   for i in range(len(sources))]
    new_records = [new_vertices[i].tobytes() + rebound[i].tobytes() + repr(groups[ids[i]]).encode()
                   for i in range(len(rebound))]
    protected_vertices = np.zeros(len(sources), dtype=bool)
    face_shapes = metadata.get('faceShapes', [])
    require(isinstance(face_shapes, list) and all(type(i) is int and 0 <= i < len(ranges) for i in face_shapes),
            'Invalid protected facial shapes')
    for i in face_shapes:
        first, count = ranges[i]
        protected_vertices[int(first):int(first) + int(count)] = True
    expected = set()
    for material, ((first, count), raw) in enumerate(zip(original.submeshes, original.materials)):
        alpha = bool(struct.unpack_from('<I', raw, 4)[0] & 3)
        for triangle in range(first // 3, (first + count) // 3):
            if alpha or protected_vertices[original.indices[triangle * 3:triangle * 3 + 3]].any():
                expected.add(triangle)
    cap_pairs = {}
    for item in metadata.get('preservedOpaqueTriangles', []):
        material = item.get('material')
        require(type(material) is int and 0 <= material < len(original.submeshes), 'Invalid cap material')
        first, count = original.submeshes[material]
        local = item.get('sourceTriangle')
        require(type(local) is int and 0 <= local < count // 3, 'Invalid cap triangle')
        triangle = first // 3 + local
        require(item.get('sourceIndices') == original.indices[triangle * 3:triangle * 3 + 3].tolist(),
                'Cap policy source indices disagree with original')
        now = item.get('candidateTriangle')
        require(type(now) is int and 0 <= now < len(candidate.indices) // 3, 'Invalid cap candidate triangle')
        require(triangle not in cap_pairs, 'Duplicate cap triangle declaration')
        cap_pairs[triangle] = now
        expected.add(triangle)

    def key(records, indices, material):
        values = tuple(records[int(i)] for i in indices)
        return material, min(values, values[1:] + values[:1], values[2:] + values[:2])

    old_keys, new_keys = {}, {}
    for model, records, target in ((original, old_records, old_keys), (candidate, new_records, new_keys)):
        for material, (first, count) in enumerate(model.submeshes):
            for triangle in range(first // 3, (first + count) // 3):
                target[triangle] = key(records, model.indices[triangle * 3:triangle * 3 + 3], material)
    available = defaultdict(list)
    for triangle, value in new_keys.items():
        available[value].append(triangle)
    pairs, used = [], set()
    for triangle in sorted(expected):
        value = old_keys[triangle]
        if triangle in cap_pairs:
            now = cap_pairs[triangle]
            require(new_keys[now] == value and now not in used, 'Protected cap triangle bytes/binding changed')
        else:
            choices = available[value]
            while choices and choices[-1] in used:
                choices.pop()
            require(bool(choices), 'Protected facial/alpha triangle bytes/binding changed')
            now = choices.pop()
        used.add(now)
        pairs.append((triangle, now))
    locked = metadata.get('lockedSourceVertexIds', [])
    require(isinstance(locked, list) and all(type(i) is int and 0 <= i < len(sources) for i in locked),
            'Invalid hard-lock IDs')
    require(len(set(locked)) == len(locked), 'Duplicate hard-lock IDs')
    witnesses = set(new_records)
    require(all(old_records[i] in witnesses for i in locked), 'Hard-locked vertex lost or changed')
    return pairs, {'protectedTriangles': sorted(expected), 'lockedSourceVertices': sorted(locked),
                   'facialShapes': face_shapes, 'capTriangles': sorted(cap_pairs),
                   'origin': 'original source metadata plus automatic alpha material locks',
                   'independentRuntimePolicyRequired': True}


def package(original_path, bank_path, candidate_path, metadata):
    original_data = bounded_read(original_path, MAX_MODEL)
    bank_data = bounded_read(bank_path, MAX_BANK)
    require(metadata.get('inputs', {}).get('modelSha256') == sha(original_data)
            and metadata.get('inputs', {}).get('bankSha256') == sha(bank_data), 'Original provenance mismatch')
    raw, sorted_metadata, order = canonicalize(candidate_path, metadata)
    original = read_model(original_path)
    layout = bank_layout(bank_data)
    require(0 < len(original.submeshes) <= 64, 'OPOS supports at most 64 driver material spans')
    with tempfile.TemporaryDirectory(prefix='opos-package-') as tmp:
        temporary = Path(tmp) / 'candidate.gmdl'
        temporary.write_bytes(raw)
        candidate = read_model(temporary)
        source_ids = np.asarray(sorted_metadata['sourceVertexIds'], dtype=np.int64)
        require(source_ids.max() < len(original.position), 'Source IDs outside original')
        av = np.frombuffer(original.vertex_bytes, VERTEX)[source_ids]
        bv = np.frombuffer(candidate.vertex_bytes, VERTEX)
        require(av['uv'].tobytes() == bv['uv'].tobytes() and av['c'].tobytes() == bv['c'].tobytes(),
                'OPOS v1 permits position/normal edits only; UV/colour changes need separate review')
        require(len(np.unique(candidate.indices)) == len(candidate.position), 'Unreferenced candidate vertex')
        compact_bank, proof = rebind(original_path, bank_path, temporary, sorted_metadata)
        compact = bank_layout(compact_bank)
        require(compact_bank[compact['tail']:] == bank_data[layout['tail']:], 'Original animation tail changed')
        pairs, policy = lock_policy(original, candidate, layout, compact['sources'], sorted_metadata)
    require(proof['pose']['method'] == 'original-authored-palette', 'OPOS requires an actual original neutral pose')
    clip_indices = [i for i, clip in enumerate(layout['clips']) if clip['name'] == proof['pose']['clip']]
    original_vertices = np.frombuffer(original.vertex_bytes, VERTEX)
    frame = proof['pose']['frame']
    clip_index = None
    for i in clip_indices:
        if frame >= layout['clips'][i]['count']:
            continue
        pe, ne = residuals(layout['sources'], original.position, original_vertices['n'], layout['clips'][i]['frames'][frame])
        if pe.max() <= proof['positionTolerance'] and ne.max() <= proof['normalTolerance']:
            clip_index = i
            break
    require(clip_index is not None, 'Neutral pose identity cannot be resolved')
    source_bytes = np.asarray(sorted_metadata['sourceVertexIds'], dtype='<u4').tobytes()
    overrides = compact['sources'].tobytes()
    protected = b''.join(struct.pack('<2I', *pair) for pair in pairs)
    payload = raw + source_bytes + overrides + protected
    header = struct.pack('<4s15I', b'OPOS', 1, 64,
                         len(original_data), crc(original_data), len(bank_data), crc(bank_data),
                         len(raw), len(source_ids), clip_index, frame, len(pairs), len(payload), crc(payload), 1, 0)
    wrapper = header + payload
    require(len(wrapper) <= MAX_WRAPPER, 'Wrapper exceeds allocation bound')
    receipt = {'format': 'OPOS', 'version': 1, 'purpose': 'preparatory Wii-only diagnostic',
               'runtimeReady': False, 'visualApproval': False, 'enabled': False,
               'originalModel': {'path': str(original_path), 'size': len(original_data), 'crc32': crc(original_data), 'sha256': sha(original_data)},
               'originalBank': {'path': str(bank_path), 'size': len(bank_data), 'crc32': crc(bank_data), 'sha256': sha(bank_data)},
               'inputCandidateSha256': metadata['candidateSha256'], 'candidateSha256': sha(raw),
               'wrapperSha256': sha(wrapper), 'wrapperSize': len(wrapper), 'payloadCrc32': crc(payload),
               'sourceVertexIds': sorted_metadata['sourceVertexIds'], 'sourceMatrixIds': sorted_metadata['sourceMatrixIds'],
               'bindingGroups': sorted_metadata['bindingGroups'], 'newToInputVertex': order,
               'neutralClipIndex': clip_index, 'neutralFrame': frame, 'policy': policy,
               'protectedTrianglePairs': pairs, 'rebindProof': proof,
               'fallbackContract': 'Missing, stale, malformed, unsupported or failed-policy OPOS leaves original model/bank untouched. Validate all originals and candidate first; commit model plus rebound sources atomically. Preserve original authoredRanges and original submesh first offsets for PAT. Only compact source-write ranges. Never replace the original animation tail or relax O3RM.'}
    return wrapper, raw, sorted_metadata, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--metadata', type=Path)
    parser.add_argument('--original', type=Path)
    parser.add_argument('--bank', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    metadata_path = args.metadata or args.candidate.with_suffix('.json')
    metadata = json.loads(bounded_read(metadata_path, 16 * 1024 * 1024))
    original = args.original or Path(metadata['inputs']['model'])
    bank = args.bank or Path(metadata['inputs']['bank'])
    outputs = [args.output, args.output.with_suffix('.gmdl'), args.output.with_suffix('.json'),
               args.output.with_suffix('.receipt.json'), args.output.with_suffix('.policy.txt')]
    require(len({p.resolve() for p in outputs}) == len(outputs), 'Output requires a distinct .opos extension')
    inputs = {p.resolve() for p in (original, bank, args.candidate, metadata_path)}
    require(not inputs.intersection(p.resolve() for p in outputs), 'Outputs must not overwrite inputs')
    wrapper, raw, sorted_metadata, receipt = package(original, bank, args.candidate, metadata)
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
    outputs[0].write_bytes(wrapper)
    outputs[1].write_bytes(raw)
    outputs[2].write_text(json.dumps(sorted_metadata, indent=2) + '\n')
    outputs[3].write_text(json.dumps(receipt, indent=2) + '\n')
    outputs[4].write_text(''.join(f't {i}\n' for i in receipt['policy']['protectedTriangles'])
                          + ''.join(f'v {i}\n' for i in receipt['policy']['lockedSourceVertices']))
    print(json.dumps({'wrapper': str(outputs[0]), 'receipt': str(outputs[3]), 'vertices': len(receipt['sourceVertexIds']),
                      'protectedTriangles': len(receipt['policy']['protectedTriangles']), 'runtimeReady': False, 'visualApproval': False}))


if __name__ == '__main__':
    main()
