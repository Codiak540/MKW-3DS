#!/usr/bin/env python3
"""Emulator A/B for Old 3DS course texture mip chains (docs/o3ds-texture-cache.md).

  run      for each VS course, two tools/o3ds_perf_run.py races with the same
           3DSX: `off` (o3ds-texmips-off.txt, the previous behaviour) and `on`
           (textures/o3ds/mips.pak staged in the emulator copy's SD folder).
  compare  per course: top/bottom captures identical or not, changed pixels,
           high-frequency energy (mean |Laplacian| of luma) of the changed
           pixels in each arm (lower = calmer, less aliasing), frame timings
           from performance.csv and the texture/VRAM lines of runtime.log.
           Writes a JSON receipt and side-by-side PNGs (off | on | diff x4)
           of the most-changed frames into --images (game-derived: keep them
           in build/, never commit them).

Only the --emu copy is written and only its own Azahar process is stopped
(o3ds_perf_run.py).
"""
import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / 'build' / 'runs'
sys.path.insert(0, str(ROOT / 'tools'))
from o3ds_course_tour import COURSES  # noqa: E402

TIMINGS = ('interval_ms', 'simulation_ms', 'course_ms', 'submit_ms', 'render_thread_ms', 'cmd_draw_ms',
           'draw_elements_ms', 'draws', 'triangles')


def label(base, course, arm):
    return f'{base}-c{course:02d}-{arm}'


def run(a):
    sd = a.emu / 'user/sdmc/3ds/mkw-3ds'
    if not (sd / 'textures/o3ds/mips.pak').is_file():
        raise SystemExit(f'{sd}/textures/o3ds/mips.pak is not staged')
    for course in a.courses:
        for arm in a.arms:
            cmd = [sys.executable, str(ROOT / 'tools/o3ds_perf_run.py'), '--emu', str(a.emu), '--mode', 'perf',
                   '--label', label(a.label, course, arm), '--binary', str(a.binary), '--course', str(course),
                   '--frames', str(a.frames), '--timeout', str(a.timeout)]
            if arm == 'off':
                cmd += ['--extra', 'o3ds-texmips-off.txt']
            elif arm == 'nearest':
                cmd += ['--extra', 'o3ds-texmips-nearest.txt']
            r = subprocess.run(cmd, capture_output=True, text=True)
            status = json.loads(r.stdout[r.stdout.index('{'):])['status'] if '{' in r.stdout else 'error'
            print(f'{course:02d} {COURSES[course]:24} {arm:7} {status}', flush=True)


def read(path):
    return np.asarray(Image.open(path).convert('RGB'), dtype=np.int16)


def laplacian(img):
    y = img[..., 0] * 0.299 + img[..., 1] * 0.587 + img[..., 2] * 0.114
    p = np.pad(y, 1, mode='edge')
    return np.abs(4 * y - p[:-2, 1:-1] - p[2:, 1:-1] - p[1:-1, :-2] - p[1:-1, 2:])


def timings(path):
    rows = [r for r in csv.DictReader(path.open()) if r.get('rendered') == '1']
    rows = rows[2:]  # first frames include load work
    out = {'frames': len(rows)}
    for key in TIMINGS:
        values = [float(r[key]) for r in rows if r.get(key) not in (None, '')]
        out[key] = round(sum(values) / len(values), 4) if values else None
    return out


def log_lines(path):
    text = path.read_text(errors='replace') if path.exists() else ''
    out = {}
    for key, pattern in (('mips', r'texture_mips loaded=.*'), ('vram', r'course textures in VRAM .*'),
                         ('loadTrack', r'load track [\d.]+ ms'), ('raceLoad', r'race_load_ms=[\d.]+')):
        m = re.findall(pattern, text)
        out[key] = m[-1] if m else None
    return out


def compare_course(a, course, images):
    off, on = RUNS / label(a.label, course, 'off'), RUNS / label(a.label, course, a.against)
    names = sorted({p.name for p in (off / 'captures').glob('race-*.ppm')} | {p.name for p in (on / 'captures').glob('race-*.ppm')})
    report = {'course': COURSES[course], 'top': {'frames': 0, 'identical': 0}, 'bottom': {'frames': 0, 'identical': 0},
              'missing': [], 'changedPixels': 0, 'energyOff': 0.0, 'energyOn': 0.0, 'calmerFrames': 0, 'worst': []}
    changed = []
    for n in names:
        pa, pb = off / 'captures' / n, on / 'captures' / n
        screen = 'bottom' if 'bottom' in n else 'top'
        if not pa.exists() or not pb.exists():
            report['missing'].append(n)
            continue
        report[screen]['frames'] += 1
        ia, ib = read(pa), read(pb)
        if ia.shape == ib.shape and np.array_equal(ia, ib):
            report[screen]['identical'] += 1
            continue
        mask = np.any(ia != ib, axis=-1)
        la, lb = laplacian(ia), laplacian(ib)
        ea, eb = float(la[mask].mean()), float(lb[mask].mean())
        report['changedPixels'] += int(mask.sum())
        report['energyOff'] += float(la[mask].sum())
        report['energyOn'] += float(lb[mask].sum())
        report['calmerFrames'] += eb <= ea
        changed.append((int(mask.sum()), n, ea, eb))
        if screen == 'bottom':
            report.setdefault('bottomChanged', []).append(n)
    if report['changedPixels']:
        report['energyOff'] = round(report['energyOff'] / report['changedPixels'], 3)
        report['energyOn'] = round(report['energyOn'] / report['changedPixels'], 3)
    report['changedFrames'] = len(changed)
    for pixels, n, ea, eb in sorted(changed, reverse=True)[:a.keep]:
        report['worst'].append({'frame': n, 'pixels': pixels, 'energyOff': round(ea, 2), 'energyOn': round(eb, 2)})
        if images:
            ia, ib = read(off / 'captures' / n), read(on / 'captures' / n)
            diff = np.clip(np.abs(ia - ib) * 4, 0, 255)
            strip = np.concatenate([ia, ib, diff], axis=1).astype(np.uint8)
            images.mkdir(parents=True, exist_ok=True)
            Image.fromarray(strip).save(images / f'c{course:02d}-{n[:-4]}.png')
    report['timingsOff'] = timings(off / 'performance.csv') if (off / 'performance.csv').exists() else None
    report['timingsOn'] = timings(on / 'performance.csv') if (on / 'performance.csv').exists() else None
    report['logOff'], report['logOn'] = log_lines(off / 'runtime.log'), log_lines(on / 'runtime.log')
    return report


def compare(a):
    out = {'label': a.label, 'against': a.against, 'courses': {}}
    for course in a.courses:
        r = compare_course(a, course, a.images)
        out['courses'][f'{course:02d}'] = r
        t0, t1 = r['timingsOff'] or {}, r['timingsOn'] or {}
        print(f"{course:02d} {r['course']:24} top {r['top']['identical']}/{r['top']['frames']} identical, "
              f"bottom {r['bottom']['identical']}/{r['bottom']['frames']}, changed px {r['changedPixels']}, "
              f"|lap| off {r['energyOff']} on {r['energyOn']} (calmer {r['calmerFrames']}/{r['changedFrames']}), "
              f"interval {t0.get('interval_ms')} -> {t1.get('interval_ms')}, render {t0.get('render_thread_ms')} -> {t1.get('render_thread_ms')}")
    if a.receipt:
        a.receipt.parent.mkdir(parents=True, exist_ok=True)
        a.receipt.write_text(json.dumps(out, indent=1) + '\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('run', 'compare'):
        p = sub.add_parser(name)
        p.add_argument('--label', required=True)
        p.add_argument('--courses', type=lambda s: [int(x) for x in s.split(',')], default=[0])
        if name == 'run':
            p.add_argument('--emu', type=Path, required=True)
            p.add_argument('--binary', type=Path, required=True)
            p.add_argument('--arms', type=lambda s: s.split(','), default=['off', 'on'])
            p.add_argument('--frames', type=int, default=1500)
            p.add_argument('--timeout', type=float, default=700)
        else:
            p.add_argument('--against', default='on', help='arm compared with off (on or nearest)')
            p.add_argument('--receipt', type=Path)
            p.add_argument('--images', type=Path)
            p.add_argument('--keep', type=int, default=3, help='side-by-side images per course')
    a = ap.parse_args()
    run(a) if a.cmd == 'run' else compare(a)


if __name__ == '__main__':
    main()
