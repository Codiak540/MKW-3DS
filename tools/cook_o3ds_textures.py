#!/usr/bin/env python3
"""Cook isolated Old 3DS primary-color texture candidates from verified local PNGs.

Packed menu/race originals take precedence exactly as in the runtime. A PNG is
accepted only beside an encoded T3X identical to that final original. Existing
source files and packs are read only; missing provenance is an explicit skip.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import zlib

from PIL import Image
from devkitpro import tool

ROOT = Path(__file__).resolve().parents[1]
SAFE = re.compile(r"[A-Za-z0-9_-]+\Z")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def texture_layout(data):
    if len(data) < 21:
        raise ValueError("truncated T3X")
    count, dimensions, fmt, levels = struct.unpack_from("<HBBB", data)
    width, height = 1 << ((dimensions & 7) + 3), 1 << (((dimensions >> 3) & 7) + 3)
    if count != 1 or dimensions & 0xc0 or fmt not in (12, 13) or levels > 7:
        raise ValueError("not a single ETC color texture")
    if struct.unpack_from("<6H", data, 5) != (width, height, 0, 1024, 1024, 0):
        raise ValueError("atlas or non-full UV coordinates")
    size, = struct.unpack_from("<I", data, 17)
    if min(width, height) >> levels < 8:
        raise ValueError("invalid mip count")
    gpu_bytes = sum((width >> i) * (height >> i) // (2 if fmt == 12 else 1) for i in range(levels + 1))
    if size & 255 or size >> 8 != gpu_bytes or len(data) != 21 + gpu_bytes:
        raise ValueError("compressed or malformed T3X payload")
    return dict(width=width, height=height, format=fmt, levels=levels, gpu_bytes=gpu_bytes)


class Assets:
    def __init__(self, root):
        self.root, self.entries = root, {}
        for name in ("menu.pak", "race.pak"):
            path = root / name
            if not path.is_file():
                continue
            with path.open("rb") as stream:
                header = stream.read(12)
                if len(header) != 12:
                    raise ValueError("truncated asset pack")
                magic, count, name_bytes = struct.unpack("<4sII", header)
                if magic != b"MPK1" or not 0 < count <= 200000 or name_bytes > 16 << 20:
                    raise ValueError("invalid asset pack header")
                table, names = stream.read(count * 12), stream.read(name_bytes)
                if len(table) != count * 12 or len(names) != name_bytes or not names or names[-1]:
                    raise ValueError("invalid asset pack table")
                minimum = 12 + count * 12 + name_bytes
                for i in range(count):
                    at, offset, size = struct.unpack_from("<III", table, i * 12)
                    if at >= name_bytes or offset < minimum or offset + size > path.stat().st_size:
                        raise ValueError("asset pack entry out of bounds")
                    relative = names[at:names.index(0, at)].decode("utf-8")
                    if Path(relative).is_absolute() or ".." in Path(relative).parts or "\\" in relative:
                        raise ValueError("invalid packed asset path")
                    self.entries.setdefault(relative, (path, offset, size))

    def read(self, relative):
        if relative in self.entries:
            path, offset, size = self.entries[relative]
            with path.open("rb") as stream:
                stream.seek(offset)
                data = stream.read(size)
                if len(data) != size:
                    raise ValueError("packed asset changed during read")
                return data
        return (self.root / relative).read_bytes()

    def models(self):
        return sorted({"models/" + p.name for p in (self.root / "models").glob("*.gmdl")} |
                      {n for n in self.entries if re.fullmatch(r"models/[^/]+\.gmdl", n)})


def color_textures(assets):
    primary, secondary = set(), set()
    for path in assets.models():
        data = assets.read(path)
        if len(data) < 24:
            raise ValueError("truncated model " + path)
        magic, version, vertices, indices, sub_count, count = struct.unpack_from("<4s5I", data)
        record = {3: 44, 4: 56, 5: 92}.get(version)
        if magic != b"GMDL" or not record or not 0 < vertices <= 65535 or sub_count > 2048 or count > 512:
            raise ValueError("invalid model " + path)
        names_at = 24 + sub_count * record
        if len(data) != names_at + count * 64 + vertices * 44 + indices * 2:
            raise ValueError("invalid original model length " + path)
        names = []
        for i in range(count):
            raw = data[names_at + i * 64:names_at + (i + 1) * 64]
            if b"\0" not in raw:
                raise ValueError("unterminated model texture")
            name = raw.split(b"\0", 1)[0].decode("ascii")
            if not SAFE.fullmatch(name):
                raise ValueError("unsafe model texture name")
            names.append(name)
        for i in range(sub_count):
            at = 24 + i * record
            first, size, texture, _flags, other = struct.unpack_from("<IIiIi", data, at)
            if first + size > indices or size % 3 or not -1 <= texture < count or not -1 <= other < count:
                raise ValueError("invalid model submesh")
            if size and texture >= 0:
                primary.add(names[texture])
            if other >= 0:
                secondary.add(names[other])
    return primary, secondary


def protected_name(name):
    return name.startswith(("hud_", "flow_", "race_")) or (name.startswith("ui_") and not name.startswith(("ui_driver_", "ui_back_", "ui_machine_")))


def cook_one(name, assets, sources, output):
    original = assets.read("textures/" + name + ".t3x")
    row = dict(texture=name, original_sha256=digest(original), status="skipped")
    if protected_name(name):
        return dict(row, reason="UI/HUD protection")
    try:
        layout = texture_layout(original)
    except ValueError as error:
        return dict(row, reason=str(error))
    if max(layout["width"], layout["height"]) <= 32:
        return dict(row, reason="already small")
    png = None
    for source in sources:
        encoded = source / "textures" / (name + ".t3x")
        image = encoded.with_suffix(".png")
        if image.is_file() and encoded.is_file() and encoded.read_bytes() == original:
            png = image
            break
    if png is None:
        return dict(row, reason="no PNG beside an exact matching final T3X")
    with Image.open(png) as image:
        if image.size != (layout["width"], layout["height"]):
            return dict(row, reason="matching-source PNG dimensions differ")
        scale = 2
        while max(image.size) // scale > 128:
            scale *= 2
        size = tuple(max(8, value // scale) for value in image.size)
        # Premultiplication keeps translucent edges free of dark halos. BOX
        # retains the authored palette and readable low-resolution silhouettes.
        reduced = image.convert("RGBA").convert("RGBa").resize(size, Image.Resampling.BOX).convert("RGBA")
    with tempfile.TemporaryDirectory(prefix="o3ds-texture-", dir=output) as directory:
        path = Path(directory) / "color.png"
        reduced.save(path)
        target = path.with_suffix(".t3x")
        command = [str(tool("tex3ds")), "-f", "etc1a4" if layout["format"] == 13 else "etc1", "-q", "high", "-z", "none"]
        if layout["levels"] and min(size) > 8:
            command += ["-m", "box"]
        subprocess.run(command + ["-o", str(target), str(path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        payload = target.read_bytes()
    actual = texture_layout(payload)
    if (actual["width"], actual["height"]) != size or actual["format"] != layout["format"] or actual["levels"] > layout["levels"] or actual["gpu_bytes"] >= layout["gpu_bytes"]:
        raise ValueError("encoded candidate differs from requested layout: " + name)
    wrapped = struct.pack("<4s5I", b"OTEX", 1, len(original), zlib.crc32(original), len(payload), zlib.crc32(payload)) + payload
    relative = "textures/o3ds/" + name + ".otex"
    destination = output / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(wrapped)
    return dict(row, status="candidate", source_png=str(png.resolve()), source_png_sha256=digest(png.read_bytes()),
                output=relative, sha256=digest(wrapped), original=layout, candidate=actual)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, action="append")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--limit", type=int, help="smoke-test the first N primary textures")
    args = parser.parse_args()
    if args.jobs < 1 or args.limit is not None and args.limit < 1:
        parser.error("jobs and limit must be positive")
    if args.assets.resolve() == args.output.resolve():
        parser.error("output must be separate from authored assets")
    sources = args.source_root or [ROOT / "build/roster", ROOT / "sd-card/3ds/mkw-3ds", ROOT / "build/vfx", ROOT / "build/garage", ROOT / "build/item-lighting"]
    assets = Assets(args.assets)
    primary, secondary = color_textures(assets)
    names = sorted(primary - secondary)
    if args.limit:
        names = names[:args.limit]
    args.output.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.jobs) as workers:
        rows = list(workers.map(lambda name: cook_one(name, assets, sources, args.output), names))
    selected = [r for r in rows if r["status"] == "candidate"]
    report = dict(schema_version=1, experimental=True, measured_fps_gain=None, policy="half primary ETC color dimensions; cap 128; protect <=32, UI/HUD and secondary maps",
                  primary_textures=len(primary), protected_secondary_textures=len(primary & secondary), checked=len(rows), candidates=len(selected),
                  source_gpu_bytes=sum(r["original"]["gpu_bytes"] for r in selected), candidate_gpu_bytes=sum(r["candidate"]["gpu_bytes"] for r in selected), textures=rows)
    (args.output / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "textures"}))


if __name__ == "__main__":
    main()
