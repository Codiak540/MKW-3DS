"""Synthetic captures test accounting/validation; they never qualify hardware."""
import copy
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "compare_o3ds_performance.py"
SPEC = importlib.util.spec_from_file_location("compare_o3ds", TOOL)
compare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compare)


def row(frame=0, rendered=True, interval=50, candidate=False):
    result = {column: 0 for column in compare.NUMERIC_COLUMNS}
    result.update({
        "course": 4, "frame": frame, "rendered": int(rendered), "interval_ms": interval,
        "hardware": "old3ds", "model_profile": "o3ds_candidate" if candidate else "original",
        "quality_reduced": 1, "stereo_wanted": 0, "race_frame_cap": 30,
        "model_variants_loaded": int(candidate),
        "model_variant_triangles": 400 if candidate and rendered else 0,
        "model_variant_draws": 10 if candidate and rendered else 0,
        "triangles": (500 if candidate else 1000) if rendered else 0,
        "draws": 20 if rendered else 0, "skinned_vertices": 100 if rendered else 0,
        "simulation_ms": 3, "player_ms": 5 if rendered else 0,
        "previous_gpu_queue_ms": 7 if rendered else 0,
        "build_id": "test-build", "content_id": "test-content", "version": "test-version",
        "racer_distance_enabled": 0, "racer_distance_limit": 25000, "racer_distance_culled": 0,
    })
    return result


class AccountingTests(unittest.TestCase):
    def test_skipped_rows_contribute_to_wall_time_and_presented_interval(self):
        rows = [row(0, True, 10), row(1, False, 20), row(1, False, 30), row(1, True, 40)]
        result = compare.summarize_rows(rows)
        self.assertEqual(result["sustained_fps"], 20)
        self.assertEqual(result["skipped_rows"], 2)
        self.assertEqual(result["rendered_interval_p95_ms"], 90)
        self.assertEqual(result["rendered_interval_p99_ms"], 90)
        self.assertEqual(result["work"]["triangles"]["mean_per_rendered_frame"], 1000)
        self.assertEqual(result["cpu"]["simulation_ms"]["total_ms_per_second"], 120)
        self.assertEqual(result["cpu"]["player_ms"]["median_ms"], 5)

    def test_periodic_skips_do_not_double_reported_fps(self):
        rows = [row(i // 2, i % 2 == 1, 1000 / 60) for i in range(120)]
        result = compare.summarize_rows(rows)
        self.assertAlmostEqual(result["sustained_fps"], 30)
        self.assertAlmostEqual(result["rendered_interval_p95_ms"], 1000 / 30)

    def test_warmup_and_minimum_duration_include_skipped_rows(self):
        rows = [row(i // 2, i % 2 == 1, 10) for i in range(6500)]
        selected = compare.measured_rows(rows)
        self.assertEqual(len(selected), 6000)
        self.assertEqual(sum(r["interval_ms"] for r in selected), 60000)
        with self.assertRaisesRegex(compare.CaptureError, "need at least 60s"):
            compare.measured_rows(rows[:-1])

    def test_percentiles_interpolate(self):
        self.assertEqual(compare.percentile([10, 20, 30], 0.95), 29)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, name, rows, headers=None):
        path = self.root / name
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=headers or sorted(set().union(*(item.keys() for item in rows))))
            writer.writeheader()
            writer.writerows(rows)
        return {"csv": name, "sha256": compare.sha256_file(path), "assets_sha256": "a" * 64 if "base" in name else "b" * 64}

    def manifest(self, repeats=3, candidate_interval=40, environment="synthetic"):
        context = {"course": 4, "build_sha256": "c" * 64, "replay_sha256": "d" * 64,
                   "settings_sha256": "e" * 64, "stereo_wanted": False, "race_frame_cap": 30,
                   "quality_reduced": True}
        pairs = []
        for run in range(repeats):
            # Vary frame origin so these are distinct synthetic files.
            base = [row(run * 10000 + i, interval=50) for i in range(1350)]
            cand = [row(run * 10000 + i, interval=candidate_interval, candidate=True) for i in range(int(67000 / candidate_interval))]
            pairs.append({"scene_id": "dense-track", "demanding": True, "context": dict(context),
                          "baseline": self.write(f"baseline-{run}.csv", base),
                          "candidate": self.write(f"candidate-{run}.csv", cand)})
        return {"schema_version": 1,
                "attestation": {"execution_environment": environment, "tester": "unit test fixture", "device_id": "no physical device"},
                "pairs": pairs}

    def distance_manifest(self, culls=True):
        manifest = self.manifest(repeats=1)
        manifest.update(experiment="racer_distance", candidate_distance=6000)
        pair = manifest["pairs"][0]
        rows = compare.read_capture(self.root / pair["candidate"]["csv"], pair["candidate"]["sha256"])
        for i, item in enumerate(rows):
            item.update(model_profile="original", model_variants_loaded=0, model_variant_triangles=0, model_variant_draws=0,
                        racer_distance_enabled=1, racer_distance_limit=6000, racer_distance_culled=i if culls else 0)
        pair["candidate"] = self.write("candidate-distance.csv", rows)
        pair["candidate"]["assets_sha256"] = pair["baseline"]["assets_sha256"]
        return manifest

    def object_gpu_manifest(self, submissions=True, cpu_savings=True):
        manifest = self.manifest(repeats=1)
        manifest["experiment"] = "object_gpu_skin"
        pair = manifest["pairs"][0]
        for role in ("baseline", "candidate"):
            rows = compare.read_capture(self.root / pair[role]["csv"], pair[role]["sha256"])
            candidate = role == "candidate"
            for i, item in enumerate(rows):
                item.update(model_profile="original", model_variants_loaded=0, model_variant_triangles=0, model_variant_draws=0,
                            object_gpu_enabled=int(candidate), object_gpu_models_loaded=int(candidate), object_gpu_fallbacks=0,
                            object_gpu_vertex_references=i * 900 if candidate and submissions else 0,
                            cpu_skinned_vertices=100 if candidate and cpu_savings else 1000,
                            gpu_skin_vertex_references=1000 if candidate else 100, triangles=1000)
            pair[role] = self.write(f"{role}-object-gpu.csv", rows)
        pair["candidate"]["assets_sha256"] = pair["baseline"]["assets_sha256"]
        return manifest

    def texture_manifest(self, loaded=True, smaller=True):
        manifest = self.manifest(repeats=1)
        manifest["experiment"] = "texture_reduction"
        pair = manifest["pairs"][0]
        for role in ("baseline", "candidate"):
            rows = compare.read_capture(self.root / pair[role]["csv"], pair[role]["sha256"])
            candidate = role == "candidate"
            for item in rows:
                item.update(model_profile="original", model_variants_loaded=0, model_variant_triangles=0, model_variant_draws=0,
                            texture_enabled=int(candidate), texture_variants_loaded=4 if candidate and loaded else 0,
                            texture_variant_fallbacks=0, texture_source_bytes=4000 if candidate else 0,
                            texture_candidate_bytes=(1000 if smaller else 4000) if candidate else 0, triangles=1000)
            pair[role] = self.write(f"{role}-textures.csv", rows)
        return manifest

    def test_repeated_synthetic_gain_stays_ineligible(self):
        result = compare.compare(self.manifest(), self.root)
        self.assertFalse(result["eligible_for_visual_review"])
        self.assertFalse(result["automatic_activation"])
        self.assertEqual(len(result["reasons"]), 1)
        self.assertIn("Physical Old 3DS", result["reasons"][0])
        self.assertEqual(result["scenes"]["dense-track"]["median_fps_gain"], 5)

    def test_emulator_gain_stays_ineligible(self):
        result = compare.compare(self.manifest(environment="emulator"), self.root)
        self.assertFalse(result["eligible_for_visual_review"])

    def test_insufficient_repeats_and_gain(self):
        result = compare.compare(self.manifest(repeats=1, candidate_interval=45), self.root)
        self.assertTrue(any("at least 3" in reason for reason in result["reasons"]))
        self.assertTrue(any("below +5 FPS" in reason for reason in result["reasons"]))

    def test_regression_rejected(self):
        result = compare.compare(self.manifest(repeats=1, candidate_interval=60), self.root)
        self.assertTrue(any("sustained FPS regressed" in reason for reason in result["reasons"]))
        self.assertTrue(any("p99" in reason for reason in result["reasons"]))

    def test_duplicate_capture_not_independent(self):
        manifest = self.manifest(repeats=1)
        manifest["pairs"].append(copy.deepcopy(manifest["pairs"][0]))
        with self.assertRaisesRegex(compare.CaptureError, "distinct"):
            compare.compare(manifest, self.root)

    def test_mismatched_context(self):
        manifest = self.manifest(repeats=2)
        manifest["pairs"][1]["context"]["settings_sha256"] = "f" * 64
        with self.assertRaisesRegex(compare.CaptureError, "mismatched context"):
            compare.compare(manifest, self.root)

    def test_binary_identity_mismatch(self):
        manifest = self.manifest(repeats=1)
        path = self.root / manifest["pairs"][0]["candidate"]["csv"]
        path.write_text(path.read_text().replace("test-build", "different-build"))
        manifest["pairs"][0]["candidate"]["sha256"] = compare.sha256_file(path)
        with self.assertRaisesRegex(compare.CaptureError, "matching compiled"):
            compare.compare(manifest, self.root)

    def test_csv_metadata_cannot_be_overridden_by_manifest(self):
        manifest = self.manifest(repeats=1)
        manifest["pairs"][0]["context"]["stereo_wanted"] = True
        with self.assertRaisesRegex(compare.CaptureError, "stereo_wanted differs"):
            compare.compare(manifest, self.root)

    def test_candidate_without_submitted_variant_is_rejected(self):
        rows = [row(0, candidate=True), row(1, candidate=True)]
        for item in rows:
            item["model_variant_triangles"] = 0
        with self.assertRaisesRegex(compare.CaptureError, "actually submitted"):
            compare.validate_rows(rows, {"course": 4, "stereo_wanted": False, "race_frame_cap": 30, "quality_reduced": True}, "candidate")

    def test_additional_experiment_cannot_change_within_capture(self):
        rows = [row(0), row(1)]
        rows[0]["render_scale"] = "1"
        rows[1]["render_scale"] = "0.5"
        with self.assertRaisesRegex(compare.CaptureError, "additional experiment"):
            compare.validate_rows(rows, {"course": 4, "stereo_wanted": False, "race_frame_cap": 30, "quality_reduced": True}, "baseline")

    def test_distance_experiment_isolated_from_geometry(self):
        result = compare.compare(self.distance_manifest(), self.root)
        self.assertEqual(result["experiment"], "racer_distance")
        self.assertEqual(result["candidate_distance"], 6000)
        self.assertFalse(result["eligible_for_visual_review"])
        self.assertGreater(result["scenes"]["dense-track"]["pairs"][0]["candidate"]["distance_culls_in_window"], 0)
        self.assertFalse(any("triangle" in reason for reason in result["reasons"]))

    def test_distance_experiment_requires_culls(self):
        result = compare.compare(self.distance_manifest(culls=False), self.root)
        self.assertTrue(any("no distance culls" in reason for reason in result["reasons"]))

    def test_distance_experiment_requires_unchanged_assets(self):
        manifest = self.distance_manifest()
        manifest["pairs"][0]["candidate"]["assets_sha256"] = "f" * 64
        with self.assertRaisesRegex(compare.CaptureError, "identical assets"):
            compare.compare(manifest, self.root)

    def test_object_gpu_experiment_isolated_and_counts_cumulative_work_once(self):
        result = compare.compare(self.object_gpu_manifest(), self.root)
        self.assertEqual(result["experiment"], "object_gpu_skin")
        self.assertFalse(result["eligible_for_visual_review"])
        pair = result["scenes"]["dense-track"]["pairs"][0]
        self.assertEqual(pair["candidate"]["object_gpu_references_in_window"], 1499 * 900)
        self.assertLess(pair["candidate"]["work"]["cpu_skinned_vertices"]["mean_per_rendered_frame"],
                        pair["baseline"]["work"]["cpu_skinned_vertices"]["mean_per_rendered_frame"])
        self.assertFalse(any("triangle" in reason for reason in result["reasons"]))

    def test_object_gpu_experiment_requires_actual_submissions(self):
        with self.assertRaisesRegex(compare.CaptureError, "actually submitted"):
            compare.compare(self.object_gpu_manifest(submissions=False), self.root)

    def test_object_gpu_experiment_requires_cpu_work_reduction(self):
        result = compare.compare(self.object_gpu_manifest(cpu_savings=False), self.root)
        self.assertTrue(any("CPU skinning work did not decrease" in reason for reason in result["reasons"]))

    def test_object_gpu_experiment_requires_unchanged_assets(self):
        manifest = self.object_gpu_manifest()
        manifest["pairs"][0]["candidate"]["assets_sha256"] = "f" * 64
        with self.assertRaisesRegex(compare.CaptureError, "identical assets"):
            compare.compare(manifest, self.root)

    def test_model_experiment_rejects_object_gpu_marker_even_both_enabled(self):
        for role in ("baseline", "candidate"):
            rows = [row(i, candidate=role == "candidate") for i in range(2)]
            for item in rows:
                item.update(object_gpu_enabled=1, object_gpu_models_loaded=1, object_gpu_fallbacks=0, object_gpu_vertex_references=1000)
            with self.assertRaisesRegex(compare.CaptureError, "object GPU marker"):
                compare.validate_rows(rows, {"course": 4, "stereo_wanted": False, "race_frame_cap": 30, "quality_reduced": True}, role)

    def test_object_gpu_metadata_is_complete_and_counters_monotonic(self):
        rows = [row(i) for i in range(2)]
        for item in rows:
            item["object_gpu_enabled"] = 0
        spec = self.write("incomplete-object-gpu.csv", rows)
        with self.assertRaisesRegex(compare.CaptureError, "incomplete object GPU"):
            compare.read_capture(self.root / spec["csv"], spec["sha256"])
        for i, item in enumerate(rows):
            item.update(object_gpu_models_loaded=0, object_gpu_fallbacks=0, object_gpu_vertex_references=100 - i)
        spec = self.write("reset-object-gpu.csv", rows)
        with self.assertRaisesRegex(compare.CaptureError, "counter reset"):
            compare.read_capture(self.root / spec["csv"], spec["sha256"])

    def test_texture_experiment_isolated_and_load_bytes_are_not_summed(self):
        result = compare.compare(self.texture_manifest(), self.root)
        self.assertEqual(result["experiment"], "texture_reduction")
        self.assertFalse(result["eligible_for_visual_review"])
        pair = result["scenes"]["dense-track"]["pairs"][0]
        self.assertEqual(pair["candidate"]["texture_load_totals"]["texture_source_bytes"], 4000)
        self.assertEqual(pair["candidate"]["texture_load_totals"]["texture_candidate_bytes"], 1000)
        self.assertFalse(any("triangle" in reason for reason in result["reasons"]))

    def test_texture_experiment_requires_loaded_smaller_textures(self):
        for options in ({"loaded": False}, {"smaller": False}):
            with self.subTest(options=options), self.assertRaisesRegex(compare.CaptureError, "actually loaded"):
                compare.compare(self.texture_manifest(**options), self.root)

    def test_texture_experiment_requires_different_assets(self):
        manifest = self.texture_manifest()
        manifest["pairs"][0]["candidate"]["assets_sha256"] = manifest["pairs"][0]["baseline"]["assets_sha256"]
        with self.assertRaisesRegex(compare.CaptureError, "must differ"):
            compare.compare(manifest, self.root)

    def test_texture_experiment_rejects_geometry_distance_and_object_gpu(self):
        for extra in ({"model_profile": "o3ds_candidate", "model_variants_loaded": 1},
                      {"racer_distance_enabled": 1, "racer_distance_limit": 6000},
                      {"object_gpu_enabled": 1, "object_gpu_models_loaded": 1, "object_gpu_fallbacks": 0, "object_gpu_vertex_references": 100}):
            with self.subTest(extra=extra):
                manifest = self.texture_manifest()
                pair = manifest["pairs"][0]
                rows = compare.read_capture(self.root / pair["candidate"]["csv"], pair["candidate"]["sha256"])
                for item in rows:
                    item.update(extra)
                pair["candidate"] = self.write("candidate-textures-mixed.csv", rows)
                with self.assertRaises(compare.CaptureError):
                    compare.compare(manifest, self.root)

    def test_other_experiments_reject_texture_marker_even_if_both_enabled(self):
        for experiment in ("model_decimation", "racer_distance", "object_gpu_skin"):
            rows = [row(i) for i in range(2)]
            for item in rows:
                item.update(texture_enabled=1, texture_variants_loaded=1, texture_variant_fallbacks=0,
                            texture_source_bytes=4000, texture_candidate_bytes=1000,
                            object_gpu_enabled=0, object_gpu_models_loaded=0, object_gpu_fallbacks=0,
                            object_gpu_vertex_references=0, cpu_skinned_vertices=100, gpu_skin_vertex_references=100)
            with self.subTest(experiment=experiment), self.assertRaisesRegex(compare.CaptureError, "texture marker"):
                compare.validate_rows(rows, {"course": 4, "stereo_wanted": False, "race_frame_cap": 30, "quality_reduced": True}, "baseline", experiment)

    def test_texture_metadata_is_complete_and_counters_monotonic(self):
        rows = [row(i) for i in range(2)]
        for item in rows:
            item["texture_enabled"] = 0
        spec = self.write("incomplete-textures.csv", rows)
        with self.assertRaisesRegex(compare.CaptureError, "incomplete texture"):
            compare.read_capture(self.root / spec["csv"], spec["sha256"])
        for key in compare.TEXTURE_COLUMNS[1:]:
            for i, item in enumerate(rows):
                item.update({name: 0 for name in compare.TEXTURE_COLUMNS})
                item[key] = 100 - i
            spec = self.write("reset-textures.csv", rows)
            with self.subTest(key=key), self.assertRaisesRegex(compare.CaptureError, "counter reset"):
                compare.read_capture(self.root / spec["csv"], spec["sha256"])

    def mips_manifest(self, loaded=True, nearest=False, mip_filter=None):
        manifest = self.manifest(repeats=1)
        manifest["experiment"] = "texture_mips"
        if mip_filter:
            manifest["mip_filter"] = mip_filter
        pair = manifest["pairs"][0]
        for role in ("baseline", "candidate"):
            rows = compare.read_capture(self.root / pair[role]["csv"], pair[role]["sha256"])
            candidate = role == "candidate"
            for item in rows:
                item.update(model_profile="o3ds_candidate", model_variants_loaded=1, model_variant_triangles=400, model_variant_draws=10,
                            texture_mips_enabled=int(candidate), texture_mips_nearest=int(candidate and nearest),
                            texture_mips_loaded=40 if candidate and loaded else 0, texture_mips_fallbacks=0,
                            texture_mips_level_bytes=90000 if candidate and loaded else 0, triangles=1000)
            pair[role] = self.write(f"{role}-mips.csv", rows)
        pair["candidate"]["assets_sha256"] = pair["baseline"]["assets_sha256"]
        return manifest

    def test_mips_experiment_keeps_models_and_reports_chain_totals(self):
        result = compare.compare(self.mips_manifest(), self.root)
        self.assertEqual((result["experiment"], result["mip_filter"]), ("texture_mips", "linear"))
        self.assertFalse(result["eligible_for_visual_review"])
        pair = result["scenes"]["dense-track"]["pairs"][0]
        self.assertEqual(pair["candidate"]["texture_mip_totals"]["texture_mips_loaded"], 40)
        self.assertEqual(pair["baseline"]["texture_mip_totals"]["texture_mips_level_bytes"], 0)
        self.assertFalse(any("triangle" in reason for reason in result["reasons"]))
        self.assertEqual(compare.compare(self.mips_manifest(nearest=True, mip_filter="nearest"), self.root)["mip_filter"], "nearest")

    def test_mips_experiment_requires_loaded_chains_and_matching_filter(self):
        with self.assertRaisesRegex(compare.CaptureError, "actually loaded"):
            compare.compare(self.mips_manifest(loaded=False), self.root)
        with self.assertRaisesRegex(compare.CaptureError, "mip_filter"):
            compare.compare(self.mips_manifest(nearest=True), self.root)
        manifest = self.mips_manifest()
        manifest["mip_filter"] = "anisotropic"
        with self.assertRaisesRegex(compare.CaptureError, "mip_filter must be"):
            compare.compare(manifest, self.root)

    def test_mips_experiment_requires_identical_assets_and_marker_off_baseline(self):
        manifest = self.mips_manifest()
        manifest["pairs"][0]["candidate"]["assets_sha256"] = "f" * 64
        with self.assertRaisesRegex(compare.CaptureError, "identical assets"):
            compare.compare(manifest, self.root)
        manifest = self.mips_manifest()
        pair = manifest["pairs"][0]
        rows = compare.read_capture(self.root / pair["baseline"]["csv"], pair["baseline"]["sha256"])
        for item in rows:
            item.update(texture_mips_enabled=1)
        pair["baseline"] = self.write("baseline-mips-on.csv", rows)
        with self.assertRaisesRegex(compare.CaptureError, "texture mip marker"):
            compare.compare(manifest, self.root)

    def test_other_experiments_reject_loaded_mip_chains(self):
        rows = [row(i) for i in range(2)]
        for item in rows:
            item.update(texture_mips_enabled=1, texture_mips_nearest=0, texture_mips_loaded=3, texture_mips_fallbacks=0, texture_mips_level_bytes=100)
        for experiment in ("model_decimation", "racer_distance"):
            with self.subTest(experiment=experiment), self.assertRaisesRegex(compare.CaptureError, "o3ds-texmips-off"):
                compare.validate_rows(rows, {"course": 4, "stereo_wanted": False, "race_frame_cap": 30, "quality_reduced": True}, "baseline", experiment)
        for item in rows:
            item.update(texture_mips_loaded=0, texture_mips_level_bytes=0)
        compare.validate_rows(rows, {"course": 4, "stereo_wanted": False, "race_frame_cap": 30, "quality_reduced": True}, "baseline", "model_decimation")

    def test_mip_metadata_is_complete_and_counters_monotonic(self):
        rows = [row(i) for i in range(2)]
        for item in rows:
            item["texture_mips_enabled"] = 0
        spec = self.write("incomplete-mips.csv", rows)
        with self.assertRaisesRegex(compare.CaptureError, "incomplete texture mip"):
            compare.read_capture(self.root / spec["csv"], spec["sha256"])
        for key in compare.MIP_COLUMNS[2:]:
            for i, item in enumerate(rows):
                item.update({name: 0 for name in compare.MIP_COLUMNS})
                item[key] = 100 - i
            spec = self.write("reset-mips.csv", rows)
            with self.subTest(key=key), self.assertRaisesRegex(compare.CaptureError, "mip counter reset"):
                compare.read_capture(self.root / spec["csv"], spec["sha256"])

    def test_model_experiment_rejects_distance_marker_even_both_enabled(self):
        rows = [row(0), row(1)]
        for item in rows:
            item.update(racer_distance_enabled=1, racer_distance_limit=6000)
        with self.assertRaisesRegex(compare.CaptureError, "distance-cull marker"):
            compare.validate_rows(rows, {"course": 4, "stereo_wanted": False, "race_frame_cap": 30, "quality_reduced": True}, "baseline")

    def test_optional_cpu_gpu_skin_counts_are_distinct(self):
        rows = [row(0), row(1)]
        for item in rows:
            item.update(cpu_skinned_vertices=25, gpu_skin_vertex_references=75)
        summary = compare.summarize_rows(rows)
        self.assertEqual(summary["work"]["cpu_skinned_vertices"]["mean_per_rendered_frame"], 25)
        self.assertEqual(summary["work"]["gpu_skin_vertex_references"]["mean_per_rendered_frame"], 75)

    def test_corrupt_hash(self):
        spec = self.write("baseline.csv", [row()])
        with self.assertRaisesRegex(compare.CaptureError, "SHA256"):
            compare.read_capture(self.root / spec["csv"], "0" * 64)

    def test_missing_metadata(self):
        item = row()
        del item["hardware"]
        spec = self.write("baseline.csv", [item], sorted(item))
        with self.assertRaisesRegex(compare.CaptureError, "missing runtime"):
            compare.read_capture(self.root / spec["csv"], spec["sha256"])

    def test_nonfinite_and_bad_frame_sequence(self):
        for name, rows, expected in (
            ("nan", [row(interval=float("nan"))], "non-finite"),
            ("gap", [row(0), row(2)], "frame sequence"),
            ("negative", [row(interval=-1)], "negative"),
        ):
            with self.subTest(name=name):
                spec = self.write(f"baseline-{name}.csv", rows)
                with self.assertRaisesRegex(compare.CaptureError, expected):
                    compare.read_capture(self.root / spec["csv"], spec["sha256"])

    def test_short_and_truncated_capture(self):
        manifest = self.manifest(repeats=1)
        manifest["pairs"][0]["baseline"] = self.write("baseline-short.csv", [row(i) for i in range(10)])
        with self.assertRaisesRegex(compare.CaptureError, "need at least 60s"):
            compare.compare(manifest, self.root)
        path = self.root / "truncated.csv"
        path.write_text(",".join(sorted(compare.REQUIRED_COLUMNS)) + "\n1,2\n")
        with self.assertRaisesRegex(compare.CaptureError, "incomplete"):
            compare.read_capture(path, compare.sha256_file(path))

    def test_cli_reports_invalid_capture_as_json(self):
        path = self.root / "manifest.json"
        path.write_text(json.dumps({"schema_version": 0}))
        report = self.root / "report.json"
        self.assertEqual(compare.main(["--manifest", str(path), "--output", str(report)]), 2)
        data = json.loads(report.read_text())
        self.assertEqual(data["status"], "invalid_capture")
        self.assertFalse(data["eligible_for_visual_review"])


if __name__ == "__main__":
    unittest.main()
