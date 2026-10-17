#!/usr/bin/env python3
"""Run the host determinism harness over courses and seeds, comparing the
per-tick state-hash streams of two physics paths.

  run.py --paths reference phased --courses all --seeds 1 2 3 --ticks 6000

Every (course, seed) runs once per path (in parallel processes); the streams
must be identical tick for tick. On the first difference the tick is reported
and both paths are re-run with --trace-tick to name the first differing field.
Results go to build/sim-determinism/runs/<label>/ and a JSON summary.
"""
import argparse, concurrent.futures, json, os, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXE = ROOT / 'build/sim-determinism/harness'
RUNS = ROOT / 'build/sim-determinism/runs'
sys.path.insert(0, str(ROOT / 'tools'))

COURSE_COUNT = 32  # VS courses in include/mkw/course_catalog.hpp order


def run_one(course, seed, path, ticks, out, extra=()):
    env = dict(os.environ, KINOKO_FILESYSTEM_ROOT=str(ROOT / 'data/extracted'))
    cmd = [str(EXE), '--course', str(course), '--seed', str(seed), '--ticks', str(ticks), '--path', path,
           '--out', str(out), *extra]
    start = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(ROOT))
    return {'course': course, 'seed': seed, 'path': path, 'code': r.returncode, 'stdout': r.stdout.strip(),
            'stderr': r.stderr.strip()[-500:], 'seconds': round(time.time() - start, 1), 'out': str(out)}


def first_difference(a, b):
    da, db = Path(a).read_bytes(), Path(b).read_bytes()
    n = min(len(da), len(db)) // 8
    for i in range(n):
        if da[i * 8:i * 8 + 8] != db[i * 8:i * 8 + 8]:
            return i
    return None if len(da) == len(db) else n


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--paths', nargs='+', default=['reference', 'phased'])
    p.add_argument('--courses', nargs='+', default=['all'])
    p.add_argument('--seeds', nargs='+', type=int, default=[1])
    p.add_argument('--ticks', type=int, default=6000)
    p.add_argument('--label', default='latest')
    p.add_argument('--jobs', type=int, default=os.cpu_count() or 4)
    p.add_argument('--engine', type=int, default=None)
    p.add_argument('--chaos', type=int, default=0, help='worker sleep jitter in microseconds for parallel runs')
    a = p.parse_args()
    courses = list(range(COURSE_COUNT)) if a.courses == ['all'] else [int(c) for c in a.courses]
    out = RUNS / a.label
    out.mkdir(parents=True, exist_ok=True)
    jobs = []
    for course in courses:
        for seed in a.seeds:
            for path in a.paths:
                extra = ['--engine', str(a.engine)] if a.engine is not None else []
                if path == 'parallel' and a.chaos:
                    extra += ['--chaos', str(a.chaos)]
                jobs.append((course, seed, path, a.ticks, out / f'c{course:02d}-s{seed}-{path}.hash', extra))
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as pool:
        for r in pool.map(lambda j: run_one(*j), jobs):
            results.append(r)
            flag = 'ok' if r['code'] == 0 else f'EXIT {r["code"]}'
            print(f"c{r['course']:02d} s{r['seed']} {r['path']:<10} {flag:<8} {r['seconds']:>6}s {r['stdout'][-90:]}", flush=True)
    summary = {'label': a.label, 'ticks': a.ticks, 'paths': a.paths, 'runs': results, 'comparisons': []}
    identical = total = 0
    kart_ticks = 0
    for course in courses:
        for seed in a.seeds:
            base = out / f'c{course:02d}-s{seed}-{a.paths[0]}.hash'
            if not base.exists():
                continue
            kart_ticks += len(base.read_bytes()) // 8 * 12
            for path in a.paths[1:]:
                other = out / f'c{course:02d}-s{seed}-{path}.hash'
                total += 1
                diff = first_difference(base, other) if other.exists() else -1
                comp = {'course': course, 'seed': seed, 'paths': [a.paths[0], path], 'first_difference': diff,
                        'ticks': len(base.read_bytes()) // 8}
                if diff is None:
                    identical += 1
                else:
                    traces = []
                    for pth in (a.paths[0], path):
                        tr = out / f'c{course:02d}-s{seed}-{pth}.trace'
                        run_one(course, seed, pth, a.ticks, out / 'scratch.hash',
                                ['--trace-tick', str(max(diff, 0)), '--trace-out', str(tr)])
                        traces.append([l.split() for l in tr.read_text().splitlines()] if tr.exists() else [])
                    field = None
                    for x, y in zip(*traces):
                        if x != y:
                            field = x[0]
                            break
                    if field is None and traces[0] and len(traces[0]) != len(traces[1]):
                        field = f'field count {len(traces[0])} vs {len(traces[1])}'
                    comp['first_field'] = field
                    print(f'DIFFERENT c{course:02d} s{seed} {a.paths[0]} vs {path}: tick {diff}, field {field}')
                summary['comparisons'].append(comp)
    summary['identical'] = identical
    summary['total'] = total
    summary['kart_ticks_per_path'] = kart_ticks
    (out / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(f'{identical}/{total} comparisons identical; {kart_ticks} kart-ticks per path; summary {out / "summary.json"}')
    return 0 if identical == total and all(r['code'] == 0 for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
