#!/usr/bin/env python3
"""Compare matched runtime_profile CSV captures without enabling model variants.

Usage: python3 tools/compare_o3ds_performance.py --manifest captures.json --output report.json
Use --template to print the context/attestation manifest format. Optional repeated
--pair BASELINE CANDIDATE arguments replace the manifest's CSV paths in order.

interval_ms is the interval between main-loop starts, including skipped render
rows. It is not a per-rendered-frame duration. FPS includes every interval;
frame-time percentiles sum intervening skipped rows between rendered rows.
These measure render-submission cadence, not independent LCD completion times.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import statistics
import sys
from pathlib import Path

MIN_REPEATS = 3
MIN_SECONDS = 60.0
WARMUP_SECONDS = 5.0
MIN_GAIN_FPS = 5.0
TAIL_REGRESSION_RATIO = 1.05
COUNTS = ("draws", "triangles", "skinned_vertices", "pose_evaluations", "pose_reuses")
OPTIONAL_COUNTS = ("cpu_skinned_vertices", "gpu_skin_vertex_references")
OBJECT_GPU_COLUMNS = ("object_gpu_enabled", "object_gpu_models_loaded", "object_gpu_fallbacks", "object_gpu_vertex_references")
TEXTURE_COLUMNS = ("texture_enabled", "texture_variants_loaded", "texture_variant_fallbacks", "texture_source_bytes", "texture_candidate_bytes")
# Course texture mip chains (include/mkw/texture_mips.hpp): marker state, then cumulative counters.
MIP_COLUMNS = ("texture_mips_enabled", "texture_mips_nearest", "texture_mips_loaded", "texture_mips_fallbacks", "texture_mips_level_bytes")
CPU_COLUMNS = (
    "simulation_ms", "ui_prepare_ms", "wait_ms", "course_ms", "scenery_ms",
    "opponents_ms", "items_ms", "player_ms", "effects_ms", "hud_ms",
    "bottom_ms", "submit_ms", "render_thread_ms", "render_begin_wait_ms",
    "record_wait_ms", "worker_ms",
)
NUMERIC_COLUMNS = (
    "course", "frame", "rendered", "interval_ms", "previous_gpu_queue_ms",
    "quality_reduced", "stereo_wanted", "race_frame_cap",
    "model_variants_loaded", "model_variant_triangles", "model_variant_draws",
    "racer_distance_enabled", "racer_distance_limit", "racer_distance_culled",
) + COUNTS + CPU_COLUMNS
IDENTITY_COLUMNS = ("build_id", "content_id", "version")
EXPERIMENT_COLUMNS = {"racer_distance_enabled", "racer_distance_limit", "render_scale", "render_width", "render_height", "presentation_ticks_per_slot"}
REQUIRED_COLUMNS = set(NUMERIC_COLUMNS) | {"hardware", "model_profile"} | set(IDENTITY_COLUMNS)
CONTEXT_FIELDS = {
    "course", "build_sha256", "replay_sha256", "settings_sha256",
    "stereo_wanted", "race_frame_cap", "quality_reduced",
}


class CaptureError(ValueError):
    """A capture cannot support a comparison."""


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_hash(value, label):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise CaptureError(f"{label} must be a lowercase SHA256 digest")


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        raise CaptureError("No complete consecutive rendered-frame intervals")
    position = (len(ordered) - 1) * fraction
    low = int(position)
    return ordered[low] + (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]) * (position - low)


def read_capture(path, expected_sha256):
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise CaptureError(f"{path}: CSV SHA256 does not match the manifest")
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")), strict=True)
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)):
            raise CaptureError(f"{path}: duplicate CSV columns")
        missing = REQUIRED_COLUMNS - set(headers)
        if missing:
            raise CaptureError(f"{path}: missing runtime metadata/metrics: {', '.join(sorted(missing))}")
        if any(key in headers for key in OBJECT_GPU_COLUMNS) and not set(OBJECT_GPU_COLUMNS) <= set(headers):
            raise CaptureError(f"{path}: incomplete object GPU metadata")
        if any(key in headers for key in TEXTURE_COLUMNS) and not set(TEXTURE_COLUMNS) <= set(headers):
            raise CaptureError(f"{path}: incomplete texture metadata")
        if any(key in headers for key in MIP_COLUMNS) and not set(MIP_COLUMNS) <= set(headers):
            raise CaptureError(f"{path}: incomplete texture mip metadata")
        rows = []
        for line, raw in enumerate(reader, 2):
            if None in raw or any(value is None for value in raw.values()):
                raise CaptureError(f"{path}:{line}: incomplete or overlong CSV row")
            row = dict(raw)
            for key in NUMERIC_COLUMNS + tuple(key for key in OPTIONAL_COUNTS + OBJECT_GPU_COLUMNS + TEXTURE_COLUMNS + MIP_COLUMNS if key in headers):
                try:
                    value = float(raw[key])
                except (ValueError, TypeError):
                    raise CaptureError(f"{path}:{line}: invalid {key}") from None
                if not math.isfinite(value) or value < 0:
                    raise CaptureError(f"{path}:{line}: non-finite or negative {key}")
                row[key] = value
            for key in ("course", "frame", "rendered", "quality_reduced", "stereo_wanted", "race_frame_cap", "racer_distance_culled") + COUNTS + ("model_variants_loaded", "model_variant_triangles", "model_variant_draws") + tuple(key for key in OPTIONAL_COUNTS + OBJECT_GPU_COLUMNS + TEXTURE_COLUMNS + MIP_COLUMNS if key in headers):
                if row[key] != int(row[key]):
                    raise CaptureError(f"{path}:{line}: {key} must be an integer")
            for key in ("rendered", "quality_reduced", "stereo_wanted", "racer_distance_enabled"):
                if row[key] not in (0, 1):
                    raise CaptureError(f"{path}:{line}: {key} must be 0 or 1")
            if row.get("object_gpu_enabled", 0) not in (0, 1):
                raise CaptureError(f"{path}:{line}: object_gpu_enabled must be 0 or 1")
            if row.get("texture_enabled", 0) not in (0, 1):
                raise CaptureError(f"{path}:{line}: texture_enabled must be 0 or 1")
            if row.get("texture_mips_enabled", 0) not in (0, 1) or row.get("texture_mips_nearest", 0) not in (0, 1):
                raise CaptureError(f"{path}:{line}: texture_mips_enabled/nearest must be 0 or 1")
            if row["interval_ms"] <= 0:
                raise CaptureError(f"{path}:{line}: interval_ms must be positive")
            if rows and row["frame"] != rows[-1]["frame"] + rows[-1]["rendered"]:
                raise CaptureError(f"{path}:{line}: missing, duplicate, or reset frame sequence")
            if rows and row["racer_distance_culled"] < rows[-1]["racer_distance_culled"]:
                raise CaptureError(f"{path}:{line}: distance-cull counter reset")
            if rows and row.get("object_gpu_vertex_references", 0) < rows[-1].get("object_gpu_vertex_references", 0):
                raise CaptureError(f"{path}:{line}: object GPU counter reset")
            if rows and any(row.get(key, 0) < rows[-1].get(key, 0) for key in TEXTURE_COLUMNS[1:]):
                raise CaptureError(f"{path}:{line}: texture counter reset")
            if rows and any(row.get(key, 0) < rows[-1].get(key, 0) for key in MIP_COLUMNS[2:]):
                raise CaptureError(f"{path}:{line}: texture mip counter reset")
            rows.append(row)
    except (UnicodeError, csv.Error) as error:
        raise CaptureError(f"{path}: invalid CSV: {error}") from error
    if not rows:
        raise CaptureError(f"{path}: empty capture")
    return rows


def measured_rows(rows, warmup_seconds=WARMUP_SECONDS, seconds=MIN_SECONDS):
    """Keep complete rows after warmup, stopping after at least seconds of time."""
    elapsed = 0.0
    measured_ms = 0.0
    result = []
    for row in rows:
        before = elapsed
        elapsed += row["interval_ms"]
        if before + 1e-7 < warmup_seconds * 1000:
            continue
        result.append(row)
        measured_ms += row["interval_ms"]
        if measured_ms + 1e-7 >= seconds * 1000:
            return result
    raise CaptureError(f"Only {measured_ms / 1000:.3f}s after warmup; need at least {seconds:.0f}s")


def summarize_rows(rows):
    """Aggregate ALL loop rows for elapsed time and simulation/CPU work."""
    elapsed_ms = sum(row["interval_ms"] for row in rows)
    rendered = [row for row in rows if row["rendered"]]
    if len(rendered) < 2:
        raise CaptureError("Need at least two rendered rows")
    elapsed = 0.0
    last_rendered = None
    intervals = []
    for row in rows:
        elapsed += row["interval_ms"]
        if row["rendered"]:
            if last_rendered is not None:
                intervals.append(elapsed - last_rendered)
            last_rendered = elapsed
    # Stage medians for render-only stages exclude intentional skipped zeros.
    # Simulation medians retain all rows; totals retain all rows in every stage.
    cpu = {}
    for key in CPU_COLUMNS:
        samples = rows if key == "simulation_ms" else rendered
        cpu[key] = {
            "median_ms": statistics.median(row[key] for row in samples),
            "total_ms_per_second": sum(row[key] for row in rows) * 1000 / elapsed_ms,
        }
    counts = {}
    for key in COUNTS + tuple(key for key in OPTIONAL_COUNTS if key in rows[0]):
        total = sum(row[key] for row in rendered)
        counts[key] = {"mean_per_rendered_frame": total / len(rendered), "per_second": total * 1000 / elapsed_ms}
    return {
        "measured_seconds": elapsed_ms / 1000,
        "loop_rows": len(rows), "rendered_frames": len(rendered),
        "skipped_rows": len(rows) - len(rendered),
        "sustained_fps": len(rendered) * 1000 / elapsed_ms,
        "rendered_interval_p95_ms": percentile(intervals, 0.95),
        "rendered_interval_p99_ms": percentile(intervals, 0.99),
        "median_gpu_queue_ms": statistics.median(row["previous_gpu_queue_ms"] for row in rendered),
        "cpu": cpu, "work": counts,
        "candidate_triangle_submissions": sum(row["model_variant_triangles"] for row in rendered),
        "candidate_draw_submissions": sum(row["model_variant_draws"] for row in rendered),
        "distance_culls_in_window": rows[-1]["racer_distance_culled"] - rows[0]["racer_distance_culled"],
        # Cumulative render-thread counter, sampled on every loop row. Never
        # sum it as if it represented work performed in one frame.
        "object_gpu_references_in_window": rows[-1].get("object_gpu_vertex_references", 0) - rows[0].get("object_gpu_vertex_references", 0),
        # These are cumulative successful-load totals, not texture traffic or
        # currently resident VRAM. A texture can load before the warmup window.
        "texture_load_totals": {key: rows[-1].get(key, 0) for key in TEXTURE_COLUMNS[1:]},
        "texture_mip_totals": {key: rows[-1].get(key, 0) for key in MIP_COLUMNS[2:]},
    }


def validate_context(context):
    if not isinstance(context, dict) or not CONTEXT_FIELDS <= context.keys():
        raise CaptureError(f"context requires {', '.join(sorted(CONTEXT_FIELDS))}")
    for key in ("build_sha256", "replay_sha256", "settings_sha256"):
        require_hash(context[key], key)
    if type(context["course"]) is not int or context["course"] < 0:
        raise CaptureError("context.course must be a nonnegative integer")
    if type(context["stereo_wanted"]) is not bool or type(context["quality_reduced"]) is not bool:
        raise CaptureError("context stereo_wanted and quality_reduced must be booleans")
    if context["race_frame_cap"] != 30:
        raise CaptureError("Old 3DS capture must retain the current 30 FPS race cap")
    if context["quality_reduced"] is not True:
        raise CaptureError("Use normal Old 3DS quality in both captures; full-quality.txt confounds a model-only comparison")


def validate_rows(rows, context, role, experiment="model_decimation", candidate_distance=None, mip_filter="linear"):
    # Mip chains ride on the Old 3DS model overlay: both arms keep it, only
    # o3ds-texmips-off.txt differs (same SD package, include/mkw/texture_mips.hpp).
    profile = "o3ds_candidate" if (experiment == "model_decimation" and role == "candidate") or experiment == "texture_mips" else "original"
    identity = {key: rows[0][key] for key in IDENTITY_COLUMNS}
    if any(not value.strip() or value.lower() in ("unknown", "synthetic", "emulator") for value in identity.values()):
        raise CaptureError(f"{role}: missing or invalid compiled runtime identity")
    experiments = {key: rows[0][key] for key in rows[0] if (key in EXPERIMENT_COLUMNS or key.startswith("experiment_")) and key not in ("racer_distance_enabled", "racer_distance_limit")}
    for row in rows:
        if any(row[key] != identity[key] for key in IDENTITY_COLUMNS):
            raise CaptureError(f"{role}: runtime identity changed during capture")
        if any(row[key] != value for key, value in experiments.items()):
            raise CaptureError(f"{role}: additional experiment settings changed during capture")
        if row["hardware"] != "old3ds":
            raise CaptureError(f"{role}: hardware must be successfully detected old3ds")
        if row["model_profile"] != profile:
            raise CaptureError(f"{role}: expected model_profile={profile}")
        distance_enabled = experiment == "racer_distance" and role == "candidate"
        if row["racer_distance_enabled"] != distance_enabled:
            raise CaptureError(f"{role}: distance-cull marker conflicts with the declared experiment")
        expected_distance = candidate_distance if distance_enabled else 25000
        if row["racer_distance_limit"] != expected_distance:
            raise CaptureError(f"{role}: distance limit differs from the declared experiment")
        object_gpu_enabled = experiment == "object_gpu_skin" and role == "candidate"
        if row.get("object_gpu_enabled", 0) != object_gpu_enabled:
            raise CaptureError(f"{role}: object GPU marker conflicts with the declared experiment")
        if not object_gpu_enabled and any(row.get(key, 0) for key in OBJECT_GPU_COLUMNS[1:]):
            raise CaptureError(f"{role}: object GPU work occurred without its experiment")
        if experiment == "object_gpu_skin" and not set(OBJECT_GPU_COLUMNS + OPTIONAL_COUNTS) <= row.keys():
            raise CaptureError(f"{role}: object GPU experiment requires skin work metadata")
        texture_enabled = experiment == "texture_reduction" and role == "candidate"
        if row.get("texture_enabled", 0) != texture_enabled:
            raise CaptureError(f"{role}: texture marker conflicts with the declared experiment")
        if not texture_enabled and any(row.get(key, 0) for key in TEXTURE_COLUMNS[1:]):
            raise CaptureError(f"{role}: candidate textures occurred without their experiment")
        if experiment == "texture_reduction" and not set(TEXTURE_COLUMNS) <= row.keys():
            raise CaptureError(f"{role}: texture experiment requires texture metadata")
        mips_enabled = experiment == "texture_mips" and role == "candidate"
        if experiment == "texture_mips":
            if not set(MIP_COLUMNS) <= row.keys():
                raise CaptureError(f"{role}: texture mip experiment requires texture mip metadata")
            if row["texture_mips_enabled"] != mips_enabled:
                raise CaptureError(f"{role}: texture mip marker conflicts with the declared experiment")
            if mips_enabled and row["texture_mips_nearest"] != (mip_filter == "nearest"):
                raise CaptureError(f"{role}: texture mip filter differs from the declared mip_filter")
        if not mips_enabled and row.get("texture_mips_loaded", 0):
            raise CaptureError(f"{role}: texture mip chains loaded without their experiment (add o3ds-texmips-off.txt)")
        for key in ("course", "stereo_wanted", "race_frame_cap", "quality_reduced"):
            if row[key] != context[key]:
                raise CaptureError(f"{role}: {key} differs from matched context")
        if profile == "original" and (row["model_variants_loaded"] or row["model_variant_triangles"] or row["model_variant_draws"]):
            raise CaptureError(f"{role}: candidate geometry was loaded or submitted")
    if profile == "o3ds_candidate" and not any(row["model_variants_loaded"] > 0 and row["model_variant_triangles"] > 0 for row in rows):
        raise CaptureError("candidate: no reduced geometry was actually submitted")
    if experiment == "object_gpu_skin" and role == "candidate" and not any(row["object_gpu_models_loaded"] > 0 and row["object_gpu_vertex_references"] > 0 for row in rows):
        raise CaptureError("candidate: no lit course-object GPU geometry was actually submitted")
    if experiment == "texture_mips" and role == "candidate" and not (rows[-1]["texture_mips_loaded"] > 0 and rows[-1]["texture_mips_level_bytes"] > 0):
        raise CaptureError("candidate: no texture mip chains were actually loaded")
    if experiment == "texture_reduction" and role == "candidate":
        last = rows[-1]
        if not (last["texture_variants_loaded"] > 0 and 0 < last["texture_candidate_bytes"] < last["texture_source_bytes"]):
            raise CaptureError("candidate: no reduced texture bytes were actually loaded")
    identity["additional_experiments"] = experiments
    return identity


def compare(manifest, root=Path("."), overrides=None):
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise CaptureError("Manifest schema_version must be 1")
    experiment = manifest.get("experiment", "model_decimation")
    if experiment not in ("model_decimation", "racer_distance", "object_gpu_skin", "texture_reduction", "texture_mips"):
        raise CaptureError("experiment must be model_decimation, racer_distance, object_gpu_skin, texture_reduction or texture_mips")
    mip_filter = manifest.get("mip_filter", "linear")
    if mip_filter not in ("linear", "nearest"):
        raise CaptureError("mip_filter must be linear or nearest")
    candidate_distance = manifest.get("candidate_distance")
    if experiment == "racer_distance" and (type(candidate_distance) not in (int, float) or not math.isfinite(candidate_distance) or not 1000 <= candidate_distance <= 25000):
        raise CaptureError("racer_distance experiment requires candidate_distance between 1000 and 25000")
    attestation = manifest.get("attestation", {})
    if not isinstance(attestation, dict) or any(not isinstance(attestation.get(key), str) or not attestation[key].strip() for key in ("execution_environment", "tester", "device_id")):
        raise CaptureError("attestation requires execution_environment, tester and device_id")
    pairs = manifest.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise CaptureError("Manifest requires at least one baseline/candidate pair")
    if overrides is not None and len(overrides) != len(pairs):
        raise CaptureError("Number of --pair overrides must match manifest pairs")
    reasons = []
    if attestation["execution_environment"] != "real_old3ds":
        reasons.append("Physical Old 3DS attestation missing: synthetic/emulator/unknown captures cannot qualify")
    scenes = {}
    seen_paths, seen_hashes = set(), set()
    global_assets = None
    global_identity = None
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict) or not isinstance(pair.get("scene_id"), str) or not pair["scene_id"].strip() or type(pair.get("demanding")) is not bool:
            raise CaptureError("Every pair requires scene_id and a boolean demanding designation")
        context = pair.get("context")
        validate_context(context)
        scene = scenes.setdefault(pair["scene_id"], {"demanding": pair["demanding"], "context": context, "pairs": []})
        if scene["context"] != context or scene["demanding"] != pair["demanding"]:
            raise CaptureError(f"{pair['scene_id']}: repeated captures have mismatched context")
        result = {}
        assets = []
        for role_index, role in enumerate(("baseline", "candidate")):
            spec = pair.get(role)
            if not isinstance(spec, dict) or not isinstance(spec.get("csv"), str) or not spec["csv"]:
                raise CaptureError(f"Pair {index + 1} {role}: CSV path required")
            require_hash(spec.get("sha256"), f"{role}.sha256")
            require_hash(spec.get("assets_sha256"), f"{role}.assets_sha256")
            assets.append(spec["assets_sha256"])
            path = Path(overrides[index][role_index]).resolve() if overrides else (Path(root) / spec["csv"]).resolve()
            if path in seen_paths or spec["sha256"] in seen_hashes:
                raise CaptureError("Repeated captures must use distinct files and distinct CSV contents")
            seen_paths.add(path)
            seen_hashes.add(spec["sha256"])
            rows = read_capture(path, spec["sha256"])
            identity = validate_rows(rows, context, role, experiment, candidate_distance, mip_filter)
            identity["build_sha256"] = context["build_sha256"]
            if global_identity is not None and identity != global_identity:
                raise CaptureError("All captures must have matching compiled build/content/version and binary SHA256")
            global_identity = identity
            measured = measured_rows(rows)
            validate_rows(measured, context, role, experiment, candidate_distance, mip_filter)
            result[role] = summarize_rows(measured)
            result[role]["csv"] = str(path)
            result[role]["sha256"] = spec["sha256"]
        if experiment in ("model_decimation", "texture_reduction") and assets[0] == assets[1]:
            raise CaptureError("Baseline and candidate asset hashes must differ")
        if experiment in ("racer_distance", "object_gpu_skin", "texture_mips") and assets[0] != assets[1]:
            raise CaptureError("Engine-only captures must use identical assets")
        if global_assets is not None and assets != global_assets:
            raise CaptureError("All captures must compare the same baseline/candidate asset sets")
        global_assets = assets
        base, candidate = result["baseline"], result["candidate"]
        result["fps_gain"] = candidate["sustained_fps"] - base["sustained_fps"]
        label = f"{pair['scene_id']} pair {len(scene['pairs']) + 1}"
        if result["fps_gain"] < -1e-6:
            reasons.append(f"{label}: sustained FPS regressed ({result['fps_gain']:+.3f})")
        if pair["demanding"] and result["fps_gain"] + 1e-6 < MIN_GAIN_FPS:
            reasons.append(f"{label}: {result['fps_gain']:+.3f} FPS is below +{MIN_GAIN_FPS:.0f} FPS")
        for metric in ("rendered_interval_p95_ms", "rendered_interval_p99_ms"):
            if candidate[metric] > base[metric] * TAIL_REGRESSION_RATIO + 1e-6:
                reasons.append(f"{label}: {metric} regressed by more than 5%")
        if experiment == "model_decimation" and candidate["work"]["triangles"]["mean_per_rendered_frame"] >= base["work"]["triangles"]["mean_per_rendered_frame"]:
            reasons.append(f"{label}: mean submitted triangle count did not decrease")
        if experiment == "racer_distance" and candidate["distance_culls_in_window"] <= 0:
            reasons.append(f"{label}: no distance culls occurred within the measured window")
        if experiment == "object_gpu_skin":
            if candidate["object_gpu_references_in_window"] <= 0:
                reasons.append(f"{label}: no lit course-object GPU submissions occurred within the measured window")
            if candidate["work"]["cpu_skinned_vertices"]["mean_per_rendered_frame"] >= base["work"]["cpu_skinned_vertices"]["mean_per_rendered_frame"]:
                reasons.append(f"{label}: CPU skinning work did not decrease")
        scene["pairs"].append(result)
    if not any(scene["demanding"] for scene in scenes.values()):
        reasons.append("At least one demanding scene must be designated before comparison")
    for name, scene in scenes.items():
        if len(scene["pairs"]) < MIN_REPEATS:
            reasons.append(f"{name}: need at least {MIN_REPEATS} distinct matched pairs")
        scene["median_fps_gain"] = statistics.median(pair["fps_gain"] for pair in scene["pairs"])
        for role in ("baseline", "candidate"):
            scene[role + "_median_fps"] = statistics.median(pair[role]["sustained_fps"] for pair in scene["pairs"])
    return {
        "schema_version": 1,
        "experiment": experiment, "candidate_distance": candidate_distance if experiment == "racer_distance" else None,
        "mip_filter": mip_filter if experiment == "texture_mips" else None,
        "status": "eligible_for_visual_review" if not reasons else "not_eligible",
        "eligible_for_visual_review": not reasons,
        "automatic_activation": False,
        "attestation": attestation,
        "runtime_identity": global_identity,
        "criteria": {"minimum_pairs_per_scene": MIN_REPEATS, "warmup_seconds": WARMUP_SECONDS,
                     "minimum_measured_seconds": MIN_SECONDS, "minimum_demanding_scene_gain_fps": MIN_GAIN_FPS,
                     "maximum_p95_p99_regression_percent": 5},
        "limitations": [
            "Physical hardware, replay/settings and binary identity are operator attestations bound to CSV hashes, not independently verified by this tool.",
            "FPS and tails measure render-submission cadence from all loop intervals; they do not independently measure LCD presentation completion.",
            "GPU and render-thread samples can lag one frame; CPU stages overlap across threads and must not be summed as frame latency.",
            "This result never approves visuals or enables reduced assets. Visual review and an explicit release decision remain required.",
        ],
        "reasons": reasons, "scenes": scenes,
    }


def template():
    return {
        "schema_version": 1,
        "experiment": "model_decimation",
        "attestation": {"execution_environment": "unknown", "tester": "REPLACE", "device_id": "REPLACE"},
        "pairs": [{
            "scene_id": "REPLACE", "demanding": True,
            "context": {"course": 0, "build_sha256": "REPLACE_WITH_BINARY_SHA256", "replay_sha256": "REPLACE_WITH_REPLAY_SHA256",
                        "settings_sha256": "REPLACE_WITH_SETTINGS_SHA256", "stereo_wanted": False, "race_frame_cap": 30, "quality_reduced": True},
            "baseline": {"csv": "baseline-1.csv", "sha256": "REPLACE_WITH_CSV_SHA256", "assets_sha256": "REPLACE_WITH_BASELINE_ASSET_MANIFEST_SHA256"},
            "candidate": {"csv": "candidate-1.csv", "sha256": "REPLACE_WITH_CSV_SHA256", "assets_sha256": "REPLACE_WITH_CANDIDATE_ASSET_MANIFEST_SHA256"},
        }],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, help="JSON context manifest containing matched capture pairs")
    parser.add_argument("--pair", nargs=2, action="append", metavar=("BASELINE", "CANDIDATE"), help="Override CSV paths in manifest order; hashes still must match")
    parser.add_argument("--output", type=Path, help="Write JSON report here (otherwise stdout)")
    parser.add_argument("--template", action="store_true", help="Print a manifest template; copy its pair for each of at least three independent runs per scene")
    args = parser.parse_args(argv)
    if args.template:
        print(json.dumps(template(), indent=2))
        return 0
    if not args.manifest:
        parser.error("--manifest is required unless --template is used")
    try:
        manifest = json.loads(args.manifest.read_text())
        report = compare(manifest, args.manifest.resolve().parent, args.pair)
    except (CaptureError, OSError, ValueError) as error:
        report = {"schema_version": 1, "status": "invalid_capture", "eligible_for_visual_review": False,
                  "automatic_activation": False, "reasons": [str(error)]}
    serialized = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(serialized)
    else:
        print(serialized, end="")
    return 0 if report["eligible_for_visual_review"] else 2


if __name__ == "__main__":
    sys.exit(main())
