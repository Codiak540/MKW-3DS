#!/usr/bin/env python3
"""Encode/decode a private Wii-only hybrid atlas and inspect actual ETC pixels.

Writes only to an explicit diagnostic output directory. The existing ETC block
codec is used as code; no MK7 cartridge, texture, geometry or metadata is read.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

import numpy as np
from PIL import Image

from bake_o3ds_position_textures import comparison
from cook_o3ds_textures import Assets, texture_layout
from devkitpro import tool
from mk7_assets import etc1_block  # Pure 64-bit ETC block decoder; no asset I/O.
from preview_o3ds_geometry import read_model
from race_map_bake import Texture
from rebind_o3ds_positions import bank_layout, require


def sha(data):
    return hashlib.sha256(data).hexdigest()


def decode(data):
    layout=texture_layout(data)
    require(layout['levels']==0,'Validation currently accepts only one mip level')
    w,h=layout['width'],layout['height'];out=np.zeros((h,w,4),np.uint8);offset=21
    for ty in range(0,h,8):
        for tx in range(0,w,8):
            for sub in range(4):
                x,y=tx+(sub&1)*4,ty+(sub>>1)*4
                alpha=None
                if layout['format']==13:
                    alpha=struct.unpack_from('<Q',data,offset)[0];offset+=8
                out[y:y+4,x:x+4,:3]=etc1_block(struct.unpack_from('<Q',data,offset)[0]);offset+=8
                for dx in range(4):
                    for dy in range(4):
                        out[y+dy,x+dx,3]=255 if alpha is None else ((alpha>>(4*(dx*4+dy)))&15)*17
    require(offset==len(data),'Unconsumed encoded texture bytes')
    # Unlike the existing BCLIM container wrapper, tex3ds's T3X row order
    # already matches its own decoded preview PNG. Do not flip it again.
    return out,layout


def raw_block(data,layout,x,y):
    require(x%4==0 and y%4==0,'ETC block coordinates must be aligned')
    stride=8 if layout['format']==12 else 16
    tile=(y//8)*(layout['width']//8)+x//8
    sub=(y%8//4)*2+x%8//4
    at=21+(tile*4+sub)*stride
    return data[at:at+stride]


def encode(source,destination,preview,fmt):
    command=[str(tool('tex3ds')),'-f',fmt,'-q','high','-z','none',
             '-o',str(destination),'-p',str(preview),str(source)]
    subprocess.run(command,check=True,capture_output=True)
    pixels,layout=decode(destination.read_bytes())
    expected=np.asarray(Image.open(preview).convert('RGBA'))
    require(np.array_equal(pixels,expected),'Local codec disagrees with tex3ds decoded preview')
    return destination.read_bytes(),pixels,layout,command


def error_stats(a,b):
    diff=np.abs(a.astype(float)-b.astype(float))
    mse=float(np.mean(diff**2))
    return dict(mean_abs=float(diff.mean()),max_abs=float(diff.max()),
                percentile99=float(np.percentile(diff,99)),
                changed_pixels=int(np.any(diff,axis=-1).sum()),
                psnr_db=None if not mse else float(10*np.log10(255*255/mse)))


def as_texture(pixels):
    t=Texture.__new__(Texture);t.levels=[pixels.astype(float)/255]
    return t


def run(args):
    receipt=json.loads(args.receipt.read_text())
    require(receipt['source']=='authored-Wii-only' and not receipt['runtimeReady'],'Expected private Wii hybrid receipt')
    for pathkey,hashkey in [('original_model','original_sha256'),('candidate','candidate_sha256'),
                           ('bank','bank_sha256'),('output','output_sha256'),('atlas','atlas_sha256')]:
        require(sha(Path(receipt[pathkey]).read_bytes())==receipt[hashkey],'Hybrid source changed: '+pathkey)
    original=read_model(receipt['original_model']);candidate=read_model(receipt['candidate']);hybrid=read_model(receipt['output'])
    metadata=json.loads(Path(receipt['metadata']).read_text())
    require(sha(Path(receipt['metadata']).read_bytes())==receipt['metadata_sha256'],'Prototype metadata changed')
    assets=Assets(args.asset_root);args.output.mkdir(parents=True,exist_ok=True)
    names=[original.texture_bytes[i:i+64].split(b'\0')[0].decode('ascii') for i in range(0,len(original.texture_bytes),64)]
    tid=struct.unpack_from('<i',original.materials[0])[0]
    base_textures={};source_reports=[];source_encoded={};source_layout={};source_pixels={}
    for source in receipt['textures']:
        index=names.index(source['name']);data=assets.read('textures/'+source['name']+'.t3x')
        require(sha(data)==source['staged_t3x_sha256'],'Staged original texture changed')
        png=Path(source['png']);require(sha(png.read_bytes())==source['png_sha256'],'Source PNG changed')
        decoded,layout=decode(data)
        # Cross-check this local codec against tex3ds for both ETC1 and ETC1A4;
        # compare re-encoding with the actual staged asset separately.
        new,pixels,_,command=encode(png,args.output/(source['name']+'-verification.t3x'),
                                    args.output/(source['name']+'-tex3ds.png'),
                                    'etc1a4' if layout['format']==13 else 'etc1')
        Image.fromarray(decoded).save(args.output/(source['name']+'-decoded.png'))
        base_textures[index]=as_texture(decoded);source_encoded[index]=data;source_layout[index]=layout;source_pixels[index]=decoded
        source_reports.append(dict(name=source['name'],index=index,encoded_sha256=sha(data),
                                   encoder_reproduces_staged_bytes=(new==data),codec_matches_encoder_preview=True,
                                   source_png_rgb_error=error_stats(np.asarray(Image.open(png).convert('RGBA'))[:,:,:3],decoded[:,:,:3]),
                                   layout=layout,verification_command=command))
    require(tid in base_textures,'Main material texture missing')
    dest=args.output/'hybrid-main.t3x';preview=args.output/'hybrid-main-tex3ds.png'
    encoded,pixels,layout,command=encode(Path(receipt['atlas']),dest,preview,'etc1')
    Image.fromarray(pixels).save(args.output/'hybrid-main-decoded.png')
    old=source_pixels[tid];old_layout=source_layout[tid]
    gutter=int(receipt['gutter_rows']);h,w=old.shape[:2]
    copied=pixels[gutter:gutter+h]
    area_error=error_stats(old,copied)
    require(area_error['changed_pixels']==0,'Original decoded atlas area changed during encoding')
    require(np.array_equal(pixels[:gutter],old[-gutter:]) and
            np.array_equal(pixels[gutter+h:gutter*2+h],old[:gutter]),'Decoded wrap gutter changed')
    blocks=[]
    for y in range(0,h,4):
        for x in range(0,w,4):
            blocks.append(raw_block(source_encoded[tid],old_layout,x,y)==raw_block(encoded,layout,x,y+gutter))
    require(all(blocks),'Copied source ETC blocks were re-encoded differently')
    packed_texture=as_texture(pixels);hybrid_textures=dict(base_textures);hybrid_textures[tid]=packed_texture
    rng=np.random.default_rng(0);probes=np.vstack((rng.random((20000,2)),
        np.array([[u,v] for u in (0,.001,.5,.999,1) for v in (0,.000001,.5,.999999,1)])))
    mapped=probes.copy();affine=receipt['primary_uv_affine']
    mapped[:,1]=affine['v_scale']*mapped[:,1]+affine['v_offset']
    old_samples=base_textures[tid].sample(probes[:,0],probes[:,1],1,1,0)
    new_samples=packed_texture.sample(mapped[:,0],mapped[:,1],1,1,0)
    maximum_sampler_error=float(np.abs(old_samples-new_samples).max())
    require(maximum_sampler_error<1e-10,'Decoded original-region repeat/bilinear parity failed')
    footer='Decoded ETC1 pixels; original atlas blocks and eyes preserved. Software diagnostic; hardware FPS pending.'
    comparison(original,candidate,hybrid,base_textures,hybrid_textures,args.output/'review-full-encoded.png',
               columns=(0,2),third_label='Reduced Wii model, decoded ETC1 textures',footer=footer)
    layout_bank=bank_layout(Path(receipt['bank']).read_bytes());matrices=layout_bank['sources']['matrix']
    regions=json.loads(Path(metadata['inputs']['semanticMetadata']).read_text())
    region=regions['entries'][metadata['inputs']['bindingFingerprint']];mask=np.isin(matrices,region['protectedMatrices'])
    comparison(original,candidate,hybrid,base_textures,hybrid_textures,args.output/'review-head-encoded.png',
               [mask,mask[metadata['sourceVertexIds']],mask[receipt['sourceVertexIds']]],columns=(0,2),
               third_label='Reduced Wii model, decoded ETC1 textures',footer=footer)
    input_pixels=np.asarray(Image.open(receipt['atlas']).convert('RGBA'))
    repair_error=error_stats(input_pixels[h+gutter*2:,:,:3],pixels[h+gutter*2:,:,:3])
    # Count error only in actual used repair triangle interiors as well as the
    # whole rectangle (which also contains unused padding/black pixels).
    used_mask=np.zeros(pixels.shape[:2],bool)
    for repair in receipt['repairs']:
        x,y,cell=repair['cell'];xx,yy=np.meshgrid(np.arange(cell)+.5,np.arange(cell)+.5)
        b=(xx-3)/(cell-6);c=(yy-3)/(cell-6)
        used_mask[y:y+cell,x:x+cell]|=(b>=0)&(c>=0)&(b+c<=1)
    used_error=error_stats(input_pixels[used_mask,:3],pixels[used_mask,:3])
    result=dict(version=1,source='authored-Wii-only',runtimeReady=False,hardwareFpsMeasured=False,
        visualApproved=False,hybrid_receipt=str(args.receipt.resolve()),hybrid_receipt_sha256=sha(args.receipt.read_bytes()),
        source_model_sha256=receipt['original_sha256'],hybrid_model_sha256=receipt['output_sha256'],
        output_texture=str(dest.resolve()),output_texture_sha256=sha(encoded),encoder_command=command,
        encoder_version=subprocess.run([str(tool('tex3ds')),'--version'],capture_output=True,text=True,check=True).stdout.splitlines()[0],
        encoder_sha256=sha(tool('tex3ds').read_bytes()),layout=layout,original_layout=old_layout,
        actual_additional_gpu_bytes=layout['gpu_bytes']-old_layout['gpu_bytes'],
        codec='Existing pure ETC block routine; T3X container decoder cross-checked against tex3ds preview for ETC1 and ETC1A4.',
        codec_source_sha256=sha((Path(__file__).parent/'mk7_assets.py').read_bytes()),
        original_decoded_area_error=area_error,original_etc_blocks_identical=sum(blocks),original_etc_blocks_total=len(blocks),
        decoded_wrap_gutters_identical=True,decoded_sampler_probes=len(probes),decoded_sampler_max_abs_error=maximum_sampler_error,
        repair_rectangle_rgb_error=repair_error,used_repair_texel_rgb_error=used_error,original_textures=source_reports,
        eye_texture_bytes_unchanged=True,main_uv_animation_identity=receipt['animation'],script_sha256=sha(Path(__file__).read_bytes()),
        limitations=['Actual encoded texels rendered through diagnostic software sampler, not the PICA game renderer.',
                     'Model geometry and animation remain separate unapproved experiments; no runtime texture staged.',
                     'Only existing Wii assets and generic codec code read; no MK7/DS assets used.'])
    (args.output/'encoded-validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('output_texture','actual_additional_gpu_bytes',
                                          'original_etc_blocks_identical','decoded_sampler_max_abs_error',
                                          'used_repair_texel_rgb_error')},indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--asset-root',default=Path('build/playable/3ds/mkw-3ds'),type=Path)
    args=parser.parse_args()
    require(not args.receipt.resolve().is_relative_to(args.output.resolve()),'Output must not contain original receipt')
    run(args)


if __name__=='__main__':main()
