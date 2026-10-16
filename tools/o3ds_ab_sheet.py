#!/usr/bin/env python3
"""Side-by-side A/B review sheets from two tools/o3ds_course_tour.py outputs:
for each course folder present in both, rows of OFF | ON | difference (changed
pixels in red over a dimmed OFF frame), and a per-frame count of changed
pixels. Usage: o3ds_ab_sheet.py OFF_DIR ON_DIR OUT_DIR [--frames N]"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('off', type=Path)
    ap.add_argument('on', type=Path)
    ap.add_argument('out', type=Path)
    ap.add_argument('--frames', type=int, default=6, help='frames per sheet (spread over the run)')
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    report = {}
    for course in sorted(p for p in a.off.iterdir() if p.is_dir() and (a.on / p.name).is_dir()):
        names = sorted(p.name for p in course.glob('*.png') if (a.on / course.name / p.name).exists())
        counts = {}
        for n in names:
            x = np.asarray(Image.open(course / n).convert('RGB'))
            y = np.asarray(Image.open(a.on / course.name / n).convert('RGB'))
            counts[n] = int((x != y).any(-1).sum()) if x.shape == y.shape else -1
        report[course.name] = counts
        if not names:
            continue
        pick = [names[i * (len(names) - 1) // max(1, a.frames - 1)] for i in range(min(a.frames, len(names)))]
        rows = []
        for n in dict.fromkeys(pick):
            x = np.asarray(Image.open(course / n).convert('RGB'))
            y = np.asarray(Image.open(a.on / course.name / n).convert('RGB'))
            d = (x // 3).copy()
            if x.shape == y.shape:
                d[(x != y).any(-1)] = (255, 0, 0)
            rows.append(np.concatenate([x, np.full((x.shape[0], 4, 3), 255, np.uint8), y,
                                        np.full((x.shape[0], 4, 3), 255, np.uint8), d], 1))
        sheet = np.concatenate([np.concatenate([r, np.full((4, r.shape[1], 3), 255, np.uint8)]) for r in rows])
        Image.fromarray(sheet).save(a.out / f'{course.name}.png')
    (a.out / 'differences.json').write_text(json.dumps(report, indent=1) + '\n')
    for c, counts in report.items():
        changed = {n: k for n, k in counts.items() if k}
        print(f'{c}: {len(counts)} frames, {len(changed)} differ' + (f' (max {max(changed.values())} px)' if changed else ''))


if __name__ == '__main__':
    main()
