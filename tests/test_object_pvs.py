"""Old 3DS object/racer visibility (tools/build_o3ds_object_pvs.py,
include/mkw/object_pvs.hpp, tools/pvs/pvs_raster.cpp input version 2).

Checks that the runtime loader accepts what the builder writes and answers
the same questions, that both sides compute the same course fingerprint,
that the box test hides a box behind a wall but never one in view, that the
rasterizer's version 1 output is unchanged, and that zone merging only adds
visible regions.
"""
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'tools/pvs')]
import build_o3ds_object_pvs as B  # noqa: E402

VERTEX = np.dtype([('p', '<f4', 3), ('n', '<f4', 3), ('uv', '<f4', 2), ('uv1', '<f4', 2), ('c', 'u1', 4)])
# Same model as fixedFingerprint() in tools/test_object_pvs.cpp.
FIXED_MODEL = dict(
    vertices=np.array([((0, 0, 0),), ((100, 0, 0),), ((100, 0, 100),), ((0, 0, 100),), ((0, 50, 0),)],
                      np.dtype([('p', '<f4', 3)])),
    indices=np.array([0, 1, 2, 2, 3, 0, 0, 1, 4], '<u2'),
    submeshes=[dict(first=0, count=6, flags=0), dict(first=6, count=3, flags=0)])


class ObjectPvsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.test = cls.dir / 'test_object_pvs'
        subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-pedantic', '-I', str(ROOT / 'include'),
                        '-o', str(cls.test), str(ROOT / 'tools/test_object_pvs.cpp')], check=True)
        cls.raster = cls.dir / 'pvs_raster'
        subprocess.run(['c++', '-O2', '-std=c++17', '-o', str(cls.raster), str(ROOT / 'tools/pvs/pvs_raster.cpp')],
                       check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def query(self, path, fp, parts, eye, lo, hi):
        out = subprocess.run([str(self.test), 'query', str(path), f'{fp:08x}', str(parts),
                              *map(str, [*eye, *lo, *hi])], check=True, capture_output=True, text=True).stdout
        return tuple(int(x) for x in out.split())

    def test_units(self):
        out = subprocess.run([str(self.test)], check=True, capture_output=True, text=True).stdout
        self.assertIn('ok', out)

    def test_fingerprint_matches_runtime(self):
        out = subprocess.run([str(self.test), 'fingerprint'], check=True, capture_output=True, text=True).stdout
        self.assertEqual(int(out, 16), B.object_fingerprint([FIXED_MODEL]))
        # Whole-triangle reordering inside a submesh keeps it; a change does not.
        moved = dict(FIXED_MODEL, indices=np.array([2, 3, 0, 0, 1, 2, 0, 1, 4], '<u2'))
        self.assertEqual(B.object_fingerprint([moved]), B.object_fingerprint([FIXED_MODEL]))
        other = dict(FIXED_MODEL, indices=np.array([0, 1, 2, 2, 3, 0, 0, 4, 1], '<u2'))
        self.assertNotEqual(B.object_fingerprint([other]), B.object_fingerprint([FIXED_MODEL]))

    def test_encode_roundtrip(self):
        # 3 x 2 columns of 1000 from (0, 0); column (2, 1) has no data.
        nx, nz = 3, 2
        have = np.ones((nz, nx), bool)
        have[1, 2] = False
        ymin, ymax = np.full((nz, nx), -100.), np.full((nz, nx), 900.)
        boxes = np.array([[100, 0, 100, 400, 200, 400], [0, -50, 0, 2900, 1500, 1900]], np.float32)
        rows = [bytes([0b00]), bytes([0b10])]   # set 0: nothing visible; set 1: big box visible
        index = np.array([0, 1, 0, 0, 1, B.NONE], np.uint32)
        data, entries = B.encode(0xABCDEF01, 2, (0., 0.), 1000., nx, nz, have, ymin, ymax, index, rows, boxes, 2)
        path = self.dir / 'roundtrip.opvs'
        path.write_bytes(data)
        small = ((150, 50, 150), (350, 150, 350))
        mid = ((500, 0, 500), (1500, 1000, 1500))
        self.assertEqual(self.query(path, 0xABCDEF01, 2, (500, 0, 500), *small), (1, 1, 1))
        self.assertEqual(self.query(path, 0xABCDEF01, 2, (500, 0, 500), *mid), (1, 1, 1))
        self.assertEqual(self.query(path, 0xABCDEF01, 2, (1500, 0, 500), *small), (1, 1, 1))
        self.assertEqual(self.query(path, 0xABCDEF01, 2, (1500, 0, 500), *mid), (1, 1, 0))
        # Outside the column's height interval or grid, or a column without data.
        self.assertEqual(self.query(path, 0xABCDEF01, 2, (500, 905, 500), *small), (1, 0, 0))
        self.assertEqual(self.query(path, 0xABCDEF01, 2, (2500, 0, 1500), *small), (1, 0, 0))
        self.assertEqual(self.query(path, 0xABCDEF01, 2, (-1, 0, 500), *small), (1, 0, 0))
        # Partly outside every box: never hidden.
        self.assertEqual(self.query(path, 0xABCDEF01, 2, (500, 0, 500), (100, 0, 100), (3000, 10, 400)), (1, 1, 0))
        # Stale identity is rejected.
        self.assertEqual(self.query(path, 0xABCDEF02, 2, (500, 0, 500), *small)[0], 0)
        self.assertEqual(self.query(path, 0xABCDEF01, 1, (500, 0, 500), *small)[0], 0)

    def run_raster(self, version, tris, flags, blocks, eyes, boxes=()):
        rec = np.zeros(len(tris), np.dtype([('v', '<f4', 9), ('b', '<u4'), ('f', '<u4')]))
        rec['v'] = np.asarray(tris, np.float32).reshape(-1, 9)
        rec['b'] = blocks
        rec['f'] = flags
        inp, out = self.dir / f'in{version}.bin', self.dir / f'out{version}.bin'
        with open(inp, 'wb') as f:
            nb = int(np.max(blocks)) + 1
            f.write(b'PVSI' + struct.pack('<5I', version, len(rec), nb, len(eyes), 64) + struct.pack('<ffi', 10., 96., -1))
            f.write(rec.tobytes() + np.asarray(eyes, '<f4').tobytes())
            if version == 2:
                f.write(struct.pack('<I', len(boxes)) + np.asarray(boxes, '<f4').tobytes())
        return inp, out

    def wall(self):
        # A large double-sided opaque wall in the plane z = 0.
        a, b, c, d = (-5000, -5000, 0), (5000, -5000, 0), (5000, 5000, 0), (-5000, 5000, 0)
        return [a + b + c, a + c + d], [1, 1]

    def test_box_hidden_behind_wall(self):
        tris, flags = self.wall()
        eyes = [(0, 0, -1000), (0, 0, 1000)]
        boxes = [(-100, -100, 500, 100, 100, 700),      # behind the wall from the first eye
                 (-100, -100, -700, 100, 100, -500),    # in front of it
                 (-50, -50, -1050, 50, 50, -950),       # holds the first eye
                 (9000, -100, 500, 9200, 100, 700)]     # seen past the wall's edge
        inp, out = self.run_raster(2, tris, flags, [0, 0], eyes, boxes)
        subprocess.run([str(self.raster), str(inp), str(out)], check=True)
        rows = np.fromfile(out, np.uint8).reshape(2, 2)
        seen = np.unpackbits(rows[:, 1:], axis=1, bitorder='little')[:, :4]
        self.assertEqual(seen[0].tolist(), [0, 1, 1, 1])
        self.assertEqual(seen[1].tolist(), [1, 0, 0, 1])

    def test_version1_output_unchanged(self):
        # The rasterizer as committed before input version 2.
        base = subprocess.run(['git', '-C', str(ROOT), 'log', '--format=%H', '--diff-filter=A', '--',
                               'tools/pvs/pvs_raster.cpp'], capture_output=True, text=True).stdout.split()
        if not base:
            self.skipTest('no committed baseline of pvs_raster.cpp')
        src = subprocess.run(['git', '-C', str(ROOT), 'show', f'{base[-1]}:tools/pvs/pvs_raster.cpp'],
                             check=True, capture_output=True).stdout
        old_src, old = self.dir / 'old_raster.cpp', self.dir / 'old_raster'
        old_src.write_bytes(src)
        subprocess.run(['c++', '-O2', '-std=c++17', '-o', str(old), str(old_src)], check=True)
        rng = np.random.default_rng(7)
        tris = rng.uniform(-3000, 3000, (300, 9))
        flags = rng.integers(0, 4, 300)
        blocks = np.arange(300) // 16
        eyes = rng.uniform(-2000, 2000, (12, 3))
        inp, out = self.run_raster(1, tris, flags, blocks, eyes)
        subprocess.run([str(self.raster), str(inp), str(out)], check=True)
        out_old = self.dir / 'out_old.bin'
        subprocess.run([str(old), str(inp), str(out_old)], check=True)
        self.assertEqual(out.read_bytes(), out_old.read_bytes())

    def test_merge_zones_is_conservative(self):
        rng = np.random.default_rng(3)
        nz, nx, nr = 6, 7, 40
        have = rng.random((nz, nx)) > .2
        flat = rng.random((nz * nx, nr)) > .6
        merged = B.merge_zones(flat.copy(), have, 5)
        cols = np.nonzero(have.ravel())[0]
        self.assertTrue((merged[cols] >= flat[cols]).all())
        self.assertLessEqual(len({merged[k].tobytes() for k in cols}), 5)
        same = B.merge_zones(flat.copy(), have, 1000)
        self.assertTrue((same == flat).all())


if __name__ == '__main__':
    unittest.main()
