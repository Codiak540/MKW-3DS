#!/usr/bin/env python3
"""Audit course/scenery textures as the Old 3DS runtime loads them.

For every course (include/mkw/course_catalog.hpp list files): the track models,
the sky models and the course's objects (objNN_*), the textures each references
(menu.pak/race.pak entries win over loose files, as at runtime), with their
T3X format, size, mip levels, payload compression and GPU bytes, and whether an
alpha-capable format actually uses alpha. Read-only.
"""
import argparse
import json
import re
import struct
import sys
import zlib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from cook_o3ds_textures import Assets  # noqa: E402

FORMATS = {0: 'RGBA8', 1: 'RGB8', 2: 'RGBA5551', 3: 'RGB565', 4: 'RGBA4', 5: 'LA8', 6: 'HILO8', 7: 'L8',
           8: 'A8', 9: 'LA4', 10: 'L4', 11: 'A4', 12: 'ETC1', 13: 'ETC1A4'}
BPP = {0: 32, 1: 24, 2: 16, 3: 16, 4: 16, 5: 16, 6: 16, 7: 8, 8: 8, 9: 8, 10: 4, 11: 4, 12: 4, 13: 8}
ALPHA = {0, 2, 4, 5, 8, 9, 11, 13}
COURSE = re.compile(r'\{"([^"]+)",Kinoko::Course::\w+,"[^"]+","(models/[^"]+\.list)","(models/[^"]*)"')


def lz11(data):
    """Nintendo LZ11 (tex3ds -z lz11) decompression."""
    if data[0] != 0x11:
        raise ValueError('not LZ11')
    size = data[1] | data[2] << 8 | data[3] << 16
    at, out = 4, bytearray()
    if size == 0:
        size = struct.unpack_from('<I', data, 4)[0]
        at = 8
    while len(out) < size:
        flags = data[at]
        at += 1
        for bit in range(8):
            if len(out) >= size:
                break
            if not flags & (0x80 >> bit):
                out.append(data[at])
                at += 1
                continue
            b = data[at] >> 4
            if b == 0:
                count = (((data[at] & 15) << 4) | (data[at + 1] >> 4)) + 0x11
                disp = (((data[at + 1] & 15) << 8) | data[at + 2]) + 1
                at += 3
            elif b == 1:
                count = (((data[at] & 15) << 12) | (data[at + 1] << 4) | (data[at + 2] >> 4)) + 0x111
                disp = (((data[at + 2] & 15) << 8) | data[at + 3]) + 1
                at += 4
            else:
                count = b + 1
                disp = (((data[at] & 15) << 8) | data[at + 1]) + 1
                at += 2
            for _ in range(count):
                out.append(out[-disp])
    return bytes(out)


def t3x(data):
    """Header and (decompressed) payload of a single-texture T3X."""
    count, dims, fmt, levels = struct.unpack_from('<HBBB', data)
    width, height = 1 << ((dims & 7) + 3), 1 << (((dims >> 3) & 7) + 3)
    at = 5 + count * 12
    info = {'subtextures': count, 'cube': bool(dims & 0x40), 'width': width, 'height': height,
            'format': FORMATS.get(fmt, fmt), 'fmt': fmt, 'levels': levels}
    header, = struct.unpack_from('<I', data, at)
    kind, size = header & 0xff, header >> 8
    payload = data[at:]
    if kind == 0:
        raw = data[at + 4:at + 4 + size]
        info['compression'] = 'none'
    elif kind == 0x11:
        raw = lz11(payload)
        info['compression'] = 'lz11'
    else:
        raw = None
        info['compression'] = hex(kind)
    faces = 6 if info['cube'] else 1
    info['gpuBytes'] = faces * sum((width >> i) * (height >> i) * BPP[fmt] // 8 for i in range(levels + 1))
    return info, raw


def alpha_used(fmt, raw, width, height):
    """True when any texel of level 0 is not fully opaque."""
    if raw is None or fmt not in ALPHA:
        return fmt in ALPHA if raw is None else False
    n = width * height
    if fmt == 13:  # ETC1A4: per 4x4 block, 8 bytes alpha then 8 bytes colour
        for b in range(n // 16):
            if raw[16 * b:16 * b + 8] != b'\xff' * 8:
                return True
        return False
    if fmt == 0:
        return any(raw[i] != 0xff for i in range(0, 4 * n, 4))
    if fmt == 2:
        return any(not (raw[i] & 1) for i in range(0, 2 * n, 2))
    if fmt == 4:
        return any((raw[i] & 0xf) != 0xf for i in range(0, 2 * n, 2))
    if fmt == 5:
        return any(raw[i] != 0xff for i in range(0, 2 * n, 2))
    if fmt == 8:
        return any(x != 0xff for x in raw[:n])
    if fmt == 9:
        return any((x & 0xf) != 0xf for x in raw[:n])
    if fmt == 11:
        return any(x != 0xff for x in raw[:n // 2])
    return True


def model_textures(assets, name):
    """(texture names, per-slot flags) of a model as the runtime resolves it."""
    rel = f'models/{name}.gmdl'
    try:
        data = assets.read(rel)
    except FileNotFoundError:
        return None
    magic, version, nv, ni, ns, nt = struct.unpack_from('<4s5I', data)
    record = {3: 44, 4: 56, 5: 92}[version]
    names = [data[24 + ns * record + 64 * i:24 + ns * record + 64 * (i + 1)].split(b'\0')[0].decode() for i in range(nt)]
    slots = {}
    for i in range(ns):
        first, count, tex, flags, sec = struct.unpack_from('<IIiIi', data, 24 + i * record)
        if not count:
            continue
        for slot, role in ((tex, 'primary'), (sec, 'secondary')):
            if slot >= 0:
                slots.setdefault(names[slot], set()).add(role)
    return slots


def courses():
    text = (ROOT / 'include' / 'mkw' / 'course_catalog.hpp').read_text()
    return [(m.group(1), m.group(2), m.group(3)) for m in COURSE.finditer(text)]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--assets', type=Path, default=ROOT / 'sd-card' / '3ds' / 'mkw-3ds')
    ap.add_argument('--receipt', type=Path)
    a = ap.parse_args()
    assets = Assets(a.assets)
    listed = lambda rel: assets.read(rel).decode().split() if rel and (rel in assets.entries or (assets.root / rel).exists()) else []
    all_models = {n[7:-5] for n in assets.entries if n.startswith('models/') and n.endswith('.gmdl')}
    all_models |= {p.stem for p in (assets.root / 'models').glob('*.gmdl')}
    textures, report = {}, {'assets': str(a.assets), 'courses': {}}
    for name, track_list, sky_list in courses():
        cid = re.search(r'course_(\d+)', track_list)
        prefix = f'obj{cid.group(1)}_' if cid else None
        groups = {'track': listed(track_list), 'sky': listed(sky_list),
                  'objects': sorted(m for m in all_models if prefix and m.startswith(prefix))}
        if name == 'Coconut Mall':
            groups['objects'] += sorted(m for m in all_models if m.startswith('coconut_'))
        course = {}
        for group, models in groups.items():
            for m in models:
                slots = model_textures(assets, m)
                if slots is None:
                    continue
                for t, roles in slots.items():
                    if t not in textures:
                        try:
                            info, raw = t3x(assets.read(f'textures/{t}.t3x'))
                            info['alpha'] = alpha_used(info['fmt'], raw, info['width'], info['height'])
                        except (FileNotFoundError, struct.error, ValueError, IndexError) as e:
                            info = {'error': str(e)}
                        textures[t] = info
                    entry = course.setdefault(t, {'groups': set(), 'roles': set()})
                    entry['groups'].add(group)
                    entry['roles'] |= roles
        summary = Counter()
        for t, e in course.items():
            info = textures[t]
            if 'error' in info:
                summary['missing'] += 1
                continue
            g = 'track' if 'track' in e['groups'] else 'sky' if 'sky' in e['groups'] else 'objects'
            summary[f'{g}:textures'] += 1
            summary[f'{g}:bytes'] += info['gpuBytes']
            if info['levels'] == 0 and min(info['width'], info['height']) > 8:
                summary[f'{g}:noMips'] += 1
                summary[f'{g}:noMipsBytes'] += info['gpuBytes']
            if info['fmt'] not in (12, 13):
                summary[f'{g}:fmt:{info["format"]}'] += 1
            if info['fmt'] in (13,) and not info['alpha']:
                summary[f'{g}:etc1a4WithoutAlpha'] += 1
            if info['fmt'] in (0, 1, 2, 3, 4) and not info['alpha']:
                summary[f'{g}:uncompressedOpaque'] += 1
        report['courses'][name] = {'summary': dict(summary),
                                   'textures': {t: {'groups': sorted(e['groups']), 'roles': sorted(e['roles'])} for t, e in course.items()}}
    report['textures'] = textures
    total = Counter()
    for c in report['courses'].values():
        total.update(c['summary'])
    report['total'] = dict(total)
    fmts = Counter((v.get('format'), v.get('levels', -1) > 0, v.get('alpha')) for v in textures.values() if 'format' in v)
    report['formats'] = {f'{f} mips={m} alpha={al}': n for (f, m, al), n in sorted(fmts.items(), key=lambda x: -x[1])}
    print(json.dumps({'textures': len(textures), 'formats': report['formats']}, indent=1))
    for name, c in report['courses'].items():
        s = c['summary']
        print(f"{name:22} track {s.get('track:textures',0):3} tex {s.get('track:bytes',0)//1024:5} KiB noMips {s.get('track:noMips',0):3} "
              f"({s.get('track:noMipsBytes',0)//1024} KiB) | objects {s.get('objects:textures',0):3} noMips {s.get('objects:noMips',0):3} | sky noMips {s.get('sky:noMips',0)}")
    if a.receipt:
        a.receipt.write_text(json.dumps(report, indent=1, default=sorted))


if __name__ == '__main__':
    main()
