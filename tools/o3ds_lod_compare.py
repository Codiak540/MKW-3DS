#!/usr/bin/env python3
"""Compare two tools/o3ds_perf_run.py archives for the Old 3DS scenery/opponent
LOD work: simulation identity (every 'QA cpu' racer line of runtime.log),
race captures pixel by pixel, and performance.csv means over rendered frames.

  o3ds_lod_compare.py OFF ON            human-readable report
  o3ds_lod_compare.py --row NAME OFF ON markdown table row
"""
import argparse
import csv
import statistics as st
from pathlib import Path

import numpy as np

KEYS = ('draws', 'triangles', 'scenery_ms', 'opponents_ms', 'pose_evaluations', 'pose_reuses',
        'skinned_vertices', 'render_thread_ms')


def cpu_lines(run):
    return [l for l in (run / 'runtime.log').read_text(errors='replace').splitlines() if l.startswith('QA cpu ')]


def ppm(path):
    parts = path.read_bytes().split(b'\n', 3)
    w, h = map(int, parts[1].split())
    return np.frombuffer(parts[3], np.uint8).reshape(h, w, -1)


def captures(a, b):
    same, worst = 0, []
    names = sorted(p.name for p in (a / 'captures').glob('*.ppm')) if (a / 'captures').exists() else []
    for n in names:
        p, q = a / 'captures' / n, b / 'captures' / n
        if not q.exists():
            worst.append((-1, n))
        elif p.read_bytes() == q.read_bytes():
            same += 1
        else:
            x, y = ppm(p), ppm(q)
            worst.append((int((x != y).any(-1).sum()) if x.shape == y.shape else -1, n))
    return same, len(names), sorted(worst, reverse=True)


def means(run):
    rows = [r for r in csv.DictReader(open(run / 'performance.csv')) if r.get('rendered') == '1']
    return {k: st.mean(float(r[k]) for r in rows if r.get(k, '') != '') for k in KEYS}, len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--row')
    ap.add_argument('off', type=Path)
    ap.add_argument('on', type=Path)
    a = ap.parse_args()
    ca, cb = cpu_lines(a.off), cpu_lines(a.on)
    sim = 'identical' if ca == cb and ca else f'DIFFERENT ({sum(x != y for x, y in zip(ca, cb))} of {len(ca)} lines)'
    same, total, worst = captures(a.off, a.on)
    (ma, fa), (mb, fb) = means(a.off), means(a.on)
    if a.row:
        cells = [a.row, f'{len(ca)} {sim}', f'{same}/{total}']
        for k in KEYS:
            x, y = ma[k], mb[k]
            fmt = '{:.0f}' if x >= 100 else '{:.2f}' if x < 10 else '{:.1f}'
            cells.append(f'{fmt.format(x)} → {fmt.format(y)} ({(y - x) / x * 100:+.1f}%)' if x else f'{x:.2f} → {y:.2f}')
        print('| ' + ' | '.join(cells) + ' |')
        return
    print(f'simulation: {len(ca)} racer lines {sim}')
    print(f'captures: {same}/{total} identical' + ''.join(f'\n  {n}: {d} pixels differ' for d, n in worst[:8]))
    print(f'{"metric":20s} {"off":>12s} {"on":>12s} {"change":>8s}   (frames {fa} / {fb})')
    for k in KEYS:
        x, y = ma[k], mb[k]
        print(f'{k:20s} {x:12.3f} {y:12.3f} {((y - x) / x * 100 if x else 0):+7.1f}%')


if __name__ == '__main__':
    main()
