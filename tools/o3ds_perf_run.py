#!/usr/bin/env python3
"""QA helper: one scripted Azahar run of a QA 3DSX, outputs archived.

Modes:
  perf    menu autoplay into a CPU-driven VS race (Mario, Luigi Circuit,
          auto:N), lockstep race captures, per-frame performance.csv and the
          CPU AI log (cpu-log.txt) for simulation comparisons.
  replay  the physics replay test (replay-test.txt + lc.rkg): two rendered
          ghost finishes must match the ghost's race timer and lap splits.

Only the emulator process this script starts is terminated. The emulator
copy and its SD folder are the automation copy (build/azahar-o3ds-auto, or --emu).
"""
import argparse, json, shutil, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# In the main checkout build/azahar-o3ds is the user's own play copy; QA runs use the automation copy.
EMU = ROOT / 'build/azahar-o3ds-auto'
SD = EMU / 'user/sdmc/3ds/mkw-3ds'
APP = EMU / 'Azahar.app/Contents/MacOS/azahar'
GHOST = ROOT / 'vendor/kinoko/samples/lc-rta-1-08-733.rkg'
MARKERS = ('menu-autoplay.txt', 'performance.txt', 'race-capture.txt', 'cpu-log.txt', 'replay-test.txt',
           'menu-performance.txt')
OUTPUTS = ('performance.csv', 'runtime.log', 'menu-autoplay-result.json', 'replay-result.json',
           'menu-performance.csv', 'sim-hash.bin', 'sim-kinoko.json')
SCRIPT = ('w30 A w20 A w20 A w20 A w20 A w20 D D A w20 A\nw20 R R L L D U A\nw20 D D U U A w20 A\n'
          '{course}\nauto:{n} S w20 D D A w20 U A w60 end\n')


def course_line(index):
    """Cup/course menu moves for a VS course index (cup order, as o3ds_course_tour.py)."""
    if index == 0:
        return 'w20 R L A w20 D U A w20 A'
    cup, slot = divmod(index, 4)
    row, col = divmod(cup, 4)
    return f"w20 {'R ' * col}{'D ' * row}A w20 {'D ' * slot}A w20 A"


def use_emulator(path):
    """Selects the emulator copy (and its SD folder) this run uses."""
    global EMU, SD, APP
    EMU = Path(path).resolve()
    SD = EMU / 'user/sdmc/3ds/mkw-3ds'
    APP = EMU / 'Azahar.app/Contents/MacOS/azahar'
    config = (EMU / 'user/config/qt-config.ini').read_text()
    if f'sdmc_directory={EMU}/user/sdmc/' not in config:
        raise SystemExit(f'{EMU}: sdmc_directory does not point at this copy')


def own_pids():
    """Emulator processes started from this worktree's copy (matched by full app path)."""
    r = subprocess.run(['pgrep', '-f', str(APP)], capture_output=True, text=True)
    return [int(x) for x in r.stdout.split()]


def stop(pids):
    for sig in ('-TERM', '-KILL'):
        if not pids:
            return
        subprocess.run(['kill', sig, *map(str, pids)], capture_output=True)
        for _ in range(15):
            pids = [p for p in pids if subprocess.run(['kill', '-0', str(p)], capture_output=True).returncode == 0]
            if not pids:
                return
            time.sleep(1)


def clean():
    for name in OUTPUTS:
        (SD / name).unlink(missing_ok=True)
    for pattern in ('race-*.ppm', 'capture-*.ppm', 'intro-*.ppm'):
        for p in SD.glob(pattern):
            p.unlink()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--mode', choices=('perf', 'replay'), required=True)
    p.add_argument('--label', required=True)
    p.add_argument('--binary', default=str(ROOT / 'build/mkw-3ds.3dsx'))
    p.add_argument('--frames', type=int, default=1500, help='auto:N simulation frames (perf)')
    p.add_argument('--course', type=int, default=0, help='VS course index in cup order (perf; 0 = Luigi Circuit)')
    p.add_argument('--emu', '--emulator', dest='emu', default=str(EMU),
                   help='private Azahar copy (its qt-config.ini must point at itself)')
    p.add_argument('--default-script', action='store_true',
                   help="the autopilot's built-in script: Luigi Circuit (kart), quit, Coconut Mall (bike)")
    p.add_argument('--capture', type=int, default=60, help='race capture interval in steps (perf, 0 = off)')
    p.add_argument('--cpu-log', type=int, default=30, help='CPU log interval in steps (perf, 0 = off)')
    p.add_argument('--extra', action='append', default=[], help='additional marker file NAME[=CONTENT]')
    p.add_argument('--timeout', type=float, default=900)
    p.add_argument('--no-profile', action='store_true', help='run without performance.txt (release-like timing paths)')
    a = p.parse_args()
    use_emulator(a.emu)
    # Before touching the SD folder: a run already in progress owns it (its
    # markers, outputs and captures would otherwise be removed under it).
    if own_pids():
        raise SystemExit('this worktree\'s emulator is already running')
    out = ROOT / 'build/runs' / a.label
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in MARKERS:
        (SD / name).unlink(missing_ok=True)
    clean()
    shutil.copyfile(a.binary, SD / 'mkw-3ds.3dsx')
    if not a.no_profile:
        (SD / 'performance.txt').write_text('1\n')
    if a.mode == 'perf':
        (SD / 'menu-autoplay.txt').write_text('1\n' if a.default_script else SCRIPT.format(n=a.frames, course=course_line(a.course)))
        if a.capture:
            (SD / 'race-capture.txt').write_text(f'{a.capture}\n')
        if a.cpu_log:
            (SD / 'cpu-log.txt').write_text(f'{a.cpu_log}\n')
        done = SD / 'menu-autoplay-result.json'
    else:
        (SD / 'replay-test.txt').write_text('1\n')
        shutil.copyfile(GHOST, SD / 'lc.rkg')
        done = SD / 'replay-result.json'
    extras = []
    for item in a.extra:
        name, _, content = item.partition('=')
        (SD / name).write_text((content or '1') + '\n')
        extras.append(name)
    start = time.time()
    # Launched through the bundle: running the executable directly shows a
    # modal "run via Azahar.app" warning before the game boots.
    subprocess.run(['open', '-n', '-g', '-a', str(EMU / 'Azahar.app'), '--args', str(SD / 'mkw-3ds.3dsx')], check=True)
    pids = []
    for _ in range(30):
        pids = own_pids()
        if pids:
            break
        time.sleep(1)
    status = 'timeout' if pids else 'no process'
    try:
        while pids and time.time() - start < a.timeout:
            if not own_pids():
                status = 'exited'
                break
            if done.exists() and done.stat().st_size > 0:
                time.sleep(3)  # let the profiler's final flush land
                status = 'done'
                break
            time.sleep(2)
    finally:
        stop(own_pids())
    elapsed = time.time() - start
    for name in OUTPUTS:
        if (SD / name).exists():
            shutil.copyfile(SD / name, out / name)
    captures = sorted(SD.glob('race-*.ppm'))
    if captures:
        (out / 'captures').mkdir()
        for c in captures:
            shutil.move(str(c), out / 'captures' / c.name)
    for name in extras:
        (SD / name).unlink(missing_ok=True)
    clean()
    summary = {'label': a.label, 'mode': a.mode, 'status': status, 'host_seconds': round(elapsed, 1),
               'binary': a.binary, 'extra_markers': extras}
    for name in ('menu-autoplay-result.json', 'replay-result.json'):
        if (out / name).exists():
            summary[name] = json.loads((out / name).read_text())
    (out / 'run.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))
    return 0 if status == 'done' else 1


if __name__ == '__main__':
    sys.exit(main())
