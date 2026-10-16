"""tools/cook_o3ds_course_textures.py --mode pack: OMIP entries, the MPK1
archive and the alpha-aware chains (docs/o3ds-texture-cache.md).
Run: .venv/bin/python -m unittest tests/test_o3ds_course_texture_mips.py"""
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import cook_o3ds_course_textures as cook  # noqa: E402


def t3x(width, height, fmt, payload):
    dims = (width.bit_length() - 4) | (height.bit_length() - 4) << 3
    return struct.pack("<HBBB6HI", 1, dims, fmt, 0, width, height, 0, 1024, 1024, 0, len(payload) << 8) + payload


def have_tex3ds():
    try:
        return cook.tool("tex3ds").exists()
    except Exception:
        return False


class AlphaTests(unittest.TestCase):
    def test_alpha_nibbles_are_column_major(self):
        word = sum(p << (4 * p) for p in range(16))  # nibble p holds p
        out = cook.decode_alpha(np.array([[word]], dtype=np.uint64))
        for x in range(4):
            for y in range(4):
                self.assertEqual(out[y, x], (x * 4 + y) * 17)

    def test_first_passing_nibble(self):
        self.assertEqual(cook.first_passing_nibble(("greater", 127)), 8)
        self.assertEqual(cook.first_passing_nibble(("greater", 136)), 9)
        self.assertEqual(cook.first_passing_nibble(("gequal", 136)), 8)
        self.assertEqual(cook.first_passing_nibble(("gequal", 0)), 0)
        self.assertEqual(cook.first_passing_nibble(("greater", 255)), 16)

    def test_coverage_is_preserved_at_every_level(self):
        a = np.zeros((64, 64))
        a[:, ::4] = 255
        a[::8, :] = 255
        colour = np.random.default_rng(1).integers(0, 255, (64, 64, 3)).astype(float)
        for test in (("greater", 127), ("gequal", 165), ("greater", 16)):
            target = cook.passes(cook.quantize(a), test).mean()
            levels = cook.alpha_chain(colour, a, 3, "alpha", test)
            self.assertEqual([lv.shape[:2] for lv in levels], [(32, 32), (16, 16), (8, 8)])
            for lv in levels:
                self.assertTrue(np.all(lv[..., 3] % 17 == 0))
                cover = cook.passes(lv[..., 3].astype(float), test).mean()
                self.assertLessEqual(abs(cover - target), 1 / lv[..., 3].size + 1e-9)
            # A plain box chain loses the strands with distance.
            box = cook.alpha_chain(colour, a, 3, "alpha", None)
            self.assertNotAlmostEqual(cook.passes(box[2][..., 3].astype(float), ("greater", 127)).mean(), target)

    def test_transparent_colour_does_not_bleed(self):
        colour = np.zeros((16, 16, 3))
        colour[:, ::2] = (255, 0, 0)  # visible red texels
        colour[:, 1::2] = (0, 0, 255)  # blue under alpha 0
        alpha = np.zeros((16, 16))
        alpha[:, ::2] = 255
        weighted = cook.alpha_chain(colour, alpha, 2, "alpha", None)
        box = cook.alpha_chain(colour, alpha, 2, "box", None)
        self.assertTrue(np.all(weighted[0][..., :3] == (255, 0, 0)))
        self.assertTrue(np.all(box[0][..., :3] == (128, 0, 128)))
        self.assertTrue(np.all(weighted[0][..., 3] == 136))  # mean 127.5 -> nibble 8


class FilterTests(unittest.TestCase):
    def test_lanczos2_taps(self):
        taps = cook.lanczos2_taps()
        self.assertEqual(len(taps), 8)
        self.assertAlmostEqual(taps.sum(), 1.0)
        np.testing.assert_allclose(taps, taps[::-1])
        self.assertTrue(taps[3] > taps[2] > 0 > taps[1])

    def test_constant_and_periodic_images(self):
        flat = np.full((32, 16, 3), 77.0)
        for wrap in (("wrap", "wrap"), ("edge", "reflect")):
            np.testing.assert_allclose(cook.downsample(flat, wrap), np.full((16, 8, 3), 77.0))
        # A texture that tiles keeps tiling: a one-texel shift of the source
        # by two texels is a one-texel shift of the level (no border seam).
        rng = np.random.default_rng(4)
        image = rng.uniform(0, 255, (32, 32, 3))
        shifted = np.roll(image, (2, 4), axis=(0, 1))
        np.testing.assert_allclose(cook.downsample(shifted, ("wrap", "wrap")),
                                   np.roll(cook.downsample(image, ("wrap", "wrap")), (1, 2), axis=(0, 1)), atol=1e-9)
        # Clamp only reads inside the texture.
        edge = np.zeros((16, 16, 1))
        edge[:, -1] = 255
        self.assertEqual(cook.downsample(edge, ("edge", "edge"))[0, 0, 0], 0)
        self.assertGreater(cook.downsample(edge, ("wrap", "wrap"))[0, 0, 0], 0)

    def test_box_is_the_plain_mean(self):
        image = np.arange(64, dtype=float).reshape(8, 8, 1)
        np.testing.assert_allclose(cook.downsample(image, ("wrap", "wrap"), "box")[0, 0, 0], (0 + 1 + 8 + 9) / 4)

    def test_chain_levels(self):
        self.assertEqual(cook.chain_levels(256, 128), 4)
        self.assertEqual(cook.chain_levels(1024, 1024), 7)
        self.assertEqual(cook.chain_levels(16, 8), 0)


class ArchiveTests(unittest.TestCase):
    def test_omip_header(self):
        original = t3x(64, 64, 12, bytes(range(256)) * 8)
        tail = bytes(range(64)) * 8
        blob = cook.omip(original, 12, 1, False, tail[:512])
        self.assertEqual(len(blob), cook.OMIP_HEADER + 512)
        magic, version, size, crc, fmt, levels, flags, tail_bytes, tail_crc, zero = struct.unpack_from("<4sIIIBBHIII", blob)
        self.assertEqual((magic, version, size, crc), (b"OMIP", 1, len(original), zlib.crc32(original)))
        self.assertEqual((fmt, levels, flags, tail_bytes, tail_crc, zero), (12, 1, 0, 512, zlib.crc32(tail[:512]), 0))
        self.assertEqual(struct.unpack_from("<H", cook.omip(original, 12, 0, True, b""), 18)[0], 1)

    def test_mpk1_round_trip(self):
        files = {"b": b"\x02" * 5, "a": b"\x01" * 40, "c_long_name": b""}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "mips.pak"
            cook.write_mpk1(path, files)
            data = path.read_bytes()
        magic, count, names_bytes = struct.unpack_from("<4sII", data)
        self.assertEqual((magic, count), (b"MPK1", 3))
        names = data[12 + 12 * count:12 + 12 * count + names_bytes]
        got = {}
        for i in range(count):
            at, offset, size = struct.unpack_from("<III", data, 12 + 12 * i)
            self.assertEqual(offset % 32, 0)
            self.assertGreaterEqual(offset, 12 + 12 * count + names_bytes)
            got[names[at:names.index(0, at)].decode()] = data[offset:offset + size]
        self.assertEqual(got, files)
        self.assertEqual(list(got), sorted(files))


@unittest.skipUnless(have_tex3ds(), "devkitPro tex3ds not installed")
class VariantTests(unittest.TestCase):
    def test_alpha_free_etc1a4_converts_with_chain(self):
        blocks = np.random.default_rng(2).integers(0, 2**63, 64 * 64 // 16, dtype=np.uint64)
        payload = b"".join(b"\xff" * 8 + int(b).to_bytes(8, "little") for b in blocks)
        original = t3x(64, 64, 13, payload)
        blob, why = cook.mip_variant(original, cook.orientation_check())
        self.assertEqual(why, "etc1a4->etc1+mips")
        fmt, levels, flags = struct.unpack_from("<BBH", blob, 16)
        self.assertEqual((fmt, levels, flags), (12, 3, 1))
        self.assertEqual(len(blob) - cook.OMIP_HEADER, (32 * 32 + 16 * 16 + 8 * 8) // 2)

    def test_used_alpha_needs_known_usage(self):
        blocks = np.random.default_rng(3).integers(0, 2**63, 32 * 32 // 16, dtype=np.uint64)
        payload = b"".join(b"\x0f" * 8 + int(b).to_bytes(8, "little") for b in blocks)
        original = t3x(32, 32, 13, payload)
        self.assertEqual(cook.mip_variant(original, False)[1], "uses alpha")
        blob, why = cook.mip_variant(original, cook.orientation_check(), True, ("alpha", ("greater", 127)))
        self.assertEqual(why, "mips (alpha-test)")
        fmt, levels, flags = struct.unpack_from("<BBH", blob, 16)
        self.assertEqual((fmt, levels, flags), (13, 2, 0))
        self.assertEqual(len(blob) - cook.OMIP_HEADER, 16 * 16 + 8 * 8)
        self.assertEqual(cook.mip_variant(original, False, False, ("alpha", None))[1], "uses alpha")

    def test_runtime_parser_accepts(self):
        exe = ROOT / "build" / "validate_texture_mips"
        if not exe.exists():
            self.skipTest("build/validate_texture_mips not built")
        original = t3x(64, 32, 12, bytes(range(256)) * 4)
        blob, _ = cook.mip_variant(original, cook.orientation_check())
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "textures").mkdir()
            (Path(d) / "textures" / "x.t3x").write_bytes(original)
            cook.write_mpk1(Path(d) / "mips.pak", {"x": blob})
            r = subprocess.run([str(exe), str(Path(d) / "mips.pak"), d], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Validated 1 mip variants", r.stdout)


if __name__ == "__main__":
    unittest.main()
