"""Synthetic OPOS writer/host-validator invariants; no proprietary assets needed."""
import copy
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from package_o3ds_positions import package
from rebind_o3ds_positions import bank_layout, sha
from preview_o3ds_geometry import VERTEX
from test_o3ds_position_rebind import fixture, model_bytes


def protected_fixture(directory, version=4):
    original, bank, candidate, metadata = fixture(directory, version)
    old = np.frombuffer(original.read_bytes(), VERTEX, count=8, offset=68)
    new = np.frombuffer(candidate.read_bytes(), VERTEX, count=6, offset=68).copy()
    new[3:] = old[[4, 5, 6]]  # Protect an unchanged second triangle as a cap decal.
    data = model_bytes(new, [0, 1, 2, 3, 4, 5])
    candidate.write_bytes(data)
    metadata['candidateSha256'] = sha(data)
    metadata['lockedSourceVertexIds'] = [0, 2, 4, 5, 6]
    metadata['preservedOpaqueTriangles'] = [dict(material=0, sourceTriangle=2,
        candidateTriangle=1, sourceIndices=[4, 5, 6], candidateIndices=[3, 4, 5])]
    return original, bank, candidate, metadata


def refresh_crc(wrapper):
    struct.pack_into('<I', wrapper, 52, zlib.crc32(wrapper[64:]) & 0xffffffff)
    return wrapper


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler_dir = tempfile.TemporaryDirectory(prefix='opos-host-tests-')
        cls.validator = Path(cls.compiler_dir.name) / 'validate-opos'
        subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-pedantic',
                        '-I' + str(ROOT / 'include'), str(ROOT / 'tools/validate_position_variant.cpp'),
                        '-o', str(cls.validator)], check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.compiler_dir.cleanup()

    def host(self, args, wrapper, policy, accepted=True, reason=None):
        directory = args[0].parent
        output, locks = directory / 'candidate.opos', directory / 'policy.txt'
        output.write_bytes(wrapper)
        locks.write_text(''.join(f't {i}\n' for i in policy['protectedTriangles'])
                         + ''.join(f'v {i}\n' for i in policy['lockedSourceVertices']))
        result = subprocess.run([str(self.validator), str(args[0]), str(args[1]), str(output), str(locks)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0 if accepted else 1, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['accepted'], accepted)
        if reason:
            self.assertIn(reason, data['reason'])
        return data

    def test_versions_identity_tail_and_neutral_pose(self):
        for version in (2, 3, 4, 5):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as tmp:
                args = protected_fixture(Path(tmp), version)
                before = [p.read_bytes() for p in args[:3]]
                wrapper, raw, metadata, receipt = package(*args)
                result = self.host(args, wrapper, receipt['policy'])
                self.assertEqual((result['vertices'], result['triangles'], result['protectedTriangles']), (6, 2, 1))
                self.assertEqual(result['neutralFrame'], 15)
                self.assertEqual(receipt['sourceVertexIds'], [0, 1, 2, 4, 5, 6])
                self.assertEqual(raw, before[2])
                self.assertTrue(receipt['rebindProof']['clipPatHairUvRootPayloadByteExact'])
                self.assertFalse(receipt['runtimeReady'])
                self.assertEqual([p.read_bytes() for p in args[:3]], before)

    def test_writer_sorts_unique_vertices_and_keeps_triangle_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = list(protected_fixture(Path(tmp)))
            original = args[2].read_bytes()
            vertices = np.frombuffer(original, VERTEX, count=6, offset=68)
            order = [3, 0, 4, 1, 5, 2]
            inverse = np.argsort(order)
            data = model_bytes(vertices[order], inverse[np.arange(6)])
            args[2].write_bytes(data)
            for key in ('sourceVertexIds', 'sourceMatrixIds', 'bindingGroups'):
                args[3][key] = [args[3][key][i] for i in order]
            args[3]['candidateSha256'] = sha(data)
            args[3]['preservedOpaqueTriangles'][0]['candidateIndices'] = inverse[[3, 4, 5]].tolist()
            wrapper, raw, metadata, receipt = package(*args)
            self.assertEqual(raw, original)
            self.assertEqual(metadata['sourceVertexIds'], [0, 1, 2, 4, 5, 6])
            self.host(args, wrapper, receipt['policy'])

    def test_writer_rejects_duplicate_uv_and_invalid_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = protected_fixture(Path(tmp))
            duplicated = copy.deepcopy(args[3]);duplicated['sourceVertexIds'][1] = 0
            with self.assertRaisesRegex(ValueError, 'duplicated source IDs'):
                package(*args[:3], duplicated)
            for field, offset in [('uv', 24), ('cap', 3 * 44), ('locked', 2 * 44)]:
                data = bytearray(args[2].read_bytes())
                at = 68 + offset
                struct.pack_into('<f', data, at, struct.unpack_from('<f', data, at)[0] + .125)
                changed = copy.deepcopy(args[3]);changed['candidateSha256'] = sha(data)
                path = Path(tmp) / (field + '.gmdl');path.write_bytes(data)
                with self.subTest(field=field), self.assertRaises(ValueError):
                    package(args[0], args[1], path, changed)

    def test_header_crc_truncation_and_stale_sources_fail_without_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = protected_fixture(Path(tmp))
            wrapper, _, _, receipt = package(*args)
            policy = receipt['policy']
            for length in [0, 1, 27, 63, 64, 65, len(wrapper) - 1]:
                with self.subTest(length=length):
                    self.host(args, wrapper[:length], policy, False)
            for at in [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60, 64, len(wrapper) - 1]:
                bad = bytearray(wrapper);bad[at] ^= 1
                with self.subTest(offset=at):
                    self.host(args, bad, policy, False)
            self.host(args, wrapper + b'\0', policy, False)
            for index in (0, 1):
                original = args[index].read_bytes();bad = bytearray(original);bad[-1] ^= 1;args[index].write_bytes(bad)
                self.host(args, wrapper, policy, False, 'stale original')
                args[index].write_bytes(original)

    def test_payload_recomputed_crc_still_rejects_semantic_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = protected_fixture(Path(tmp))
            wrapper, raw, _, receipt = package(*args)
            policy = receipt['policy']
            vertex = 64 + 68
            indices = vertex + 6 * 44
            ids = 64 + len(raw)
            sources = ids + 6 * 4
            pairs = sources + 6 * 28
            corruptions = [
                ('duplicate ID', ids + 4, '<I', 0, 'mapping'),
                ('wrong bone', sources + 24, '<I', 1, 'binding'),
                ('NaN local position', sources, '<f', float('nan'), 'binding'),
                ('NaN UV', vertex + 24, '<f', float('nan'), 'invalid model'),
                ('UV edit', vertex + 24, '<f', .5, 'UV/colour'),
                ('colour edit', vertex + 40, '<I', 0, 'UV/colour'),
                ('local roundtrip', sources, '<f', 99, 'roundtrip'),
                ('model roundtrip', vertex, '<f', 99, 'roundtrip'),
                ('bad index', indices, '<H', 99, 'invalid model'),
                ('unreferenced vertex', indices, '<H', 1, 'unreferenced'),
                ('empty span', 64 + 28, '<I', 0, 'invalid model'),
                ('material flags', 64 + 36, '<I', 1, 'material'),
                ('cap mapping', pairs + 4, '<I', 0, 'protected triangle'),
            ]
            for name, offset, fmt, value, reason in corruptions:
                bad = bytearray(wrapper);struct.pack_into(fmt, bad, offset, value);refresh_crc(bad)
                with self.subTest(name=name):
                    self.host(args, bad, policy, False, reason)
            wrong = copy.deepcopy(policy);wrong['protectedTriangles'] = []
            self.host(args, wrapper, wrong, False, 'protected triangle count')
            wrong = copy.deepcopy(policy);wrong['lockedSourceVertices'].append(7)
            self.host(args, wrapper, wrong, False, 'locked source vertex')

    def test_coherent_locked_edits_and_wrong_original_pose_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = protected_fixture(Path(tmp))
            wrapper, raw, _, receipt = package(*args)
            # A coherent world/local edit passes neutral roundtrip but fails the
            # original lock policy. Source0 bone is identity plus translation.
            for vertex_id in (0, 3):
                bad = bytearray(wrapper)
                for at in (64 + 68 + vertex_id * 44, 64 + len(raw) + 6 * 4 + vertex_id * 28):
                    struct.pack_into('<f', bad, at, struct.unpack_from('<f', bad, at)[0] + .125)
                # Vertex3 uses x scale1.5: adjust the local delta accordingly.
                if vertex_id == 3:
                    at = 64 + len(raw) + 6 * 4 + vertex_id * 28
                    struct.pack_into('<f', bad, at, struct.unpack_from('<f', bad, at)[0] - .125 + .125 / 1.5)
                refresh_crc(bad)
                self.host(args, bad, receipt['policy'], False,
                          'locked source' if vertex_id == 0 else 'protected triangle')
            bad = bytearray(wrapper);struct.pack_into('<I', bad, 40, 0)
            self.host(args, bad, receipt['policy'], False, 'original neutral roundtrip')
            # Even with refreshed original identity, malformed bank data fails.
            original_bank = args[1].read_bytes();bad_bank = bytearray(original_bank)
            layout = bank_layout(original_bank)
            struct.pack_into('<I', bad_bank, layout['tail'] + 40, 0xffffffff)
            args[1].write_bytes(bad_bank)
            bad = bytearray(wrapper);struct.pack_into('<I', bad, 24, zlib.crc32(bad_bank) & 0xffffffff)
            self.host(args, bad, receipt['policy'], False, 'invalid model/bank')

    def test_exact_texture_table_material_membership_and_oriented_locks(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = protected_fixture(Path(tmp))
            # Two material spans share one texture but may never borrow each
            # other's source vertices. DSKM shape membership remains unchanged.
            material = bytearray(args[0].read_bytes()[32:68])
            struct.pack_into('<i', material, 0, 0)
            for path, counts in ((args[0], [6, 6]), (args[2], [3, 3])):
                old = path.read_bytes();nv, ni = struct.unpack_from('<2I', old, 8)
                data = (struct.pack('<4s5I', b'GMDL', 3, nv, ni, 2, 1)
                        + struct.pack('<2I', 0, counts[0]) + material
                        + struct.pack('<2I', counts[0], counts[1]) + material
                        + b'mario'.ljust(64, b'\0') + old[68:])
                path.write_bytes(data)
            args[3]['inputs']['modelSha256'] = sha(args[0].read_bytes())
            args[3]['candidateSha256'] = sha(args[2].read_bytes())
            args[3]['preservedOpaqueTriangles'][0].update(material=1, sourceTriangle=0)
            wrapper, raw, _, receipt = package(*args)
            self.host(args, wrapper, receipt['policy'])
            indices = 64 + len(raw) - 12
            for name, offset, fmt, value, reason in [
                ('texture name', 64 + 112, '<I', 0, 'texture table'),
                ('texture selection', 64 + 32, '<I', 0xffffffff, 'material'),
                ('source borrowed across material', indices, '<H', 3, 'material/source'),
            ]:
                bad = bytearray(wrapper);struct.pack_into(fmt, bad, offset, value);refresh_crc(bad)
                with self.subTest(name=name):
                    self.host(args, bad, receipt['policy'], False, reason)
            bad = bytearray(wrapper)
            struct.pack_into('<3H', bad, indices + 6, 4, 3, 5)
            self.host(args, refresh_crc(bad), receipt['policy'], False, 'protected triangle')


if __name__ == '__main__':
    unittest.main()
