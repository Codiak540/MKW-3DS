#!/usr/bin/env python3
"""Diagnostic Wii-only repair tiles for triangles crossing original UV charts.

Copies the original opaque atlas into a taller atlas with wrap gutters and bakes
only incompatible triangles. Keeps all draw spans; facial texture/UV animation
is untouched. No runtime assets or animation banks are generated.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

import numpy as np
from PIL import Image

from bake_o3ds_position_textures import (comparison, project, signature,
                                        verified_textures, pad)
from cook_o3ds_textures import Assets
from preview_o3ds_geometry import read_model, VERTEX
from preview_o3ds_positions import validate
from race_map_bake import Texture
from rebind_o3ds_positions import bank_layout, require
from reproject_o3ds_position_uvs import original_charts, protected_vertices


def sha(data):
    return hashlib.sha256(data).hexdigest()


def animation_audit(data, layout, shape):
    """Read already-validated DSKM sections by their structural offsets."""
    summary=layout['summary'];shapes=summary['shapes'];matrices=summary['matrices']
    cursor=layout['tail'];pat=[];uv=[]
    for kind,count,stride in [('primary',summary['clips'],matrices*84+shapes*4),
                              ('pat',struct.unpack_from('<I',data,24)[0],shapes*4)]:
        for _ in range(count):
            name=data[cursor:cursor+32].split(b'\0')[0].decode('ascii')
            count=struct.unpack_from('<I',data,cursor+40)[0];cursor+=44
            if kind=='pat':
                values=np.unique(np.frombuffer(data,'<i4',count=count*shapes,offset=cursor).reshape(count,shapes)[:,shape])
                require(np.array_equal(values,[-1]),'Opaque material has animated texture selection')
                pat.append(dict(name=name,values=values.tolist()))
            cursor+=count*stride
    if summary['version']>=3:
        nodes,clips=struct.unpack_from('<2I',data,cursor+4);cursor+=12+nodes*8
        for _ in range(clips):
            count=struct.unpack_from('<I',data,cursor+40)[0];cursor+=44+(count+1)*nodes*84
    if summary['version']>=4:
        clips=struct.unpack_from('<I',data,cursor+4)[0];cursor+=8
        for _ in range(clips):
            name=data[cursor:cursor+32].split(b'\0')[0].decode('ascii')
            count=struct.unpack_from('<I',data,cursor+40)[0];cursor+=44
            values=np.frombuffer(data,'<f4',count=count*shapes*6,offset=cursor).reshape(count,shapes,6)
            cursor+=values.nbytes
            require(np.all(values[:,shape,:]==[1,0,0,0,1,0]),'Opaque material has nonidentity UV animation')
            uv.append(dict(name=name,opaque_identity=True,frames=count))
    return dict(opaque_shape=shape,pat=pat,uv=uv,original_bank_sha256=sha(data))


def crossing_triangles(original,candidate,matrices,groups,source):
    triangles,materials,labels,charts,vertex_triangles=original_charts(original,matrices,groups)
    vertices=np.frombuffer(original.vertex_bytes,dtype=VERTEX)
    def key(i):
        return (tuple(float(x) for x in vertices['p'][i]),tuple(float(x) for x in vertices['uv'][i,:2]),
                int(matrices[i]),groups[i])
    equivalent={}
    for i in range(len(vertices)):
        equivalent.setdefault(key(i),set()).update(int(labels[t]) for t in vertex_triangles[i])
    vertex_charts=[equivalent[key(i)] for i in range(len(vertices))]
    crossings=[]
    for ti,tri in enumerate(candidate.indices.reshape(-1,3)):
        if not set.intersection(*(vertex_charts[int(si)] for si in source[tri])):crossings.append(ti)
    return crossings,triangles


def rectangle_tiles(positions,triangles,width,height):
    p=positions[triangles];area=np.linalg.norm(np.cross(p[:,1]-p[:,0],p[:,2]-p[:,0]),axis=1)*.5
    weight=np.sqrt(np.maximum(area,1e-9))
    def pack(scale):
        cells=np.maximum(8,np.ceil(weight*scale).astype(int)+6)
        if cells.max(initial=0)>min(width,height):return None
        result=[None]*len(cells);x=y=shelf=0
        for i in sorted(range(len(cells)),key=lambda i:(-cells[i],i)):
            cell=int(cells[i])
            if x+cell>width:x=0;y+=shelf;shelf=0
            if y+cell>height:return None
            result[i]=(x,y,cell);x+=cell;shelf=max(shelf,cell)
        return result
    low,high=0.,float(min(width,height)/max(weight.max(initial=1),1))
    result=pack(low);require(result is not None,'Repair rectangle too small')
    for _ in range(24):
        middle=(low+high)/2;attempt=pack(middle)
        if attempt is None:high=middle
        else:low,result=middle,attempt
    return result


def run(args):
    metadata=json.loads(args.metadata.read_text())
    original,candidate=read_model(args.original),read_model(args.candidate)
    validate(metadata,original,candidate)
    require(candidate.source_indices is None,'Relocated prototype must not have O3RM')
    bank=args.bank.read_bytes();require(sha(bank)==metadata['inputs']['bankSha256'],'Bank source mismatch')
    layout=bank_layout(bank);matrices=layout['sources']['matrix'];source=np.array(metadata['sourceVertexIds'])
    require(len(matrices)==len(original.position) and np.array_equal(matrices[source],metadata['sourceMatrixIds']),
            'Original source/matrix mapping mismatch')
    groups=[tuple(i for i,(first,count) in enumerate(layout['ranges']) if first<=v<first+count)
            for v in range(len(original.position))]
    protected,face_count,cap_count=protected_vertices(metadata,original,candidate)
    repair,original_triangles=crossing_triangles(original,candidate,matrices,groups,source)
    require(0<len(repair)<=64,'Expected bounded set of 1..64 repair triangles')
    repair_set=set(repair);triangles=candidate.indices.reshape(-1,3)
    material=0;first,count=candidate.submeshes[material]
    require(all(first//3<=t<(first+count)//3 for t in repair),'Repair crosses multiple material spans')
    protected_triangles={int(t['candidateTriangle']) for t in metadata.get('preservedOpaqueTriangles',[])}
    require(not repair_set&protected_triangles,'Repair would modify a protected cap triangle')
    tid,flags,secondary=struct.unpack_from('<iIi',candidate.materials[material])
    require(tid>=0 and secondary<0 and not flags&3,'Expected opaque single-texture material')
    require(((flags>>8)&3,(flags>>10)&3)==(1,1),'Only verified repeat/repeat sampler supported')
    original_shape=[i for i,(start,n) in enumerate(layout['ranges'])
                    if start==original.submeshes[material][0] and n==original.submeshes[material][1]]
    require(len(original_shape)==1,'Opaque material does not match one authored animation shape')
    animation=animation_audit(bank,layout,original_shape[0])
    names,textures,texture_proof=verified_textures(original,args.asset_root,args.texture_root)
    encoded=Assets(args.asset_root).read('textures/'+names[tid]+'.t3x')
    require(encoded[4]==0,'Mipmapped atlas packing requires separate mip guards')
    texture=textures[tid];pixels=np.rint(texture.levels[0]*255).astype(np.uint8)
    height,width=pixels.shape[:2]
    require((width,height)==(256,128),'Diagnostic currently supports a 256x128 original atlas')
    require(encoded[3]==12,'Expected original ETC1 texture for memory accounting')
    used=candidate.uv[candidate.indices[first:first+count]]
    require((used>=0).all() and (used<=1).all(),'Opaque UVs outside one repeat period')
    gutter=4;atlas_size=256;repair_y=height+2*gutter
    canvas=np.zeros((atlas_size,atlas_size,4),dtype=np.uint8)
    canvas[gutter:gutter+height]=pixels
    canvas[:gutter]=pixels[-gutter:]
    canvas[gutter+height:repair_y]=pixels[:gutter]
    def remap_uv(uv):
        value=np.array(uv,copy=True)
        value[...,1]=1-(gutter+(1-value[...,1])*height)/atlas_size
        return value
    original_vertices=np.frombuffer(original.vertex_bytes,dtype=VERTEX)
    candidate_vertices=np.frombuffer(candidate.vertex_bytes,dtype=VERTEX)
    donors={};oldfirst,oldcount=original.submeshes[material]
    for tri in original.indices[oldfirst:oldfirst+oldcount].reshape(-1,3):
        donors.setdefault(signature(matrices,groups,tri),[]).append(tri)
    tiles=rectangle_tiles(candidate.position,triangles[repair],width,atlas_size-repair_y)
    tile_uvs={};tile_reports=[]
    extent=max(float(np.ptp(original.position,axis=0).max()),1.)
    for ti,(x,y,cell) in zip(repair,tiles):
        tri=triangles[ti];key=signature(matrices,groups,source[tri])
        require(key in donors,'Repair triangle has no original material/matrix/shape donor')
        donor=np.array(donors[key]);local=np.array([[3,3],[cell-3,3],[3,cell-3]],float)
        xx,yy=np.meshgrid(np.arange(cell)+.5,np.arange(cell)+.5)
        b=(xx-3)/(cell-6);c=(yy-3)/(cell-6);a=1-b-c
        inside=(a>=-1e-7)&(b>=-1e-7)&(c>=-1e-7)
        weights=np.column_stack([a[inside],b[inside],c[inside]])
        colors,stats=project(weights@candidate.position[tri],weights@candidate_vertices['n'][tri],
                             original.position[donor],original_vertices['n'][donor],original.uv[donor],
                             texture,flags,extent,'nearest')
        tile=np.zeros((cell,cell,4),np.uint8);tile[inside]=colors;coverage=inside.copy();pad(tile,coverage,3)
        canvas[repair_y+y:repair_y+y+cell,x:x+cell]=tile
        pixel_uv=local+np.array([x,repair_y+y])
        tile_uvs[ti]=np.column_stack([pixel_uv[:,0]/atlas_size,1-pixel_uv[:,1]/atlas_size])
        tile_reports.append(dict(triangle=ti,cell=[x,repair_y+y,cell],source_matrix_shape_group=key,**stats))
    args.output.mkdir(parents=True,exist_ok=True)
    atlas_name='mario_wii_hybrid_main';atlas_path=args.output/(atlas_name+'.png');Image.fromarray(canvas).save(atlas_path)
    new_texture=Texture.__new__(Texture);new_texture.levels=[canvas.astype(float)/255]
    # Verify the source region at dense arbitrary UVs plus both repeat seams.
    rng=np.random.default_rng(0)
    probes=np.vstack((rng.random((20000,2)),np.array([[u,v] for u in (0,.001,.5,.999,1)
                                                  for v in (0,.000001,.5,.999999,1)])))
    sampled=texture.sample(probes[:,0],probes[:,1],1,1,0)
    mapped=remap_uv(probes);packed=new_texture.sample(mapped[:,0],mapped[:,1],1,1,0)
    maximum_error=float(np.abs(sampled-packed).max())
    require(maximum_error<1e-10,'Original atlas bilinear/wrap samples changed')
    # Retain shared vertices for compatible triangles, split only repaired
    # corners, then compact unused vertices within each authored material span.
    records=[];out_indices=[];out_source=[];out_candidate=[];spans=[]
    for mi,(start,n) in enumerate(candidate.submeshes):
        lo=len(out_indices);mapping={}
        compatible=[t for t in range(start//3,(start+n)//3) if t not in repair_set]
        for vi in sorted(set(int(v) for t in compatible for v in triangles[t])):
            record=candidate_vertices[vi].copy()
            if mi==material:record['uv'][:2]=remap_uv(record['uv'][:2])
            mapping[vi]=len(records);records.append(record);out_candidate.append(vi);out_source.append(int(source[vi]))
        for ti in range(start//3,(start+n)//3):
            if ti in repair_set:
                for vi,uv in zip(triangles[ti],tile_uvs[ti]):
                    record=candidate_vertices[vi].copy();record['uv'][:2]=uv
                    out_indices.append(len(records));records.append(record)
                    out_candidate.append(int(vi));out_source.append(int(source[vi]))
            else:out_indices.extend(mapping[int(vi)] for vi in triangles[ti])
        spans.append((lo,len(out_indices)-lo))
    records=np.array(records,dtype=VERTEX);out_indices=np.array(out_indices,dtype='<u2')
    require(len(records)<=len(candidate_vertices)+len(repair)*3,'Unexpected vertex overhead')
    header=bytearray(candidate.data[:24]);struct.pack_into('<I',header,8,len(records))
    changed_names=names[:];changed_names[tid]=atlas_name
    data=bytes(header)+b''.join(struct.pack('<2I',*span)+mat for span,mat in zip(spans,candidate.materials))
    data+=b''.join(name.encode('ascii').ljust(64,b'\0') for name in changed_names)+records.tobytes()+out_indices.tobytes()
    path=args.output/'hybrid-diagnostic.gmdl';path.write_bytes(data);output=read_model(path)
    require(np.array_equal(output.position[output.indices],candidate.position[candidate.indices]),'Geometry changed')
    for field in ('n','c'):
        require(records[field][output.indices].tobytes()==candidate_vertices[field][candidate.indices].tobytes(),field+' changed')
    require(output.submeshes==candidate.submeshes and output.materials==candidate.materials,'Draw spans changed')
    for mi in metadata['faceShapes']:
        start,n=candidate.submeshes[mi]
        require(records[output.indices[start:start+n]].tobytes()==candidate_vertices[candidate.indices[start:start+n]].tobytes(),
                'Eye/face overlay bytes changed')
    for entry in metadata.get('preservedOpaqueTriangles',[]):
        ti=int(entry['candidateTriangle']);oi=output.indices[ti*3:ti*3+3];ci=triangles[ti]
        require(np.array_equal(records['p'][oi],candidate_vertices['p'][ci]),'Cap geometry changed')
        require(np.array_equal(records['uv'][oi,:2],remap_uv(candidate_vertices['uv'][ci,:2])),'Cap atlas remap changed')
    image_textures=dict(textures);image_textures[tid]=new_texture
    footer=f'Only {len(repair)} triangles rebaked; original atlas samples preserved. Diagnostic only, no hardware FPS approval.'
    comparison(original,candidate,output,textures,image_textures,args.output/'comparison.png',
               third_label='Same geometry, selective chart repair',footer=footer)
    comparison(original,candidate,output,textures,image_textures,args.output/'review-full.png',
               third_label='Wii-only reduced model, selective texture repair',columns=(0,2),
               footer='Intentional angular style; geometry and source texture provenance verified. Gameplay approval pending.')
    region_path=Path(metadata['inputs']['semanticMetadata'])
    require(sha(region_path.read_bytes())==metadata['inputs']['semanticSha256'],'Semantic metadata changed')
    region=json.loads(region_path.read_text())['entries'].get(metadata['inputs']['bindingFingerprint'])
    if region and region.get('protectedMatrices'):
        mask=np.isin(matrices,region['protectedMatrices'])
        comparison(original,candidate,output,textures,image_textures,args.output/'head-comparison.png',
                   [mask,mask[source],mask[out_source]],third_label='Same geometry, selective chart repair',footer=footer)
        comparison(original,candidate,output,textures,image_textures,args.output/'review-head.png',
                   [mask,mask[source],mask[out_source]],columns=(0,2),
                   third_label='Wii-only reduced model, selective texture repair',
                   footer='Eye overlay and cap emblem retained; source-only texture repair. Gameplay approval pending.')
    receipt=dict(version=1,source='authored-Wii-only',runtimeReady=False,visualApproved=False,hardwareFpsMeasured=False,
        original_model=str(args.original.resolve()),original_sha256=sha(original.data),candidate=str(args.candidate.resolve()),
        candidate_sha256=sha(candidate.data),metadata=str(args.metadata.resolve()),metadata_sha256=sha(args.metadata.read_bytes()),
        bank=str(args.bank.resolve()),bank_sha256=sha(bank),output=str(path.resolve()),output_sha256=sha(data),
        atlas=str(atlas_path.resolve()),atlas_sha256=sha(atlas_path.read_bytes()),textures=texture_proof,
        source_triangles=original.triangle_count,candidate_triangles=candidate.triangle_count,
        compact_vertices=len(candidate_vertices),output_vertices=len(records),additional_vertices=len(records)-len(candidate_vertices),
        repaired_triangles=len(repair),compatible_triangles=candidate.triangle_count-len(repair),additional_textures=0,
        additional_draw_spans=0,original_texture_size=[width,height],output_texture_size=[atlas_size,atlas_size],
        original_format='ETC1',original_mipmaps=0,original_gpu_texture_bytes=width*height//2,
        hypothetical_same_format_gpu_texture_bytes=atlas_size*atlas_size//2,
        hypothetical_additional_gpu_texture_bytes=(atlas_size*atlas_size-width*height)//2,
        encoded_texture_created=False,wrap=['repeat','repeat'],gutter_rows=gutter,
        original_pixels_identical=bool(np.array_equal(canvas[gutter:gutter+height],pixels)),
        original_sampler_max_abs_error=maximum_error,original_sampler_probes=len(probes),
        primary_uv_affine=dict(v_scale=.5,v_offset=1-(gutter+height)/atlas_size),
        protected_face_triangles=face_count,protected_cap_triangles=cap_count,eye_bytes_identical=True,
        cap_geometry_and_source_sampling_identical=True,geometry_triangle_soup_identical=True,
        normals_and_colors_identical=True,draw_spans_and_materials_identical=True,animation=animation,
        sourceVertexIds=out_source,sourceMatrixIds=[int(matrices[i]) for i in out_source],
        bindingGroups=[list(groups[i]) for i in out_source],candidateVertexIds=out_candidate,
        repairs=tile_reports,script_sha256=sha(Path(__file__).read_bytes()),
        limitations=['Diagnostic PNG only; re-encoding ETC1 may change quantized colors and needs encoded-pixel validation.',
                     'Original opaque UVs remapped affinely; eye UVs and eye texture table entries unchanged.',
                     'Repairs sample original material/matrix/shape donors; new triangle interiors still need visual approval.',
                     'Selective UV duplication requires separate validated binding packaging; no runtime bank produced.',
                     'No DS/MK7 inputs used.'])
    (args.output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:receipt[k] for k in ('output','repaired_triangles','candidate_triangles','output_vertices',
                                          'additional_vertices','original_sampler_max_abs_error')},indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('original','candidate','metadata','bank','output'):parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--asset-root',type=Path,default=Path('build/playable/3ds/mkw-3ds'))
    parser.add_argument('--texture-root',type=Path,action='append')
    args=parser.parse_args();args.texture_root=args.texture_root or [Path('sd-card/3ds/mkw-3ds'),Path('build/roster')]
    require(not any(p.resolve().is_relative_to(args.output.resolve()) for p in
                    (args.original,args.candidate,args.metadata,args.bank)),'Output must not contain source inputs')
    run(args)


if __name__=='__main__':main()
