"""Offline pose-rebinding invariants, using synthetic geometry only."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from rebind_o3ds_positions import SOURCE, bank_layout, rebind
from build_o3ds_character_regions import binding_fingerprint
from preview_o3ds_geometry import VERTEX


def digest(data):
    return hashlib.sha256(data).hexdigest()


def matrix_pair(linear, translation):
    return np.concatenate((np.column_stack((linear, translation)).reshape(-1),
                           np.linalg.inv(linear).T.reshape(-1))).astype('<f4')


def model_bytes(vertices, triangles):
    material = struct.pack('<iIiI2fI', -1, 0, -1, 0xffffffff, 0, 0, 0) + bytes(8)
    assert len(material) == 36
    indices = np.asarray(triangles, dtype='<u2')
    return (struct.pack('<4s5I', b'GMDL', 3, len(vertices), len(indices), 1, 0)
            + struct.pack('<2I', 0, len(indices)) + material + vertices.tobytes() + indices.tobytes())


def fixture(directory, version=4):
    points = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],
                       [2, 0, 0], [3, 0, 0], [2, 1, 0], [2, 0, 1]], dtype='<f4')
    sources = np.zeros(8, dtype=SOURCE)
    sources['p'] = points
    sources['n'] = [0, 1, 0]
    sources['matrix'] = [0] * 4 + [1] * 4
    matrices = np.array([matrix_pair(np.eye(3), [1, 3, 0]),
                         matrix_pair(np.diag([1.5, .75, 2]), [-1, 1, 3])])
    vertices = np.zeros(8, dtype=VERTEX)
    for i, vertex in enumerate(sources):
        pair = matrices[vertex['matrix']]
        affine = pair[:12].reshape(3, 4)
        vertices['p'][i] = affine[:, :3] @ vertex['p'] + affine[:, 3]
        n = pair[12:].reshape(3, 3) @ vertex['n']
        vertices['n'][i] = n / np.linalg.norm(n)
    vertices['c'] = 255
    triangles = [0, 1, 2, 0, 3, 1, 4, 5, 6, 4, 7, 5]
    original_data = model_bytes(vertices, triangles)
    header = struct.pack('<4s6I', b'DSKM', version, 8, 2, 1, 1, 1)
    if version == 5:
        header += struct.pack('<i', -1)
    clip_header = lambda name, count: name.encode().ljust(32, b'\0') + struct.pack('<3I', count, 1, count)
    # drive15 is the authored neutral frame; all other frames translate both
    # bones independently so the animation test exercises actual movement.
    frames = []
    for f in range(16):
        p = matrices.copy()
        p[0, 3] += (f - 15) * .1
        p[1, 7] -= (f - 15) * .05
        frames.append(p.tobytes() + struct.pack('<I', 1))
    tail = clip_header('drive', 16) + b''.join(frames)
    tail += clip_header('face', 2) + struct.pack('<2i', -1, -1)
    if version >= 3:
        tail += b'HLOC' + struct.pack('<2I', 0, 0)
    if version >= 4:
        tail += b'UVAN' + struct.pack('<I', 1) + clip_header('face', 2)
        tail += struct.pack('<12f', *([1, 0, 0, 0, 1, 0] * 2))
    if version == 5:
        tail += b'ROOT' + struct.pack('<I', 1) + np.zeros((16, 3), '<f4').tobytes()
    bank_data = header + sources.tobytes() + struct.pack('<2I', 0, 8) + tail
    ids = [0, 1, 2, 4, 5, 6]
    candidate = vertices[ids].copy()
    candidate['p'][1] += [.1, -.03, .07]
    candidate['p'][4] += [.04, .06, -.08]
    candidate['n'][4] = np.array([.1, 1, .1]) / np.linalg.norm([.1, 1, .1])
    candidate_data = model_bytes(candidate, [0, 1, 2, 3, 4, 5])
    original, bank, output = (directory / name for name in ('original.gmdl', 'original.dskm', 'candidate.gmdl'))
    original.write_bytes(original_data); bank.write_bytes(bank_data); output.write_bytes(candidate_data)
    metadata = {'inputs': {'modelSha256': digest(original_data), 'bankSha256': digest(bank_data),
                            'bindingFingerprint': binding_fingerprint(bank_data)},
                'candidateSha256': digest(candidate_data), 'sourceVertexIds': ids,
                'sourceMatrixIds': [0, 0, 0, 1, 1, 1], 'bindingGroups': [[0]] * 6}
    return original, bank, output, metadata


class RebindTests(unittest.TestCase):
    def test_all_bank_versions_preserve_animation_payload_and_binding(self):
        for version in (2, 3, 4, 5):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as tmp:
                args = fixture(Path(tmp), version)
                old = args[1].read_bytes(); layout = bank_layout(old)
                output, proof = rebind(*args)
                new = bank_layout(output)
                self.assertEqual(new['summary']['vertices'], 6)
                self.assertEqual(output[new['tail']:], old[layout['tail']:])
                self.assertEqual(proof['pose'], {'method': 'original-authored-palette', 'clip': 'drive', 'frame': 15})
                self.assertEqual(proof['animation']['framesChecked'], 16)
                self.assertEqual(proof['modifiedPositions'], 2)
                self.assertEqual(proof['modifiedNormals'], 1)
                self.assertLess(proof['roundTripMaxPositionResidual'], proof['positionTolerance'])
                self.assertGreater(max(c['maximumRetainedVertexDisplacement'] for c in proof['animation']['clips']), .1)
                self.assertEqual(args[1].read_bytes(), old)
                np.testing.assert_array_equal(new['sources']['matrix'], [0, 0, 0, 1, 1, 1])
                np.testing.assert_array_equal(new['ranges'], [[0, 6]])

    def test_rejects_changed_provenance_binding_and_shape_membership(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = fixture(Path(tmp))
            changes = [('candidateSha256', '0' * 64), ('sourceVertexIds', [0, 1, 1, 4, 5, 6]),
                       ('sourceMatrixIds', [0] * 6), ('bindingGroups', [[]] * 6)]
            for key, value in changes:
                with self.subTest(key=key):
                    metadata = copy.deepcopy(args[3]); metadata[key] = value
                    with self.assertRaises(ValueError):
                        rebind(*args[:3], metadata)

    def test_no_pose_match_is_rejected_without_implicit_fit(self):
        with tempfile.TemporaryDirectory() as tmp:
            original, bank, candidate, metadata = fixture(Path(tmp))
            data = bytearray(original.read_bytes())
            # Shift one original authored vertex; update identity so this tests
            # the geometry-to-pose correspondence rather than the hash check.
            position_offset = 24 + 44
            struct.pack_into('<f', data, position_offset, 100)
            original.write_bytes(data); metadata['inputs']['modelSha256'] = digest(data)
            with self.assertRaisesRegex(ValueError, 'No original pose matches'):
                rebind(original, bank, candidate, metadata)

    def test_protected_face_triangle_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = fixture(Path(tmp)); args[3]['faceShapes'] = [0]
            with self.assertRaisesRegex(ValueError, 'protected facial/alpha triangles'):
                rebind(*args)

    def test_atlas_split_vertices_require_opt_in_and_preserve_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            original, bank, candidate, metadata = fixture(Path(tmp))
            data = candidate.read_bytes()
            vertices = np.frombuffer(data, dtype=VERTEX, count=6, offset=24 + 44).copy()
            # Duplicate one vertex for a second per-triangle atlas UV island.
            vertices = np.concatenate((vertices, vertices[5:6]))
            vertices['uv'][6] = [.1, .2, .1, .2]
            new_data = model_bytes(vertices, [0, 1, 2, 3, 4, 5, 3, 6, 4])
            candidate.write_bytes(new_data)
            metadata['candidateSha256'] = digest(new_data)
            metadata['sourceVertexIds'].append(6)
            metadata['sourceMatrixIds'].append(1)
            metadata['bindingGroups'].append([0])
            with self.assertRaisesRegex(ValueError, 'strictly increasing'):
                rebind(original, bank, candidate, metadata)
            output, proof = rebind(original, bank, candidate, metadata, allow_split_vertices=True)
            parsed = bank_layout(output)
            self.assertEqual(proof['duplicateSourceBindings'], 1)
            self.assertFalse(proof['sourceIdsStrictlyIncreasing'])
            np.testing.assert_array_equal(parsed['ranges'], [[0, 7]])
            np.testing.assert_array_equal(parsed['sources'][5], parsed['sources'][6])


if __name__ == '__main__':
    unittest.main()
