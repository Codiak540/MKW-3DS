#!/usr/bin/env python3
"""Prepare the Old 3DS course texture mip hardware A/B (docs/o3ds-texture-cache.md).

Writes a Homebrew Launcher test folder, never a release CIA:
  <out>/3ds/mkw-3ds/mkw-3ds-texmips-qa.3dsx    the QA build (external assets)
  <out>/3ds/mkw-3ds/textures/o3ds/mips.pak     validated with the runtime parser
  <out>/3ds/mkw-3ds/performance.txt            performance.csv recording marker
  <out>/autoplay/<course>.txt                   menu-autoplay.txt scripts: VS race,
                                                Mario, CPU-driven for 75 s (auto:4500)
  <out>/HARDWARE-TEST.txt                      the checklist (from the doc)
  <out>/texmips-manifest-template.json         tools/compare_o3ds_performance.py input
  <out>/receipt.json                           sha256 of everything above
Both arms use the same 3DSX and SD files; only o3ds-texmips-off.txt differs.
The archive is game-derived: keep <out> private (it lives in build/).
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Heavy courses first (most course texture bytes), then Luigi Circuit.
AUTOPLAY_COURSES = (11, 3, 6, 0)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--binary', type=Path, default=ROOT / 'build' / 'mkw-3ds.3dsx', help='QA 3DSX (tools/build.py --external-assets)')
    ap.add_argument('--mips', type=Path, default=ROOT / 'build' / 'o3ds-texmips3' / 'textures' / 'o3ds' / 'mips.pak')
    ap.add_argument('--assets', type=Path, default=ROOT / 'sd-card' / '3ds' / 'mkw-3ds', help='originals the archive must match')
    ap.add_argument('--out', type=Path, default=ROOT / 'build' / 'o3ds-texmips-qa')
    a = ap.parse_args()
    exe = ROOT / 'build' / 'validate_texture_mips'
    subprocess.run(['c++', '-std=c++17', '-O2', '-I', str(ROOT / 'include'), str(ROOT / 'tools' / 'validate_texture_mips.cpp'),
                    '-o', str(exe)], check=True)
    check = subprocess.run([str(exe), str(a.mips), str(a.assets)], capture_output=True, text=True)
    if check.returncode:
        raise SystemExit('mips.pak rejected by the runtime parser:\n' + check.stderr[:4000])
    if a.out.exists():
        shutil.rmtree(a.out)
    sd = a.out / '3ds' / 'mkw-3ds'
    (sd / 'textures' / 'o3ds').mkdir(parents=True)
    shutil.copy2(a.binary, sd / 'mkw-3ds-texmips-qa.3dsx')
    shutil.copy2(a.mips, sd / 'textures' / 'o3ds' / 'mips.pak')
    (sd / 'performance.txt').write_text('1\n')
    import sys
    sys.path.insert(0, str(ROOT / 'tools'))
    from o3ds_course_tour import COURSES
    from o3ds_perf_run import SCRIPT, course_line
    (a.out / 'autoplay').mkdir()
    for course in AUTOPLAY_COURSES:
        name = f"{course:02d}-{COURSES[course].replace(' ', '_').replace(chr(39), '')}.txt"
        (a.out / 'autoplay' / name).write_text(SCRIPT.format(n=4500, course=course_line(course)))
    doc = (ROOT / 'docs' / 'o3ds-texture-cache.md').read_text()
    section = re.search(r'## 5\. Hardware test checklist\n(.*?)(?=\n## )', doc, re.S)
    (a.out / 'HARDWARE-TEST.txt').write_text('Old 3DS course texture mips: hardware A/B\n\n' + (section.group(1) if section else 'See docs/o3ds-texture-cache.md section 5.\n'))
    build = sha(sd / 'mkw-3ds-texmips-qa.3dsx')
    template = json.loads(subprocess.run(['python3', str(ROOT / 'tools' / 'compare_o3ds_performance.py'), '--template'],
                                         capture_output=True, text=True, check=True).stdout)
    template['experiment'] = 'texture_mips'
    template['mip_filter'] = 'linear'
    template['attestation']['execution_environment'] = 'real_old3ds'
    pair = template['pairs'][0]
    pair['context']['build_sha256'] = build
    pair['baseline']['csv'], pair['candidate']['csv'] = 'off-1.csv', 'on-1.csv'
    pair['baseline']['assets_sha256'] = pair['candidate']['assets_sha256'] = 'REPLACE_WITH_SD_ASSET_MANIFEST_SHA256 (same for both arms)'
    (a.out / 'texmips-manifest-template.json').write_text(json.dumps(template, indent=2) + '\n')
    receipt = {'binary': str(a.binary), 'binarySha256': build, 'mips': str(a.mips), 'mipsSha256': sha(a.mips),
               'validation': check.stdout.strip(), 'files': {str(p.relative_to(a.out)): sha(p) for p in sorted(a.out.rglob('*')) if p.is_file()}}
    (a.out / 'receipt.json').write_text(json.dumps(receipt, indent=1) + '\n')
    print(json.dumps({k: receipt[k] for k in ('binarySha256', 'mipsSha256', 'validation')}, indent=1))


if __name__ == '__main__':
    main()
