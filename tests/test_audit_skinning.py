"""Synthetic layout/path tests, never evidence of hardware performance."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("audit_skinning", Path(__file__).resolve().parents[1] / "tools/audit_skinning.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def words(*values):
    return struct.pack("<" + "I" * len(values), *values)


def source(ids):
    return b"".join(struct.pack("<6fI", 0, 0, 0, 0, 1, 0, i) for i in ids)


def dskm(version=5, matrices=21, ids=(0, 5, 20), root=-1, frames=2, visibility=(1, 1)):
    header = b"DSKM" + words(version, len(ids), matrices, 1, 1, 0)
    if version == 5:
        header += struct.pack("<i", root)
    data = header + source(ids) + words(0, len(ids))
    data += bytes(32) + words(frames, 1, frames)
    data += b"".join(bytes(matrices * 84) + words(visibility[f]) for f in range(frames))
    if version >= 3:
        data += b"HLOC" + words(0, 0)
    if version >= 4:
        data += b"UVAN" + words(0)
    if version == 5 and root == -1:
        data += b"ROOT" + words(1) + bytes(frames * 12)
    return data


def dobj(matrices=21, ids=(0, 5, 20), clips=False, morph=False):
    data = b"DOBJ" + words(1, len(ids), matrices, 1, 0, int(clips), 0, 0, 0)
    data += source(ids) + words(0, len(ids)) + bytes(matrices * 48)
    if clips:
        # frames=4, step=2: samples at 0, 2, 3 (not floor(4/2)).
        data += bytes(32) + words(4, 1, 2, 1) + words(0) + bytes(matrices * 48 + 3 * 48)
    if morph:
        data += b"MORF" + words(1) + words(0, 1, 4) + words(0) + bytes(24 + 4 * 4)
    return data


def model(version=5, vertices=3, indices=(0, 1, 2), lighting=0):
    size = {3: 44, 4: 56, 5: 92}[version]
    sub = bytearray(size)
    struct.pack_into("<IIiIi", sub, 0, 0, len(indices), -1, 0, -1)
    if version >= 4:
        struct.pack_into("<I", sub, 44, lighting)
    return b"GMDL" + words(version, vertices, len(indices), 1, 0) + sub + bytes(vertices * 44) + struct.pack("<" + "H" * len(indices), *indices)


class LayoutTests(unittest.TestCase):
    def test_all_supported_driver_versions(self):
        for version in range(2, 6):
            b = audit.parse_bank(dskm(version))
            self.assertEqual((b["version"], b["source_matrix_ids"], b["visibility_constant"]), (version, [0, 5, 20], True))

    def test_driver_visibility_changes_prevent_rigid_prediction(self):
        b = audit.parse_bank(dskm(ids=(0, 0, 0), visibility=(1, 0)))
        result = audit.census_pair(audit.parse_model(model()), b)
        self.assertFalse(result["driver_rigid_conditions_met"])

    def test_driver_root_in_palette_has_no_root_appendix(self):
        self.assertEqual(audit.parse_bank(dskm(root=0))["root_slot"], 0)

    def test_dobj_samples_and_morph_extension(self):
        b = audit.parse_bank(dobj(clips=True, morph=True))
        self.assertEqual((b["clips"], b["morph_records"]), (1, 1))

    def test_long_srt_period_uses_sampled_payload_bound(self):
        data = b"DOBJ" + words(1, 0, 1, 1, 0, 0, 0, 1, 0)
        data += words(0, 0) + bytes(48)
        header_at = len(data)
        data += bytes(32) + words(24000, 1, 16, 1) + words(0) + bytes(1501 * 48)
        self.assertEqual(audit.parse_bank(data)["format"], "DOBJ")
        with self.assertRaises(audit.FormatError):
            audit.parse_bank(data[:-1])
        for frames, step in ((0, 16), ((1 << 24) + 1, 16), (24000, 0), (24000, 65)):
            bad = bytearray(data)
            struct.pack_into("<I", bad, header_at + 32, frames)
            struct.pack_into("<I", bad, header_at + 40, step)
            with self.assertRaises(audit.FormatError):
                audit.parse_bank(bad)

    def test_every_truncated_bank_rejected(self):
        for data in (dskm(), dobj(clips=True, morph=True)):
            # DOBJ permits omission of its entire optional MORF extension.
            optional_at = data.find(b"MORF")
            for end in range(len(data)):
                if end != optional_at:
                    with self.assertRaises(audit.FormatError):
                        audit.parse_bank(data[:end])

    def test_bad_versions_and_invalid_binding_rejected(self):
        for data in (dskm(6), dobj(ids=(0, 5, 21)), dskm(root=21)):
            with self.assertRaises(audit.FormatError):
                audit.parse_bank(data)

    def test_trailing_bytes_rejected(self):
        for data in (dskm(), dobj()):
            with self.assertRaises(audit.FormatError):
                audit.parse_bank(data + b"!")

    def test_all_model_versions_and_bad_index(self):
        for version in range(3, 6):
            self.assertEqual(audit.parse_model(model(version))["vertices"], 3)
        with self.assertRaises(audit.FormatError):
            audit.parse_model(model(indices=(0, 1, 3)))


class CensusTests(unittest.TestCase):
    def pair(self, bank, **kwargs):
        return audit.census_pair(audit.parse_model(model(**kwargs)), audit.parse_bank(bank))

    def test_large_palette_compaction_uses_matrix_count_not_max_id(self):
        result = self.pair(dskm())
        self.assertEqual(result["index_referenced_matrix_ids"], [0, 5, 20])
        self.assertTrue(result["upload_compaction_to_19_possible"])
        self.assertEqual(result["mixed_matrix_triangles"], 1)
        self.assertIn("palette_limit", result["conditional_path"])

    def test_unreferenced_vertices_distinguished(self):
        result = self.pair(dskm(ids=tuple(range(21))), vertices=21)
        self.assertEqual(result["all_source_matrices"], 21)
        self.assertEqual(result["index_referenced_matrices"], 3)
        self.assertTrue(result["upload_compaction_to_19_possible"])
        self.assertFalse(result["all_source_compaction_to_19_possible"])

    def test_course_path_prioritizes_rigid_over_lighting_and_palette(self):
        result = self.pair(dobj(ids=(20, 20, 20)), lighting=5)
        self.assertEqual(result["conditional_path"], "rigid_parts")

    def test_weighted_lit_cpu_and_unlit_gpu(self):
        for lighting, expected in ((5, "cpu_lit"), (1, "gpu_unlit"), (4, "gpu_unlit")):
            result = self.pair(dobj(matrices=3, ids=(0, 1, 2)), lighting=lighting)
            self.assertEqual(result["conditional_path"], expected)

    def test_material_only_and_mismatched_vertices(self):
        self.assertEqual(self.pair(dobj(ids=()))["conditional_path"], "material_only")
        with self.assertRaises(audit.FormatError):
            self.pair(dobj(ids=(0, 0)))

    def test_authoritative_visual_mapping_and_missing_bank(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {"models/course_00_visuals.list": root / "list", "models/mesh_000.gmdl": root / "model", "models/other.dobj": root / "bank"}
            paths["models/course_00_visuals.list"].write_text("r n mesh_000 other\n")
            paths["models/mesh_000.gmdl"].write_bytes(model())
            paths["models/other.dobj"].write_bytes(dobj())
            result = audit.audit(paths)
            self.assertEqual(result["models"][0]["mapping_evidence"], "course_visuals_list")
            self.assertFalse(result["errors"])
            del paths["models/other.dobj"]
            self.assertEqual(audit.audit(paths)["summary"]["errors"], 1)


if __name__ == "__main__":
    unittest.main()
