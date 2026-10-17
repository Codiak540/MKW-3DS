#!/usr/bin/env python3
"""Old 3DS course textures: mip chains and alpha-free ETC1A4 -> ETC1.

--mode pack (the runtime path, docs/o3ds-texture-cache.md) writes
textures/o3ds/mips.pak: one OMIP entry per original course texture name
(include/mkw/texture_mips.hpp). Nothing in models/o3ds changes, so it covers
every course model, including the tiled track models O3RM v2 cannot rename.
The runtime keeps the original level 0 and only appends levels; it checks the
original's size and CRC32 and, for a conversion, that every alpha nibble is
0xF, and falls back to the original texture on any mismatch.

  - opaque ETC1 course textures (track lists, skies, objNN_*/coconut_* objects)
    gain a chain down to 8 texels: each level is a Lanczos-2 2:1 downsample
    of the previous one (decoded level 0 first) with the material's wrap at
    the borders, encoded by tex3ds (ETC1, high quality). Lanczos-2 instead of a
    box keeps thin features (curbs, lane lines) visible on oblique surfaces,
    which isotropic mip selection otherwise blurs (--filter box for A/B);
  - ETC1A4 textures whose every alpha nibble is 0xF become ETC1 (colour blocks
    verbatim, half the bytes, every sampled texel identical) plus the chain;
  - ETC1A4 textures whose alpha is used keep ETC1A4 and gain an alpha-aware
    chain (alpha_chain): alpha-weighted colour for blended and alpha-tested
    materials, and for alpha-tested ones each level's alpha is scaled so the
    share of texels passing the material's test matches level 0 (distant
    foliage keeps its density). Textures whose materials disagree (different
    tests or blend factors) keep level 0 only;
  - textures used as a secondary (light/environment) map by any model get no
    chain (an alpha-free ETC1A4 one is still converted); textures with mip
    levels already, or that are not one full uncompressed ETC image
    keep the original path.

The older modes rename slots through the existing O3RM v2 `_o3` texture rename.
Two kinds of `<name>_o3<hex>` sibling (textures/o3ds/<sibling>.t3x), both
built from the T3X the runtime loads (menu.pak/race.pak first):

  exact   ETC1A4 textures whose every alpha nibble is 0xF become ETC1: the
          colour blocks are copied verbatim and ETC1 samples alpha 1.0, so
          every sampled texel is identical; half the bytes.
  mips    opaque ETC1 (or exact-converted) primary course textures gain a box
          mip chain. Level 0 is byte-identical, so magnified and 1:1 surfaces
          sample the same texels; only minified (distant/oblique) ones change.
          Levels 1..n are tex3ds ETC1 encodes of the decoded level 0.

A course sidecar (track list models and objNN_* objects) renames a slot only
when every submesh using that slot is eligible; O3RM v1 sidecars are rewritten
as v2 (identical records, a strictly increasing map is also nondecreasing) and
only if every UV0 is within the v2 range. Nothing outside --out is written.

Renderer::moveToVram moves whole mip chains into VRAM (within its reserve).
"""
import argparse
import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from cook_o3ds_textures import Assets, color_textures  # noqa: E402
from devkitpro import tool  # noqa: E402

MODIFIERS = np.array([[2, 8], [5, 17], [9, 29], [13, 42], [18, 60], [24, 80], [33, 106], [47, 183]], np.int32)
COURSE = re.compile(r'"(models/course_\d+)\.list"')


def header(data):
    count, dims, fmt, levels = struct.unpack_from('<HBBB', data)
    if count != 1 or dims & 0xc0 or fmt not in (12, 13):
        raise ValueError('not a single ETC texture')
    width, height = 1 << ((dims & 7) + 3), 1 << (((dims >> 3) & 7) + 3)
    if struct.unpack_from('<6H', data, 5) != (width, height, 0, 1024, 1024, 0):
        raise ValueError('atlas or partial texture')
    size, = struct.unpack_from('<I', data, 17)
    if size & 0xff:
        raise ValueError('compressed payload')
    level0 = width * height // (2 if fmt == 12 else 1)
    if len(data) != 21 + (size >> 8):
        raise ValueError('length')
    return dict(width=width, height=height, fmt=fmt, levels=levels, level0=level0, payload=data[21:])


def blocks(payload, width, height, fmt):
    """3DS tile order -> (by, bx) grid of colour words (and alpha words)."""
    step = 8 if fmt == 12 else 16
    n = width * height // 16
    raw = np.frombuffer(payload[:n * step], dtype='<u8').reshape(n, step // 8)
    colour, alpha = raw[:, -1], raw[:, 0] if fmt == 13 else None
    # 8x8 tiles row-major; inside a tile, 4x4 blocks TL, TR, BL, BR.
    tiles_x = width // 8
    order = np.arange(n)
    tile, sub = order // 4, order % 4
    by = (tile // tiles_x) * 2 + sub // 2
    bx = (tile % tiles_x) * 2 + sub % 2
    grid = np.zeros((height // 4, width // 4), np.uint64)
    grid[by, bx] = colour
    agrid = None
    if alpha is not None:
        agrid = np.zeros_like(grid)
        agrid[by, bx] = alpha
    return grid, agrid


def decode_etc1(grid):
    """ETC1 colour words -> RGB uint8 image (rows in stored order)."""
    hi = (grid >> np.uint64(32)).astype(np.uint32)
    lo = (grid & np.uint64(0xffffffff)).astype(np.uint32)
    diff = (hi >> 1) & 1
    flip = hi & 1
    t1, t2 = (hi >> 5) & 7, (hi >> 2) & 7
    base = np.zeros(grid.shape + (2, 3), np.int32)
    for c, (s_ind, s_diff) in enumerate(((28, 27), (20, 19), (12, 11))):
        a4, b4 = (hi >> s_ind) & 15, (hi >> (s_ind - 4)) & 15
        a5 = (hi >> s_diff) & 31
        d3 = ((hi >> (s_diff - 3)) & 7).astype(np.int32)
        d3 = np.where(d3 >= 4, d3 - 8, d3)
        b5 = (a5.astype(np.int32) + d3) & 31
        e5 = lambda v: (v << 3) | (v >> 2)
        base[..., 0, c] = np.where(diff == 1, e5(a5.astype(np.int32)), a4.astype(np.int32) * 17)
        base[..., 1, c] = np.where(diff == 1, e5(b5), b4.astype(np.int32) * 17)
    h, w = grid.shape
    out = np.zeros((h * 4, w * 4, 3), np.uint8)
    for x in range(4):
        for y in range(4):
            p = x * 4 + y
            idx = (((lo >> (p + 16)) & 1) << 1) | ((lo >> p) & 1)
            sub = np.where(flip == 1, int(y >= 2), int(x >= 2))
            table = np.where(sub == 1, t2, t1)
            mag = MODIFIERS[table, idx & 1]
            delta = np.where(idx >= 2, -mag, mag)
            colour = np.where(sub[..., None] == 1, base[..., 1, :], base[..., 0, :]) + delta[..., None]
            out[y::4, x::4] = np.clip(colour, 0, 255).astype(np.uint8)
    return out


def tex3ds_etc1(image, mips):
    with tempfile.TemporaryDirectory(prefix='o3ds-course-tex-') as d:
        png = Path(d) / 'level0.png'
        Image.fromarray(image).save(png)
        out = Path(d) / 'out.t3x'
        cmd = [str(tool('tex3ds')), '-f', 'etc1', '-q', 'high', '-z', 'none'] + (['-m', 'box'] if mips else []) + ['-o', str(out), str(png)]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return out.read_bytes()


def orientation_check():
    """tex3ds row order vs decode: encode a gradient, decode, compare."""
    y, x = np.mgrid[0:64, 0:64]
    image = np.stack([x * 4, y * 4, (x + y) * 2], -1).astype(np.uint8)
    t = header(tex3ds_etc1(image, False))
    decoded = decode_etc1(blocks(t['payload'], 64, 64, 12)[0])
    straight = np.abs(decoded.astype(int) - image).mean()
    flipped = np.abs(decoded[::-1].astype(int) - image).mean()
    if min(straight, flipped) > 6:
        raise SystemExit(f'ETC1 decoder self-check failed ({straight:.1f}/{flipped:.1f})')
    return flipped < straight


def build(original, mode, flipped):
    """Sibling T3X bytes (or None) and a description."""
    t = header(original)
    if t['levels']:
        return None, 'already mipmapped'
    grid, agrid = blocks(t['payload'], t['width'], t['height'], t['fmt'])
    if t['fmt'] == 13 and not np.all(agrid == np.uint64(0xffffffffffffffff)):
        return None, 'uses alpha'
    colour = grid.astype('<u8')
    # Re-tile the colour words in the original order (ETC1: 8 bytes per block).
    step = 8 if t['fmt'] == 12 else 16
    n = t['width'] * t['height'] // 16
    words = np.frombuffer(t['payload'][:n * step], dtype='<u8').reshape(n, step // 8)[:, -1]
    level0 = words.astype('<u8').tobytes()
    kind = 'exact' if t['fmt'] == 13 else None
    if mode == 'mips' and min(t['width'], t['height']) > 8:
        image = decode_etc1(colour)
        if flipped:
            image = image[::-1]
        enc = tex3ds_etc1(np.ascontiguousarray(image), True)
        m = header(enc)
        if m['levels'] == 0 or (m['width'], m['height']) != (t['width'], t['height']):
            return None, 'tex3ds produced no mips'
        payload = level0 + m['payload'][len(level0):]
        head = bytearray(enc[:21])
        struct.pack_into('<I', head, 17, len(payload) << 8)
        return bytes(head) + payload, ('exact+mips' if kind else 'mips')
    if not kind:
        return None, 'opaque ETC1 (mips not requested)'
    head = bytearray(original[:21])
    head[3] = 12
    struct.pack_into('<I', head, 17, len(level0) << 8)
    return bytes(head) + level0, kind


def eligible_slot(model, slot, mode):
    """Every submesh using `slot` may take the sibling."""
    for s in model.submeshes:
        if not s.count:
            continue
        if s.secondary == slot and mode == 'mips':
            return False   # light/environment maps keep level 0 only
    return any(s.texture == slot or s.secondary == slot for s in model.submeshes if s.count)


OMIP_HEADER = 32


def omip(original, fmt, levels, drop_alpha, tail):
    """OMIP v1 bytes (include/mkw/texture_mips.hpp)."""
    head = struct.pack('<4sIII', b'OMIP', 1, len(original), zlib.crc32(original))
    head += struct.pack('<BBHIII', fmt, levels, 1 if drop_alpha else 0, len(tail), zlib.crc32(tail), 0)
    assert len(head) == OMIP_HEADER
    return head + tail


def decode_alpha(agrid):
    """ETC1A4 alpha words -> (H, W) uint8, nibble * 17 (rows in stored order).
    Nibble p = x * 4 + y of the 4x4 block (column-major, like ETC1 indices)."""
    h, w = agrid.shape
    out = np.zeros((h * 4, w * 4), np.uint8)
    for x in range(4):
        for y in range(4):
            out[y::4, x::4] = ((agrid >> np.uint64(4 * (x * 4 + y))) & np.uint64(15)).astype(np.uint8) * 17
    return out


def tex3ds_level(rgba):
    """One ETC1A4 level (no mips) as PICA payload bytes. Alpha must already be
    a multiple of 17: tex3ds truncates to 4 bits, which then keeps it exactly."""
    with tempfile.TemporaryDirectory(prefix='o3ds-course-tex-') as d:
        png, out = Path(d) / 'level.png', Path(d) / 'level.t3x'
        Image.fromarray(rgba, 'RGBA').save(png)
        cmd = [str(tool('tex3ds')), '-f', 'etc1a4', '-q', 'high', '-z', 'none', '-o', str(out), str(png)]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        t = header(out.read_bytes())
        if (t['width'], t['height'], t['fmt'], t['levels']) != (rgba.shape[1], rgba.shape[0], 13, 0):
            raise ValueError('tex3ds ETC1A4 level mismatch')
        return t['payload']


def passes(alpha, test):
    """Texels passing the material's alpha test (comparator, reference)."""
    comparator, reference = test
    return alpha > reference if comparator == 'greater' else alpha >= reference


def quantize(alpha):
    return np.clip(np.rint(np.clip(alpha, 0, 255) / 17), 0, 15) * 17


def first_passing_nibble(test):
    """Smallest 4-bit alpha (x17) the test passes: 16 = none, 0 = all."""
    comparator, reference = test
    return reference // 17 + 1 if comparator == 'greater' else -(-reference // 17)


BAYER4 = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) / 16.0


def alpha_chain(colour, alpha, levels, weighting, test):
    """Level images 1..levels (RGBA uint8, stored row order) from level 0.

    Colour of each 2x2 is alpha-weighted ('alpha': what a blended or
    alpha-tested surface shows, so transparent texels' colour never bleeds
    in) or a plain mean ('box'); alpha is the plain mean. For an alpha-tested
    material every level then passes the test on the same share of texels as
    level 0 (coverage preserving, so distant foliage keeps its density instead
    of thinning out): alpha is scaled to the closest share, and the remaining
    error is fixed by rank (highest filtered alpha first, ties broken by a 4x4
    ordered dither so a uniform region becomes a stable sparse pattern)."""
    c, a = colour.astype(np.float64), alpha.astype(np.float64)
    if test:
        nibble = first_passing_nibble(test)
        target = passes(quantize(a), test).mean()
    out = []
    for _ in range(levels):
        h, w = a.shape[0] // 2, a.shape[1] // 2
        a4 = a.reshape(h, 2, w, 2)
        c4 = c.reshape(h, 2, w, 2, 3)
        mean_c = c4.mean(axis=(1, 3))
        if weighting == 'alpha':
            weight = a4.sum(axis=(1, 3))
            weighted = (c4 * a4[..., None]).sum(axis=(1, 3)) / np.maximum(weight, 1e-9)[..., None]
            c = np.where(weight[..., None] > 0, weighted, mean_c)
        else:
            c = mean_c
        a = a4.mean(axis=(1, 3))
        level = quantize(a)
        if test and 0 < nibble <= 15:
            lo, hi, scale = 0.0, 16.0, 1.0
            best = abs(passes(level, test).mean() - target)
            for _ in range(40):
                s = (lo + hi) / 2
                cover = passes(quantize(a * s), test).mean()
                if abs(cover - target) < best:
                    best, scale = abs(cover - target), s
                lo, hi = (s, hi) if cover < target else (lo, s)
            level = quantize(a * scale)
            tie = np.tile(BAYER4, (h // 4 + 1, w // 4 + 1))[:h, :w] * 1e-3
            order = np.argsort(-(a * scale + tie), axis=None, kind='stable')
            keep = np.zeros(level.size, bool)
            keep[order[:int(round(target * level.size))]] = True
            keep = keep.reshape(level.shape)
            level = np.where(keep, np.maximum(level, nibble * 17), np.minimum(level, (nibble - 1) * 17))
        out.append(np.dstack([np.clip(np.rint(c), 0, 255), level]).astype(np.uint8))
    return out


def alpha_variant(original, t, grid, agrid, flipped, usage):
    """OMIP keeping ETC1A4 (alpha used) with an alpha-aware chain, or None."""
    if usage is None:
        return None, 'uses alpha'
    weighting, test = usage
    if min(t['width'], t['height']) <= 8:
        return None, 'too small for mips'
    colour, alpha = decode_etc1(grid.astype('<u8')), decode_alpha(agrid)
    if flipped:
        colour, alpha = colour[::-1], alpha[::-1]
    levels = chain_levels(t['width'], t['height'])
    tail = b''
    for image in alpha_chain(colour, alpha, levels, weighting, test):
        tail += tex3ds_level(np.ascontiguousarray(image[::-1] if flipped else image))
    expected = sum((t['width'] >> i) * (t['height'] >> i) for i in range(1, levels + 1))
    if len(tail) != expected:
        return None, 'unexpected ETC1A4 chain'
    kind = 'alpha-test' if test else 'alpha-blend' if weighting == 'alpha' else 'alpha-box'
    return omip(original, 13, levels, False, tail), f'mips ({kind})'


def texture_usage(assets):
    """{texture: {'alpha': (weighting, test) or None, 'wrap': (s, t)}} over
    every model using it as a primary map, from the GMDL submesh state
    src/renderer.cpp applies: flags bit 0 alpha test, bit 1 blend, bits 8-9 /
    10-11 S/T wrap (clamp, repeat, mirror, clamp), bit 31 source state
    (comparator in bits 26-28, reference in bits 29-30 or animationFrames bits
    16-23, source factor ONE when bit 7, a custom source factor in
    animationFrames bits 24-27). 'alpha' is None when the uses disagree or the
    test is not GREATER/GEQUAL; a wrap the uses disagree on becomes 'edge'."""
    comparators = {4: 'greater', 6: 'gequal'}
    wraps = ('edge', 'wrap', 'reflect', 'edge')
    seen, wrap_seen = {}, {}
    for path in assets.models():
        data = assets.read(path)
        magic, version, nv, ni, ns, nt = struct.unpack_from('<4s5I', data)
        record = {3: 44, 4: 56, 5: 92}[version]
        names = [data[24 + ns * record + 64 * i:24 + ns * record + 64 * (i + 1)].split(b'\0')[0].decode() for i in range(nt)]
        for i in range(ns):
            first, count, tex, flags, sec = struct.unpack_from('<IIiIi', data, 24 + i * record)
            frames, = struct.unpack_from('<I', data, 24 + i * record + 40)
            if not count or tex < 0:
                continue
            source = bool(flags >> 31)
            extended = frames >> 16 if source else 0
            test = None
            if flags & 1:
                comparator = (flags >> 26) & 7 if source else 4
                reference = (extended & 255 or (0, 128, 165, 255)[(flags >> 29) & 3]) if source else 127
                test = 'always' if comparator == 7 else (comparators.get(comparator, 'other'), reference)
                test = None if test == 'always' else test
            weighting = 'box'
            if flags & 1 or (flags & 2 and not (source and flags & 128) and not (extended >> 8) & 15):
                weighting = 'alpha'
            seen.setdefault(names[tex], set()).add((weighting, test))
            wrap_seen.setdefault(names[tex], set()).add((wraps[(flags >> 8) & 3], wraps[(flags >> 10) & 3]))
    usage = {}
    for name, uses in seen.items():
        tests = {u[1] for u in uses}
        weightings = {u[0] for u in uses}
        alpha = None
        if len(tests) == 1 and len(weightings) == 1 and not any(t and t[0] == 'other' for t in tests):
            alpha = (weightings.pop(), tests.pop())
        axes = list(zip(*wrap_seen[name]))
        wrap = tuple(next(iter(m)) if len(set(m)) == 1 else 'edge' for m in axes)
        usage[name] = {'alpha': alpha, 'wrap': wrap}
    return usage


def lanczos2_taps():
    """8 weights for a 2:1 decimation: source texel centres at offsets
    -3.5..3.5 from the destination centre, Lanczos-2 in destination units."""
    d = np.arange(-3.5, 4.0, 1.0) / 2
    w = np.sinc(d) * np.sinc(d / 2)
    return w / w.sum()


def downsample(image, wrap, kind='lanczos2'):
    """Halve an (H, W, C) float image. Boundaries follow the material's wrap
    (wrap = repeat, reflect = mirrored repeat, edge = clamp), so a tiled road
    has no seam at distance. 'box' is the plain 2x2 mean."""
    if kind == 'box':
        h, w = image.shape[0] // 2, image.shape[1] // 2
        return image.reshape(h, 2, w, 2, -1).mean(axis=(1, 3))
    taps = lanczos2_taps()
    modes = {'wrap': 'wrap', 'reflect': 'symmetric', 'edge': 'edge'}
    for axis, mode in ((0, wrap[1]), (1, wrap[0])):
        n = image.shape[axis] // 2
        pad = [(0, 0)] * image.ndim
        pad[axis] = (3, 4)
        p = np.pad(image, pad, mode=modes[mode])
        out = 0
        for k, weight in enumerate(taps):
            idx = np.arange(n) * 2 + k  # source 2i-3+k, shifted by the pad of 3
            out = out + weight * np.take(p, idx, axis=axis)
        image = out
    return image


def colour_chain(colour, levels, wrap, kind):
    """ETC1 level images 1..levels (uint8 RGB) from level 0, each level from
    the previous unclipped one."""
    c, out = colour.astype(np.float64), []
    for _ in range(levels):
        c = downsample(c, wrap, kind)
        out.append(np.clip(np.rint(c), 0, 255).astype(np.uint8))
    return out


def chain_levels(width, height):
    levels = 0
    while min(width >> (levels + 1), height >> (levels + 1)) >= 8 and levels < 7:
        levels += 1
    return levels


def mip_variant(original, flipped, chain=True, alpha=None, wrap=('wrap', 'wrap'), kind='lanczos2'):
    """(OMIP bytes or None, description) for one original T3X; chain=False
    only converts an alpha-free ETC1A4 (secondary maps keep a single level).
    alpha: (weighting, test) for an ETC1A4 whose alpha is used; wrap: the
    material's (S, T) wrap; kind: the colour downsample filter."""
    t = header(original)
    if t['levels']:
        return None, 'already mipmapped'
    grid, agrid = blocks(t['payload'], t['width'], t['height'], t['fmt'])
    drop = t['fmt'] == 13
    if drop and not np.all(agrid == np.uint64(0xffffffffffffffff)):
        return alpha_variant(original, t, grid, agrid, flipped, alpha if chain else None)
    levels, tail = 0, b''
    if chain and min(t['width'], t['height']) > 8:
        image = decode_etc1(grid.astype('<u8'))
        if flipped:
            image = image[::-1]
        levels = chain_levels(t['width'], t['height'])
        for level in colour_chain(image, levels, wrap, kind):
            enc = header(tex3ds_etc1(np.ascontiguousarray(level), False))
            if (enc['width'], enc['height'], enc['fmt'], enc['levels']) != (level.shape[1], level.shape[0], 12, 0):
                return None, 'unexpected tex3ds level'
            tail += enc['payload']
        expected = sum((t['width'] >> i) * (t['height'] >> i) // 2 for i in range(1, levels + 1))
        if len(tail) != expected:
            return None, 'unexpected tex3ds chain'
    if not levels and not drop:
        return None, 'too small for mips' if chain else 'secondary map in some model'
    return omip(original, 12, levels, drop, tail), ('etc1a4->etc1' + ('+mips' if levels else '')) if drop else 'mips'


def write_mpk1(path, files):
    """MPK1 archive (tools/pack_menu_assets.py layout), names sorted."""
    names = sorted(files)
    blob = b''.join(n.encode() + b'\0' for n in names)
    header_bytes = 12 + 12 * len(names) + len(blob)
    offset = (header_bytes + 31) & ~31
    entries, payloads, at = [], [], 0
    for n in names:
        entries.append(struct.pack('<III', at, offset, len(files[n])))
        at += len(n.encode()) + 1
        padded = files[n] + b'\0' * (((len(files[n]) + 31) & ~31) - len(files[n]))
        payloads.append(padded)
        offset += len(padded)
    with path.open('wb') as out:
        out.write(b'MPK1' + struct.pack('<II', len(names), len(blob)))
        out.write(b''.join(entries) + blob + b'\0' * (((header_bytes + 31) & ~31) - header_bytes))
        out.write(b''.join(payloads))


def course_texture_groups(assets):
    """{texture: {'groups': set, 'courses': set}} for every course model, as the audit resolves them."""
    from audit_o3ds_textures import courses, model_textures
    listed = lambda rel: assets.read(rel).decode().split() if rel and (rel in assets.entries or (assets.root / rel).exists()) else []
    all_models = {n[7:-5] for n in assets.entries if n.startswith('models/') and n.endswith('.gmdl')}
    all_models |= {p.stem for p in (assets.root / 'models').glob('*.gmdl')}
    found = {}
    for name, track_list, sky_list in courses():
        cid = re.search(r'course_(\d+)', track_list)
        prefix = f'obj{cid.group(1)}_' if cid else None
        groups = {'track': listed(track_list), 'sky': listed(sky_list),
                  'objects': sorted(m for m in all_models if prefix and m.startswith(prefix))}
        if name == 'Coconut Mall':
            groups['objects'] += sorted(m for m in all_models if m.startswith('coconut_'))
        for group, models in groups.items():
            for m in models:
                for t, roles in (model_textures(assets, m) or {}).items():
                    e = found.setdefault(t, {'groups': set(), 'courses': set(), 'roles': set()})
                    e['groups'].add(group)
                    e['courses'].add(name)
                    e['roles'] |= roles
    return found


def pack(a):
    assets = Assets(a.assets)
    flipped = bool(orientation_check())
    _, secondary = color_textures(assets)
    found = course_texture_groups(assets)
    usage = texture_usage(assets)
    wanted = set(a.groups.split(','))
    excluded = set(filter(None, a.exclude.split(',')))
    receipt = {'mode': 'pack', 'assets': str(a.assets), 'flippedRows': flipped, 'textures': {}, 'courses': {}}

    def one(name):
        e = found[name]
        if not e['groups'] & wanted:
            return name, None, 'group not selected'
        if name in excluded:
            return name, None, 'excluded (--exclude)'
        chain = name not in secondary and 'secondary' not in e['roles']
        try:
            original = assets.read(f'textures/{name}.t3x')
            use = usage.get(name, {'alpha': None, 'wrap': ('edge', 'edge')})
            blob, why = mip_variant(original, flipped, chain, use['alpha'] if a.alpha else None, use['wrap'], a.filter)
        except (ValueError, FileNotFoundError, struct.error) as err:
            return name, None, str(err)
        return name, (original, blob), why

    files = {}
    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        for name, result, why in pool.map(one, sorted(found)):
            e = found[name]
            entry = {'groups': sorted(e['groups']), 'courses': len(e['courses'])}
            if result is None or result[1] is None:
                entry.update(status='kept', reason=why)
            else:
                original, blob = result
                t = header(original)
                fmt, levels, flags = struct.unpack_from('<BBH', blob, 16)
                before = len(t['payload'])
                after = t['width'] * t['height'] // (2 if fmt == 12 else 1) + len(blob) - OMIP_HEADER
                entry.update(status=why, size=[t['width'], t['height']], levels=levels, bytesBefore=before, bytesAfter=after,
                             archiveBytes=len(blob))
                files[name] = blob
            receipt['textures'][name] = entry
    out = a.out / 'textures' / 'o3ds'
    out.mkdir(parents=True, exist_ok=True)
    write_mpk1(out / 'mips.pak', files)
    for name, e in found.items():
        r = receipt['textures'][name]
        for course in e['courses']:
            c = receipt['courses'].setdefault(course, {'textures': 0, 'mipmapped': 0, 'converted': 0, 'kept': 0,
                                                       'bytesBefore': 0, 'bytesAfter': 0})
            c['textures'] += 1
            if r['status'] == 'kept':
                c['kept'] += 1
                continue
            c['mipmapped'] += r['levels'] > 0
            c['converted'] += r['status'].startswith('etc1a4')
            c['bytesBefore'] += r['bytesBefore']
            c['bytesAfter'] += r['bytesAfter']
    done = [r for r in receipt['textures'].values() if r['status'] != 'kept']
    reasons = {}
    for r in receipt['textures'].values():
        key = r['status'] if r['status'] != 'kept' else 'kept: ' + r['reason']
        reasons[key] = reasons.get(key, 0) + 1
    data = (out / 'mips.pak').read_bytes()
    receipt['totals'] = {'courseTextures': len(found), 'entries': len(files), 'status': reasons,
                         'bytesBefore': sum(r['bytesBefore'] for r in done), 'bytesAfter': sum(r['bytesAfter'] for r in done),
                         'archiveBytes': len(data), 'archiveSha256': hashlib.sha256(data).hexdigest()}
    (a.out / 'o3ds-course-textures-pack-receipt.json').write_text(json.dumps(receipt, indent=1, sort_keys=True))
    print(json.dumps(receipt['totals'], indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--package', type=Path, help='package with models/o3ds (sidecars to rename in; exact/mips modes)')
    ap.add_argument('--assets', type=Path, default=ROOT / 'sd-card' / '3ds' / 'mkw-3ds')
    ap.add_argument('--out', type=Path, required=True, help='output package (models/o3ds + textures/o3ds)')
    ap.add_argument('--mode', choices=('pack', 'exact', 'mips'), default='pack')
    ap.add_argument('--groups', default='track,sky,objects', help='pack: course model groups to cover')
    ap.add_argument('--jobs', type=int, default=8, help='pack: parallel tex3ds encodes')
    ap.add_argument('--exclude', default='', help='pack: texture names (comma separated) that keep level 0 only')
    ap.add_argument('--filter', choices=('lanczos2', 'box'), default='lanczos2',
                    help='pack: ETC1 colour downsample (alpha chains use the alpha-weighted box)')
    ap.add_argument('--no-alpha', dest='alpha', action='store_false',
                    help='pack: leave ETC1A4 textures whose alpha is used without a chain')
    a = ap.parse_args()
    if a.mode == 'pack':
        return pack(a)
    if not a.package:
        ap.error('--package is required for exact/mips')
    from o3ds_lowpoly_io import read_variant
    assets = Assets(a.assets)
    flipped = bool(orientation_check())
    catalog = (ROOT / 'include' / 'mkw' / 'course_catalog.hpp').read_text()
    track = set()
    for rel in COURSE.findall(catalog):
        rel += '.list'
        if rel in assets.entries or (assets.root / rel).exists():
            track |= set(assets.read(rel).decode().split())
    course_models = lambda stem: stem in track or re.match(r'obj\d+_', stem) or stem.startswith('coconut_')
    if a.out.resolve() != a.package.resolve():
        if a.out.exists():
            shutil.rmtree(a.out)
        subprocess.run(['cp', '-c', '-R', str(a.package), str(a.out)], check=True)
    (a.out / 'textures' / 'o3ds').mkdir(parents=True, exist_ok=True)
    siblings, receipt = {}, {'mode': a.mode, 'flippedRows': flipped, 'textures': {}, 'models': {}, 'skipped': {}}
    for side in sorted((a.out / 'models' / 'o3ds').glob('*.gmdl')):
        stem = side.stem
        if not course_models(stem):
            continue
        data = bytearray(side.read_bytes())
        gv, nv, ni, ns, nt = struct.unpack_from('<5I', data, 4)
        end = 24 + ns * {3: 44, 4: 56, 5: 92}[gv] + nt * 64 + nv * 44 + ni * 2
        if data[end:end + 4] != b'O3RM':
            receipt['skipped'][stem] = 'index-only sidecar cannot rename textures'
            continue
        model, remap, version = read_variant(side)
        uv = model.records[:, 24:32].copy().view('<f4')
        if version == 1 and not (np.abs(uv) <= 64).all():
            receipt['skipped'][stem] = 'UV0 outside the O3RM v2 range'
            continue
        names_at = 24 + len(model.submeshes) * {3: 44, 4: 56, 5: 92}[model.header[0]]
        renamed = {}
        for slot, name in enumerate(model.names):
            if '_o3' in name or not eligible_slot(model, slot, a.mode):
                continue
            if name not in siblings:
                try:
                    payload, why = build(assets.read(f'textures/{name}.t3x'), a.mode, flipped)
                except (ValueError, FileNotFoundError) as e:
                    payload, why = None, str(e)
                if payload is None:
                    siblings[name] = None
                    receipt['textures'][name] = {'status': 'kept', 'reason': why}
                else:
                    sibling = f'{name}_o3{hashlib.sha256(payload).hexdigest()[:8]}'
                    if len(sibling) >= 64:
                        siblings[name] = None
                        receipt['textures'][name] = {'status': 'kept', 'reason': 'name too long'}
                    else:
                        (a.out / 'textures' / 'o3ds' / f'{sibling}.t3x').write_bytes(payload)
                        siblings[name] = sibling
                        orig = header(assets.read(f'textures/{name}.t3x'))
                        new = header(payload)
                        receipt['textures'][name] = {'status': why, 'sibling': sibling, 'bytesBefore': len(orig['payload']),
                                                     'bytesAfter': len(new['payload']), 'levels': new['levels'],
                                                     'size': [orig['width'], orig['height']]}
            if siblings.get(name):
                raw = siblings[name].encode().ljust(64, b'\0')
                data[names_at + 64 * slot:names_at + 64 * (slot + 1)] = raw
                renamed[name] = siblings[name]
        if renamed:
            if version == 1:
                at = data.rindex(b'O3RM')
                struct.pack_into('<I', data, at + 4, 2)
            side.write_bytes(bytes(data))
            receipt['models'][stem] = {'renamed': renamed, 'o3rmVersion': max(version, 2)}
    done = [t for t in receipt['textures'].values() if t['status'] != 'kept']
    receipt['totals'] = {'siblings': len(done), 'bytesBefore': sum(t['bytesBefore'] for t in done),
                         'bytesAfter': sum(t['bytesAfter'] for t in done), 'models': len(receipt['models']),
                         'skippedModels': len(receipt['skipped'])}
    (a.out / f'o3ds-course-textures-{a.mode}-receipt.json').write_text(json.dumps(receipt, indent=1))
    print(json.dumps(receipt['totals']))


if __name__ == '__main__':
    main()
