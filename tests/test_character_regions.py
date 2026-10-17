import hashlib
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from build_o3ds_character_regions import binding_fingerprint


def bank(version=4, clips=2):
    header = struct.pack('<4s6I', b'DSKM', version, 1, 2, clips, 1, 0)
    if version == 5:
        header += struct.pack('<i', -1)
    return header + struct.pack('<6fI2I', 1, 2, 3, 0, 1, 0, 1, 0, 1)


class CharacterRegionFingerprintTests(unittest.TestCase):
    def test_versions_and_clip_counts_share_only_identical_binding_layouts(self):
        expected = hashlib.sha256(struct.pack('<I', 2) + bank()[28:]).hexdigest()
        for version in (2, 3, 4, 5):
            self.assertEqual(binding_fingerprint(bank(version, 30)), expected)

    def test_changed_positions_matrices_or_shape_ranges_invalidate_semantics(self):
        original = bank()
        for offset in (12, 28, 52, 56, 60):
            changed = bytearray(original)
            changed[offset] ^= 1
            self.assertNotEqual(binding_fingerprint(changed), binding_fingerprint(original))

    def test_invalid_and_truncated_banks_cannot_supply_semantics(self):
        for data in (bank()[:20], bank()[:-1], b'FAIL' + bank()[4:], bank(6)):
            with self.assertRaises(ValueError):
                binding_fingerprint(data)


if __name__ == '__main__':
    unittest.main()
