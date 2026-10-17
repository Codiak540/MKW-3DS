#!/usr/bin/env python3
"""Record pgo/race-profile.tar.gz: branch and call counts from a scripted race.

1. Builds the instrumented QA 3DSX (MKW_PGO=generate tools/build.py --external-assets).
2. Creates the build/obj directory tree under the worktree emulator's SD
   (sdmc:/3ds/mkw-3ds/gcov): libgcov writes there but cannot create folders.
3. Runs the scripted Old 3DS race (tools/o3ds_perf_run.py: VS, Luigi Circuit,
   11 CPU racers, layout pinned to the main core). The menu autopilot dumps
   the counts when it finishes.
4. Packs every .gcda into pgo/race-profile.tar.gz (sorted, no timestamps).

Afterwards an ordinary `tools/build.py` uses the new profile. Counts only
steer code layout and inlining; results are unchanged.
"""
import io, shutil, subprocess, sys, tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# In the main checkout build/azahar-o3ds is the user's own play copy; QA runs use the automation copy.
SD = ROOT / 'build/azahar-o3ds-auto/user/sdmc/3ds/mkw-3ds'
GCOV = SD / 'gcov'


def main():
    env = dict(**__import__('os').environ, MKW_PGO='generate')
    subprocess.run([sys.executable, str(ROOT / 'tools/build.py'), '--external-assets'], check=True, env=env)
    shutil.rmtree(GCOV, ignore_errors=True)
    for d in (ROOT / 'build/obj').rglob('*'):
        if d.is_dir():
            (GCOV / 'build' / d.relative_to(ROOT / 'build')).mkdir(parents=True, exist_ok=True)
    (GCOV / 'build/obj').mkdir(parents=True, exist_ok=True)
    instrumented = ROOT / 'build/pgo-generate.3dsx'
    shutil.copyfile(ROOT / 'build/mkw-3ds.3dsx', instrumented)
    subprocess.run([sys.executable, str(ROOT / 'tools/o3ds_perf_run.py'), '--mode', 'perf', '--label', 'pgo-generate',
                    '--binary', str(instrumented), '--cpu-log', '0', '--capture', '0',
                    '--extra', 'o3ds-main-only.txt', '--timeout', '1800'], check=True)
    files = sorted(GCOV.joinpath('build').rglob('*.gcda'))
    if not files:
        raise SystemExit('no .gcda counts were written')
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz', format=tarfile.USTAR_FORMAT) as bundle:
        for f in files:
            info = tarfile.TarInfo(f.relative_to(GCOV / 'build').as_posix())
            info.size = f.stat().st_size
            info.mtime = 0
            bundle.addfile(info, io.BytesIO(f.read_bytes()))
    out = ROOT / 'pgo/race-profile.tar.gz'
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(buffer.getvalue())
    shutil.rmtree(GCOV, ignore_errors=True)
    instrumented.unlink(missing_ok=True)
    print(f'{len(files)} profiles -> {out.relative_to(ROOT)} ({out.stat().st_size} bytes)')


if __name__ == '__main__':
    main()
