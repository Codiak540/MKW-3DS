#!/usr/bin/env python3
"""Mean/p90 of performance.csv columns over lockstep race frames (as tools/o3ds_perf_verify.py selects them)."""
import csv, statistics, sys
from pathlib import Path
cols = ['simulation_ms', 'interval_ms', 'wait_ms', 'render_thread_ms', 'worker_ms']
for label in sys.argv[1:]:
    path = Path('build/runs') / label / 'performance.csv'
    rows = [r for r in csv.DictReader(path.open()) if r.get('rendered') == '1' and r.get('physics_ticks') == '2' and float(r.get('draws', 0)) > 40]
    out = [f'{label:<20} frames={len(rows):>5}']
    for c in cols:
        if rows and c in rows[0]:
            v = sorted(float(r[c]) for r in rows)
            out.append(f'{c} mean {statistics.fmean(v):.3f} p90 {v[int(len(v) * 0.9)]:.3f}')
    print('  '.join(out))
