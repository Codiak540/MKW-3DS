import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import cook_o3ds_textures as cooker


def texture(width=64, height=64, fmt=12):
    dimensions = (width.bit_length() - 4) | (height.bit_length() - 4) << 3
    payload = bytes(width * height // (2 if fmt == 12 else 1))
    return struct.pack("<HBBB6HI", 1, dimensions, fmt, 0, width, height, 0, 1024, 1024, 0, len(payload) << 8) + payload


class TextureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.assets = self.root / "assets"
        self.source = self.root / "source"
        self.output = self.root / "output"
        for path in (self.assets, self.source, self.output):
            (path / "textures").mkdir(parents=True)
        self.original = texture()
        (self.assets / "textures/color.t3x").write_bytes(self.original)
        (self.source / "textures/color.t3x").write_bytes(self.original)
        Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(self.source / "textures/color.png")

    def tearDown(self):
        self.temp.cleanup()

    def cook(self):
        return cooker.cook_one("color", cooker.Assets(self.assets), [self.source], self.output)

    def test_exact_source_required(self):
        (self.source / "textures/color.t3x").write_bytes(self.original[:-1] + b"x")
        self.assertEqual(self.cook()["reason"], "no PNG beside an exact matching final T3X")
        self.assertFalse(list(self.output.rglob("*.otex")))

    def test_png_dimensions_must_match_source(self):
        Image.new("RGBA", (32, 32)).save(self.source / "textures/color.png")
        self.assertEqual(self.cook()["reason"], "matching-source PNG dimensions differ")

    def test_candidate_keeps_format_and_records_provenance(self):
        def encode(command, **kwargs):
            target = Path(command[command.index("-o") + 1])
            with Image.open(command[-1]) as image:
                self.assertEqual(image.size, (32, 32))
            target.write_bytes(texture(32, 32))
        with patch.object(cooker.subprocess, "run", side_effect=encode):
            row = self.cook()
        self.assertEqual(row["status"], "candidate")
        self.assertEqual(row["original"]["gpu_bytes"], 2048)
        self.assertEqual(row["candidate"]["gpu_bytes"], 512)
        data = (self.output / row["output"]).read_bytes()
        self.assertEqual(struct.unpack_from("<4sI", data), (b"OTEX", 1))
        self.assertEqual(row["original_sha256"], cooker.digest(self.original))

    def test_unsupported_or_malformed_layouts(self):
        for offset in (0, 1, 2, 3, 4, 5, 9, 17):
            bad = bytearray(self.original)
            bad[offset] ^= 0x80
            with self.assertRaises(ValueError):
                cooker.texture_layout(bad)
        with self.assertRaises(ValueError):
            cooker.texture_layout(self.original + b"x")

    def test_small_textures_preserved(self):
        (self.assets / "textures/color.t3x").write_bytes(texture(32, 32))
        self.assertEqual(self.cook()["reason"], "already small")

    def test_ui_and_hud_protected_but_3d_menu_models_allowed(self):
        for name in ("ui_title", "hud_rank", "flow_wipe", "race_bottom_ui"):
            self.assertTrue(cooker.protected_name(name))
        for name in ("ui_driver_0_000", "ui_back_0_000", "ui_machine_0_000", "course_00_000"):
            self.assertFalse(cooker.protected_name(name))

    def test_packed_original_precedes_loose_copy(self):
        name = b"textures/color.t3x\0"
        offset = 24 + len(name)
        packed = texture(128, 128)
        (self.assets / "menu.pak").write_bytes(b"MPK1" + struct.pack("<IIIII", 1, len(name), 0, offset, len(packed)) + name + packed)
        self.assertEqual(cooker.Assets(self.assets).read("textures/color.t3x"), packed)
        self.assertEqual(self.cook()["reason"], "no PNG beside an exact matching final T3X")

    def test_pack_out_of_bounds_rejected(self):
        (self.assets / "menu.pak").write_bytes(b"MPK1" + struct.pack("<IIIII", 1, 2, 0, 1000, 10) + b"a\0")
        with self.assertRaises(ValueError):
            cooker.Assets(self.assets)


if __name__ == "__main__":
    unittest.main()
