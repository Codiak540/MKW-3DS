#!/usr/bin/env python3
"""Pick, per vehicle part, the cheapest Old 3DS candidate that still looks right.

Each candidate (tools/cook_o3ds_vehicles.py sets plus the earlier refined set)
is rendered next to the original from behind, the rear quarter, the side and
the front (the textured software preview renderer). Two numbers per view:
  shape   pixels covered by one image but not the other / original coverage
  colour  mean colour difference where both cover, 0..1
A candidate is only considered when no large piece (>= 12% of the part)
shrank below 60% of its size (tools/o3ds_piece_guard.py): the earlier refined
set loses one on about a third of all vehicle parts (Bowser's exhaust block,
Mario's steering wheel).
A candidate passes a tier when every view is within that tier's limits (the
side view, a thin profile, gets a looser shape limit; opponents are mostly
seen from behind). Limits were calibrated on parts checked by eye: Mario's
Standard Kart (old set loses the steering wheel and M plate: rear colour
0.126; lean set loses the exhaust cluster: rear shape 0.151), Bowser's bike
(old set loses the exhaust block: rear-quarter shape 0.148). The
player's own vehicle (models/o3ds/) uses the strict tier, CPU opponents
(models/o3ds/rival/) the loose one. No passing candidate: the player keeps the
original (no sidecar); an opponent falls back to the player's choice.

Usage: select_o3ds_vehicles.py [--vehicles build/o3ds-vehicles] [--refined build/o3ds-models-refined]
       [--reselect]   (re-pick from the stored selection.json measurements)
Writes <vehicles>/selected/models/o3ds[/rival] and selection.json.
"""
import argparse
import json
import math
import os
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from o3ds_lowpoly_io import read_gmdl, read_variant  # noqa: E402
from o3ds_piece_guard import failing_pieces  # noqa: E402
from preview_o3ds_lowpoly import render  # noqa: E402

VIEWS = ((180, 0), (210, 20), (90, 0), (0, 0))   # yaw, pitch (degrees)
TIERS = {
    # name: (max shape, max colour) for each of VIEWS (rear, rear quarter, side, front)
    # The player's own vehicle is seen from behind all race: rear views strictest
    # (Mario's exhaust pipes read as one dark slab in game at rear colour 0.088).
    'player': ((0.06, 0.07), (0.08, 0.08), (0.15, 0.10), (0.12, 0.10)),
    'rival': ((0.13, 0.16), (0.13, 0.16), (0.26, 0.16), (0.18, 0.16)),
}
SIZE = 128


def views(model, roots, center, scale):
    cache = {}
    return [render(model, roots, math.radians(y), math.radians(p), SIZE, center, scale, cache, coverage=True)
            for y, p in VIEWS]


def compare(ref, cand):
    out = []
    for (ri, rc), (ci, cc) in zip(ref, cand):
        shape = float((rc ^ cc).sum()) / max(1.0, float(rc.sum()))
        both = rc & cc
        colour = float(np.abs(ri[both] - ci[both]).mean() / 255.0) if both.any() else 1.0
        out.append((round(shape, 4), round(colour, 4)))
    return out


def judge(job):
    name, source, candidates, textures = job
    try:
        orig = read_gmdl(source)
        P = orig.floats[:, 0:3].astype(np.float64)
        lo, hi = P.min(0), P.max(0)
        center, scale = (lo + hi) / 2, SIZE / max(1e-6, (hi - lo).max()) * 0.9
        ref = views(orig, textures, center, scale)
        result = {'triangles': len(orig.indices) // 3, 'candidates': {}}
        for label, path in candidates:
            g, remap, _ = read_variant(path)
            roots = [next(p.parent for p in Path(path).parents if p.name == 'models') / 'textures'] + textures
            metrics = compare(ref, views(g, roots, center, scale))
            whole = len(failing_pieces(orig, g, remap, 0.6, 0.12)) == 0
            result['candidates'][label] = {'triangles': len(g.indices) // 3, 'views': metrics, 'pieces': whole}
        return name, result
    except Exception as error:  # an unreadable part keeps its original
        return name, {'error': f'{type(error).__name__}: {error}'}


def passes(metrics, tier):
    return all(s <= shape and c <= colour for (s, c), (shape, colour) in zip(metrics, TIERS[tier]))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--vehicles', type=Path, default=ROOT / 'build' / 'o3ds-vehicles')
    ap.add_argument('--refined', type=Path, default=ROOT / 'build' / 'o3ds-models-refined')
    ap.add_argument('--textures', type=Path, default=ROOT / 'sd-card' / '3ds' / 'mkw-3ds' / 'textures')
    ap.add_argument('--jobs', type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument('--reselect', action='store_true', help='reuse the stored measurements')
    a = ap.parse_args()
    listing = json.loads((a.vehicles / 'sources' / 'listing-all.json').read_text())
    sets = [('refined', a.refined)] + [(n, a.vehicles / n) for n in ('lean', 'careful', 'fine')]
    jobs = []
    for rel, source in sorted(listing.items()):
        if not rel.endswith('.gmdl'):
            continue
        name = Path(rel).name
        cands = [(label, root / 'models' / 'o3ds' / name) for label, root in sets
                 if (root / 'models' / 'o3ds' / name).exists()]
        jobs.append((name, source, cands, [a.textures]))
    if a.reselect:
        results = json.loads((a.vehicles / 'selected' / 'selection.json').read_text())['models']
    else:
        with ProcessPoolExecutor(a.jobs) as pool:
            results = dict(pool.map(judge, jobs, chunksize=4))
    out = a.vehicles / 'selected'
    if out.exists():
        shutil.rmtree(out)
    (out / 'models' / 'o3ds' / 'rival').mkdir(parents=True)
    paths = {label: root for label, root in sets}
    totals = {'original': 0, 'player': 0, 'rival': 0}
    picks = {'player': {}, 'rival': {}}
    for name, r in sorted(results.items()):
        if 'error' in r:
            continue
        totals['original'] += r['triangles']
        order = sorted(r['candidates'].items(), key=lambda kv: kv[1]['triangles'])
        chosen = {}
        for tier in ('player', 'rival'):
            pick = next((label for label, c in order if c['pieces'] and passes(c['views'], tier)), None)
            chosen[tier] = pick
            picks[tier][pick or 'original'] = picks[tier].get(pick or 'original', 0) + 1
        r['player'], r['rival'] = chosen['player'], chosen['rival'] or chosen['player']
        if r['player']:
            shutil.copy2(paths[r['player']] / 'models' / 'o3ds' / name, out / 'models' / 'o3ds' / name)
        if r['rival'] and r['rival'] != r['player']:
            shutil.copy2(paths[r['rival']] / 'models' / 'o3ds' / name, out / 'models' / 'o3ds' / 'rival' / name)
        for tier in ('player', 'rival'):
            totals[tier] += r['candidates'][r[tier]]['triangles'] if r[tier] else r['triangles']
    (out / 'selection.json').write_text(json.dumps({'tiers': TIERS, 'views': VIEWS, 'totals': totals,
                                                    'picks': picks, 'models': results}, indent=1))
    print(json.dumps({'totals': totals, 'picks': picks}))
    errors = [n for n, r in results.items() if 'error' in r]
    if errors:
        print(f'{len(errors)} parts unreadable (keep originals), e.g. {errors[:3]}')


if __name__ == '__main__':
    main()
