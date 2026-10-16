#!/usr/bin/env python3
"""Read-only census of cooked skin banks; no runtime or FPS claims.

Layouts and conditional path checks follow src/animation.cpp, object_bank.cpp,
course_objects.cpp and renderer.cpp. No game content is extracted or rewritten.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import struct

MAX_GPU_MATRICES = 19


class FormatError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise FormatError(message)


class Reader:
    def __init__(self, data):
        self.data, self.at = data, 0

    def take(self, size):
        require(0 <= size <= len(self.data) - self.at, "truncated payload")
        result = memoryview(self.data)[self.at:self.at + size]
        self.at += size
        return result

    def words(self, count):
        return struct.unpack("<" + "I" * count, self.take(count * 4))

    def tag(self, expected):
        require(self.take(4) == expected, "missing " + expected.decode())

    def end(self):
        require(self.at == len(self.data), "trailing bytes")


def parse_bank(data):
    r = Reader(data)
    magic = bytes(r.take(4))
    version, vertices, matrices = r.words(3)
    require(0 < matrices <= 128 and vertices <= 65535, "invalid skin dimensions")
    root_slot = None
    if magic == b"DSKM":
        require(2 <= version <= 5 and len(data) <= 12 * 1024 * 1024, "unsupported DSKM")
        clips, shapes, faces = r.words(3)
        require(0 < clips <= 64 and shapes <= 64 and faces <= 64, "invalid DSKM counts")
        if version == 5:
            root_slot = struct.unpack("<i", r.take(4))[0]
            require(-1 <= root_slot < matrices, "invalid root slot")
    elif magic == b"DOBJ":
        require(version == 1 and len(data) <= 8 * 1024 * 1024, "unsupported DOBJ")
        shapes, nodes, clips, pats, srts, clrs = r.words(6)
        require(shapes <= 256 and nodes <= 256 and max(clips, pats, srts, clrs) <= 64, "invalid DOBJ counts")
    else:
        raise FormatError("unknown bank magic")
    sources = r.take(vertices * 28)
    ids = [struct.unpack_from("<I", sources, i * 28 + 24)[0] for i in range(vertices)]
    require(all(i < matrices for i in ids), "out of range skin matrix")
    ranges = [r.words(2) for _ in range(shapes)]
    require(not vertices or all(first <= vertices and count <= vertices - first for first, count in ranges), "out of range shape")
    result = dict(format=magic.decode(), version=version, vertices=vertices, matrices=matrices,
                  shapes=shapes, clips=clips, source_matrix_ids=ids, root_slot=root_slot,
                  unique_position_matrix_bindings=len({bytes(sources[i * 28:i * 28 + 12]) + bytes(sources[i * 28 + 24:i * 28 + 28]) for i in range(vertices)}),
                  unique_position_normal_matrix_bindings=len({bytes(sources[i * 28:(i + 1) * 28]) for i in range(vertices)}))
    if magic == b"DSKM":
        frame_counts = []
        shown = None
        visibility_constant = True

        def driver_clip(stride, inspect_visibility=False):
            nonlocal shown, visibility_constant
            r.take(32)
            duration, _loop, count = r.words(3)
            require(0 < count <= 2000 and duration == count, "invalid DSKM clip")
            payload = r.take(count * stride)
            if inspect_visibility:
                for frame in range(count):
                    flags = tuple(bool(v) for v in struct.unpack_from("<" + "I" * shapes, payload, frame * stride + matrices * 84))
                    if shown is None:
                        shown = flags
                    visibility_constant &= flags == shown
            return count

        for _ in range(clips):
            frame_counts.append(driver_clip(matrices * 84 + shapes * 4, True))
        for _ in range(faces):
            driver_clip(shapes * 4)
        hair_nodes = hair_clips = uv_clips = 0
        if version >= 3:
            r.tag(b"HLOC")
            hair_nodes, hair_clips = r.words(2)
            require(bool(hair_nodes) == bool(hair_clips) and hair_nodes <= 8 and hair_clips <= 2, "invalid hair counts")
            hair = []
            for _ in range(hair_nodes):
                matrix, parent = r.words(2)
                require(matrix < matrices and parent < matrices and matrix != parent, "invalid hair binding")
                require(all(old_matrix != matrix and old_parent != matrix for old_matrix, old_parent in hair), "invalid hair order")
                hair.append((matrix, parent))
            for _ in range(hair_clips):
                r.take(32)
                duration, _loop, count = r.words(3)
                require(0 < count <= 2000 and duration == count, "invalid hair clip")
                r.take((count + 1) * hair_nodes * 84)
        if version >= 4:
            r.tag(b"UVAN")
            uv_clips, = r.words(1)
            require(uv_clips <= 64, "invalid UV clip count")
            for _ in range(uv_clips):
                driver_clip(shapes * 24)
        if version == 5 and root_slot == -1:
            r.tag(b"ROOT")
            require(r.words(1)[0] == clips, "invalid root track count")
            r.take(sum(frame_counts) * 12)
        result.update(hair_clips=hair_clips, uv_clips=uv_clips, visibility_constant=visibility_constant)
    else:
        r.take(matrices * 48)
        for _ in range(nodes):
            node = r.take(140)
            parent, slot = struct.unpack_from("<ii", node, 32)
            require(parent < nodes and slot < matrices, "invalid node")
        frame_counts = []

        def object_header(extra, max_frames=20000):
            r.take(32)
            values = r.words(2 + extra)
            require(0 < values[0] <= max_frames, "invalid DOBJ clip frames")
            return values

        def sample_count(frames, step):
            require(0 < step <= 64, "invalid sampling step")
            return 1 if frames <= 1 else (frames - 1 + step - 1) // step + 1

        def indices(count, limit):
            values = r.words(count)
            require(all(v < limit for v in values), "out of range clip binding")

        for _ in range(clips):
            frames, _loop, step, count = object_header(2)
            frame_counts.append(frames)
            require(count <= matrices, "invalid CHR count")
            indices(count, matrices)
            r.take(matrices * 48 + sample_count(frames, step) * count * 48)
        for _ in range(pats):
            frames, _loop = object_header(0)
            r.take(frames * shapes * 8)
        for _ in range(srts):
            frames, _loop, step, count = object_header(2, 1 << 24)
            require(count <= shapes, "invalid SRT count")
            indices(count, shapes)
            r.take(sample_count(frames, step) * count * 48)
        for _ in range(clrs):
            frames, _loop, count = object_header(1)
            require(count <= shapes, "invalid CLR count")
            indices(count, shapes)
            r.take(count * 4 + frames * count * 12)
        morphs = 0
        if r.at < len(data):
            r.tag(b"MORF")
            morphs, = r.words(1)
            require(morphs <= clips, "invalid morph count")
            for _ in range(morphs):
                clip, count, frames = r.words(3)
                require(clip < clips and frames == frame_counts[clip] and count <= vertices, "invalid morph record")
                indices(count, vertices)
                r.take(count * 24 + frames * 4)
        result.update(nodes=nodes, morph_records=morphs)
    r.end()
    return result


def parse_model(data):
    r = Reader(data)
    r.tag(b"GMDL")
    version, vertices, index_count, sub_count, textures = r.words(5)
    record = {3: 44, 4: 56, 5: 92}.get(version)
    require(record and 0 < vertices <= 65535 and index_count <= 3000000 and index_count % 3 == 0
            and sub_count <= 2048 and textures <= 512, "invalid GMDL header")
    submeshes = []
    for _ in range(sub_count):
        raw = r.take(record)
        first, count, texture, _flags, secondary = struct.unpack_from("<IIiIi", raw)
        require(first <= index_count and count <= index_count - first and count % 3 == 0, "invalid submesh indices")
        require(-1 <= texture < textures and -1 <= secondary < textures, "invalid texture index")
        lighting = struct.unpack_from("<I", raw, 44)[0] if version >= 4 else 0
        submeshes.append(dict(first=first, count=count, lighting=lighting))
    r.take(textures * 64 + vertices * 44)
    indices = struct.unpack("<" + "H" * index_count, r.take(index_count * 2))
    require(all(i < vertices for i in indices), "out of range GMDL index")
    r.end()
    return dict(version=version, vertices=vertices, indices=indices, submeshes=submeshes)


def census_pair(model, bank):
    require(bank["vertices"] in (0, model["vertices"]), "bank/model vertex mismatch")
    require(bank["format"] != "DSKM" or (bank["vertices"] == model["vertices"] and len(model["submeshes"]) <= 64), "DSKM/model dimensions rejected by runtime")
    ids = bank["source_matrix_ids"]
    referenced = set()
    mixed = triangles = 0
    per_submesh = []
    for sub in model["submeshes"]:
        indices = model["indices"][sub["first"]:sub["first"] + sub["count"]]
        used = {ids[i] for i in indices} if ids else set()
        referenced.update(used)
        triangles += len(indices) // 3
        if ids:
            mixed += sum(len({ids[a], ids[b], ids[c]}) > 1 for a, b, c in zip(indices[::3], indices[1::3], indices[2::3]))
        per_submesh.append(len(used))
    # Exactly the ObjectAssets::load litNormals predicate, including empty spans.
    lit = any(s["lighting"] & 1 and s["lighting"] & 12 for s in model["submeshes"])
    eligible = bool(ids) and bank["matrices"] <= MAX_GPU_MATRICES
    if not ids:
        conditional_path = "material_only"
    elif bank["format"] == "DOBJ":
        conditional_path = "rigid_parts" if not mixed else "cpu_lit" if lit else "gpu_unlit" if eligible else "cpu_unlit_palette_limit"
    else:
        conditional_path = "gpu_if_prepare_skinning_called_and_allocation_succeeds" if eligible else "cpu_if_prepare_skinning_called_palette_limit"
    return dict(vertices=model["vertices"], triangles=triangles, submeshes=len(model["submeshes"]),
                bank_vertices=bank["vertices"], full_palette_matrices=bank["matrices"],
                all_source_matrices=len(set(ids)), index_referenced_matrices=len(referenced),
                index_referenced_matrix_ids=sorted(referenced), per_submesh_matrix_counts=per_submesh,
                mixed_matrix_triangles=mixed, course_object_lit_normals=bool(lit),
                conditional_path=conditional_path,
                source_vertex_buffer_bytes=model["vertices"] * 44,
                unique_position_matrix_bindings=bank["unique_position_matrix_bindings"],
                unique_position_normal_matrix_bindings=bank["unique_position_normal_matrix_bindings"],
                lit_weighted_gpu_offload_candidate=bank["format"] == "DOBJ" and bool(mixed) and bool(lit) and eligible,
                upload_compaction_to_19_possible=bool(referenced) and bank["matrices"] > MAX_GPU_MATRICES and len(referenced) <= MAX_GPU_MATRICES,
                all_source_compaction_to_19_possible=bool(ids) and bank["matrices"] > MAX_GPU_MATRICES and len(set(ids)) <= MAX_GPU_MATRICES,
                driver_rigid_conditions_met=bool(ids) and not mixed and not bank.get("hair_clips", 0) and bank.get("visibility_constant", False))


def asset_listing(assets=None, listing=None):
    if listing:
        mapping = json.loads(Path(listing).read_text())
        require(isinstance(mapping, dict), "listing must be a mapping")
        for relative, source in mapping.items():
            require(isinstance(source, str) and not Path(relative).is_absolute() and ".." not in Path(relative).parts and "\\" not in relative, "invalid listing entry")
        return {relative: Path(source) for relative, source in mapping.items()}
    return {"models/" + p.name: p for p in (Path(assets) / "models").iterdir() if p.is_file() and not p.name.startswith("._")}


def audit(mapping):
    bindings, errors, pairs = {}, [], []
    for name, path in sorted(mapping.items()):
        if not re.fullmatch(r"models/course_\d+_visuals\.list", name):
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            fields = line.split()
            require(len(fields) == 4 and re.fullmatch(r"[a-z0-9_]+", fields[2]) and re.fullmatch(r"-|[a-z0-9_]+", fields[3]), "invalid course visual entry")
            if fields[3] != "-":
                model, bank = f"models/{fields[2]}.gmdl", f"models/{fields[3]}.dobj"
                require(model not in bindings or bindings[model][0] == bank, "conflicting course visual mapping")
                bindings[model] = (bank, "course_visuals_list")
    if "models/bomb_blast.gmdl" in mapping:
        bindings["models/bomb_blast.gmdl"] = ("models/bomb_core.dskm", "race_hud_explicit_alias")
    used_banks = set()
    for name in sorted(n for n in mapping if re.fullmatch(r"models/[^/]+\.gmdl", n)):
        try:
            if name not in bindings:
                stem = name[:-5]
                stems = {stem, re.sub(r"_000$", "", stem)}
                candidates = sorted({s + ext for s in stems for ext in (".dskm", ".dobj") if s + ext in mapping})
                require(len(candidates) <= 1, "ambiguous matching animation banks")
                if not candidates:
                    continue
                bindings[name] = (candidates[0], "filename_convention_only")
            bank_name, evidence = bindings[name]
            require(bank_name in mapping, "missing animation bank " + bank_name)
            model_data, bank_data = mapping[name].read_bytes(), mapping[bank_name].read_bytes()
            model, bank = parse_model(model_data), parse_bank(bank_data)
            result = census_pair(model, bank)
            result.update(model=name, bank=bank_name, mapping_evidence=evidence,
                          bank_format=bank["format"], bank_version=bank["version"],
                          model_sha256=hashlib.sha256(model_data).hexdigest(), bank_sha256=hashlib.sha256(bank_data).hexdigest())
            pairs.append(result)
            used_banks.add(bank_name)
        except (FormatError, OSError) as exc:
            errors.append(dict(model=name, error=str(exc)))
    return dict(schema_version=1, gpu_palette_limit=MAX_GPU_MATRICES, measured_runtime=False,
                limitations=["Static eligibility only: no instance frequency, visibility, animation timing, memory-allocation result or FPS is measured.",
                             "DSKM filename matches do not identify the actual caller; menu rigid partitions, cached effects and race skin preparation differ.",
                             "Referenced matrices are the union over all submesh indices, without pose visibility or runtime culling.",
                             "Compaction concerns GPU uploads and vertex indices only; full pose, hair and node matrices may still be required on CPU.",
                             "Lit/unlit flag is exactly the course-object predicate, not a classification of every shader/material caller.",
                             "Bank structural bounds are validated; floating-point payload correctness and runtime texture loading are not tested.",
                             "Loose models only: packed-only entries require an authoritative package listing; no packs are extracted."],
                summary=dict(pairs=len(pairs), errors=len(errors), paths=dict(Counter(p["conditional_path"] for p in pairs)),
                             upload_compaction_candidates=sum(p["upload_compaction_to_19_possible"] for p in pairs),
                             lit_weighted_gpu_offload_candidates=sum(p["lit_weighted_gpu_offload_candidate"] for p in pairs)),
                models=pairs, errors=errors,
                unmatched_banks=sorted(n for n in mapping if n.endswith((".dskm", ".dobj")) and n not in used_banks))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--assets", type=Path, help="cooked root containing models/")
    source.add_argument("--listing", type=Path, help="authoritative package path-to-source JSON")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = audit(asset_listing(args.assets, args.listing))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"]))
    return bool(report["errors"])


if __name__ == "__main__":
    raise SystemExit(main())
