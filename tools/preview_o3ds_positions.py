#!/usr/bin/env python3
"""Render an isolated Wii position-update prototype after provenance checks."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from preview_o3ds_geometry import (read_model, load_textures, camera_projection,
                                  render_panel, VIEWS, PANEL_WIDTH, PANEL_HEIGHT, VERTEX)


def validate(metadata, baseline, candidate):
    if metadata.get('source') != 'authored-Wii-only' or not metadata.get('prototype'):
        raise ValueError('Expected an authored Wii prototype receipt')
    if hashlib.sha256(baseline.data).hexdigest() != metadata['inputs']['modelSha256'] or hashlib.sha256(candidate.data).hexdigest() != metadata['candidateSha256']:
        raise ValueError('Prototype input/output hash mismatch')
    if baseline.materials != candidate.materials or baseline.texture_bytes != candidate.texture_bytes:
        raise ValueError('Prototype changed authored Wii materials/textures')
    source_ids = np.asarray(metadata['sourceVertexIds'], dtype=np.int64)
    if len(source_ids) != len(candidate.position) or np.any(source_ids < 0) or np.any(source_ids >= len(baseline.position)):
        raise ValueError('Invalid source vertex mapping')
    original = np.frombuffer(baseline.vertex_bytes, dtype=VERTEX)
    compact = np.frombuffer(candidate.vertex_bytes, dtype=VERTEX)
    for material in metadata['materials']:
        i = material['material']
        first, count = candidate.submeshes[i]
        old_first, old_count = baseline.submeshes[i]
        if count > old_count or not np.isin(source_ids[candidate.indices[first:first + count]], baseline.indices[old_first:old_first + old_count]).all():
            raise ValueError('Prototype crosses authored material membership')
        if material['lockedShape']:
            if count != old_count or original[baseline.indices[old_first:old_first + count]].tobytes() != compact[candidate.indices[first:first + count]].tobytes():
                raise ValueError('Protected eye/face overlay changed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('metadata', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--asset-root', default=Path('build/playable/3ds/mkw-3ds'), type=Path)
    parser.add_argument('--texture-root', action='append', type=Path)
    parser.add_argument('--mode', choices=('textured', 'shaded', 'silhouette'), default='textured')
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text())
    baseline = read_model(metadata['inputs']['model'])
    candidate = read_model(args.metadata.with_suffix('.gmdl'))
    validate(metadata, baseline, candidate)
    roots = args.texture_root or [Path('build/roster'), Path('sd-card/3ds/mkw-3ds')]
    textures = load_textures(baseline, args.asset_root, roots) if args.mode == 'textured' else None
    used = baseline.position[np.unique(baseline.indices)]
    center = (used.min(axis=0) + used.max(axis=0)) * 0.5
    image = Image.new('RGB', (856, 1000), (12, 17, 26))
    draw = ImageDraw.Draw(image)
    small, medium, large = [ImageFont.load_default(size=size) for size in (13, 16, 20)]
    method = 'retained-position' if metadata.get('retainPositions') else 'position-update'
    draw.text((20, 14), f'Original Wii / Wii-only {method} prototype', fill='#f0f4fa', font=large)
    draw.text((20, 45), f'{args.mode.title()} diagnostic; authored eye/face overlay triangles unchanged', fill='#d1dbea', font=medium)
    draw.text((20, 72), 'Diagnostic neutral pose only. Binding, animation and gameplay approval pending.', fill='#ffce80', font=small)
    draw.text((20, 94), f'Target {metadata["ratio"]:.2f}; error {metadata["maxError"]:.2f}; actual {candidate.triangle_count} triangles', fill='#d1dbea', font=small)
    for row, (view, yaw, pitch) in enumerate(VIEWS):
        projection = camera_projection(baseline.position, center, yaw, pitch)
        fit = projection[np.unique(baseline.indices), :2]
        half_width, half_height = np.maximum(np.max(np.abs(fit), axis=0), 1e-9)
        scale = min((PANEL_WIDTH - 32) / (2 * half_width), (PANEL_HEIGHT - 32) / (2 * half_height))
        for column, model in enumerate((baseline, candidate)):
            x, y = 20 + column * 416, 130 + row * 278
            draw.text((x, y), f'{"Original" if column == 0 else "Prototype"}: {model.triangle_count} triangles', fill='#e8eef8', font=medium)
            projection = camera_projection(model.position, center, yaw, pitch)
            image.paste(render_panel(model, projection, scale, args.mode, textures), (x, y + 25))
            draw.text((x + 9, y + 33), view, fill='#b8c5d8', font=small)
    draw.text((20, 978), 'Raw prototype is not runtime ready. No DS/MK7 geometry or texture data used.', fill='#ffce80', font=small)
    info = PngImagePlugin.PngInfo()
    info.add_text('Prototype SHA256', metadata['candidateSha256'])
    info.add_text('Input GMDL SHA256', metadata['inputs']['modelSha256'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output, pnginfo=info)
    print(args.output)


if __name__ == '__main__':
    main()
