#!/usr/bin/env python3
"""QA: verify one engine change against a reference build in the worktree emulator.

Runs (tools/o3ds_perf_run.py):
  <label>-replay  physics replay test (ghost race timer/lap splits, twice)
  <label>-sim     CPU-driven VS race with the per-racer CPU log every 30 steps
                  and lockstep captures every 60 steps
  <label>-perf    the same race for timing (captures every 300 steps, no log)
then compares the -sim CPU log (positions, speeds, ranks, items, AI state),
race results and captures with <reference>-sim, and prints the race-frame
profile means of <reference>-perf and <label>-perf side by side.
"""
import argparse, csv, json, statistics, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / 'build/runs'
SIM_PREFIXES = ('QA cpu ', 'hb step=', 'results race', 'race end option', 'QA give', 'QA held')


def run(label, mode, binary, *extra):
    cmd = [sys.executable, str(ROOT / 'tools/o3ds_perf_run.py'), '--mode', mode, '--label', label,
           '--binary', binary, *extra]
    r = subprocess.run(cmd, capture_output=True, text=True)
    summary = json.loads((RUNS / label / 'run.json').read_text()) if (RUNS / label / 'run.json').exists() else {}
    if r.returncode or summary.get('status') != 'done':
        print(r.stdout[-2000:], r.stderr[-2000:])
    return summary


def sim_lines(label):
    log = RUNS / label / 'runtime.log'
    return [l.rstrip('\n') for l in log.open(errors='replace') if l.startswith(SIM_PREFIXES)] if log.exists() else []


def captures(label):
    d = RUNS / label / 'captures'
    return {p.name: p.read_bytes() for p in sorted(d.glob('*.ppm'))} if d.exists() else {}


def race_rows(label):
    path = RUNS / label / 'performance.csv'
    rows = list(csv.DictReader(path.open()))
    return [x for x in rows if x['rendered'] == '1' and x['physics_ticks'] == '2' and float(x['draws']) > 40]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('label')
    p.add_argument('--reference', default='baseline')
    p.add_argument('--binary', default=str(ROOT / 'build/mkw-3ds.3dsx'))
    p.add_argument('--reference-binary', default=str(ROOT / 'build/baseline/mkw-3ds.3dsx'))
    p.add_argument('--skip', default='', help='comma list of replay,sim,perf to skip for the candidate')
    p.add_argument('--extra', action='append', default=[], help='marker passed to every candidate run')
    a = p.parse_args()
    skip = set(filter(None, a.skip.split(',')))
    extra = [x for m in a.extra for x in ('--extra', m)]
    ref = a.reference
    for kind, args in (('sim', ['--cpu-log', '30', '--capture', '60']), ('perf', ['--cpu-log', '0', '--capture', '300'])):
        if not (RUNS / f'{ref}-{kind}' / 'run.json').exists():
            print('reference', kind, run(f'{ref}-{kind}', 'perf', a.reference_binary, *args).get('status'))
    result = {'label': a.label}
    if 'replay' not in skip:
        s = run(f'{a.label}-replay', 'replay', a.binary, *extra)
        result['replay'] = s.get('replay-result.json', {'pass': False, 'status': s.get('status')})
    if 'sim' not in skip:
        s = run(f'{a.label}-sim', 'perf', a.binary, '--cpu-log', '30', '--capture', '60', *extra)
        ref_lines, new_lines = sim_lines(f'{ref}-sim'), sim_lines(f'{a.label}-sim')
        first = next((i for i, (x, y) in enumerate(zip(ref_lines, new_lines)) if x != y), None)
        result['sim'] = {'status': s.get('status'), 'reference_lines': len(ref_lines), 'lines': len(new_lines),
                         'identical': ref_lines == new_lines and len(ref_lines) > 100}
        if first is not None:
            result['sim']['first_difference'] = [ref_lines[first], new_lines[first]]
        rc, nc = captures(f'{ref}-sim'), captures(f'{a.label}-sim')
        result['captures'] = {'reference': len(rc), 'candidate': len(nc),
                              'identical': sum(1 for k in rc if nc.get(k) == rc[k]),
                              'different': sorted(k for k in rc if k in nc and nc[k] != rc[k])}
    if 'perf' not in skip:
        s = run(f'{a.label}-perf', 'perf', a.binary, '--cpu-log', '0', '--capture', '300', *extra)
        result['perf_status'] = s.get('status')
    print(json.dumps(result, indent=2))
    (RUNS / f'{a.label}-verify.json').write_text(json.dumps(result, indent=2) + '\n')
    if 'perf' not in skip:
        r0, r1 = race_rows(f'{ref}-perf'), race_rows(f'{a.label}-perf')
        print(f'race frames: {len(r0)} vs {len(r1)}')
        for c in csv.DictReader((RUNS / f'{ref}-perf/performance.csv').open()).fieldnames:
            if not (c.endswith('_ms') or c in ('draws', 'triangles', 'pose_evaluations', 'skinned_vertices',
                                               'cpu_skinned_vertices', 'flush_bytes', 'command_usage')):
                continue
            try:
                v0 = statistics.mean(float(x[c]) for x in r0)
                v1 = statistics.mean(float(x[c]) for x in r1)
            except (ValueError, statistics.StatisticsError):
                continue
            if abs(v0) < 0.01 and abs(v1) < 0.01:
                continue
            pct = f'{100 * (v1 - v0) / v0:+7.1f}%' if v0 else ''
            print(f'{c:28s}{v0:11.3f}{v1:11.3f} {pct}')


if __name__ == '__main__':
    main()
