#!/usr/bin/env python3
"""Restore pinned public source dependencies; never fetch game files."""
import json,subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1];deps=json.loads((root/'dependencies.json').read_text())
PATCHES={'kinoko':'patches/kinoko-arm11.patch','citro3d':deps['citro3d']['patch']}

def current(dest,patch):
 """Bring an existing pinned checkout to exactly upstream + this repository's
 patch. A checkout from an earlier build holds an older version of the patch:
 it (and any other edit) goes to the checkout's git stash first, so nothing is
 lost, and the pinned commit is patched again."""
 git=['git','-c','user.name=mkw-bootstrap','-c','user.email=mkw-bootstrap@localhost','-C',str(dest)]
 if subprocess.run(git+['apply','--reverse','--check',str(patch)],capture_output=True).returncode==0:return
 if subprocess.check_output(git+['status','--porcelain'],text=True).strip():
  print(f'{dest.name}: updating to the current {patch.name} (the previous edits are kept in its git stash)')
  subprocess.run(git+['stash','push','--include-untracked','-m','before '+patch.name],check=True)
 subprocess.run(git+['apply',str(patch)],check=True)

for name in ('kinoko','noclip','gclib','citro3d'):
 dest=root/'vendor'/name;entry=deps[name];patch=root/PATCHES[name] if name in PATCHES else None
 if dest.exists():
  actual=subprocess.check_output(['git','-C',str(dest),'rev-parse','HEAD'],text=True).strip()
  if actual!=entry['commit']:raise SystemExit(f'{name}: unexpected revision; refusing to overwrite existing checkout')
  if patch:current(dest,patch)
  continue
 subprocess.run(['git','clone','--no-checkout',entry['url'],str(dest)],check=True)
 subprocess.run(['git','-C',str(dest),'checkout','--detach',entry['commit']],check=True)
 if patch:subprocess.run(['git','-C',str(dest),'apply',str(patch)],check=True)
print('Source dependencies ready. Run npm ci and install requirements.txt into a project virtualenv.')
