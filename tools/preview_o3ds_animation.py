#!/usr/bin/env python3
"""Compare original and rebound Wii geometry in sampled authored poses.

This software diagnostic uses static base textures. It does not reproduce
runtime PAT/UV animation, visibility, lighting or pose blending.
"""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from preview_o3ds_geometry import read_model, load_textures, camera_projection, render_panel, VERTEX
from preview_o3ds_positions import validate
from rebind_o3ds_positions import bank_layout, apply_pose, with_secondary_hair, require
from race_map_bake import Texture


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('metadata', type=Path)
    parser.add_argument('--bank', required=True, type=Path)
    parser.add_argument('--proof', type=Path)
    parser.add_argument('--clip', default='drive')
    parser.add_argument('--frames', default='0,15,30')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--asset-root', default=Path('build/playable/3ds/mkw-3ds'), type=Path)
    parser.add_argument('--hybrid-receipt', type=Path,
                        help='Overlay a hash-verified diagnostic atlas with identical posed geometry')
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text())
    proof = json.loads((args.proof or args.bank.with_suffix('.rebind.json')).read_text())
    original = read_model(metadata['inputs']['model'])
    candidate = read_model(args.metadata.with_suffix('.gmdl'))
    validate(metadata, original, candidate)
    old_data = Path(metadata['inputs']['bank']).read_bytes()
    new_data = args.bank.read_bytes()
    for label, data in [('originalModel', original.data), ('candidateModel', candidate.data),
                        ('originalBank', old_data), ('candidateBank', new_data)]:
        require(digest(data) == proof[label + 'Sha256'], f'{label} identity differs from rebind proof')
    old, new = bank_layout(old_data), bank_layout(new_data)
    require(len(new['sources']) == len(candidate.position), 'Candidate bank/model mismatch')
    require(old_data[old['tail']:] == new_data[new['tail']:], 'Authored animation payload changed')
    clips = [clip for clip in old['clips'] if clip['name'] == args.clip]
    require(len(clips) == 1, 'Expected exactly one matching authored clip')
    clip = clips[0]
    frames = [int(value) for value in args.frames.split(',')]
    require(0 < len(frames) <= 12 and all(0 <= frame < clip['count'] for frame in frames), 'Frame outside authored clip')
    textures = load_textures(original, args.asset_root, [Path('build/roster'), Path('sd-card/3ds/mkw-3ds')])
    candidate_textures = textures
    display_candidate, candidate_mapping = candidate, None
    hybrid = None
    if args.hybrid_receipt:
        hybrid = json.loads(args.hybrid_receipt.read_text())
        require(hybrid.get('source') == 'authored-Wii-only', 'Expected Wii-only hybrid receipt')
        for key, data in [('original_sha256', original.data), ('candidate_sha256', candidate.data),
                          ('bank_sha256', old_data), ('metadata_sha256', args.metadata.read_bytes())]:
            require(hybrid.get(key) == digest(data), 'Hybrid input differs: ' + key)
        display_candidate = read_model(hybrid['output'])
        require(digest(display_candidate.data) == hybrid['output_sha256'], 'Hybrid model changed')
        atlas_path = Path(hybrid['atlas'])
        require(digest(atlas_path.read_bytes()) == hybrid['atlas_sha256'], 'Hybrid atlas changed')
        candidate_mapping = np.asarray(hybrid['candidateVertexIds'])
        require(candidate_mapping.dtype.kind in 'iu' and candidate_mapping.ndim == 1
                and len(candidate_mapping) == len(display_candidate.position)
                and candidate_mapping.min() >= 0 and candidate_mapping.max() < len(candidate.position),
                'Invalid hybrid vertex mapping')
        candidate_mapping = candidate_mapping.astype(np.int64)
        require(display_candidate.materials == candidate.materials
                and display_candidate.submeshes == candidate.submeshes
                and np.array_equal(candidate_mapping[display_candidate.indices], candidate.indices),
                'Hybrid changed triangle connectivity/material spans')
        raw = np.frombuffer(candidate.vertex_bytes, dtype=VERTEX)
        baked = np.frombuffer(display_candidate.vertex_bytes, dtype=VERTEX)
        for field in ('p', 'n', 'c'):
            require(baked[field].tobytes() == raw[field][candidate_mapping].tobytes(),
                    'Hybrid changed geometry attributes: ' + field)
        for shape in metadata['faceShapes']:
            first, count = candidate.submeshes[shape]
            require(baked[display_candidate.indices[first:first + count]].tobytes()
                    == raw[candidate.indices[first:first + count]].tobytes(), 'Hybrid changed facial overlay')
        old_names = [candidate.texture_bytes[i:i+64] for i in range(0, len(candidate.texture_bytes), 64)]
        new_names = [display_candidate.texture_bytes[i:i+64] for i in range(0, len(display_candidate.texture_bytes), 64)]
        changed = [i for i, (a, b) in enumerate(zip(old_names, new_names)) if a != b]
        require(len(old_names) == len(new_names) and len(changed) == 1, 'Hybrid must replace one atlas slot')
        texture = Texture.__new__(Texture)
        with Image.open(atlas_path) as atlas:
            require(list(atlas.size) == hybrid['output_texture_size'], 'Hybrid atlas dimensions differ')
            texture.levels = [np.asarray(atlas.convert('RGBA'), dtype=float) / 255]
        candidate_textures = dict(textures)
        candidate_textures[changed[0]] = texture
    poses = []
    for frame in frames:
        palette, _ = with_secondary_hair(old, clip['frames'][frame], args.clip, frame)
        original_position = apply_pose(old['sources'], palette)[0]
        candidate_position = apply_pose(new['sources'], palette)[0]
        if candidate_mapping is not None:
            candidate_position = candidate_position[candidate_mapping]
        poses.append((replace(original, position=original_position),
                      replace(display_candidate, position=candidate_position)))
    points = np.concatenate([pair[0].position[np.unique(original.indices)] for pair in poses])
    center = (points.min(0) + points.max(0)) * .5
    yaw, pitch = np.pi / 4, np.pi / 9
    fit = camera_projection(points, center, yaw, pitch)
    half = np.maximum(np.max(np.abs(fit[:, :2]), axis=0), 1e-9)
    scale = min(368 / (2 * half[0]), 208 / (2 * half[1]))
    output = Image.new('RGB', (856, 140 + len(frames) * 280), (12, 17, 26))
    draw = ImageDraw.Draw(output)
    small, medium, large = [ImageFont.load_default(size=n) for n in (13, 16, 20)]
    draw.text((20, 16), 'Wii-only geometry: original / rebound prototype', fill='#f0f4fa', font=large)
    draw.text((20, 49), 'Exact authored palettes and Wii base textures; same camera and scale', fill='#d1dbea', font=medium)
    draw.text((20, 77), 'Static textures: PAT/UV animation, visibility and runtime blending are not shown.', fill='#ffce80', font=small)
    for row, (frame, pair) in enumerate(zip(frames, poses)):
        for column, model in enumerate(pair):
            x, y = 20 + column * 416, 110 + row * 280
            draw.text((x, y), f'{args.clip} frame {frame} | {model.triangle_count} triangles', fill='#e8eef8', font=medium)
            projection = camera_projection(model.position, center, yaw, pitch)
            output.paste(render_panel(model, projection, scale, 'textured',
                                      candidate_textures if column else textures), (x, y + 26))
    draw.text((20, output.height - 22), 'Offline deformation check only. Not runtime or performance approval.', fill='#ffce80', font=small)
    info = PngImagePlugin.PngInfo()
    for label in ('originalModel', 'candidateModel', 'originalBank', 'candidateBank'):
        info.add_text(label + 'Sha256', proof[label + 'Sha256'])
    if hybrid:
        info.add_text('hybridModelSha256', hybrid['output_sha256'])
        info.add_text('hybridAtlasSha256', hybrid['atlas_sha256'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output.save(args.output, pnginfo=info)
    print(args.output)


if __name__ == '__main__':
    main()
