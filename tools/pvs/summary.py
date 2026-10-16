#!/usr/bin/env python3
"""Markdown rows for off/on run pairs: summary.py NAME:OFF:ON ..."""
import csv, statistics as st, sys
from pathlib import Path
RUNS = Path(__file__).resolve().parents[2] / 'build/runs'


def mean(run, k):
    return st.mean(float(x[k]) for x in csv.DictReader(open(RUNS / run / 'performance.csv')) if x['rendered'] == '1')


def captures(a, b):
    ca = sorted((RUNS / a / 'captures').glob('*.ppm'))
    same = sum((RUNS / b / 'captures' / p.name).exists() and p.read_bytes() == (RUNS / b / 'captures' / p.name).read_bytes() for p in ca)
    return f'{same}/{len(ca)}'


for arg in sys.argv[1:]:
    name, off, on = arg.split(':')
    cells = [name, captures(off, on)]
    for k, fmt in (('triangles', '{:.0f}'), ('draws', '{:.1f}'), ('cull_ms', '{:.3f}'), ('render_thread_ms', '{:.2f}')):
        a, b = mean(off, k), mean(on, k)
        cells.append(f'{fmt.format(a)} → {fmt.format(b)} ({(b - a) / a * 100:+.1f}%)')
    print('| ' + ' | '.join(cells) + ' |')
