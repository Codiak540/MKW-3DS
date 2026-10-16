#!/usr/bin/env python3
"""Build the host determinism harness (build/sim-determinism/harness).

Compiles Kinoko (vendor/kinoko, the worktree copy with its patches), the app's
race logic (cpu_ai, race_grid, race_items, item_native, item_world) and
tools/sim_determinism/harness.cpp with clang++ at -O2, no fast-math, no FMA
contraction. Incremental by mtime; `--clean` rebuilds.
"""
import concurrent.futures, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KIN = ROOT / 'vendor/kinoko'
OUT = ROOT / 'build/sim-determinism'
CXX = os.environ.get('CXX', 'clang++')
FLAGS = ['-std=gnu++23', '-O2', '-g', '-ffp-contract=off', '-fno-fast-math', '-fno-exceptions', '-fno-rtti',
         '-fcheck-new', '-fno-strict-aliasing', '-DREVOLUTION', '-DMKW_SIM_HARNESS',          '-Wall', '-Wno-unused-private-field', '-Wno-unknown-warning-option', '-Wno-mismatched-tags', '-Wno-ignored-attributes', '-Wno-unused-but-set-variable',
         '-I' + str(KIN / 'include'), '-I' + str(KIN / 'source'), '-I' + str(ROOT / 'include')]
APP = ['cpu_ai.cpp', 'race_items.cpp', 'item_native.cpp', 'item_world.cpp', 'race_grid.cpp', 'shell_guidance.cpp']


def sources():
    kin = [p for p in sorted(KIN.glob('source/**/*.cc'))
           if not p.name.startswith('._') and (p.parent.name != 'host' or p.name == 'SceneCreatorDynamic.cc')]
    return kin + [ROOT / 'src' / a for a in APP] + [ROOT / 'tools/sim_determinism/harness.cpp']


def main():
    clean = '--clean' in sys.argv
    jobs = int(os.environ.get('JOBS', os.cpu_count() or 4))
    srcs = sources()
    headers = list(KIN.rglob('*.hh')) + list(KIN.rglob('*.inc')) + list((ROOT / 'include').rglob('*.hpp'))
    newest_header = max(h.stat().st_mtime for h in headers)

    def compile(src):
        obj = OUT / 'obj' / src.relative_to(ROOT).with_suffix('.o')
        obj.parent.mkdir(parents=True, exist_ok=True)
        if not clean and obj.exists() and obj.stat().st_mtime > max(src.stat().st_mtime, newest_header):
            return obj, None
        r = subprocess.run([CXX, *FLAGS, '-c', str(src), '-o', str(obj)], capture_output=True, text=True)
        return obj, (src, r.returncode, r.stdout + r.stderr)

    OUT.mkdir(parents=True, exist_ok=True)
    failed = False
    objs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for obj, result in pool.map(compile, srcs):
            objs.append(obj)
            if result:
                src, code, text = result
                if code:
                    failed = True
                    print(f'FAILED {src.relative_to(ROOT)}\n{text[-4000:]}')
                elif text.strip():
                    print(text[-2000:])
    if failed:
        sys.exit(1)
    exe = OUT / 'harness'
    r = subprocess.run([CXX, '-o', str(exe), *map(str, objs)], capture_output=True, text=True)
    if r.returncode:
        print(r.stdout + r.stderr)
        sys.exit(1)
    print(exe.relative_to(ROOT))


if __name__ == '__main__':
    main()
