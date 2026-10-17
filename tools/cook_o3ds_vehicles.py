#!/usr/bin/env python3
"""Old 3DS vehicles: candidate cooks for tools/select_o3ds_vehicles.py.

Three candidate sets, each run with the piece guard until no guarded piece
vanishes or shrinks:
  fine     bodies/handles, error 0.02, every piece >= 85% of its size
  careful  every part, error 0.04, every piece >= 85% of its size
  lean     every part, error 0.06, large pieces (>= 12% of the model) >= 60%
The selector then renders each part and picks, per part, the cheapest candidate
that still looks like the original: strictly for the player's own vehicle
(models/o3ds/), loosely for CPU opponents (models/o3ds/rival/, loaded first by
src/racer_visuals.cpp).

Originals are read as the runtime sees them (menu.pak/race.pak win over loose
files). Usage: cook_o3ds_vehicles.py [--out build/o3ds-vehicles]
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from cook_o3ds_textures import Assets  # noqa: E402

PART = re.compile(r'^models/[a-z0-9_]+_(kart|bike)_(body|handle|tire_[fr][lr]|[fr]_sus|sus_fr|arm_[fr][lr]|swingarm)'
                  r'\.(gmdl|dskm)$')
SETS = {
    # name: (listing, ratio, max error, protect features, guard min extent, guard min piece)
    'fine': ('careful', 0.5, 0.02, 'true', 0.85, 0.0),
    'careful': ('all', 0.4, 0.04, 'true', 0.85, 0.0),
    'lean': ('all', 0.3, 0.06, 'false', 0.6, 0.12),
}
CAREFUL = re.compile(r'_(body|handle)\.(gmdl|dskm)$')


def extract(originals, dest):
    assets = Assets(originals.parent)
    names = set(assets.entries) | {f'models/{p.name}' for p in originals.iterdir()}
    listing = {}
    (dest / 'models').mkdir(parents=True, exist_ok=True)
    for rel in sorted(n for n in names if PART.match(n)):
        out = dest / rel
        out.write_bytes(assets.read(rel))
        listing[rel] = str(out.resolve())
    return listing


def cook(listing_path, out, ratio, max_error, protect, min_extent, min_piece, rounds=6):
    locks = out.parent / f'{out.name}-locks.json'
    locks.write_text(json.dumps({'version': 1, 'locks': {}}))
    for attempt in range(rounds):
        if out.exists():
            shutil.rmtree(out)
        subprocess.run(['node', str(ROOT / 'tools' / 'cook_o3ds_geometry.mjs'), '--listing', str(listing_path),
                        '--output', str(out), '--profile', 'aggressive', '--ratio', str(ratio),
                        '--max-error', str(max_error), '--protect-features', protect, '--road-locks', str(locks)],
                       check=True, stdout=subprocess.DEVNULL)
        guard = subprocess.run([sys.executable, str(ROOT / 'tools' / 'o3ds_piece_guard.py'), str(listing_path),
                                str(out / 'models' / 'o3ds'), str(locks), '--min-extent', str(min_extent),
                                '--min-piece', str(min_piece)], capture_output=True, text=True)
        print(f'{out.name} round {attempt + 1}: {guard.stdout.strip().splitlines()[-1]}', flush=True)
        if guard.returncode == 0:
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--originals', type=Path, default=ROOT / 'sd-card' / '3ds' / 'mkw-3ds' / 'models')
    ap.add_argument('--out', type=Path, default=ROOT / 'build' / 'o3ds-vehicles')
    ap.add_argument('--sets', default=','.join(SETS))
    a = ap.parse_args()
    listing = extract(a.originals, a.out / 'sources')
    listings = {'all': listing, 'careful': {k: v for k, v in listing.items() if CAREFUL.search(k)}}
    paths = {}
    for name, entries in listings.items():
        paths[name] = a.out / 'sources' / f'listing-{name}.json'
        paths[name].write_text(json.dumps(entries, indent=1))
    print(f'{sum(n.endswith(".gmdl") for n in listing)} vehicle parts', flush=True)
    ok = True
    for name in a.sets.split(','):
        scope, *params = SETS[name]
        ok &= cook(paths[scope], a.out / name, *params)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
