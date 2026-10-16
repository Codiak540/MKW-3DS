#!/usr/bin/env python3
"""Prepare a private, opt-in Old 3DS geometry experiment from the authored Wii pipeline.

This stages a Homebrew Launcher test package, never a release CIA. Baseline
and candidate use the same executable and assets; only the SD marker differs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from stage_copy import stage_copy

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / 'build/playable/3ds/mkw-3ds'


def run(*args):
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def stage_candidates(candidates, activate_candidate=False, profile="aggressive"):
    """Add validated sidecars to an already packaged QA build."""
    report = json.loads((candidates / 'receipt.json').read_text())
    verified = []
    for original_name, row in report['models'].items():
        if not row.get('candidate'):
            continue
        original = Path(original_name)
        relative = Path(row['candidate'])
        if (original.parent != Path('models') or original.suffix != '.gmdl' or
                relative != Path('models/o3ds') / original.name):
            raise ValueError('Invalid geometry candidate path')
        source = candidates / relative
        if digest(source) != row['candidateSha256'] or digest(STAGE / original) != row['originalSha256']:
            raise ValueError('Geometry candidate or original changed: ' + str(original))
        if row.get('animation'):
            animation = Path(row['animation'])
            if (animation.parent != Path('models') or animation.suffix not in ('.dskm', '.dobj') or
                    digest(STAGE / animation) != row['animationSha256']):
                raise ValueError('Geometry animation bindings changed: ' + str(original))
        verified.append((source, relative.as_posix()))
    if not verified:
        raise SystemExit('No safe geometry reductions were generated; existing package is unchanged.')
    manifest = {name: value for name, value in json.loads((STAGE / 'manifest.json').read_text()).items()
                if not name.startswith('models/o3ds/') and name not in ('o3ds-models-test.txt', 'performance.txt')}
    baseline_manifest = dict(manifest)
    for old in (STAGE / 'models/o3ds').glob('*.gmdl'):
        old.unlink()
    variants = {}
    for src, relative in sorted(verified):
        dest = STAGE / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        stage_copy(src, dest)
        manifest[relative] = variants[relative] = digest(dest)
    # The marker is confined to a QA package, with runtime Old 3DS detection.
    (STAGE / 'o3ds-models-test.txt').unlink(missing_ok=True)
    if activate_candidate:
        (STAGE / 'o3ds-models-test.txt').write_text('1\n')
        manifest['o3ds-models-test.txt'] = digest(STAGE / 'o3ds-models-test.txt')
    (STAGE / 'performance.txt').write_text('1\n')
    manifest['performance.txt'] = digest(STAGE / 'performance.txt')
    (STAGE / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    receipt = {
        'schema_version': 1,
        'status': 'experimental_unmeasured_not_release_enabled',
        'package': str(STAGE),
        'executable_sha256': digest(STAGE / 'mkw-3ds.3dsx'),
        'baseline_assets_sha256': hashlib.sha256(json.dumps(baseline_manifest, sort_keys=True).encode()).hexdigest(),
        'candidate_assets_sha256': hashlib.sha256(json.dumps({**baseline_manifest, **variants}, sort_keys=True).encode()).hexdigest(),
        'geometry_receipt': str(candidates / 'receipt.json'),
        'geometry_receipt_sha256': digest(candidates / 'receipt.json'),
        'geometry_profile': profile,
        'candidate_active': activate_candidate,
        'variant_count': len(variants),
        'baseline': 'No o3ds-models-test.txt; existing Old 3DS quality settings remain active.',
        'candidate': 'Create /3ds/mkw-3ds/o3ds-models-test.txt on SD and restart the same executable.',
        'release_gate': 'At least 5 FPS on real Old 3DS in repeated matched demanding scenes, visual review and regression checks.',
    }
    (ROOT / 'build/o3ds-test-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))
    return receipt


def stage_textures(candidates, activate=False):
    """Stage a separately switchable texture experiment without changing originals."""
    report = json.loads((candidates / 'receipt.json').read_text())
    manifest = json.loads((STAGE / 'manifest.json').read_text())
    manifest = {name: value for name, value in manifest.items()
                if not name.startswith('textures/o3ds/') and name != 'o3ds-textures-test.txt'}
    for old in (STAGE / 'textures/o3ds').glob('*.otex'):
        old.unlink()
    count = 0
    for row in report['textures']:
        if row['status'] != 'candidate':
            continue
        relative = Path(row['output'])
        if relative.parent != Path('textures/o3ds') or relative.suffix != '.otex':
            raise ValueError('Invalid texture candidate path')
        source = candidates / relative
        if digest(source) != row['sha256']:
            raise ValueError('Texture candidate changed: ' + str(relative))
        destination = STAGE / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage_copy(source, destination)
        manifest[relative.as_posix()] = row['sha256']
        count += 1
    marker = STAGE / 'o3ds-textures-test.txt'
    marker.unlink(missing_ok=True)
    if activate:
        marker.write_text('1\n')
        manifest[marker.name] = digest(marker)
    (STAGE / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    receipt_path = ROOT / 'build/o3ds-test-receipt.json'
    receipt = json.loads(receipt_path.read_text())
    receipt.update(texture_candidate_count=count, texture_candidate_active=activate,
                   texture_receipt=str(candidates / 'receipt.json'),
                   texture_receipt_sha256=digest(candidates / 'receipt.json'),
                   staged_assets_sha256=hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest())
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    print(f'Staged {count} texture candidates; texture experiment active: {activate}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('disc', nargs='?', type=Path,
                        help='completed, validated USA ISO/WBFS; omit after cooking')
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--profile', choices=('conservative', 'aggressive'), default='aggressive')
    parser.add_argument('--ratio', type=float, default=None)
    parser.add_argument('--max-error', type=float, default=None)
    parser.add_argument('--reuse-cooked-assets', action='store_true',
                        help='reuse the completed authored cook; the packager still validates source receipts')
    parser.add_argument('--activate-candidate', action='store_true',
                        help='start this QA package with reduced models on Old 3DS; New 3DS keeps originals')
    parser.add_argument('--with-texture-candidates', action='store_true',
                        help='also cook and stage a separate, initially disabled Old 3DS texture experiment')
    args = parser.parse_args()
    if args.jobs < 1 or (args.ratio is not None and not 0 < args.ratio < 1):
        parser.error('jobs must be positive and ratio between 0 and 1')
    if args.max_error is not None and not 0 < args.max_error <= (.15 if args.profile == 'aggressive' else .02):
        parser.error('max-error must be positive and within the selected profile limit')
    if args.disc and (not args.disc.is_file() or args.disc.suffix.lower() not in ('.iso', '.wbfs')):
        parser.error('disc must be a completed ISO or WBFS; unpack/convert archives first')
    if args.disc and args.reuse_cooked_assets:
        parser.error('do not combine a new disc with --reuse-cooked-assets')

    # The release builder owns extraction, hash validation, all authored
    # conversion stages, overlays, animation banks, textures and asset packs.
    if not args.reuse_cooked_assets:
        run(sys.executable, 'tools/release/build_cia.py',
            *([args.disc.resolve()] if args.disc else []), '--cook-only', '--jobs', args.jobs)
    run(sys.executable, 'tools/build.py', '--external-assets', '--jobs', args.jobs)
    run(sys.executable, 'tools/package_sd.py', '--no-zip')
    # Simplify the final staged assets, after every lighting/roster overlay.
    # Bone IDs are accepted only after matching the original authored rig to
    # the final DSKM bindings. Missing semantics preserve character topology.
    regions = ROOT / 'build/o3ds-character-regions.json'
    if args.profile == 'aggressive':
        run(sys.executable, 'tools/build_o3ds_character_regions.py',
            '--assets', STAGE, '--output', regions)
    candidates = ROOT / ('build/o3ds-models-aggressive' if args.profile == 'aggressive' else 'build/o3ds-models')
    run('node', 'tools/cook_o3ds_geometry.mjs', '--assets', STAGE,
        '--output', candidates, '--profile', args.profile,
        *(['--character-regions', regions] if args.profile == 'aggressive' else []),
        *(['--ratio', args.ratio] if args.ratio is not None else []),
        *(['--max-error', args.max_error] if args.max_error is not None else []))

    stage_candidates(candidates, args.activate_candidate, args.profile)
    if args.with_texture_candidates:
        textures = ROOT / 'build/o3ds-textures'
        run(sys.executable, 'tools/cook_o3ds_textures.py', '--assets', STAGE,
            '--output', textures, '--jobs', args.jobs)
        stage_textures(textures)


if __name__ == '__main__':
    main()
