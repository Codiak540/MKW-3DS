#!/usr/bin/env python3
"""Draw-call batching audit for Old 3DS course rendering. Read-only.

For each course's track models (course_NN.list) and course objects (objNN_*),
as the Old 3DS runtime loads them (models/o3ds sidecar spans when present):

  draws      non-empty submeshes = draw calls per instance when nothing is culled
  adjacent   runs of consecutive submeshes whose whole material record (every
             byte but first/count) is identical: Renderer::drawNow already skips
             their state setup (sameMaterial) but still issues one
             C3D_DrawElements per submesh. Merging such a run is pixel-exact
             (same state, same triangle order) and keeps block culling
             (visibility blocks are built per index range).
  distinct   submeshes that share a material record with a non-adjacent one:
             merging them needs a reorder, exact only for opaque, depth-writing
             materials (and only where the reorder cannot change ties).

Sidecar spans must stay 1:1 with the original submeshes (readModelVariant), so
merges cannot be expressed in O3RM data; see docs/o3ds-asset-gpu.md.
Optionally summarises draw counts from emulator performance.csv captures.
"""
import argparse
import csv
import json
import re
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from cook_o3ds_textures import Assets  # noqa: E402

RECORD = {3: 44, 4: 56, 5: 92}
COURSE = re.compile(r'\{"([^"]+)",Kinoko::Course::\w+,"[^"]+","(models/course_(\d+)\.list)"')


def spans(data):
    version, nv, ni, ns, nt = struct.unpack_from('<5I', data, 4)
    size = RECORD[version]
    out = []
    for i in range(ns):
        raw = data[24 + i * size:24 + (i + 1) * size]
        first, count = struct.unpack_from('<II', raw)
        out.append((count, raw[8:], struct.unpack_from('<I', raw, 12)[0]))
    return out


def analyse(data):
    s = [x for x in spans(data) if x[0]]
    runs, merged = 0, 0
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1][1] == s[i][1]:
            j += 1
        if j > i:
            runs += 1
            merged += j - i
        i = j + 1
    keys = Counter(x[1] for x in s)
    shared = sum(n - 1 for n in keys.values() if n > 1)
    opaque = sum(n - 1 for k, n in keys.items() if n > 1 and not (struct.unpack_from('<I', k, 4)[0] & 2))
    return {'draws': len(s), 'adjacentRuns': runs, 'adjacentSaved': merged, 'sharedMaterialSaved': shared,
            'sharedOpaqueSaved': opaque, 'materials': len(keys), 'triangles': sum(x[0] for x in s) // 3}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--assets', type=Path, default=ROOT / 'sd-card' / '3ds' / 'mkw-3ds')
    ap.add_argument('--package', type=Path, help='low-poly package (models/o3ds sidecars win, as on Old 3DS)')
    ap.add_argument('--runs', type=Path, nargs='*', default=[], help='emulator run folders with performance.csv')
    ap.add_argument('--receipt', type=Path)
    a = ap.parse_args()
    assets = Assets(a.assets)
    names = {n[7:-5] for n in assets.entries if n.startswith('models/') and n.endswith('.gmdl')}
    names |= {p.stem for p in (a.assets / 'models').glob('*.gmdl')}

    def load(stem):
        side = a.package / 'models' / 'o3ds' / f'{stem}.gmdl' if a.package else None
        if side and side.exists():
            return side.read_bytes(), 'o3ds'
        rel = f'models/{stem}.gmdl'
        return (assets.read(rel), 'original') if stem in names else (None, None)

    report = {'courses': {}}
    text = (ROOT / 'include' / 'mkw' / 'course_catalog.hpp').read_text()
    for course, track_list, cid in COURSE.findall(text):
        track = assets.read(track_list).decode().split() if track_list in assets.entries or (a.assets / track_list).exists() else []
        objects = sorted(n for n in names if n.startswith(f'obj{cid}_') and not n.endswith('_shadow_000'))
        entry = {}
        for group, models in (('track', track), ('objects', objects)):
            total = Counter()
            per = {}
            for m in models:
                data, source = load(m)
                if data is None:
                    continue
                r = analyse(data)
                r['source'] = source
                per[m] = r
                total.update({k: v for k, v in r.items() if isinstance(v, int)})
            entry[group] = {'models': len(per), **dict(total), 'perModel': per}
        report['courses'][course] = entry
    runs = {}
    for run in a.runs:
        path = run / 'performance.csv'
        if not path.exists():
            continue
        rows = list(csv.DictReader(path.open()))
        by = defaultdict(list)
        for r in rows:
            if r.get('rendered') == '1' and r.get('draws'):
                by[r['course']].append(r)
        runs[run.name] = {c: {'frames': len(v), 'drawsMean': round(sum(int(x['draws']) for x in v) / len(v), 1),
                              'drawsMax': max(int(x['draws']) for x in v),
                              'trianglesMean': round(sum(int(x['triangles']) for x in v) / len(v), 1),
                              'culledSubmeshesMean': round(sum(int(x['culled_submeshes']) for x in v) / len(v), 1),
                              'drawElementsCallsMean': round(sum(float(x.get('draw_elements_calls') or 0) for x in v) / len(v), 1),
                              'partCullCallsMean': round(sum(float(x.get('part_cull_calls') or 0) for x in v) / len(v), 1)}
                          for c, v in by.items()}
    report['emulatorDraws'] = runs
    print(f"{'course':24} {'trackDraws':>10} {'adjSaved':>8} {'shared':>6} | {'objModels':>9} {'objDraws':>8} {'adjSaved':>8} {'shared':>6}")
    for c, e in report['courses'].items():
        t, o = e['track'], e['objects']
        print(f"{c:24} {t.get('draws',0):10} {t.get('adjacentSaved',0):8} {t.get('sharedMaterialSaved',0):6} | {o['models']:9} {o.get('draws',0):8} {o.get('adjacentSaved',0):8} {o.get('sharedMaterialSaved',0):6}")
    if runs:
        print(json.dumps(runs, indent=1))
    if a.receipt:
        a.receipt.write_text(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
