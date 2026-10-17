#!/usr/bin/env python3
"""Textured software preview: original Wii driver vs O3DS low-poly sidecar.

Diagnostic only (orthographic, neutral authored pose, simple lambert light).
"""
import argparse
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from o3ds_lowpoly_io import read_gmdl, read_variant  # noqa: E402

BG = np.array([30, 36, 50], np.float64)


def wrap(x, size, mode):
    if mode == 1:
        return np.mod(x, size)
    if mode == 2:
        m = np.mod(x, 2 * size)
        return np.where(m < size, m, 2 * size - 1 - m)
    return np.clip(x, 0, size - 1)


def load_tex(name, roots, cache):
    if name in cache:
        return cache[name]
    for r in roots:
        p = Path(r) / f'{name}.png'
        if p.exists():
            cache[name] = np.asarray(Image.open(p).convert('RGBA')).astype(np.float64)
            return cache[name]
    raise FileNotFoundError(name)


def render(model, roots, yaw, pitch, size, center, scale, cache, flat=False, gray=False, coverage=False):
    f = model.floats
    P = f[:, 0:3].astype(np.float64) - center
    N = f[:, 3:6].astype(np.float64)
    UV = f[:, 6:8].astype(np.float64)
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    R = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    R = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]]) @ R
    Q = P @ R.T
    NN = N @ R.T
    X = size / 2 + Q[:, 0] * scale
    Y = size / 2 - Q[:, 1] * scale
    Z = Q[:, 2]
    img = np.tile(BG, (size, size, 1))
    zb = np.full((size, size), -1e30)
    light = np.array([0.35, 0.55, 0.75])
    light /= np.linalg.norm(light)
    for sub in model.submeshes:
        if sub.texture < 0 or sub.count == 0:
            continue
        tex = load_tex(model.names[sub.texture], roots, cache)
        H, W = tex.shape[:2]
        idx = model.indices[sub.first:sub.first + sub.count].reshape(-1, 3)
        for a, b, c in idx:
            xs, ys = X[[a, b, c]], Y[[a, b, c]]
            x0, x1 = int(max(0, math.floor(xs.min()))), int(min(size - 1, math.ceil(xs.max())))
            y0, y1 = int(max(0, math.floor(ys.min()))), int(min(size - 1, math.ceil(ys.max())))
            if x1 < x0 or y1 < y0:
                continue
            den = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
            if abs(den) < 1e-9:
                continue
            gy, gx = np.mgrid[y0:y1 + 1, x0:x1 + 1]
            px, py = gx + 0.5, gy + 0.5
            l0 = ((ys[1] - ys[2]) * (px - xs[2]) + (xs[2] - xs[1]) * (py - ys[2])) / den
            l1 = ((ys[2] - ys[0]) * (px - xs[2]) + (xs[0] - xs[2]) * (py - ys[2])) / den
            l2 = 1 - l0 - l1
            m = (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
            if not m.any():
                continue
            z = l0 * Z[a] + l1 * Z[b] + l2 * Z[c]
            m &= z > zb[gy, gx]
            if not m.any():
                continue
            u = l0 * UV[a, 0] + l1 * UV[b, 0] + l2 * UV[c, 0]
            v = l0 * UV[a, 1] + l1 * UV[b, 1] + l2 * UV[c, 1]
            x = u * W - 0.5
            y = (1 - v) * H - 0.5
            x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
            fx, fy = (x - x0)[..., None], (y - y0)[..., None]
            ws, wt = (sub.flags >> 8) & 3, (sub.flags >> 10) & 3
            at = lambda yy, xx: tex[wrap(yy, H, wt), wrap(xx, W, ws)]
            col = (at(y0, x0) * (1 - fx) * (1 - fy) + at(y0, x0 + 1) * fx * (1 - fy) +
                   at(y0 + 1, x0) * (1 - fx) * fy + at(y0 + 1, x0 + 1) * fx * fy)
            m &= col[..., 3] > 100
            if not m.any():
                continue
            if gray:
                col = np.full(col.shape, 205.0)
            if flat:
                n = np.cross(Q[b] - Q[a], Q[c] - Q[a])
                n = n / (np.linalg.norm(n) + 1e-12)
                n = np.broadcast_to(n, l0.shape + (3,))
            else:
                n = l0[..., None] * NN[a] + l1[..., None] * NN[b] + l2[..., None] * NN[c]
                n = n / (np.linalg.norm(n, axis=-1, keepdims=True) + 1e-12)
            shade = 0.55 + 0.45 * np.clip(np.abs((n * light).sum(-1)), 0, 1)
            rgb = col[..., :3] * shade[..., None]
            img[gy[m], gx[m]] = rgb[m]
            zb[gy[m], gx[m]] = z[m]
    return (img, zb > -1e30) if coverage else img


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('original', type=Path)
    ap.add_argument('variant', type=Path, nargs='+')
    ap.add_argument('--textures', type=Path, action='append', required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--size', type=int, default=300)
    ap.add_argument('--zoom', type=float, default=1.0)
    ap.add_argument('--focus', choices=('full', 'head'), default='full')
    ap.add_argument('--views', default='0,90,45,180', help='yaw angles in degrees')
    a = ap.parse_args()
    models = [('Wii original', read_gmdl(a.original), a.textures)]
    for v in a.variant:
        g, remap, ver = read_variant(v)
        root = next(p.parent for p in v.parents if p.name == 'models')
        label = root.name + ('/' + v.parent.name if v.parent.name != 'o3ds' else '')
        models.append((label, g, [root / 'textures'] + a.textures))
    P = models[0][1].floats[:, 0:3].astype(np.float64)
    lo, hi = P.min(0), P.max(0)
    center = (lo + hi) / 2
    ext = (hi - lo).max()
    if a.focus == 'head':
        center = np.array([center[0], hi[1] - 0.22 * (hi[1] - lo[1]), center[2]])
        ext *= 0.45
    scale = a.size / ext * 0.9 * a.zoom
    views = [(math.radians(float(y)), math.radians(20) if float(y) % 90 else 0.0) for y in a.views.split(',')]
    rows = []
    for label, m, roots in models:
        cache = {}
        tiles = [render(m, roots, yaw, pitch, a.size, center, scale, cache) for yaw, pitch in views]
        row = np.concatenate(tiles, 1)
        rows.append((label, m, row))
    W = rows[0][2].shape[1]
    out = Image.new('RGB', (W, (a.size + 18) * len(rows)), tuple(BG.astype(int)))
    d = ImageDraw.Draw(out)
    for i, (label, m, row) in enumerate(rows):
        out.paste(Image.fromarray(np.clip(row, 0, 255).astype(np.uint8)), (0, i * (a.size + 18) + 18))
        d.text((6, i * (a.size + 18) + 3), f'{label}: {len(m.indices) // 3} triangles, {m.header[1]} vertices', fill=(230, 230, 230))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    out.save(a.output)


if __name__ == '__main__':
    main()
