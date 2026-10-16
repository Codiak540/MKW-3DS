#!/usr/bin/env python3
"""QA course tour: boot a (private) Azahar copy once per VS course, let the
menu autopilot pick it, and collect the course intro fly-over frames and a few
race frames for visual review.

Only the emulator process this script starts is ever stopped.
Usage: o3ds_course_tour.py EMULATOR_DIR OUT_DIR [--courses 0,5,8] [--race 900]
"""
import argparse
import shutil
import subprocess
import time
from pathlib import Path

from PIL import Image

COURSES = ["Luigi Circuit", "Moo Moo Meadows", "Mushroom Gorge", "Toad's Factory",
           "Mario Circuit", "Coconut Mall", "DK Summit", "Wario's Gold Mine",
           "Daisy Circuit", "Koopa Cape", "Maple Treeway", "Grumble Volcano",
           "Dry Dry Ruins", "Moonview Highway", "Bowser's Castle", "Rainbow Road",
           "GCN Peach Beach", "DS Yoshi Falls", "SNES Ghost Valley 2", "N64 Mario Raceway",
           "N64 Sherbet Land", "GBA Shy Guy Beach", "DS Delfino Square", "GCN Waluigi Stadium",
           "DS Desert Hills", "GBA Bowser Castle 3", "N64 DK's Jungle Parkway", "GCN Mario Circuit",
           "SNES Mario Circuit 3", "DS Peach Gardens", "GCN DK Mountain", "N64 Bowser's Castle"]


def script(index, race):
    cup, slot = divmod(index, 4)
    row, col = divmod(cup, 4)
    return ("w30 A w20 A w20 A w20 A w20 A w20 D D A w20 A\n"   # strap, intro, title, license, main, VS, solo
            "w20 A\n"                                          # character (cursor default)
            "w20 A w20 A\n"                                    # vehicle, drift
            f"w20 {'R ' * col}{'D ' * row}A w20 {'D ' * slot}A w20 A\n"
            f"auto:{race} S w20 D D A w20 U A w60 end\n")


def launch(emulator, sd):
    """Via the app bundle (running the inner binary shows a blocking dialog).
    The copy's qt-config.ini must point sdmc/nand at the copy itself."""
    config = (emulator / 'user' / 'config' / 'qt-config.ini').read_text()
    if f'sdmc_directory={emulator.resolve()}/user/sdmc/' not in config:
        raise SystemExit(f'{emulator}: sdmc_directory does not point at this copy')
    subprocess.run(['open', '-n', '-a', str((emulator / 'Azahar.app').resolve()), '--args',
                    str((sd / 'mkw-3ds.3dsx').resolve())], check=True)


def stop(emulator):
    """Stop only Azahar instances running from this copy."""
    marker = str((emulator / 'Azahar.app').resolve())
    out = subprocess.run(['pgrep', '-f', marker], capture_output=True, text=True).stdout.split()
    for pid in out:
        subprocess.run(['kill', '-9', pid])
    time.sleep(2)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('emulator', type=Path)
    ap.add_argument('out', type=Path)
    ap.add_argument('--courses', default=','.join(str(i) for i in range(32)))
    ap.add_argument('--race', type=int, default=900)
    ap.add_argument('--every', type=int, default=150, help='race capture interval (simulation steps)')
    ap.add_argument('--timeout', type=int, default=240)
    a = ap.parse_args()
    sd = a.emulator / 'user' / 'sdmc' / '3ds' / 'mkw-3ds'
    want_race = a.race // a.every
    for index in (int(x) for x in a.courses.split(',')):
        dest = a.out / f'{index:02d}-{COURSES[index].replace(" ", "_").replace("/", "")}'
        dest.mkdir(parents=True, exist_ok=True)
        for p in list(sd.glob('race-*.ppm')) + list(sd.glob('intro-*.ppm')) + list(sd.glob('*-result.json')):
            p.unlink()
        (sd / 'menu-autoplay.txt').write_text(script(index, a.race))
        (sd / 'race-capture.txt').write_text(f'{a.every}\n')
        start = time.time()
        launch(a.emulator, sd)
        try:
            while time.time() - start < a.timeout:
                time.sleep(5)
                races = [p for p in sd.glob('race-*.ppm') if 'bottom' not in p.name]
                if len(races) >= want_race:
                    time.sleep(3)
                    break
        finally:
            stop(a.emulator)
        shots = sorted(sd.glob('intro-*.ppm')) + sorted(p for p in sd.glob('race-*.ppm') if 'bottom' not in p.name)
        for p in shots:
            Image.open(p).convert('RGB').save(dest / (p.stem + '.png'))
        for p in ('runtime.log', 'performance.csv'):
            if (sd / p).exists():
                shutil.copy2(sd / p, dest / p)
        print(f'{index:02d} {COURSES[index]}: {len(shots)} frames in {time.time() - start:.0f}s', flush=True)
    for p in ('menu-autoplay.txt', 'race-capture.txt'):
        (sd / p).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
