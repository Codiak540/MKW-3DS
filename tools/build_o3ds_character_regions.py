#!/usr/bin/env python3
"""Identify original character features using the authored Wii exporter's rig.

The output contains bone names, matrix numbers and hashes, never vertex data.
An entry is published only when the reconstructed source-binding fingerprint
exactly matches its existing cooked DSKM. This prevents applying guessed bone
numbers to a different character, pose-bank layout or modified source model.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

from cook_menu_models import DRIVERS
from cook_roster import ROSTER

ROOT = Path(__file__).resolve().parents[1]

# Insert a read-only inventory exit immediately after the exporter's original
# vertex/palette/attachment traversal. Reuse that traversal instead of keeping
# an independently maintained parser or inferring bone IDs from texture names.
INVENTORY = r'''
if(process.argv[3] === '--character-regions') {
 const direct = new Map<number,Map<string,number>>();
 for(const node of model.nodes) {
  if(!direct.has(node.mtxId)) direct.set(node.mtxId,new Map());
  direct.get(node.mtxId)!.set(node.name,1);
 }
 const envelopes = new Map<number,Map<string,number>>();
 for(const op of model.sceneGraph.nodeMixOps) {
  if(op.op === ByteCodeOp.EVPMTX) {
   envelopes.set(op.mtxId,new Map([[model.nodes[op.nodeId].name,1]]));
  } else {
   const contributors = new Map<string,number>();
   op.blendMtxIds.forEach((id:number,i:number)=>{
    const source=envelopes.get(id),weight=op.weights[i];
    if(!source || !Number.isFinite(weight) || weight<0) throw new Error('Invalid weighted rig contributor');
    for(const [name,w] of source) contributors.set(name,(contributors.get(name)||0)+w*weight);
   });
   direct.set(op.dstMtxId,contributors);
  }
 }
 const weights=palette.map(id=>[...(direct.get(id)||new Map<string,number>())].map(([node,weight])=>({node,weight})));
 const nodes=weights.map(row=>row.filter(x=>x.weight>1e-8).map(x=>x.node));
 const protectedMatrices=nodes.flatMap((names,i)=>names.some(n=>/(?:face|mouth|hair|cap|tang|tongue|ear|horn|hat|crown)/i.test(n))?[i]:[]);
 const prefix=Buffer.alloc(4);prefix.writeUInt32LE(palette.length);
 const source=Buffer.alloc(vertices.length*28);
 vertices.forEach((v,i)=>{for(let j=0;j<6;++j)source.writeFloatLE(v[j],i*28+j*4);source.writeUInt32LE(v[6],i*28+24);});
 const ranges=Buffer.alloc(shapeRanges.length*8);
 shapeRanges.forEach((r,i)=>{ranges.writeUInt32LE(r[0],i*8);ranges.writeUInt32LE(r[1],i*8+4);});
 console.log(JSON.stringify({vertices:vertices.length,matrices:palette.length,shapes:shapeRanges.length,
  bindingSha256:regionHash('sha256').update(prefix).update(source).update(ranges).digest('hex'),
  protectedMatrices,nodes,weights}));
 process.exit(0);
}
'''

# Same offline parser slices used by tools/bundle_cooker.mjs. Browser renderers
# and Rust support are deliberately absent from this read-only asset tool.
BUNDLE = r'''
import * as esbuild from 'esbuild';
import fs from 'node:fs';import path from 'node:path';
const request=JSON.parse(fs.readFileSync(0,'utf8'));
const marker='const attachedShapes=';
let source=fs.readFileSync('tools/export_driver.ts','utf8');
if(source.split(marker).length!==2)throw new Error('Authored exporter attachment boundary changed');
source="import {createHash as regionHash} from 'node:crypto';\n"+source.replace(marker,request.inventory+'\n'+marker);
await esbuild.build({stdin:{contents:source,resolveDir:path.resolve('tools'),loader:'ts'},bundle:true,
 platform:'node',format:'esm',outfile:request.output,plugins:[{name:'offline-parser',setup(b){
 b.onLoad({filter:/[\\/]rres[\\/]brres\.ts$/},a=>({contents:fs.readFileSync(a.path,'utf8').replace("import { GXTextureMapping }","import type { GXTextureMapping }"),loader:'ts',resolveDir:path.dirname(a.path)}));
 b.onResolve({filter:/gx_render\.js$/},a=>({path:path.resolve(a.resolveDir,a.path.replace(/\.js$/,'.ts')),namespace:'optimizer'}));
 b.onLoad({filter:/.*/,namespace:'optimizer'},a=>{const s=fs.readFileSync(a.path,'utf8');return{contents:"import * as GX from './gx_enum.js';\nimport type * as GX_Material from './gx_material.js';\n"+s.slice(s.indexOf('function autoOptimizeMaterialHasPostTexMtxBlock'),s.indexOf('\n}',s.indexOf('export function autoOptimizeMaterial('))+2),loader:'ts',resolveDir:path.dirname(a.path)};});
 b.onResolve({filter:/rustlib\.js$/},()=>({path:'unused-browser-rust',namespace:'shim'}));
 b.onLoad({filter:/.*/,namespace:'shim'},()=>({contents:'export const rust = new Proxy({}, {get(){throw new Error("Unexpected browser-only Rust dependency");}});'}));
 }}]});
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


def binding_fingerprint(data):
    if len(data) < 28 or data[:4] != b'DSKM':
        raise ValueError('Unsupported character animation bank')
    version, vertices, matrices, _, shapes = struct.unpack_from('<5I', data, 4)
    start = 32 if version == 5 else 28
    end = start + vertices * 28 + shapes * 8
    if version not in (2, 3, 4, 5) or not 0 < matrices <= 128 or not 0 < vertices <= 65535 or shapes > 64 or end > len(data):
        raise ValueError('Invalid character animation bindings')
    return digest(struct.pack('<I', matrices) + data[start:end])


def source_jobs(assets):
    for i, (slug, _, _, code, size) in enumerate(ROSTER):
        for kind in ('kart', 'bike'):
            stem = f'{slug}_{size}df_{kind}'
            yield stem, ROOT/'assets/vehicles'/stem/'driver_model.brres', assets/'models'/f'{stem}_driver.dskm'
        yield f'ui_driver_{i}', ROOT/'assets/menu-models/Driver'/f'{DRIVERS[i]}.brres', assets/'models'/f'ui_driver_{i}.dskm'
        yield f'award_{code}', ROOT/'build/ceremony/Award'/f'{code}.brres', assets/'models'/f'award_{code}.dskm'


def build(args):
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    helper = ROOT/'build/o3ds-character-region-exporter.mjs'
    helper.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['node', '--input-type=module', '-e', BUNDLE], cwd=ROOT, check=True,
                   input=json.dumps({'inventory': INVENTORY, 'output': str(helper)}), text=True)
    result = {'version': 1, 'signature': 'sha256(u32le(matrixCount) + sourceVertices28 + shapeRanges8)',
              'generatorSha256': digest(Path(__file__).read_bytes()), 'exporterSha256': digest(helper.read_bytes()),
              'entries': {}, 'skipped': []}
    for name, source, bank in source_jobs(args.assets):
        if not source.is_file() or not bank.is_file():
            result['skipped'].append({'name': name, 'reason': 'missing local source or cooked bank'})
            continue
        proc = subprocess.run(['node', str(helper), str(source), '--character-regions'], cwd=ROOT,
                              check=True, capture_output=True, text=True)
        metadata = json.loads(proc.stdout)
        fingerprint = binding_fingerprint(bank.read_bytes())
        if metadata['bindingSha256'] != fingerprint:
            raise ValueError(f'Source rig does not match existing cooked bindings: {name}')
        provenance = {'name': name, 'source': str(source.relative_to(ROOT)), 'sourceSha256': digest(source.read_bytes()),
                      'bank': bank.name, 'bankSha256': digest(bank.read_bytes())}
        entry = {key: metadata[key] for key in ('protectedMatrices', 'nodes', 'weights', 'vertices', 'matrices', 'shapes')}
        if fingerprint in result['entries']:
            existing = result['entries'][fingerprint]
            if any(existing[key] != value for key, value in entry.items()):
                raise ValueError(f'Conflicting semantics for identical bindings: {name}')
            existing['sources'].append(provenance)
        else:
            result['entries'][fingerprint] = {**entry, 'sources': [provenance]}
    if not result['entries']:
        raise ValueError('No verified original character rigs found')
    banks = list((args.assets/'models').glob('*.dskm'))
    matched = [p.name for p in banks if binding_fingerprint(p.read_bytes()) in result['entries']]
    result['coverage'] = {'sampledSources': sum(len(e['sources']) for e in result['entries'].values()),
                          'uniqueBindings': len(result['entries']), 'matchingBanks': len(matched), 'banks': matched}
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result['coverage'].items() if key != 'banks'}))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', type=Path, default=ROOT/'build/playable/3ds/mkw-3ds')
    parser.add_argument('--output', type=Path, default=ROOT/'build/o3ds-character-regions.json')
    build(parser.parse_args())


if __name__ == '__main__':
    main()
