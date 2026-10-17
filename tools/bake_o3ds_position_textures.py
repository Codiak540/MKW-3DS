#!/usr/bin/env python3
"""Wii-only diagnostic texture projection for relocated geometry prototypes.

Writes raw, non-runtime GMDL plus private PNG atlases and comparisons. It never
loads DS/MK7 assets, replaces source files, or changes animation/runtime code.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from cook_o3ds_textures import Assets, SAFE
from preview_o3ds_geometry import (read_model, Model, VERTEX, VIEWS,
                                  camera_projection, render_panel)
from preview_o3ds_positions import validate
from race_map_bake import Texture
from rebind_o3ds_positions import bank_layout, require


def sha(data):
    return hashlib.sha256(data).hexdigest()


def verified_textures(model, asset_root, roots):
    names = [model.texture_bytes[i:i+64].split(b'\0')[0].decode('ascii')
             for i in range(0, len(model.texture_bytes), 64)]
    used = {struct.unpack_from('<i', m)[0] for m, (_, n) in zip(model.materials, model.submeshes) if n}
    assets, textures, proof = Assets(asset_root), {}, []
    for index in sorted(used-{-1}):
        require(0 <= index < len(names) and SAFE.fullmatch(names[index]), 'Unsafe texture name')
        name = names[index]
        encoded = assets.read('textures/'+name+'.t3x')
        source = None
        for root in roots:
            png = root/'textures'/(name+'.png')
            t3x = png.with_suffix('.t3x')
            if png.is_file() and t3x.is_file() and t3x.read_bytes() == encoded:
                source = png
                break
        require(source is not None, 'No PNG matching exact staged T3X: '+name)
        with Image.open(source) as im:
            pixels = np.asarray(im.convert('RGBA'), dtype=np.float64)/255
        texture = Texture.__new__(Texture)
        texture.levels = [pixels]
        textures[index] = texture
        proof.append(dict(name=name, png=str(source.resolve()), png_sha256=sha(source.read_bytes()),
                          staged_t3x_sha256=sha(encoded), size=[pixels.shape[1], pixels.shape[0]]))
    return names, textures, proof


def signature(bones, groups, tri):
    return tuple(sorted(set((int(bones[i]), groups[i]) for i in tri)))


def closest_weights(points, triangle):
    """Exact closest point on a triangle: interior projection or one of 3 edges."""
    edges = triangle[1:]-triangle[0]
    gram = edges@edges.T
    if abs(np.linalg.det(gram)) < 1e-16:
        return None, None
    uv = (points-triangle[0])@edges.T@np.linalg.inv(gram)
    weights = np.column_stack((1-uv.sum(1), uv))
    inside = (weights >= 0).all(1)
    distance = np.where(inside, np.sum((points-weights@triangle)**2, axis=1), np.inf)
    for a,b in ((0,1),(1,2),(2,0)):
        edge = triangle[b]-triangle[a]
        denominator = edge@edge
        if denominator < 1e-16:
            continue
        t = np.clip((points-triangle[a])@edge/denominator,0,1)
        candidate = np.zeros_like(weights)
        candidate[:,a], candidate[:,b] = 1-t,t
        score = np.sum((points-candidate@triangle)**2,axis=1)
        take = score < distance
        weights[take],distance[take] = candidate[take],score[take]
    return weights,distance


def project(points, normals, triangles, source_normals, source_uv, texture, flags, extent, surface='nearest'):
    """Bidirectional normal rays, then constrained nearest-surface fallback."""
    length = np.linalg.norm(normals,axis=1,keepdims=True)
    normal = normals/np.maximum(length,1e-12)
    best = np.full(len(points),np.inf)
    chosen = np.full(len(points),-1)
    bary = np.zeros((len(points),3))
    ray_distance = extent*.35
    origin = points+normal*ray_distance
    direction = -normal
    donor_normals=source_normals.mean(1)
    donor_normals/=np.maximum(np.linalg.norm(donor_normals,axis=1,keepdims=True),1e-12)
    for ti,t in enumerate(triangles):
        e1,e2 = t[1]-t[0],t[2]-t[0]
        h = np.cross(direction,e2)
        det = h@e1
        inverse = np.divide(1.,det,out=np.zeros_like(det),where=np.abs(det)>1e-10)
        delta = origin-t[0]
        u = np.einsum('ij,ij->i',delta,h)*inverse
        q = np.cross(delta,e1)
        v = np.einsum('ij,ij->i',direction,q)*inverse
        travel = q@e2*inverse
        score=travel if surface=='visible' else abs(travel-ray_distance)
        facing=normal@donor_normals[ti]>.1
        hit = (facing&(abs(det)>1e-10)&(u>=-1e-7)&(v>=-1e-7)&(u+v<=1+1e-7)&
               (travel>=0)&(travel<=2*ray_distance)&(score<best))
        chosen[hit],best[hit] = ti,score[hit]
        bary[hit] = np.column_stack((1-u-v,u,v))[hit]
    missing = np.flatnonzero(chosen<0)
    for ti,t in enumerate(triangles):
        if not len(missing):
            break
        weights,distance = closest_weights(points[missing],t)
        if weights is None:
            continue
        distance = np.sqrt(distance)
        distance=np.where(normal[missing]@donor_normals[ti]>.1,distance,np.inf)
        take = distance < best[missing]
        select = missing[take]
        chosen[select],best[select],bary[select] = ti,distance[take],weights[take]
    # A pathological normal cannot justify sampling an unrelated material or
    # rig group. Last fallback stays within the supplied, verified donor set.
    last=np.flatnonzero(chosen<0)
    for ti,t in enumerate(triangles):
        if not len(last):break
        weights,distance=closest_weights(points[last],t)
        if weights is None:continue
        distance=np.sqrt(distance);take=distance<best[last];select=last[take]
        chosen[select],best[select],bary[select]=ti,distance[take],weights[take]
    require((chosen>=0).all(), 'No nondegenerate source triangle for projection')
    uv = np.einsum('ni,nij->nj',bary,source_uv[chosen])
    rgba = texture.sample(uv[:,0],uv[:,1],(flags>>8)&3,(flags>>10)&3,0)
    distances=np.linalg.norm(points-np.einsum('ni,nij->nj',bary,triangles[chosen]),axis=1)
    return np.clip(rgba*255,0,255).astype(np.uint8),dict(
        fallback_samples=int(len(missing)), maximum_distance=float(distances.max(initial=0)))


def pad(canvas, coverage, count=2):
    for _ in range(count):
        old,pixels = coverage.copy(),canvas.copy()
        for axis,direction in ((0,1),(0,-1),(1,1),(1,-1)):
            take = np.roll(old,direction,axis)&~coverage
            if axis == 0:
                take[0 if direction==1 else -1,:] = False
            else:
                take[:,0 if direction==1 else -1] = False
            canvas[take] = np.roll(pixels,direction,axis)[take]
            coverage[take] = True


def triangle_tiles(positions, triangles, size):
    """Spend atlas pixels on geometric area instead of tiny triangles equally."""
    p=positions[triangles]
    area=np.linalg.norm(np.cross(p[:,1]-p[:,0],p[:,2]-p[:,0]),axis=1)*.5
    weight=np.sqrt(np.maximum(area,1e-9))
    def pack(scale):
        cells=np.maximum(7,np.ceil(weight*scale).astype(int)+4)
        if cells.max(initial=0)>size:return None
        result=[None]*len(cells);x=y=shelf=0
        for i in sorted(range(len(cells)),key=lambda i:(-cells[i],i)):
            cell=int(cells[i])
            if x+cell>size:x=0;y+=shelf;shelf=0
            if y+cell>size:return None
            result[i]=(x,y,cell);x+=cell;shelf=max(shelf,cell)
        return result
    low,high=0.,float(size/max(weight.max(initial=1),1))
    result=pack(low)
    require(result is not None,'Atlas too small for protected per-triangle padding')
    for _ in range(24):
        middle=(low+high)/2;attempt=pack(middle)
        if attempt is None:high=middle
        else:low,result=middle,attempt
    return result


def comparison(original, candidate, baked, textures, baked_textures, output, focus=None,
               third_label='Same geometry, projected Wii texture',
               footer='Atlas introduces UV seams/vertex duplication. Triangle counts do not establish hardware FPS gains.',
               columns=(0,1,2)):
    models = [original,candidate,baked]
    if focus is not None:
        filtered=[]
        for model,keep in zip(models,focus):
            indices=[];spans=[]
            for first,count in model.submeshes:
                triangles=model.indices[first:first+count].reshape(-1,3)
                triangles=triangles[np.all(keep[triangles],axis=1)]
                spans.append((len(indices),triangles.size));indices.extend(triangles.ravel())
            filtered.append(Model(model.path,model.data,model.version,model.position,model.color,
                                  np.array(indices),spans,model.materials,model.texture_bytes,
                                  model.vertex_bytes,uv=model.uv,alpha=model.alpha))
        models=filtered
    extent=models[0].position[np.unique(models[0].indices)]
    center=(extent.min(0)+extent.max(0))/2
    canvas=Image.new('RGB',(24+416*len(columns),1000),(12,17,26));draw=ImageDraw.Draw(canvas)
    small=ImageFont.load_default(size=13);font=ImageFont.load_default(size=17)
    draw.text((20,15),'Wii-only geometry / Wii texture projection experiment',font=font,fill='white')
    draw.text((20,43),'Original textures and DSKM bindings only; facial overlay kept exact. Diagnostic, not runtime-ready.',font=small,fill='#ffc767')
    labels=['Original Wii','Relocated geometry, inherited UV',third_label]
    for row,(view,yaw,pitch) in enumerate(VIEWS):
        bounds=camera_projection(extent,center,yaw,pitch)
        scale=min(365/np.ptp(bounds[:,0]),220/np.ptp(bounds[:,1]))
        for col,model_index in enumerate(columns):
            model=models[model_index]
            x,y=20+416*col,85+row*286
            draw.text((x,y),labels[model_index],font=small,fill='white')
            draw.text((x,y+18),f'{len(model.indices)//3} triangles',font=small,fill='#bccbd8')
            projection=camera_projection(model.position,center,yaw,pitch)
            canvas.paste(render_panel(model,projection,scale,'textured',baked_textures if model_index==2 else textures),(x,y+36))
            draw.text((x+9,y+45),view,font=small,fill='#bccbd8')
    draw.text((20,970),footer,font=small,fill='#ffc767')
    canvas.save(output)


def bake(args):
    metadata=json.loads(args.metadata.read_text())
    original,candidate=read_model(args.original),read_model(args.candidate)
    validate(metadata,original,candidate)
    require(metadata.get('source')=='authored-Wii-only','Only Wii-authored prototype inputs accepted')
    bank=args.bank.read_bytes()
    require(sha(bank)==metadata['inputs']['bankSha256'],'Original DSKM provenance mismatch')
    layout=bank_layout(bank)
    require(len(layout['sources'])==len(original.position),'Bank/model count mismatch')
    source_ids=np.asarray(metadata['sourceVertexIds'],dtype=np.int64)
    bones=layout['sources']['matrix']
    groups=[tuple(i for i,(first,count) in enumerate(layout['ranges']) if first<=v<first+count)
            for v in range(len(original.position))]
    require(np.array_equal(metadata['sourceMatrixIds'],bones[source_ids]),'Matrix mapping mismatch')
    names,textures,texture_proof=verified_textures(original,args.asset_root,args.texture_root)
    source_vertices=np.frombuffer(candidate.vertex_bytes,dtype=VERTEX)
    original_vertices=np.frombuffer(original.vertex_bytes,dtype=VERTEX)
    record_size={3:44,4:56,5:92}[candidate.version]
    records=[];spans=[];new_materials=[];new_names=names[:];atlas_paths=[];expanded_source=[]
    image_textures=dict(textures);reports=[]
    extent=max(float(np.ptp(original.position,axis=0).max()),1.)
    original_faces=set(metadata.get('faceShapes',[]))
    face_ids=[]
    for face in original_faces:
        at,n=original.submeshes[face];face_ids.extend(original.indices[at:at+n])
    face_ids=np.array(face_ids,dtype=np.int64)
    facial_matrices=set(int(x) for x in bones[face_ids])
    front=original_vertices['n'][face_ids].sum(0).astype(float) if len(face_ids) else np.zeros(3)
    front/=max(float(np.linalg.norm(front)),1e-12)
    args.output.mkdir(parents=True,exist_ok=True)
    for mi,((first,count),material) in enumerate(zip(candidate.submeshes,candidate.materials)):
        triangles=candidate.indices[first:first+count].reshape(-1,3)
        oldfirst,oldcount=original.submeshes[mi]
        original_triangles=original.indices[oldfirst:oldfirst+oldcount].reshape(-1,3)
        tid,flags,secondary=struct.unpack_from('<iIi',material)
        protected=mi in original_faces or bool(flags&3) or tid<0 or secondary>=0
        first_new=len(records)
        output_material=bytearray(material)
        row=dict(material=mi,triangles=len(triangles),protected=protected,
                 fallback_samples=0,maximum_projection_distance=0.,direct_mixed_samples=0,
                 front_landmark_projection_samples=0)
        if protected:
            for tri in triangles:
                records.extend(source_vertices[tri].copy())
                expanded_source.extend(source_ids[tri])
            require(count==oldcount and source_vertices[candidate.indices[first:first+count]].tobytes()==
                    np.frombuffer(original.vertex_bytes,dtype=VERTEX)[original.indices[oldfirst:oldfirst+oldcount]].tobytes(),
                    'Protected overlay/material was modified')
        else:
            donors={}
            for tri in original_triangles:
                donors.setdefault(signature(bones,groups,tri),[]).append(tri)
            tiles=triangle_tiles(candidate.position,triangles,args.atlas_size)
            canvas=np.zeros((args.atlas_size,args.atlas_size,4),np.uint8)
            coverage=np.zeros(canvas.shape[:2],bool)
            for ti,tri in enumerate(triangles):
                key=signature(bones,groups,source_ids[tri])
                require(key in donors,'No source material/matrix/shape group')
                source=np.array(donors[key])
                x,y,cell=tiles[ti]
                atlas=np.array([[x+2,y+2],[x+cell-2,y+2],[x+2,y+cell-2]],dtype=float)
                xx,yy=np.meshgrid(np.arange(x+2,x+cell-2)+.5,np.arange(y+2,y+cell-2)+.5)
                wb=(xx-atlas[0,0])/(cell-4);wc=(yy-atlas[0,1])/(cell-4);wa=1-wb-wc
                inside=wa>=-1e-7
                weights=np.column_stack([wa[inside],wb[inside],wc[inside]])
                if len(key)>1:
                    # Deforming boundaries were locked by the prototype. Their
                    # inherited authored UV interpolation is already exact.
                    uv=weights@candidate.uv[tri]
                    rgba=np.clip(textures[tid].sample(uv[:,0],uv[:,1],(flags>>8)&3,(flags>>10)&3,0)*255,0,255).astype(np.uint8)
                    row['direct_mixed_samples']+=len(weights)
                else:
                    points=weights@candidate.position[tri]
                    normals=weights@source_vertices['n'][tri]
                    # Derive one consistent frontal projector from the exact
                    # Wii facial overlay. This prevents a moved cap vertex's
                    # changed normal from dragging the emblem across the cap.
                    if args.projector=='front-landmarks' and key[0][0] in facial_matrices and np.linalg.norm(front)>.5:
                        # Include upward cap planes: coarse normals can tilt
                        # away from the face while the emblem remains frontal.
                        frontal=normals@front>-.15
                        normals[frontal]=front
                        row['front_landmark_projection_samples']+=int(frontal.sum())
                    rgba,stats=project(points,normals,original.position[source],original_vertices['n'][source],original.uv[source],textures[tid],flags,extent,args.surface)
                    row['fallback_samples']+=stats['fallback_samples']
                    row['maximum_projection_distance']=max(row['maximum_projection_distance'],stats['maximum_distance'])
                region=canvas[y+2:y+cell-2,x+2:x+cell-2]
                region[inside]=rgba
                coverage[y+2:y+cell-2,x+2:x+cell-2][inside]=True
                vertices=source_vertices[tri].copy()
                vertices['uv'][:,:2]=np.column_stack([atlas[:,0]/args.atlas_size,1-atlas[:,1]/args.atlas_size])
                records.extend(vertices);expanded_source.extend(source_ids[tri])
            pad(canvas,coverage,2)
            atlas_name=f'position_bake_{mi:02d}_{args.atlas_size}'
            atlas_path=args.output/(atlas_name+'.png')
            Image.fromarray(canvas).save(atlas_path);atlas_paths.append(atlas_path)
            new_id=len(new_names);new_names.append(atlas_name)
            struct.pack_into('<i',output_material,0,new_id)
            texture=Texture.__new__(Texture);texture.levels=[canvas.astype(float)/255];image_textures[new_id]=texture
            row.update(atlas=str(atlas_path),atlas_size=args.atlas_size,
                       tile_min_cell=min(t[2] for t in tiles),tile_max_cell=max(t[2] for t in tiles),
                       tile_allocation='square-root geometric area with protected padding',
                       atlas_sha256=sha(atlas_path.read_bytes()))
        spans.append((first_new,len(records)-first_new));new_materials.append(bytes(output_material));reports.append(row)
    records=np.array(records,dtype=VERTEX)
    require(len(records)<=65535,'Diagnostic triangle-soup vertex limit exceeded')
    header=bytearray(candidate.data[:24]);struct.pack_into('<4I',header,8,len(records),len(records),len(spans),len(new_names))
    materials=b''.join(struct.pack('<2I',first,count)+material for (first,count),material in zip(spans,new_materials))
    texture_names=b''.join(n.encode('ascii').ljust(64,b'\0') for n in new_names)
    encoded=bytes(header)+materials+texture_names+records.tobytes()+np.arange(len(records),dtype='<u2').tobytes()
    destination=args.output/'projected-diagnostic.gmdl';destination.write_bytes(encoded)
    baked=read_model(destination)
    require(np.array_equal(baked.position,candidate.position[candidate.indices]),'Projection changed geometry')
    require(records['n'].tobytes()==source_vertices['n'][candidate.indices].tobytes(),'Projection changed normals')
    comparison(original,candidate,baked,textures,image_textures,args.output/'comparison.png')
    region_path=Path(metadata['inputs']['semanticMetadata'])
    regions=json.loads(region_path.read_text())
    region=regions['entries'].get(metadata['inputs']['bindingFingerprint'])
    if region and region.get('protectedMatrices'):
        head_bones=region['protectedMatrices']
        masks=[np.isin(bones,head_bones),np.isin(bones[source_ids],head_bones),np.isin(bones[np.array(expanded_source)],head_bones)]
        comparison(original,candidate,baked,textures,image_textures,args.output/'head-comparison.png',masks)
    receipt=dict(version=1,source='authored-Wii-only',runtimeReady=False,hardwareFpsMeasured=False,
        original_model=str(args.original.resolve()),original_sha256=sha(original.data),
        candidate=str(args.candidate.resolve()),candidate_sha256=sha(candidate.data),
        bank=str(args.bank.resolve()),bank_sha256=sha(bank),metadata_sha256=sha(args.metadata.read_bytes()),
        geometry_metadata=str(args.metadata.resolve()), inputs=metadata['inputs'],
        textures=texture_proof,output=str(destination),output_sha256=sha(encoded),
        projector=args.projector,surface_selection=args.surface,
        source_triangles=original.triangle_count,candidate_triangles=candidate.triangle_count,
        candidate_vertices=len(candidate.position),atlas_vertices=len(records),
        sourceVertexIds=[int(i) for i in expanded_source],
        sourceMatrixIds=[int(bones[i]) for i in expanded_source],
        bindingGroups=[list(groups[i]) for i in expanded_source],
        candidateVertexIds=candidate.indices.tolist(),
        material_change_contract='Original texture-table prefix retained; opaque primary texture index replaced by appended Wii-derived atlas; all other material bytes and protected span bytes unchanged.',
        geometry_triangle_soup_identical=True,normals_identical=True,protected_overlay_bytes_identical=True,
        materials=reports,script_sha256=sha(Path(__file__).read_bytes()),
        limitations=['Diagnostic per-triangle UV atlas adds vertex duplication; no runtime bank is generated.',
                     'Projection is constrained to original material, skin matrix and shape group; source atlas pixels may be bilinearly resampled.',
                     'Ray misses use nearest triangles within the same group; inspect texture landmarks and seams.',
                     'No DS/MK7 input geometry, textures or rig data are used.'])
    (args.output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original',required=True,type=Path)
    parser.add_argument('--candidate',required=True,type=Path)
    parser.add_argument('--metadata',required=True,type=Path)
    parser.add_argument('--bank',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--atlas-size',type=int,choices=(256,512),default=256)
    parser.add_argument('--projector',choices=('normal','front-landmarks'),default='normal')
    parser.add_argument('--surface',choices=('nearest','visible'),default='nearest')
    parser.add_argument('--asset-root',type=Path,default=Path('build/playable/3ds/mkw-3ds'))
    parser.add_argument('--texture-root',type=Path,action='append')
    args=parser.parse_args()
    args.texture_root=args.texture_root or [Path('sd-card/3ds/mkw-3ds'),Path('build/roster')]
    inputs=[args.original,args.candidate,args.metadata,args.bank]
    require(not any(p.resolve().is_relative_to(args.output.resolve()) for p in inputs),'Output must not contain source inputs')
    receipt=bake(args)
    print(json.dumps({k:receipt[k] for k in ('output','candidate_triangles','candidate_vertices','atlas_vertices')},indent=2))


if __name__=='__main__':
    main()
