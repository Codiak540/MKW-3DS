#!/usr/bin/env python3
"""Render diagnostic PNG comparisons of cooked GMDL geometry variants.

This is a neutral-pose, orthographic software geometry viewer, not the game's
renderer. Optional verified source PNGs show base color and alpha, but cannot
approve final materials, lighting, animation, or gameplay.
Both columns use the baseline bounds and identical cameras at 400 x 240 pixels.
No source assets are modified.
"""

import argparse
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import struct

import numpy as np
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from gx_material import GMDL_SUBMESH_BYTES
from cook_o3ds_textures import Assets, SAFE, ROOT
from race_map_bake import Texture as DiagnosticTexture, alpha_pass


PANEL_WIDTH, PANEL_HEIGHT = 400, 240
VERTEX = np.dtype([('p', '<f4', 3), ('n', '<f4', 3), ('uv', '<f4', 4), ('c', 'u1', 4)])
BACKGROUND = (24, 31, 44)
VIEWS = [('Front (+Z)', 0.0, 0.0), ('Side (+X)', math.pi / 2, 0.0),
         ('Oblique', math.pi / 4, math.pi / 9)]


@dataclass
class Model:
    path: Path
    data: bytes
    version: int
    position: np.ndarray
    color: np.ndarray
    indices: np.ndarray
    submeshes: list
    materials: list
    texture_bytes: bytes
    vertex_bytes: bytes
    source_indices: np.ndarray | None = None
    original_vertices: int = 0
    uv: np.ndarray | None = None
    alpha: np.ndarray | None = None

    @property
    def triangle_count(self):
        return len(self.indices) // 3


def read_model(path):
    """Use the authored cooker's shared stride table, including GMDL v5."""
    path = Path(path)
    data = path.read_bytes()
    if len(data) < 24:
        raise ValueError(f'Truncated GMDL header: {path}')
    magic, version, nv, ni, ns, nt = struct.unpack_from('<4s5I', data)
    if (magic != b'GMDL' or version not in GMDL_SUBMESH_BYTES or
            not 0 < nv <= 65535 or ni > 3000000 or ni % 3 or ns > 2048 or nt > 512):
        raise ValueError(f'Unsupported GMDL header: {path}')
    stride = GMDL_SUBMESH_BYTES[version]
    texture_start = 24 + ns * stride
    vertex_start = texture_start + nt * 64
    index_start = vertex_start + nv * VERTEX.itemsize
    end = index_start + ni * 2
    source_indices, original_vertices = None, nv
    if len(data) > end:
        if len(data) < end + 16:
            raise ValueError(f'Truncated O3RM remap: {path}')
        tag, remap_version, original_vertices, compact_vertices = struct.unpack_from('<4sIII', data, end)
        if (tag != b'O3RM' or remap_version != 1 or compact_vertices != nv or
                not nv < original_vertices <= 65535 or len(data) != end + 16 + nv * 4):
            raise ValueError(f'Invalid O3RM remap: {path}')
        source_indices = np.frombuffer(data, dtype='<u4', count=nv, offset=end + 16).astype(np.int64)
        if np.any(source_indices >= original_vertices) or np.any(np.diff(source_indices) <= 0):
            raise ValueError(f'Unsorted or invalid O3RM source vertices: {path}')
    elif len(data) != end:
        raise ValueError(f'Truncated or trailing GMDL bytes: {path}')
    submeshes, materials, next_index = [], [], 0
    for n in range(ns):
        at = 24 + n * stride
        first, count = struct.unpack_from('<II', data, at)
        if first != next_index or count % 3 or first + count > ni:
            raise ValueError(f'Noncontiguous GMDL submesh: {path}')
        submeshes.append((first, count))
        materials.append(data[at + 8:at + stride])
        next_index += count
    if next_index != ni:
        raise ValueError(f'Unclaimed GMDL indices: {path}')
    vertex_bytes = data[vertex_start:index_start]
    vertices = np.frombuffer(vertex_bytes, dtype=VERTEX)
    if not all(np.isfinite(vertices[field]).all() for field in ('p', 'n', 'uv')):
        raise ValueError(f'Nonfinite GMDL vertex: {path}')
    indices = np.frombuffer(data, dtype='<u2', count=ni, offset=index_start).astype(np.int64)
    if not ni or np.max(indices) >= nv:
        raise ValueError(f'Empty or out-of-range GMDL indices: {path}')
    return Model(path, data, version, vertices['p'].astype(np.float64),
                 vertices['c'][:, :3].astype(np.float64), indices, submeshes,
                 materials, data[texture_start:vertex_start], vertex_bytes,
                 source_indices, original_vertices, vertices['uv'][:, :2].astype(np.float64),
                 vertices['c'][:, 3].astype(np.float64))


def load_textures(model, asset_root, source_roots):
    """Match PNG provenance to the exact packed/loose T3X the game loads."""
    assets = Assets(asset_root)
    names = [model.texture_bytes[i:i + 64].split(b'\0')[0].decode('ascii')
             for i in range(0, len(model.texture_bytes), 64)]
    used = {struct.unpack_from('<i', material)[0] for material, (_, count) in zip(model.materials, model.submeshes) if count}
    textures = {}
    for index in used - {-1}:
        if not 0 <= index < len(names) or not SAFE.fullmatch(names[index]):
            raise ValueError('Invalid model texture index/name')
        name = names[index]
        encoded = assets.read('textures/' + name + '.t3x')
        found = None
        for root in source_roots:
            t3x = root / 'textures' / (name + '.t3x')
            png = t3x.with_suffix('.png')
            if png.is_file() and t3x.is_file() and t3x.read_bytes() == encoded:
                found = png
                break
        if found is None:
            raise ValueError('No PNG with exact final T3X provenance: ' + name)
        texture = DiagnosticTexture.__new__(DiagnosticTexture)
        with Image.open(found) as image:
            texture.levels = [np.asarray(image.convert('RGBA'), dtype=np.float64) / 255.0]
        textures[index] = texture
    return textures


def validate_pair(baseline, candidate):
    if candidate.source_indices is not None:
        if candidate.original_vertices != len(baseline.position):
            raise ValueError('Candidate remap must reference the baseline vertex count')
        expected = np.frombuffer(baseline.vertex_bytes, dtype=VERTEX)[candidate.source_indices].tobytes()
    else:
        expected = baseline.vertex_bytes
    if (baseline.version != candidate.version or
            expected != candidate.vertex_bytes or
            baseline.texture_bytes != candidate.texture_bytes or
            baseline.materials != candidate.materials):
        raise ValueError('Candidate must preserve mapped baseline vertices, materials, and textures exactly')
    for original, reduced in zip(baseline.submeshes, candidate.submeshes):
        first, count = original
        candidate_first, candidate_count = reduced
        candidate_indices = candidate.indices[candidate_first:candidate_first + candidate_count]
        if candidate.source_indices is not None:
            candidate_indices = candidate.source_indices[candidate_indices]
        if candidate_count > count or not np.isin(
                candidate_indices,
                baseline.indices[first:first + count]).all():
            raise ValueError('Candidate must reduce indices within each original submesh')


def camera_projection(positions, center, yaw, pitch):
    right = np.array([math.cos(yaw), 0, -math.sin(yaw)])
    outward = np.array([math.sin(yaw) * math.cos(pitch), math.sin(pitch),
                        math.cos(yaw) * math.cos(pitch)])
    up = np.cross(outward, right)
    return (positions - center) @ np.column_stack((right, up, outward))


def render_panel(model, projection, scale, mode, textures=None):
    """Double-sided diagnostic rasterization with orthographic z-buffer."""
    screen = projection.copy()
    screen[:, 0] = screen[:, 0] * scale + PANEL_WIDTH / 2
    screen[:, 1] = PANEL_HEIGHT / 2 - screen[:, 1] * scale
    pixels = np.full((PANEL_HEIGHT, PANEL_WIDTH, 3), BACKGROUND, dtype=np.uint8)
    depth = np.full((PANEL_HEIGHT, PANEL_WIDTH), -np.inf)
    light = np.array([-0.35, 0.65, 1.0])
    light /= np.linalg.norm(light)
    jobs = []
    for (first, count), material in zip(model.submeshes, model.materials):
        sub = (first, count, *struct.unpack_from('<iIiI4fI', material))
        for triangle in model.indices[first:first + count].reshape(-1, 3):
            jobs.append((triangle, sub))
    if mode == 'textured':
        jobs.sort(key=lambda job: (bool(job[1][3] & 2), float(projection[job[0], 2].mean()) if job[1][3] & 2 else 0))
    for triangle, sub in jobs:
        p = screen[triangle]
        xmin, ymin = np.maximum(np.floor(p[:, :2].min(axis=0)).astype(int), (0, 0))
        xmax, ymax = np.minimum(np.ceil(p[:, :2].max(axis=0)).astype(int),
                               (PANEL_WIDTH - 1, PANEL_HEIGHT - 1))
        if xmin > xmax or ymin > ymax:
            continue
        a, b, c = p
        denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(denominator) < 1e-10:
            continue
        x, y = np.meshgrid(np.arange(xmin, xmax + 1) + 0.5,
                           np.arange(ymin, ymax + 1) + 0.5)
        wa = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / denominator
        wb = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / denominator
        wc = 1.0 - wa - wb
        z = wa * a[2] + wb * b[2] + wc * c[2]
        depth_region = depth[ymin:ymax + 1, xmin:xmax + 1]
        visible = (wa >= -1e-9) & (wb >= -1e-9) & (wc >= -1e-9) & (z > depth_region)
        if not visible.any():
            continue
        if mode == 'silhouette':
            rgb = np.array([218, 231, 244], dtype=np.uint8)
        else:
            # Deliberate diagnostic face lighting: not the game's lighting.
            q = projection[triangle]
            normal = np.cross(q[1] - q[0], q[2] - q[0])
            length = np.linalg.norm(normal)
            shade = 0.40 + 0.60 * abs(float(normal @ light) / length) if length else 1.0
            rgb = np.clip(model.color[triangle].mean(axis=0) * shade, 0, 255).astype(np.uint8)
        if mode == 'textured':
            weights = np.stack((wa, wb, wc), axis=-1)
            uv = (weights @ model.uv[triangle]).reshape(-1, 2)
            texture, flags = sub[2:4]
            # export_brres.ts stores V as 1-v. Reuse the course-map sampler,
            # whose PNG lookup restores 1-V and matches GX clamp/repeat/mirror.
            rgba = (textures[texture].sample(uv[:, 0], uv[:, 1], (flags >> 8) & 3, (flags >> 10) & 3, 0)
                    if texture >= 0 else np.ones((len(uv), 4)))
            rgba[:, :3] *= (weights @ model.color[triangle]).reshape(-1, 3) / 255.0
            rgba[:, 3] *= (weights @ model.alpha[triangle]).reshape(-1) / 255.0
            visible &= alpha_pass(sub, np.rint(rgba[:, 3] * 255)).reshape(visible.shape)
            # Strong ambient keeps facial texture detail legible. This is
            # synthetic inspection lighting, never a retail-lighting claim.
            rgb = np.clip(rgba[:, :3].reshape((*visible.shape, 3)) * (0.72 + 0.28 * shade) * 255, 0, 255)
            target = pixels[ymin:ymax + 1, xmin:xmax + 1]
            if flags & 2:
                alpha = rgba[:, 3].reshape((*visible.shape, 1))
                rgb = rgb * alpha + target * (1 - alpha)
            target[visible] = rgb[visible].astype(np.uint8)
            depth_region[visible] = z[visible]
            continue
        depth_region[visible] = z[visible]
        pixels[ymin:ymax + 1, xmin:xmax + 1][visible] = rgb
    return Image.fromarray(pixels)


def comparison_image(baseline, candidate, title, mode, textures=None):
    validate_pair(baseline, candidate)
    # Ignore unused vertices when fitting the model. The candidate still gets
    # exactly the baseline's center and scale, so lost extremities stay visible.
    used = baseline.position[np.unique(baseline.indices)]
    center = (used.min(axis=0) + used.max(axis=0)) * 0.5
    margin, gap, header, row_height = 20, 16, 126, 278
    image = Image.new('RGB', (margin * 2 + PANEL_WIDTH * 2 + gap, header + row_height * 3 + 40), (12, 17, 26))
    draw = ImageDraw.Draw(image)
    small, medium, large = (ImageFont.load_default(size=size) for size in (13, 16, 20))
    display_title = title.encode('ascii', 'replace').decode('ascii')
    while draw.textbbox((0, 0), display_title, font=large)[2] > image.width - margin * 2:
        display_title = display_title[:-4] + '...'
    draw.text((margin, 14), display_title, fill='#f0f4fa', font=large)
    draw.text((margin, 43), 'DIAGNOSTIC SOFTWARE PREVIEW - not game or animation approval', fill='#ffce80', font=medium)
    description = 'Verified Wii PNGs | UV0 + alpha | basic vertex color / synthetic lighting' if mode == 'textured' else 'No textures or alpha | synthetic face lighting'
    draw.text((margin, 66), 'Neutral pose | orthographic | Y up | ' + description, fill='#b8c5d8', font=small)
    draw.text((margin, 86), f'Each panel: 400 x 240 pixels. Identical baseline framing. Mode: {mode}.', fill='#b8c5d8', font=small)
    reduction = 100.0 * (1.0 - candidate.triangle_count / baseline.triangle_count)
    columns = [(baseline, f'Original: {baseline.triangle_count:,} triangles'),
               (candidate, f'Old 3DS candidate: {candidate.triangle_count:,} ({reduction:.1f}% fewer)')]
    for row, (view_name, yaw, pitch) in enumerate(VIEWS):
        baseline_projection = camera_projection(baseline.position, center, yaw, pitch)
        fit = baseline_projection[np.unique(baseline.indices), :2]
        half_width, half_height = np.maximum(np.max(np.abs(fit), axis=0), 1e-9)
        scale = min((PANEL_WIDTH - 32) / (2 * half_width), (PANEL_HEIGHT - 32) / (2 * half_height))
        for column, (model, label) in enumerate(columns):
            x, y = margin + column * (PANEL_WIDTH + gap), header + row * row_height
            draw.text((x, y), label, fill='#e8eef8', font=medium)
            projection = camera_projection(model.position, center, yaw, pitch)
            image.paste(render_panel(model, projection, scale, mode, textures), (x, y + 25))
            draw.text((x + 9, y + 33), view_name, fill='#b8c5d8', font=small)
    draw.text((margin, image.height - 31), 'Face shading is synthetic. Inspect the real game on Old 3DS before accepting visual quality.', fill='#ffce80', font=small)
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path, help='Original cooked .gmdl')
    parser.add_argument('candidate', type=Path, help='Corresponding models/o3ds/*.gmdl sidecar')
    parser.add_argument('--output', required=True, type=Path, help='Diagnostic comparison .png')
    parser.add_argument('--title', help='Display label, defaulting to the baseline filename')
    parser.add_argument('--mode', choices=('shaded', 'silhouette', 'textured'), default='shaded')
    parser.add_argument('--textured', action='store_true', help='Show verified original Wii base-color textures and alpha')
    parser.add_argument('--asset-root', type=Path, help='Final package with textures and menu/race packs; default baseline parent-parent')
    parser.add_argument('--texture-root', type=Path, action='append', help='Cooked source root containing adjacent PNG/T3X files; repeatable')
    args = parser.parse_args()
    if args.output.suffix.lower() != '.png':
        parser.error('--output must end in .png')
    if args.output.resolve() in (args.baseline.resolve(), args.candidate.resolve()):
        parser.error('Output cannot replace a source asset')
    try:
        baseline, candidate = read_model(args.baseline), read_model(args.candidate)
        mode = 'textured' if args.textured else args.mode
        textures = None
        if mode == 'textured':
            roots = args.texture_root or [ROOT / 'build/roster', ROOT / 'sd-card/3ds/mkw-3ds', ROOT / 'build/vfx', ROOT / 'build/garage', ROOT / 'build/item-lighting']
            textures = load_textures(baseline, args.asset_root or args.baseline.parent.parent, roots)
        image = comparison_image(baseline, candidate, args.title or baseline.path.name, mode, textures)
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('Description', 'Diagnostic software neutral-pose preview; optional verified base-color PNGs; not gameplay/material/animation approval.')
        metadata.add_text('Mode', mode)
        metadata.add_text('Baseline SHA256', hashlib.sha256(baseline.data).hexdigest())
        metadata.add_text('Candidate SHA256', hashlib.sha256(candidate.data).hexdigest())
        metadata.add_text('Triangle counts', f'{baseline.triangle_count} original; {candidate.triangle_count} candidate')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        image.save(args.output, pnginfo=metadata)
    except (OSError, ValueError) as error:
        parser.exit(1, f'preview_o3ds_geometry: {error}\n')
    print(f'{args.output}: {baseline.triangle_count:,} → {candidate.triangle_count:,} triangles (diagnostic only)')


if __name__ == '__main__':
    main()
