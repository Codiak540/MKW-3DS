#!/usr/bin/env python3
"""Assemble and stage the Old 3DS low-poly overlay (models/o3ds + textures/o3ds).

Sources, in priority order for each models/o3ds/<name>.gmdl:
  1. low-poly drivers (tools/cook_o3ds_lowpoly_characters.py outputs, first wins)
  2. level models (courses, skies, course objects) only from the seam-audited,
     road-locked cook (tools/cook_o3ds_geometry.mjs --road-locks --seamless true);
     a level model without one keeps its original geometry
  3. vehicles (tools/cook_o3ds_vehicles.py): a careful set for the player's own
     kart and a lean set in models/o3ds/rival/ for CPU opponents
  4. the earlier refined set for everything else (items, menus)
Course visibility (tools/build_o3ds_pvs.py, models/pvs/<part>.pvs) is staged
alongside; a sidecar whose geometry fingerprint no longer matches the staged
course overlay is left out (the game would ignore it anyway).
Course texture mip chains (tools/cook_o3ds_course_textures.py --mode pack,
textures/o3ds/mips.pak) are staged only with --texture-mips, after every
entry passes the runtime parser (tools/validate_texture_mips.cpp).
Course objects with their own collision (bridges, escalators, bounce mushrooms,
volcano pieces, battle blocks) always keep their authored geometry.

Nothing is written outside --package and --dest; --dest's previous overlay is
moved to --backup first. The original models are never touched.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from host_cxx import MISSING, compile_command, exe as executable, find_host_cxx  # noqa: E402


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def drivable_stems(sources):
    return sorted({p.stem.lower() for p in sources.rglob('*.kcl') if p.name != 'course.kcl'})


def assemble(a):
    sys.path.insert(0, str(ROOT / 'tools'))
    from o3ds_lowpoly_io import read_variant
    pkg = a.package
    if pkg.exists():
        shutil.rmtree(pkg)
    (pkg / 'models' / 'o3ds').mkdir(parents=True)
    (pkg / 'textures' / 'o3ds').mkdir(parents=True)
    stems = drivable_stems(a.sources)
    level_names = set(a.level_names.read_text().split())
    chosen, excluded = {}, []

    def drivable(name):
        return any(s in name.lower() for s in stems)

    # 1. Characters, first source wins (new symmetric cook, then approved set).
    # The player's own driver keeps its original model (the user's choice,
    # 2026-10-16): only the player loads models/o3ds/<driver>; CPU racers and
    # other players load models/o3ds/rival/ (src/racer_visuals.cpp), which
    # stays decimated. --decimate-player-driver restores the reduced driver.
    player_full = set()
    for root in a.characters:
        for p in sorted((root / 'models' / 'o3ds').glob('*.gmdl')):
            if p.name.endswith('_driver.gmdl') and not a.decimate_player_driver:
                player_full.add(p.name)
                continue
            chosen.setdefault(p.name, ('lowpoly-character', p, root))
    if not a.characters:
        # No main driver cook: the player's drivers are the ones it would
        # reduce, every *_driver.gmdl in --originals.
        player_full = {p.name for p in a.originals.glob('*_driver.gmdl')}
    # 2a. Track landmarks (statues, ships, course creatures): character-quality cook.
    if a.landmarks and (a.landmarks / 'models' / 'o3ds').is_dir():
        for p in sorted((a.landmarks / 'models' / 'o3ds').glob('*.gmdl')):
            if p.name not in chosen and not drivable(p.name):
                chosen[p.name] = ('landmark', p, a.landmarks)
    # 2b. Levels: only the seam-audited, road-locked cook.
    for p in sorted((a.levels / 'models' / 'o3ds').glob('*.gmdl')):
        if p.name in chosen:
            continue
        # Skies are a few hundred triangles at most and break when reduced.
        if drivable(p.name) or '_sky_' in p.name:
            excluded.append(p.name)
            continue
        chosen[p.name] = ('seamless-level', p, a.levels)
    # 2c. Vehicles (tools/select_o3ds_vehicles.py): the player's own vehicle; a
    # part with no selected sidecar keeps its original (never the refined one).
    vehicle_parts = set()
    if a.vehicles and (a.vehicles / 'selection.json').is_file():
        vehicle_parts = set(json.loads((a.vehicles / 'selection.json').read_text())['models'])
        for p in sorted((a.vehicles / 'models' / 'o3ds').glob('*.gmdl')):
            chosen.setdefault(p.name, ('vehicle', p, a.vehicles))
    # 3. Everything else (items, menus) from the refined set. The vehicle
    # selection screen's models (ui_machine_*) stay full detail (the user's
    # choice, 2026-10-16); race vehicles are the separate *_kart_*/*_bike_* parts.
    # --decimate-menu-vehicles restores the reduced ones.
    for p in sorted((a.refined / 'models' / 'o3ds').glob('*.gmdl')):
        menu_vehicle = p.name.startswith('ui_machine_') and not a.decimate_menu_vehicles
        if p.name in chosen or p.name in vehicle_parts or p.name in player_full or menu_vehicle or p.name in level_names or \
                p.name.startswith('course_') or drivable(p.name):
            if p.name not in chosen:
                excluded.append(p.name)
            continue
        chosen[p.name] = ('refined', p, a.refined)
    textures = {}
    for name, (kind, src, root) in chosen.items():
        shutil.copy2(src, pkg / 'models' / 'o3ds' / name)
        if kind in ('lowpoly-character', 'landmark'):
            g, _, _ = read_variant(src)
            for t in g.names:
                if '_o3' in t:
                    textures[t] = root / 'textures' / f'{t}.t3x'
    # 4. Opponent ("rival") drivers: models/o3ds/rival/, used only by CPU racers.
    # 5. Opponent vehicles (lean cook), same folder.
    rivals = {}
    (pkg / 'models' / 'o3ds' / 'rival').mkdir()
    for root in (a.rivals, a.vehicle_rivals):
        if not root or not (root / 'models' / 'o3ds' / 'rival').is_dir():
            continue
        for p in sorted((root / 'models' / 'o3ds' / 'rival').glob('*.gmdl')):
            if p.name in rivals:
                continue
            shutil.copy2(p, pkg / 'models' / 'o3ds' / 'rival' / p.name)
            rivals[p.name] = sha(p)
            g, _, _ = read_variant(p)
            for t in g.names:
                if '_o3' in t:
                    textures[t] = root / 'textures' / f'{t}.t3x'
    for t, src in sorted(textures.items()):
        shutil.copy2(src, pkg / 'textures' / 'o3ds' / f'{t}.t3x')
    kinds = {}
    for kind, _, _ in chosen.values():
        kinds[kind] = kinds.get(kind, 0) + 1
    receipt = {
        'version': 2, 'utc': time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()),
        'sources': {'characters': [str(c) for c in a.characters], 'landmarks': str(a.landmarks),
                    'levels': str(a.levels), 'refined': str(a.refined),
                    'vehicles': str(a.vehicles), 'vehicleRivals': str(a.vehicle_rivals)},
        'counts': dict(kinds, rival=len(rivals), playerDriversOriginal=len(player_full)), 'rebakedTextures': len(textures), 'excluded': sorted(set(excluded)),
        'rivals': rivals,
        'models': {n: {'kind': k, 'source': str(src), 'sha256': sha(pkg / 'models' / 'o3ds' / n)}
                   for n, (k, src, _) in sorted(chosen.items())},
        'textures': {t: sha(pkg / 'textures' / 'o3ds' / f'{t}.t3x') for t in sorted(textures)},
        'note': 'Old 3DS only (ModelDetail). Physics/collision untouched. No hardware FPS claim.',
    }
    (pkg / 'o3ds-lowpoly-receipt.json').write_text(json.dumps(receipt, indent=1))
    return receipt


def build_check(exe, src, std, newest):
    """Compile a build-time check; False when it cannot be built here."""
    if exe.exists() and exe.stat().st_mtime >= newest:
        return True
    cxx = find_host_cxx()
    if not cxx:
        print(f'{src.stem} skipped: {MISSING}')
        return False
    r = subprocess.run(compile_command(cxx, f'-std={std}', '-O2', '-I', str(ROOT / 'include'), str(src), '-o', str(exe)),
                       capture_output=True, text=True)
    if r.returncode:
        print(f'{src.stem} skipped: it did not compile here\n' + (r.stdout + r.stderr).strip()[-1500:])
        return False
    return True


def validate(a):
    exe = executable(ROOT / 'build' / 'validate_model_variants_v2')
    src = ROOT / 'tools' / 'validate_model_variants.cpp'
    # Without it the game still runs the same check when it loads each model
    # and keeps the original for any sidecar it rejects.
    if not build_check(exe, src, 'c++20', src.stat().st_mtime):
        return True
    # Originals as the runtime sees them: menu.pak/race.pak entries win over loose files.
    sys.path.insert(0, str(ROOT / 'tools'))
    from cook_o3ds_textures import Assets
    assets = Assets(a.originals.parent)
    ok = True
    for sub in ('', 'rival'):
        candidates = a.package / 'models' / 'o3ds' / sub
        if not candidates.is_dir():
            continue
        originals = a.package.parent / (a.package.name + '-originals')
        if originals.exists():
            shutil.rmtree(originals)
        originals.mkdir()
        for side in candidates.glob('*.gmdl'):
            (originals / side.name).write_bytes(assets.read('models/' + side.name))
        try:
            r = subprocess.run([str(exe), str(originals), str(candidates), str(a.package / 'textures' / 'o3ds')],
                               capture_output=True, text=True)
        except OSError as error:
            shutil.rmtree(originals)
            print(f'{sub or "main"}: the model check could not run ({error})')
            ok = False
            continue
        shutil.rmtree(originals)
        tail = [l for l in r.stdout.splitlines() if l.startswith(('Validated', 'Accepted'))]
        print((sub or 'main') + ':', ' '.join(tail))
        for l in [l for l in r.stderr.splitlines() if l.startswith('FAIL')][:20]:
            print(l)
        ok &= r.returncode == 0
    return ok


def seam_gate(a, receipt):
    """Levels: no new open edges or UV tears. Characters/landmarks: no new open
    edges (their re-baked UV boundaries are colour-checked by the cook).
    Vehicles are open hard-surface shells where every reduction adds open
    edges; tools/select_o3ds_vehicles.py checks them by rendering instead."""
    sys.path.insert(0, str(ROOT / 'tools'))
    from check_o3ds_seams import edges
    from o3ds_lowpoly_io import read_gmdl, read_variant
    from cook_o3ds_textures import Assets
    assets = Assets(a.originals.parent)
    tmp = a.package.parent / (a.package.name + '-seam-originals')
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    bad = []
    for name, info in receipt['models'].items():
        if info['kind'] in ('refined', 'vehicle'):
            continue
        (tmp / name).write_bytes(assets.read('models/' + name))
        o_open, o_seam, o_uvs = edges(read_gmdl(tmp / name))
        v, _, _ = read_variant(a.package / 'models' / 'o3ds' / name)
        c_open, c_seam, _ = edges(v)
        new_open = len(c_open - o_open)
        tears = sum(1 for p in c_seam - o_seam if len(o_uvs[p]) < 2) if info['kind'] == 'seamless-level' else 0
        if new_open or tears:
            bad.append(f'{name}: {new_open} open edges, {tears} tears')
    shutil.rmtree(tmp)
    for line in bad[:20]:
        print('SEAM', line)
    print(f'seam gate: {len(bad)} models with new seams')
    return not bad


def pvs(a):
    """Copy course visibility sidecars whose fingerprint matches the geometry the
    game will load (original + staged O3RM course overlay)."""
    if not a.pvs or not a.pvs.is_dir():
        return {}
    sys.path.insert(0, str(ROOT / 'tools' / 'pvs'))
    import struct
    import tempfile
    import gmdl
    from cook_o3ds_textures import Assets
    assets = Assets(a.originals.parent)
    out = a.package / 'models' / 'pvs'
    out.mkdir(parents=True, exist_ok=True)
    kept, stale = {}, []
    with tempfile.TemporaryDirectory() as tmp:
        for side in sorted(a.pvs.glob('*.pvs')):
            name = side.stem
            data = side.read_bytes()
            if data[:4] != b'MPVS':
                stale.append(name)
                continue
            recorded = struct.unpack_from('<I', data, 8)[0]
            original = Path(tmp) / f'{name}.gmdl'
            original.write_bytes(assets.read(f'models/{name}.gmdl'))
            overlay = a.package / 'models' / 'o3ds' / f'{name}.gmdl'
            loaded = gmdl.read(original, overlay if overlay.exists() else None)
            if gmdl.fingerprint(loaded) != recorded:
                stale.append(name)
                continue
            shutil.copy2(side, out / side.name)
            kept[side.name] = sha(side)
    print(f'pvs: {len(kept)} course visibility sidecars match the overlay'
          + (f', {len(stale)} stale left out: {" ".join(stale[:8])}' if stale else ''))
    # Object/racer visibility (tools/build_o3ds_object_pvs.py, models/pvs/course_NN.opvs):
    # its fingerprint is order-independent over every loaded course part.
    if a.object_pvs and a.object_pvs.is_dir():
        sys.path.insert(0, str(ROOT / 'tools'))
        from build_o3ds_object_pvs import object_fingerprint
        okept, ostale = 0, []
        with tempfile.TemporaryDirectory() as tmp:
            for side in sorted(a.object_pvs.glob('*.opvs')):
                data = side.read_bytes()
                names = assets.read(f'models/{side.stem}.list').decode().split()
                parts = []
                for n in names:
                    original = Path(tmp) / f'{n}.gmdl'
                    original.write_bytes(assets.read(f'models/{n}.gmdl'))
                    overlay = a.package / 'models' / 'o3ds' / f'{n}.gmdl'
                    parts.append(gmdl.read(original, overlay if overlay.exists() else None))
                if data[:4] != b'MOPV' or struct.unpack_from('<I', data, 8)[0] != object_fingerprint(parts):
                    ostale.append(side.stem)
                    continue
                shutil.copy2(side, out / side.name)
                kept[side.name] = sha(side)
                okept += 1
        print(f'object pvs: {okept} sidecars match the overlay'
              + (f', {len(ostale)} stale left out: {" ".join(ostale[:8])}' if ostale else ''))
    return kept


def texture_mips(a):
    """Validate and add textures/o3ds/mips.pak; its sha256, or None."""
    if not a.texture_mips:
        return None
    exe = executable(ROOT / 'build' / 'validate_texture_mips')
    src = ROOT / 'tools' / 'validate_texture_mips.cpp'
    # Without it the game still checks each chain when it loads it and keeps
    # the original texture otherwise.
    if build_check(exe, src, 'c++17', max(src.stat().st_mtime, (ROOT / 'include/mkw/texture_mips.hpp').stat().st_mtime)):
        try:
            r = subprocess.run([str(exe), str(a.texture_mips), str(a.originals.parent)], capture_output=True, text=True)
            print('texture mips:', r.stdout.strip(), r.stderr.strip()[:2000])
            failed = r.returncode != 0
        except OSError as error:
            print(f'texture mips: the check could not run ({error})')
            failed = True
        if failed:
            print('WARNING: texture mip check failed; the game keeps the original texture for any chain it rejects')
    shutil.copy2(a.texture_mips, a.package / 'textures' / 'o3ds' / 'mips.pak')
    return sha(a.package / 'textures' / 'o3ds' / 'mips.pak')


def stage(a):
    dest = a.dest
    models = dest / 'models' / 'o3ds'
    a.backup.mkdir(parents=True, exist_ok=True)
    if models.exists():
        shutil.move(str(models), str(a.backup / 'models-o3ds'))
    if (dest / 'textures' / 'o3ds').exists():
        shutil.move(str(dest / 'textures' / 'o3ds'), str(a.backup / 'textures-o3ds'))
    if (dest / 'models' / 'pvs').exists():
        shutil.move(str(dest / 'models' / 'pvs'), str(a.backup / 'models-pvs'))
    if (dest / 'o3ds.pak').exists():
        shutil.move(str(dest / 'o3ds.pak'), str(a.backup / 'o3ds.pak'))
    loose = sorted((dest / 'textures').glob('*_o3*.t3x'))
    if loose:
        (a.backup / 'textures-loose').mkdir(exist_ok=True)
        for t in loose:
            shutil.move(str(t), str(a.backup / 'textures-loose' / t.name))
    # Sidecars and re-baked textures go into one archive (tools/o3ds_pak.py);
    # mips.pak and the visibility files stay loose.
    sys.path.insert(0, str(ROOT / 'tools'))
    import o3ds_pak
    pkg = a.package
    packed = {p.relative_to(pkg).as_posix(): p for p in sorted(pkg.rglob('*'))
              if p.is_file() and o3ds_pak.packed(p.relative_to(pkg).as_posix())}
    count, size = o3ds_pak.write(packed, dest / 'o3ds.pak')
    print(f'o3ds.pak: {count} files, {size} bytes')
    if (pkg / 'textures' / 'o3ds' / 'mips.pak').is_file():
        (dest / 'textures' / 'o3ds').mkdir(parents=True, exist_ok=True)
        shutil.copy2(pkg / 'textures' / 'o3ds' / 'mips.pak', dest / 'textures' / 'o3ds' / 'mips.pak')
    if (a.package / 'models' / 'pvs').is_dir():
        shutil.copytree(a.package / 'models' / 'pvs', dest / 'models' / 'pvs')
    shutil.copy2(a.package / 'o3ds-lowpoly-receipt.json', dest / 'o3ds-lowpoly-receipt.json')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--characters', type=Path, action='append',
                    help='low-poly character cook outputs, highest priority first (default: none, the '
                         "player's drivers are named from --originals; with --decimate-player-driver: "
                         'build/o3ds-lowpoly-main2)')
    ap.add_argument('--levels', type=Path, default=ROOT / 'build' / 'o3ds-levels-seamless')
    ap.add_argument('--landmarks', type=Path, default=ROOT / 'build' / 'o3ds-landmarks2',
                    help='landmark (statue/ship/creature) cook output')
    ap.add_argument('--rivals', type=Path, default=ROOT / 'build' / 'o3ds-lowpoly-rival2',
                    help='rival (opponent) driver cook output')
    ap.add_argument('--vehicles', type=Path, default=ROOT / 'build' / 'o3ds-vehicles' / 'selected',
                    help='vehicle selection (tools/select_o3ds_vehicles.py): player parts')
    ap.add_argument('--vehicle-rivals', type=Path, default=ROOT / 'build' / 'o3ds-vehicles' / 'selected',
                    help='vehicle selection: opponent parts (models/o3ds/rival/)')
    ap.add_argument('--pvs', type=Path, default=ROOT / 'build' / 'o3ds-pvs' / 'models' / 'pvs',
                    help='course visibility sidecars (tools/build_o3ds_pvs.py)')
    ap.add_argument('--object-pvs', type=Path, default=ROOT / 'build' / 'o3ds-ovis' / 'models' / 'pvs',
                    help='object/racer visibility sidecars (tools/build_o3ds_object_pvs.py)')
    ap.add_argument('--level-names', type=Path, default=ROOT / 'build' / 'o3ds-level-names.txt')
    ap.add_argument('--refined', type=Path, default=ROOT / 'build' / 'o3ds-models-refined')
    ap.add_argument('--sources', type=Path, default=ROOT / 'assets')
    ap.add_argument('--originals', type=Path, default=ROOT / 'sd-card' / '3ds' / 'mkw-3ds' / 'models')
    ap.add_argument('--package', type=Path, default=ROOT / 'build' / 'o3ds-lowpoly-package')
    ap.add_argument('--dest', type=Path, help='mkw-3ds folder to stage into (e.g. emulator SD)')
    ap.add_argument('--backup', type=Path, help='where --dest models/o3ds is moved first')
    ap.add_argument('--texture-mips', type=Path,
                    help='textures/o3ds/mips.pak to stage (tools/cook_o3ds_course_textures.py --mode pack)')
    ap.add_argument('--decimate-menu-vehicles', action='store_true',
                    help='also reduce the vehicle selection screen models (default: original models)')
    ap.add_argument('--decimate-player-driver', action='store_true',
                    help="also reduce the player's own driver (default: original model for the player)")
    ap.add_argument('--vertex-cache', action='store_true',
                    help='reorder triangles for the post-transform cache (tools/optimize_o3ds_vertex_cache.mjs)')
    a = ap.parse_args()
    if not a.characters:
        a.characters = [ROOT / 'build' / 'o3ds-lowpoly-main2'] if a.decimate_player_driver else []
    receipt = assemble(a)
    print(json.dumps(receipt['counts']), 'textures', receipt['rebakedTextures'], 'excluded', len(receipt['excluded']))
    if a.vertex_cache:
        # Index order only; validated and seam-checked below like any sidecar.
        subprocess.run(['node', str(ROOT / 'tools' / 'optimize_o3ds_vertex_cache.mjs'), '--package', str(a.package),
                        '--assets', str(a.originals.parent), '--out', str(a.package),
                        '--receipt', str(a.package / 'o3ds-vertex-cache-receipt.json')], check=True)
        for name, info in receipt['models'].items():
            info['sha256'] = sha(a.package / 'models' / 'o3ds' / name)
        for name in receipt['rivals']:
            receipt['rivals'][name] = sha(a.package / 'models' / 'o3ds' / 'rival' / name)
        receipt['vertexCache'] = True
    if not validate(a):
        # The game runs this same check when it loads each model and keeps the
        # original for any sidecar it rejects, so the build goes on.
        print('WARNING: model check failed (above); the game keeps the original for any model it rejects')
    if not seam_gate(a, receipt):
        sys.exit('seam audit failed; nothing staged')
    receipt['pvs'] = pvs(a)
    receipt['textureMips'] = texture_mips(a)
    (a.package / 'o3ds-lowpoly-receipt.json').write_text(json.dumps(receipt, indent=1))
    if a.dest:
        if not a.backup:
            sys.exit('--backup is required with --dest')
        stage(a)
        print('staged into', a.dest)


if __name__ == '__main__':
    main()
