#!/usr/bin/env python3
"""Old 3DS overlay: cook build/o3ds-lowpoly-package from this builder's own disc.

The CIA builder's last cook step (tools/release/build_cia.py); then
tools/package_sd.py ships the package. Reads only the converted Mario Kart
Wii data (sd-card/3ds/mkw-3ds, through cook_o3ds_textures.Assets as the game
reads it: menu.pak/race.pak entries win over loose files; the course KCLs in
assets/) and runs this repository's tools in the order docs/o3ds-lowpoly.md
gives. No Mario Kart DS or Mario Kart 7 data is read.

Stages, each stamped in --work. A stage is skipped while its stamp records
the same inputs (sizes and times of the cooked assets), tool sources and
settings and its outputs exist; once one stage runs, every later one runs.
  level-sources  names of every course part and placed course object; their
                 models, animation banks and visuals lists copied out of the
                 paks (level-sources/models, level-listing.json)
  levels         KCL road locks + topology locks, the piece guard run to
                 convergence against the relative-error cook, then the level
                 cook at absolute error 600 (levels/); course skies (left out
                 by the stage) and EXCLUDED_LEVELS keep their authored meshes
  landmarks      statues, creatures, vehicles, trees (tools/o3ds/landmarks.txt)
                 with the character cook (landmarks/)
  drivers        the CPU opponents' driver cook; the player's own drivers keep
                 their original models (the stage names them from the cooked
                 assets, so no main driver cook runs)
  refined        everything else (items, menus) from the packaged models,
                 with verified character head regions (refined/)
  vehicles       three guarded candidate sets, then the render-checked pick
  texture-mips   course texture mip chains (texmips/textures/o3ds/mips.pak)
  stage          tools/stage_o3ds_lowpoly.py --vertex-cache into --package
  visibility     track and object visibility from the staged package, read
                 through a links folder (staged/models, o3ds -> package)
  final          the stage again: visibility files whose geometry fingerprint
                 matches the staged overlay are folded in

Usage: cook_o3ds_overlay.py [--work build/o3ds-overlay]
                            [--package build/o3ds-lowpoly-package] [--jobs N] [--force]
Logs: <work>/logs/<stage>.log.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from cook_o3ds_textures import Assets  # noqa: E402

# Coconut Mall's main mesh smears shop-front texture across the outdoor arch
# when reduced, for 7% fewer triangles (docs/o3ds-lowpoly.md): it stays authored.
EXCLUDED_LEVELS = ('course_05_000',)
LEVEL_ERROR = '600'        # level cook bound, course units
GUARD_ROUNDS = 8           # piece guard: cook again until no piece is lost
LANDMARK_RATIO = '0.4'
LANDMARKS = ROOT / 'tools' / 'o3ds' / 'landmarks.txt'

# The tools each stage runs (as programs, so tools/release/build_cia.py reads
# this table into the cook step's fingerprint) and the sources they depend on.
COOK_SOURCES = {
    'level-sources': ('tools/cook_o3ds_textures.py',),
    'levels': ('tools/o3ds_course_roads.py', 'tools/o3ds_kcl.py', 'tools/o3ds_lowpoly_io.py',
               'tools/cook_o3ds_lowpoly_characters.py', 'tools/o3ds_level_topology_locks.py',
               'tools/o3ds_free_borders.py', 'tools/cook_o3ds_geometry.mjs', 'tools/o3ds_piece_guard.py',
               'package-lock.json'),
    'landmarks': ('tools/cook_o3ds_lowpoly_characters.py', 'tools/o3ds_lowpoly_io.py', 'tools/o3ds/landmarks.txt'),
    'drivers': ('tools/cook_o3ds_lowpoly_characters.py', 'tools/o3ds_lowpoly_io.py'),
    'refined': ('tools/package_sd.py', 'tools/build_o3ds_character_regions.py', 'tools/export_driver.ts',
                'tools/cook_o3ds_geometry.mjs', 'package-lock.json'),
    'vehicles': ('tools/cook_o3ds_vehicles.py', 'tools/select_o3ds_vehicles.py', 'tools/o3ds_piece_guard.py',
                 'tools/preview_o3ds_lowpoly.py', 'tools/cook_o3ds_geometry.mjs', 'tools/o3ds_lowpoly_io.py'),
    'texture-mips': ('tools/cook_o3ds_course_textures.py', 'tools/audit_o3ds_textures.py'),
    'stage': ('tools/stage_o3ds_lowpoly.py', 'tools/check_o3ds_seams.py', 'tools/validate_model_variants.cpp',
              'include/mkw/model_variant.hpp', 'tools/optimize_o3ds_vertex_cache.mjs', 'tools/o3ds_gmdl.mjs',
              'tools/validate_texture_mips.cpp', 'include/mkw/texture_mips.hpp', 'tools/host_cxx.py'),
    'visibility': ('tools/build_o3ds_pvs.py', 'tools/build_o3ds_object_pvs.py', 'tools/pvs/gmdl.py',
                   'tools/pvs/pvs_raster.cpp', 'tools/o3ds_kcl.py', 'tools/host_cxx.py'),
    'final': ('tools/stage_o3ds_lowpoly.py',),
}


class Cook:
    def __init__(self, a):
        self.a = a
        self.sd, self.sources, self.work, self.package = a.assets, a.sources, a.work, a.package
        self.jobs = str(a.jobs)
        self.assets = Assets(self.sd)
        self.listing = package_listing(self.package)
        self.inputs = input_digest(self)
        self.settings = dict(assets=str(self.sd.resolve()), sources=str(self.sources.resolve()),
                             package=str(self.package.resolve()), excluded=EXCLUDED_LEVELS,
                             levelError=LEVEL_ERROR, guardRounds=GUARD_ROUNDS, landmarkRatio=LANDMARK_RATIO)

    def path(self, *parts):
        return self.work.joinpath(*parts)

    def run(self, stage, cmd, ok=(0,)):
        log = self.path('logs', f'{stage}.log')
        cmd = [str(c) for c in cmd]
        with log.open('a') as stream:
            stream.write('$ ' + ' '.join(cmd) + '\n')
            stream.flush()
            r = subprocess.run(cmd, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        if r.returncode not in ok:
            tail = log.read_text(errors='replace').splitlines()[-15:]
            raise SystemExit('\n'.join(tail) + f'\n{Path(cmd[1]).name} failed (exit {r.returncode}); log: {log}')
        return r.returncode

    def fresh(self, *parts):
        d = self.path(*parts)
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        return d


def package_listing(package):
    """The packaged file list (tools/package_sd.py --list): the models the game
    loads, with the cook steps' overlays applied, minus any Old 3DS overlay."""
    def listing():
        return subprocess.run([sys.executable, str(ROOT / 'tools' / 'package_sd.py'), '--list'], cwd=ROOT,
                              capture_output=True, text=True)
    r = listing()
    shipped = ROOT / 'build' / 'o3ds-lowpoly-package'
    if r.returncode and 'Old 3DS overlay changed since staging' in r.stderr and package == shipped.resolve():
        # An interrupted earlier cook of this same package: it is rebuilt anyway.
        shutil.rmtree(shipped)
        r = listing()
    if r.returncode:
        raise SystemExit(r.stderr.strip()[-2000:] + '\ntools/package_sd.py --list failed')
    return {rel: src for rel, src in json.loads(r.stdout).items()
            if re.fullmatch(r'models/[^/]+|textures/[^/]+|[^/]+\.pak', rel)}


def input_digest(c):
    """Sizes and modification times of everything the stages read."""
    files = set(c.listing.values()) | {str(p) for p in (c.sd / 'models').iterdir()}
    files |= {str(p) for p in (c.sd / 'textures').iterdir()} | {str(p) for p in c.sources.rglob('*.kcl')}
    digest = hashlib.sha256()
    for f in sorted(files):
        st = os.stat(f)
        digest.update(f'{f}\0{st.st_size}\0{st.st_mtime_ns}\n'.encode())
    return digest.hexdigest()


# ---- stages ------------------------------------------------------------------

def level_sources(c):
    """Every course list part (course_NN.list, course_NN_sky.list) and course
    visual object (course_NN_visuals.list), as the game loads them."""
    models = c.sd / 'models'
    names = set()
    visuals = sorted(models.glob('course_*_visuals.list'))
    for p in sorted(models.glob('course_*.list')):
        if re.fullmatch(r'course_\d+(_sky)?\.list', p.name):
            names.update(p.read_text().split())
    for p in visuals:
        names.update(f[2] for f in map(str.split, p.read_text().splitlines()) if len(f) == 4)
    names = sorted(n + '.gmdl' for n in names)
    c.path('level-names.txt').write_text('\n'.join(names) + '\n')
    out = c.fresh('level-sources', 'models')

    def source(rel):
        if rel in c.assets.entries:  # packed: copied out (the pak wins at runtime)
            (out / Path(rel).name).write_bytes(c.assets.read(rel))
            return str((out / Path(rel).name).resolve())
        return str((c.sd / rel).resolve()) if (c.sd / rel).is_file() else None

    required = ['models/' + n for n in names]
    for p in visuals:
        required += [f'models/{f[3]}.dobj' for f in map(str.split, p.read_text().splitlines())
                     if len(f) == 4 and f[3] != '-']
    listing = {rel: source(rel) for rel in required}
    missing = [rel for rel, src in listing.items() if not src]
    listing.update({'models/' + p.name: str(p.resolve()) for p in visuals})
    for n in names:  # animation banks: <stem>.dskm/.dobj, also without _000
        for base in (n[:-5], re.sub(r'_000$', '', n[:-5])):
            for ext in ('.dskm', '.dobj'):
                bank = source(f'models/{base}{ext}')
                if bank:
                    listing[f'models/{base}{ext}'] = bank
    if missing:
        raise SystemExit(f'level sources missing from the cooked assets: {missing[:10]}')
    c.path('level-listing.json').write_text(json.dumps(dict(sorted(listing.items())), indent=1))
    print(f'  {len(names)} level models, {len(listing)} listed files')


def levels(c):
    listing, roads, locks = c.path('level-listing.json'), c.path('course-roads.json'), c.path('level-locks.json')
    c.run('levels', [sys.executable, 'tools/o3ds_course_roads.py', '--assets', c.sd, '--sources', c.sources,
                     '--output', roads])
    c.run('levels', [sys.executable, 'tools/o3ds_level_topology_locks.py', listing, roads, locks])

    def cook(out, *bound):
        if out.exists():
            shutil.rmtree(out)
        c.run('levels', ['node', 'tools/cook_o3ds_geometry.mjs', '--listing', listing, '--output', out,
                         '--profile', 'aggressive', '--road-locks', locks, '--seamless', 'true', *bound])
    # Every connected authored piece (a flag pole, a fence post) keeps >= 85% of
    # its size: the guard locks the pieces the relative-error cook shrinks, and
    # the absolute-error cook then keeps them as authored.
    guard_cook = c.path('levels-guard')
    for round_ in range(1, GUARD_ROUNDS + 1):
        cook(guard_cook)
        if c.run('levels', [sys.executable, 'tools/o3ds_piece_guard.py', listing, guard_cook / 'models' / 'o3ds',
                            locks], ok=(0, 1)) == 0:
            break
        print(f'  piece guard round {round_}: more pieces locked')
    else:
        raise SystemExit(f'piece guard did not converge in {GUARD_ROUNDS} rounds')
    shutil.rmtree(guard_cook)
    out = c.path('levels')
    cook(out, '--absolute-error', LEVEL_ERROR)
    for name in EXCLUDED_LEVELS:
        (out / 'models' / 'o3ds' / f'{name}.gmdl').unlink(missing_ok=True)
    print(f"  {len(list((out / 'models' / 'o3ds').glob('*.gmdl')))} level sidecars")


def landmarks(c):
    models = c.path('level-sources', 'models')
    groups = {}
    for line in LANDMARKS.read_text().splitlines():
        fields = line.split('#')[0].split()
        if fields and (models / f'{fields[0]}.gmdl').is_file():
            groups.setdefault(fields[1] if len(fields) > 1 else None, []).append(fields[0])
    out = c.fresh('landmarks')
    for cap, names in sorted(groups.items(), key=lambda g: g[0] or ''):
        c.run('landmarks', [sys.executable, 'tools/cook_o3ds_lowpoly_characters.py', '--models', models,
                            '--textures', c.sd / 'textures', '--output', out, '--landmark', '--ratio', LANDMARK_RATIO,
                            '--jobs', c.jobs, *(['--landmark-cap', cap] if cap else []),
                            *[x for n in sorted(names) for x in ('--only', n)]])
    print(f"  {len(list((out / 'models' / 'o3ds').glob('*.gmdl')))} landmark sidecars")


def drivers(c):
    out = c.fresh('drivers-rival')
    c.run('drivers', [sys.executable, 'tools/cook_o3ds_lowpoly_characters.py', '--models', c.sd / 'models',
                      '--textures', c.sd / 'textures', '--output', out, '--variant', 'rival', '--jobs', c.jobs])


def staged_models(c):
    """Links folder with the packaged models/ (what the console's SD card holds)."""
    folder = c.fresh('staged', 'models')
    for rel, src in c.listing.items():
        if rel.startswith('models/'):
            (folder / Path(rel).name).symlink_to(Path(src).resolve())
    return folder


def refined(c):
    staged_models(c)
    regions = c.path('character-regions.json')
    c.run('refined', [sys.executable, 'tools/build_o3ds_character_regions.py', '--assets', c.path('staged'),
                      '--output', regions])
    listing = c.path('staged-listing.json')
    listing.write_text(json.dumps({rel: str(Path(src).resolve()) for rel, src in sorted(c.listing.items())
                                   if rel.startswith('models/')}, indent=1))
    out = c.path('refined')
    if out.exists():
        shutil.rmtree(out)
    c.run('refined', ['node', 'tools/cook_o3ds_geometry.mjs', '--listing', listing, '--output', out,
                      '--profile', 'aggressive', '--character-regions', regions, '--character-head-mode', 'preserve'])


def vehicles(c):
    out = c.path('vehicles')
    if out.exists():
        shutil.rmtree(out)
    c.run('vehicles', [sys.executable, 'tools/cook_o3ds_vehicles.py', '--originals', c.sd / 'models', '--out', out])
    c.run('vehicles', [sys.executable, 'tools/select_o3ds_vehicles.py', '--vehicles', out,
                       '--refined', c.path('refined'), '--textures', c.sd / 'textures', '--jobs', c.jobs])


def texture_mips(c):
    out = c.path('texmips')
    if out.exists():
        shutil.rmtree(out)
    c.run('texture-mips', [sys.executable, 'tools/cook_o3ds_course_textures.py', '--mode', 'pack',
                           '--assets', c.sd, '--out', out, '--jobs', c.jobs])


def stage_command(c):
    selected = c.path('vehicles', 'selected')
    return [sys.executable, 'tools/stage_o3ds_lowpoly.py', '--rivals', c.path('drivers-rival'),
            '--levels', c.path('levels'), '--landmarks', c.path('landmarks'), '--vehicles', selected, '--vehicle-rivals', selected, '--refined', c.path('refined'),
            '--level-names', c.path('level-names.txt'), '--sources', c.sources, '--originals', c.sd / 'models',
            '--package', c.package, '--vertex-cache',
            '--texture-mips', c.path('texmips', 'textures', 'o3ds', 'mips.pak'),
            '--pvs', c.path('pvs', 'models', 'pvs'), '--object-pvs', c.path('ovis', 'models', 'pvs')]


def stage(c):
    # Visibility comes after this stage: none may be folded in from a past run.
    for d in ('pvs', 'ovis'):
        shutil.rmtree(c.path(d), ignore_errors=True)
    c.run('stage', stage_command(c))


def visibility(c):
    # Visibility fingerprints cover the staged overlay's geometry and index order.
    folder = staged_models(c)
    (folder / 'o3ds').symlink_to((c.package / 'models' / 'o3ds').resolve())
    for tool, out in (('build_o3ds_pvs.py', 'pvs'), ('build_o3ds_object_pvs.py', 'ovis')):
        shutil.rmtree(c.path(out), ignore_errors=True)
        c.run('visibility', [sys.executable, f'tools/{tool}', '--models', folder, '--assets', c.sources / 'courses',
                             '--out', c.path(out, 'models', 'pvs')])
        # Per-camera rasters (the bulk of the output); the sidecars stay.
        shutil.rmtree(c.path(out, 'models', 'work'), ignore_errors=True)


def final(c):
    c.run('final', stage_command(c))
    receipt = json.loads((c.package / 'o3ds-lowpoly-receipt.json').read_text())
    built = {p.name for d in ('pvs', 'ovis') for p in c.path(d, 'models', 'pvs').glob('*') if p.suffix in ('.pvs', '.opvs')}
    stale = sorted(built - set(receipt['pvs']))
    print(f"  {receipt['counts']} textures {receipt['rebakedTextures']}, visibility {len(receipt['pvs'])}"
          f"/{len(built)}" + (f', left out (fingerprint differs): {stale}' if stale else ''))


STAGES = [
    ('level-sources', level_sources, ['level-listing.json', 'level-names.txt']),
    ('levels', levels, ['levels/receipt.json', 'level-locks.json']),
    ('landmarks', landmarks, ['landmarks/lowpoly-receipt.json']),
    ('drivers', drivers, ['drivers-rival/lowpoly-receipt.json']),
    ('refined', refined, ['refined/receipt.json']),
    ('vehicles', vehicles, ['vehicles/selected/selection.json']),
    ('texture-mips', texture_mips, ['texmips/textures/o3ds/mips.pak']),
    ('stage', stage, []),
    ('visibility', visibility, ['pvs/models/pvs', 'ovis/models/pvs']),
    ('final', final, []),
]


def stage_key(c, name):
    digest = hashlib.sha256(json.dumps([name, c.settings, c.inputs]).encode())
    for rel in COOK_SOURCES[name]:
        digest.update(rel.encode() + b'\0' + (ROOT / rel).read_bytes().replace(b'\r\n', b'\n'))
    return digest.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--assets', type=Path, default=ROOT / 'sd-card' / '3ds' / 'mkw-3ds', help='cooked SD folder')
    ap.add_argument('--sources', type=Path, default=ROOT / 'assets', help='converted disc files (course KCLs)')
    ap.add_argument('--work', type=Path, default=ROOT / 'build' / 'o3ds-overlay')
    ap.add_argument('--package', type=Path, default=ROOT / 'build' / 'o3ds-lowpoly-package')
    ap.add_argument('--jobs', type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument('--force', action='store_true', help='run every stage')
    a = ap.parse_args()
    a.work = a.work.resolve()
    a.package = a.package.resolve()
    for d in ('logs', 'stamps'):
        (a.work / d).mkdir(parents=True, exist_ok=True)
    c = Cook(a)
    rerun = a.force
    outputs_of = {'stage': [c.package / 'o3ds-lowpoly-receipt.json'], 'final': [c.package / 'o3ds-lowpoly-receipt.json']}
    for name, fn, outputs in STAGES:
        stamp = c.path('stamps', f'{name}.done')
        key = stage_key(c, name)
        paths = outputs_of.get(name, [c.path(o) for o in outputs])
        if not rerun and stamp.is_file() and stamp.read_text().split()[0] == key and all(p.exists() for p in paths):
            print(f'[o3ds overlay] {name}: done')
            continue
        rerun = True
        stamp.unlink(missing_ok=True)
        print(f'[o3ds overlay] {name} ...', flush=True)
        start = time.monotonic()
        fn(c)
        stamp.write_text(f'{key}\n{time.monotonic() - start:.0f} s\n')
        print(f'[o3ds overlay] {name}: {time.monotonic() - start:.0f} s', flush=True)
    print(f'[o3ds overlay] package: {c.package}')


if __name__ == '__main__':
    main()
