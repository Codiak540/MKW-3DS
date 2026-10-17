#!/usr/bin/env python3
"""Compare two o3ds_perf_run.py archives: race captures pixel by pixel and
performance.csv means (rendered race frames)."""
import csv, statistics as st, sys
from pathlib import Path
import numpy as np

KEYS = ['interval_ms', 'simulation_ms', 'course_ms', 'scenery_ms', 'opponents_ms', 'draws', 'triangles',
        'culled_triangles', 'culled_submeshes', 'cull_ms', 'cull_calls', 'draw_elements_ms', 'draw_elements_calls',
        'cmd_draw_ms', 'render_thread_ms']


def ppm(path):
    data = path.read_bytes()
    parts = data.split(b'\n', 3)
    w, h = map(int, parts[1].split())
    return np.frombuffer(parts[3], np.uint8).reshape(h, w, -1)


def main(a, b):
    a, b = Path(a), Path(b)
    ca = sorted((a / 'captures').glob('*.ppm')) if (a / 'captures').exists() else []
    same = diff = missing = 0
    worst = []
    for p in ca:
        q = b / 'captures' / p.name
        if not q.exists():
            missing += 1; continue
        if p.read_bytes() == q.read_bytes():
            same += 1; continue
        x, y = ppm(p), ppm(q)
        n = int((x != y).any(-1).sum()) if x.shape == y.shape else -1
        diff += 1; worst.append((n, p.name))
    print(f'captures: {same} identical, {diff} different, {missing} missing (of {len(ca)})')
    for n, name in sorted(worst, reverse=True)[:8]:
        print(f'  {name}: {n} pixels differ')
    rows = {}
    for run in (a, b):
        r = [x for x in csv.DictReader(open(run / 'performance.csv')) if x.get('rendered') == '1']
        rows[run] = r
    print(f'{"metric":22s} {a.name:>14s} {b.name:>14s} {"change":>8s}   (frames {len(rows[a])} / {len(rows[b])})')
    for k in KEYS:
        try:
            va = st.mean(float(x[k]) for x in rows[a] if x.get(k, '') != '')
            vb = st.mean(float(x[k]) for x in rows[b] if x.get(k, '') != '')
        except (st.StatisticsError, KeyError, ValueError):
            continue
        ch = (vb - va) / va * 100 if va else 0
        print(f'{k:22s} {va:14.3f} {vb:14.3f} {ch:+7.1f}%')


if __name__ == '__main__':
    main(*sys.argv[1:3])
