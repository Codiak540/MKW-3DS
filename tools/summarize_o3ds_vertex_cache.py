#!/usr/bin/env python3
"""Compact per-category / per-course summary of an o3ds-vertex-cache-receipt.json
(tools/optimize_o3ds_vertex_cache.mjs), triangle-weighted ACMR at each FIFO size."""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = (('rival', lambda k: k.startswith('rival/')), ('track', lambda k: bool(re.match(r'course_\d+_000\.gmdl$', k))),
              ('course-object', lambda k: k.startswith(('obj', 'coconut_'))), ('menu-ui', lambda k: k.startswith(('ui_', 'award_'))),
              ('racer-kart-item', lambda k: True))


def category(name):
    return next(c for c, test in CATEGORIES if test(name))


def aggregate(items):
    t = sum(v['triangles'] for _, v in items)
    if not t:
        return None
    w = lambda key, i: sum(v[key][i] * v['triangles'] for _, v in items) / t
    before, after = [w('acmrBefore', i) for i in range(4)], [w('acmrAfter', i) for i in range(4)]
    return {'models': len(items), 'triangles': t, 'acmrBefore': [round(x, 4) for x in before],
            'acmrAfter': [round(x, 4) for x in after], 'transformsSavedPct': [round(100 * (1 - b / a), 1) if a else 0 for a, b in zip(before, after)],
            'acmrLowerBound': round(sum(v['acmrBound'] * v['triangles'] for _, v in items) / t, 4)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('receipt', type=Path)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    r = json.loads(a.receipt.read_text())
    items = list(r['models'].items())
    out = {'tool': r['tool'], 'select': r['select'], 'fifoSizes': r['sizes'], 'totals': aggregate(items),
           'kept': r['totals']['kept'], 'changedModels': r['totals']['changed'], 'conflictPairs': r['totals']['conflicts'],
           'byCategory': {c: aggregate([x for x in items if category(x[0]) == c]) for c, _ in CATEGORIES}}
    catalog = (ROOT / 'include' / 'mkw' / 'course_catalog.hpp').read_text()
    out['byCourse'] = {}
    for name, cid in re.findall(r'\{"([^"]+)",Kinoko::Course::\w+,"[^"]+","models/course_(\d+)\.list"', catalog):
        out['byCourse'][name] = {'track': aggregate([x for x in items if x[0] == f'course_{cid}_000.gmdl']),
                                 'objects': aggregate([x for x in items if x[0].startswith(f'obj{cid}_')])}
    gain = lambda v: (v['acmrBefore'][2] - v['acmrAfter'][2]) * v['triangles']
    out['topModels'] = {k: {f: v[f] for f in ('triangles', 'acmrBefore', 'acmrAfter', 'acmrBound', 'conflictPairs')}
                        for k, v in sorted(items, key=lambda x: -gain(x[1]))[:25]}
    a.out.write_text(json.dumps(out, indent=1))
    for c, v in out['byCategory'].items():
        if v:
            print(f"{c:16} {v['models']:5} models {v['triangles']:8} tris  ACMR {v['acmrBefore']} -> {v['acmrAfter']}  saved% {v['transformsSavedPct']}")


if __name__ == '__main__':
    main()
