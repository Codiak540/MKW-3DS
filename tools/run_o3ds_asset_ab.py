#!/usr/bin/env python3
"""Emulator A/B for Old 3DS asset-data changes (same executable, same autoplay).

  run      stage one models/o3ds (+ optional textures/o3ds) overlay into a private
           Azahar copy, run menu-autoplay.txt with lockstep race captures and
           performance.txt, collect race-*.ppm and performance.csv, stop that
           emulator process only.
  compare  pixel-compare the captures of two runs (identical names).

Only the --emulator copy is written. Captures are lockstep (src/main.cpp: two
simulation ticks per presented frame while race-capture.txt exists), so two
builds or asset sets compare frame for frame.
"""
import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = ('race-', 'performance.csv', 'menu-performance.csv', 'menu-autoplay-result.json')


def sd(emulator):
    return emulator / 'user' / 'sdmc' / '3ds' / 'mkw-3ds'


def clean(root):
    for p in root.iterdir():
        if p.name.startswith(OUTPUTS) or p.name.startswith('race-'):
            p.unlink()


def replace_tree(src, dest):
    if dest.exists():
        shutil.rmtree(dest)
    # APFS clone where possible: no extra disk for unchanged files.
    subprocess.run(['cp', '-c', '-R', str(src), str(dest)], check=True)


def run(a):
    root = sd(a.emulator)
    out = a.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    clean(root)
    shutil.copy2(a.exe, root / 'mkw-3ds.3dsx')
    if a.models:
        replace_tree(a.models, root / 'models' / 'o3ds')
    if a.textures:
        replace_tree(a.textures, root / 'textures' / 'o3ds')
    for marker in a.marker_off:
        (root / marker).unlink(missing_ok=True)
    for marker in a.marker_on:
        (root / marker).write_text('1\n')
    (root / 'menu-autoplay.txt').write_text(a.script + '\n')
    (root / 'race-capture.txt').write_text(f'{a.capture}\n')
    (root / 'performance.txt').write_text('1\n')
    # Launched through the bundle (LaunchServices, in the background): run
    # directly, Azahar 2126 stops at a modal "run via Azahar.app" warning.
    bundle = (a.emulator / 'Azahar.app').resolve()
    binary = str(bundle / 'Contents' / 'MacOS' / 'azahar')
    pids = lambda: {int(x) for x in subprocess.run(['pgrep', '-f', binary], capture_output=True, text=True).stdout.split()}
    before = pids()
    start = time.time()
    subprocess.run(['open', '-n', '-g', '--stdout', str(out / 'azahar.log'), '--stderr', str(out / 'azahar.log'),
                    str(bundle), '--args', str(root / 'mkw-3ds.3dsx')], check=True)
    pid = None
    while time.time() - start < 30 and not pid:
        new = pids() - before
        pid = min(new) if new else None
        time.sleep(0.5)
    alive = lambda: pid is not None and subprocess.run(['kill', '-0', str(pid)], capture_output=True).returncode == 0
    result = root / 'menu-autoplay-result.json'
    status = 'timeout' if pid else 'not started'
    try:
        while pid and time.time() - start < a.timeout:
            if not alive():
                status = 'exited'
                break
            if result.exists():
                time.sleep(3)  # let performance.csv close
                status = 'done'
                break
            time.sleep(2)
    finally:
        # Only this emulator copy's process is stopped.
        if alive():
            subprocess.run(['kill', str(pid)])
            for _ in range(15):
                if not alive():
                    break
                time.sleep(1)
            if alive():
                subprocess.run(['kill', '-9', str(pid)])
    for p in sorted(root.iterdir()):
        if p.name.startswith(OUTPUTS):
            shutil.move(str(p), out / p.name)
    if (root / 'runtime.log').exists():
        shutil.copy2(root / 'runtime.log', out / 'runtime.log')
    summary = {'status': status, 'seconds': round(time.time() - start, 1), 'script': a.script, 'capture': a.capture,
               'models': str(a.models) if a.models else None, 'textures': str(a.textures) if a.textures else None,
               'exe': str(a.exe), 'captures': len(list(out.glob('race-*.ppm')))}
    if (out / 'menu-autoplay-result.json').exists():
        summary['autoplay'] = json.loads((out / 'menu-autoplay-result.json').read_text())
    summary['performance'] = performance(out / 'performance.csv')
    (out / 'summary.json').write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary))
    return status == 'done'


def performance(path):
    """Mean of the numeric performance.csv columns over race frames."""
    if not path.exists():
        return None
    rows = list(csv.reader(path.open()))
    head = next((i for i, r in enumerate(rows) if r and r[0] and not r[0].startswith('#') and any(c.isalpha() for c in r[0])), None)
    if head is None:
        return None
    names = rows[head]
    data = [r for r in rows[head + 1:] if len(r) == len(names)]
    means = {}
    for i, n in enumerate(names):
        try:
            vals = [float(r[i]) for r in data]
        except ValueError:
            continue
        if vals:
            means[n] = round(sum(vals) / len(vals), 4)
    means['frames'] = len(data)
    return means


def read_ppm(path):
    data = path.read_bytes()
    parts = data.split(b'\n', 3)
    w, h = map(int, parts[1].split())
    return w, h, parts[3]


def compare(a):
    names = sorted({p.name for p in a.a.glob('race-*.ppm')} | {p.name for p in a.b.glob('race-*.ppm')})
    report = {'a': str(a.a), 'b': str(a.b), 'frames': len(names), 'identical': 0, 'different': [], 'missing': []}
    for n in names:
        pa, pb = a.a / n, a.b / n
        if not pa.exists() or not pb.exists():
            report['missing'].append(n)
            continue
        wa, ha, da = read_ppm(pa)
        wb, hb, db = read_ppm(pb)
        if (wa, ha) != (wb, hb):
            report['different'].append({'frame': n, 'size': 'mismatch'})
            continue
        if da == db:
            report['identical'] += 1
            continue
        diff = sum(1 for i in range(0, len(da), 3) if da[i:i + 3] != db[i:i + 3])
        maxd = max(abs(x - y) for x, y in zip(da, db))
        report['different'].append({'frame': n, 'pixels': diff, 'maxChannelDelta': maxd})
    print(json.dumps(report, indent=1))
    if a.report:
        a.report.write_text(json.dumps(report, indent=1))
    return not report['different'] and not report['missing']


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('--emulator', type=Path, default=ROOT / 'build' / 'azahar-o3ds')
    r.add_argument('--exe', type=Path, default=ROOT / 'build' / 'mkw-3ds.3dsx')
    r.add_argument('--models', type=Path, help='models/o3ds overlay to stage')
    r.add_argument('--textures', type=Path, help='textures/o3ds overlay to stage')
    r.add_argument('--marker-on', action='append', default=[], help='QA marker file to create (e.g. o3ds-textures-test.txt)')
    r.add_argument('--marker-off', action='append', default=[], help='QA marker file to remove')
    r.add_argument('--script', default='w30 A w20 A w20 A w20 A w20 A w20 D D A w20 A w20 A w20 A w20 A w20 A w20 A auto:600 end')
    r.add_argument('--capture', type=int, default=20)
    r.add_argument('--timeout', type=float, default=900)
    r.add_argument('--out', type=Path, required=True)
    c = sub.add_parser('compare')
    c.add_argument('a', type=Path)
    c.add_argument('b', type=Path)
    c.add_argument('--report', type=Path)
    a = ap.parse_args()
    ok = run(a) if a.cmd == 'run' else compare(a)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
