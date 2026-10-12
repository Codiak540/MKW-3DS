#!/usr/bin/env python3
"""Battle and award-screen content, converted ahead of their runtime (0.5.0).

Battle (Balloon Battle, Coin Runners, Team VS) can then arrive as a quick
update: the kart balloons (Common.szs balloon.brres: red and blue), Lakitu's
battle board (jugem_battle.brres) and every texture of the race HUD archive
(Scene/UI/Race.szs: battle points, team scores, coins, congratulations) and
the award screen (Scene/UI/Award.szs), plus Rosalina's Luma (Common.szs
tico.brres, with its wait loop). Layouts become generated code in later
updates; only the images must be on the SD card. UI textures go under
textures/ui/<archive>/ to stay clear of the textures folder's FAT32 limit.
"""
import argparse,hashlib,json,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'vendor/gclib')]
from unpack_szs import unpack
from cook_ceremony import cook_static,export
from cook_model import cook
from cook_race_items import decode_tpl
from devkitpro import tool
OUT=ROOT/'sd-card/3ds/mkw-3ds'
EXTRACTED=ROOT/'data/extracted'
WORK=ROOT/'build/battle'
UI_ARCHIVES={'race':'Scene/UI/Race.szs','award':'Scene/UI/Award.szs'}

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def safe(text):return ''.join(c if c.isalnum() else '_' for c in text.lower())

def convert_ui(key,archive):
    folder=WORK/'ui'/key;unpack(EXTRACTED/archive,folder)
    out=OUT/'textures/ui'/key
    if out.exists():shutil.rmtree(out)
    out.mkdir(parents=True)
    rows=[]
    for tpl in sorted(p for p in folder.rglob('*.tpl') if p.is_file()):
        relative=tpl.relative_to(folder)
        name=safe(relative.parent.parent.as_posix() if relative.parent.name=='timg' else relative.parent.as_posix())+'__'+safe(tpl.stem)
        png=WORK/'png'/key/(name+'.png');png.parent.mkdir(parents=True,exist_ok=True)
        image=decode_tpl(tpl).convert('RGBA');image.save(png)
        target=out/(name+'.t3x')
        subprocess.run([str(tool('tex3ds')),'-f','rgba8','-z','none','-o',str(target),str(png)],check=True,stdout=subprocess.DEVNULL)
        rows.append({'source':relative.as_posix(),'texture':target.relative_to(OUT).as_posix(),'width':image.width,'height':image.height})
    if len({r['texture'] for r in rows})!=len(rows):raise ValueError(f'{key}: texture name collision')
    return rows

def cook_animated(source,model,clip,name):
    """A skinned model bound in its clip's first frame, with its animation bank."""
    work=WORK/'exports'/name;export(source,work,model,clip,'0')
    parts=cook(work,OUT,name,256,preserve_vertices=True,texture_prefix=name)
    if len(parts)!=1:raise ValueError(f'{name}: expected one model, got {len(parts)}')
    shutil.copyfile(OUT/'models'/(parts[0]['name']+'.gmdl'),OUT/'models'/(name+'.gmdl'))
    bank=OUT/'models'/(name+'.dskm')
    audit=json.loads(subprocess.run(['node',str(ROOT/'build/export_driver.mjs'),str(source),str(bank),model],check=True,capture_output=True,text=True).stdout)
    return {'model':f'models/{name}.gmdl','bank':f'models/{name}.dskm','clips':[c['name'] for c in audit['clips']]}

def main():
    argparse.ArgumentParser().parse_args()
    WORK.mkdir(parents=True,exist_ok=True)
    common=WORK/'common';unpack(EXTRACTED/'Race/Common.szs',common)
    manifest={'version':1,
        'balloons':{'red':cook_static(common/'balloon.brres','ballon','battle_balloon_red'),
                    'blue':cook_static(common/'balloon.brres','ballon_b','battle_balloon_blue')},
        'lakituBoard':cook_static(common/'jugem_battle.brres','board_battle','lakitu_board_battle'),
        'luma':cook_animated(common/'tico.brres','tico','wait','rosalina_luma'),
        'ui':{key:convert_ui(key,archive) for key,archive in UI_ARCHIVES.items()},
        'sources':{f:digest(EXTRACTED/f) for f in ['Race/Common.szs',*UI_ARCHIVES.values()]}}
    (OUT/'models/battle.json').write_text(json.dumps(manifest,indent=1)+'\n')
    print('battle: 2 balloons, Lakitu board, Luma,',', '.join(f'{len(v)} {k} UI textures' for k,v in manifest['ui'].items()))

if __name__=='__main__':main()
