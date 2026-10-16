#!/usr/bin/env python3
"""Reproject moved Wii prototype vertices within their original texture charts.

Diagnostic only: preserves compact topology, original textures/materials, exact
facial overlays and explicitly protected opaque triangles. No runtime changes,
new atlas, UV vertex splits, animation bank, or assets from other games.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

import numpy as np

from bake_o3ds_position_textures import closest_weights, comparison, verified_textures
from preview_o3ds_geometry import read_model, VERTEX
from preview_o3ds_positions import validate
from rebind_o3ds_positions import bank_layout, require


def sha(data):
    return hashlib.sha256(data).hexdigest()


def original_charts(model, matrices, groups):
    """Join triangles only across exact position+UV edges in one material.

    Endpoint identity additionally includes original matrix and shape membership;
    sharing only a position (a UV seam) never connects charts.
    """
    triangles=model.indices.reshape(-1,3)
    vertices=np.frombuffer(model.vertex_bytes,dtype=VERTEX)
    parent=list(range(len(triangles)))
    vertex_triangles=[[] for _ in vertices]
    materials=np.empty(len(triangles),dtype=np.int64)
    def find(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    def join(a,b):
        a,b=find(a),find(b)
        if a!=b:parent[max(a,b)]=min(a,b)
    for material,(first,count) in enumerate(model.submeshes):
        edges={}
        for ti in range(first//3,(first+count)//3):
            tri=triangles[ti];materials[ti]=material
            keys=[]
            for vi in tri:
                vi=int(vi);vertex_triangles[vi].append(ti)
                keys.append((tuple(float(x) for x in vertices['p'][vi]),
                             tuple(float(x) for x in vertices['uv'][vi,:2]),
                             int(matrices[vi]),groups[vi]))
            for a,b in ((0,1),(1,2),(2,0)):
                edge=tuple(sorted((keys[a],keys[b])))
                if edge in edges:join(ti,edges[edge])
                else:edges[edge]=ti
    labels=np.array([find(i) for i in range(len(triangles))],dtype=np.int64)
    charts={int(c):np.flatnonzero(labels==c) for c in np.unique(labels)}
    return triangles,materials,labels,charts,vertex_triangles


def protected_vertices(metadata,original,candidate):
    """Validate exact protected spans/triangles before allowing UV edits."""
    old=np.frombuffer(original.vertex_bytes,dtype=VERTEX)
    new=np.frombuffer(candidate.vertex_bytes,dtype=VERTEX)
    protected=np.zeros(len(new),bool)
    face_triangles=0
    for mi,(first,count) in enumerate(candidate.submeshes):
        tid,flags,secondary=struct.unpack_from('<iIi',candidate.materials[mi])
        locked=metadata['materials'][mi]['lockedShape']
        if locked or bool(flags&3) or tid<0 or secondary>=0:
            oldfirst,oldcount=original.submeshes[mi]
            require(count==oldcount and new[candidate.indices[first:first+count]].tobytes()==
                    old[original.indices[oldfirst:oldfirst+oldcount]].tobytes(),
                    'Protected overlay/material is not exact')
            protected[candidate.indices[first:first+count]]=True
            face_triangles+=count//3
    opaque=metadata.get('preservedOpaqueTriangles',[])
    for entry in opaque:
        mi=int(entry['material']);source_ti=int(entry['sourceTriangle']);target_ti=int(entry['candidateTriangle'])
        require(0<=mi<len(original.submeshes),'Protected material index invalid')
        oldfirst,oldcount=original.submeshes[mi];first,count=candidate.submeshes[mi]
        require(0<=source_ti<oldcount//3 and first<=target_ti*3<first+count,
                'Protected triangle outside original material')
        oi=original.indices[oldfirst+source_ti*3:oldfirst+source_ti*3+3]
        ci=candidate.indices[target_ti*3:target_ti*3+3]
        require(list(oi)==entry['sourceIndices'] and list(ci)==entry['candidateIndices'],
                'Protected triangle receipt index mismatch')
        require(any(old[oi].tobytes()==new[np.roll(ci,shift)].tobytes() for shift in range(3)),
                'Protected opaque triangle bytes or winding changed')
        protected[ci]=True
    return protected,face_triangles,len(opaque)


def run(args):
    metadata=json.loads(args.metadata.read_text())
    original,candidate=read_model(args.original),read_model(args.candidate)
    validate(metadata,original,candidate)
    require(candidate.source_indices is None,'Relocated prototypes must not carry an O3RM trailer')
    bank=args.bank.read_bytes()
    require(sha(bank)==metadata['inputs']['bankSha256'],'Original bank provenance mismatch')
    layout=bank_layout(bank);matrices=layout['sources']['matrix']
    require(len(matrices)==len(original.position),'Original bank/model vertex count mismatch')
    source=np.asarray(metadata['sourceVertexIds'],dtype=np.int64)
    require(np.array_equal(matrices[source],metadata['sourceMatrixIds']),'Source matrix receipt mismatch')
    groups=[tuple(i for i,(first,count) in enumerate(layout['ranges']) if first<=v<first+count)
            for v in range(len(original.position))]
    triangles,materials,labels,charts,vertex_triangles=original_charts(original,matrices,groups)
    protected,face_count,opaque_count=protected_vertices(metadata,original,candidate)
    vertices=np.frombuffer(candidate.vertex_bytes,dtype=VERTEX).copy()
    old=np.frombuffer(original.vertex_bytes,dtype=VERTEX)
    require(np.array_equal(vertices['uv'],old['uv'][source]),'Input must retain original source UVs')
    moved=np.any(vertices['p']!=old['p'][source],axis=1)
    candidate_materials=[set() for _ in vertices]
    for mi,(first,count) in enumerate(candidate.submeshes):
        for vi in np.unique(candidate.indices[first:first+count]):candidate_materials[vi].add(mi)
    reports=[]
    for vi in np.flatnonzero(moved&~protected):
        si=int(source[vi]);members=vertex_triangles[si]
        ids=set(int(labels[t]) for t in members)
        report=dict(vertex=int(vi),sourceVertex=si,matrix=int(matrices[si]))
        if len(ids)!=1 or len(candidate_materials[vi])!=1:
            report.update(status='unchanged-ambiguous-chart');reports.append(report);continue
        chart=next(iter(ids));mi=next(iter(candidate_materials[vi]))
        donors=[int(t) for t in charts[chart] if materials[t]==mi and
                all(matrices[x]==matrices[si] and groups[x]==groups[si] for x in triangles[t])]
        if not donors:
            report.update(status='unchanged-no-matching-rigid-chart',chart=chart);reports.append(report);continue
        best=None
        for ti in donors:
            weights,distance=closest_weights(candidate.position[[vi]],original.position[triangles[ti]])
            if weights is None:continue
            uv=weights[0]@old['uv'][triangles[ti],:2]
            score=(float(distance[0]),float(np.sum((uv-old['uv'][si,:2])**2)),ti)
            if best is None or score<best[0]:best=(score,uv,ti,weights[0])
        if best is None:
            report.update(status='unchanged-degenerate-chart',chart=chart);reports.append(report);continue
        score,uv,ti,weights=best
        displacement=float(np.linalg.norm(candidate.position[vi]-original.position[si]))
        # The source corner lies on its source surface. A farther donor than
        # that corner means our rigid-group constraint excluded that surface;
        # do not pull its UV across a remote part of a connected body chart.
        if np.sqrt(score[0])>displacement+1e-5:
            report.update(status='unchanged-no-near-rigid-chart',chart=chart,
                          rejectedSurfaceDistance=float(np.sqrt(score[0])),
                          sourceDisplacement=displacement)
            reports.append(report);continue
        require(np.isfinite(uv).all() and abs(weights.sum()-1)<1e-6 and (weights>=-1e-6).all(),
                'Invalid chart interpolation')
        vertices['uv'][vi,:2]=uv
        report.update(status='projected',chart=chart,material=int(mi),sourceTriangle=ti,
                      donorTriangleCount=len(donors),barycentric=weights.tolist(),
                      surfaceDistance=float(np.sqrt(score[0])),originalUv=old['uv'][si,:2].tolist(),
                      projectedUv=vertices['uv'][vi,:2].tolist())
        reports.append(report)
    require(vertices[protected].tobytes()==np.frombuffer(candidate.vertex_bytes,dtype=VERTEX)[protected].tobytes(),
            'Protected bytes changed during UV projection')
    require(vertices['p'].tobytes()==np.frombuffer(candidate.vertex_bytes,dtype=VERTEX)['p'].tobytes() and
            vertices['n'].tobytes()==np.frombuffer(candidate.vertex_bytes,dtype=VERTEX)['n'].tobytes(),
            'UV projection changed position/normal')
    # Replace only the existing vertex section. All prefix/index bytes stay exact.
    offset=len(candidate.data)-len(candidate.indices)*2-len(candidate.vertex_bytes)
    data=candidate.data[:offset]+vertices.tobytes()+candidate.data[offset+len(candidate.vertex_bytes):]
    args.output.mkdir(parents=True,exist_ok=True)
    path=args.output/'reprojected-diagnostic.gmdl';path.write_bytes(data)
    output=read_model(path)
    require(output.submeshes==candidate.submeshes and output.materials==candidate.materials and
            output.texture_bytes==candidate.texture_bytes and np.array_equal(output.indices,candidate.indices),
            'Reprojection changed topology, draw spans or texture table')
    _,textures,texture_proof=verified_textures(original,args.asset_root,args.texture_root)
    footer='Original atlas and draw spans; no new vertices. Diagnostic only; no hardware FPS or roster approval.'
    comparison(original,candidate,output,textures,textures,args.output/'comparison.png',
               third_label='Same compact geometry, chart UV reprojection',footer=footer)
    region_path=Path(metadata['inputs']['semanticMetadata'])
    require(sha(region_path.read_bytes())==metadata['inputs']['semanticSha256'],'Semantic metadata hash mismatch')
    regions=json.loads(region_path.read_text())['entries'].get(metadata['inputs']['bindingFingerprint'])
    if regions and regions.get('protectedMatrices'):
        mask=np.isin(matrices,regions['protectedMatrices'])
        comparison(original,candidate,output,textures,textures,args.output/'head-comparison.png',
                   [mask,mask[source],mask[source]],
                   third_label='Same compact geometry, chart UV reprojection',footer=footer)
    changed=np.any(vertices['uv']!=np.frombuffer(candidate.vertex_bytes,dtype=VERTEX)['uv'],axis=1)
    receipt=dict(version=1,source='authored-Wii-only',runtimeReady=False,visualApproved=False,
        hardwareFpsMeasured=False,original_model=str(args.original.resolve()),original_sha256=sha(original.data),
        candidate=str(args.candidate.resolve()),candidate_sha256=sha(candidate.data),
        metadata=str(args.metadata.resolve()),metadata_sha256=sha(args.metadata.read_bytes()),
        bank=str(args.bank.resolve()),bank_sha256=sha(bank),output=str(path.resolve()),output_sha256=sha(data),
        source_triangles=original.triangle_count,candidate_triangles=output.triangle_count,
        source_vertices=len(original.position),candidate_vertices=len(output.position),
        additional_vertices=0,additional_textures=0,additional_draw_spans=0,
        original_chart_count=len(charts),moved_vertices=int(moved.sum()),
        uv_changed_vertices=int(changed.sum()),protected_vertices=int(protected.sum()),
        protected_face_triangles=face_count,protected_opaque_triangles=opaque_count,
        geometry_identical=True,normals_identical=True,protected_bytes_identical=True,
        indices_identical=True,materials_and_texture_table_identical=True,textures=texture_proof,
        vertices=reports,script_sha256=sha(Path(__file__).read_bytes()),
        limitations=['Nearest source point is constrained to original position/UV-edge chart, material, matrix and shape.',
                     'Only moved, unprotected vertices have UVs changed; triangle interiors may still distort landmarks.',
                     'Original Wii texture pixels are unchanged. No animation/runtime format is produced.',
                     'No DS/MK7 geometry, textures, rig data or geometry-derived metadata used.'])
    (args.output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    output_metadata=dict(metadata,candidateSha256=sha(data),runtimeReady=False,visualApproved=False,
                         uvProjectionReceipt=str((args.output/'receipt.json').resolve()))
    (args.output/'reprojected-diagnostic.json').write_text(json.dumps(output_metadata,indent=2)+'\n')
    print(json.dumps({k:receipt[k] for k in ('output','candidate_triangles','candidate_vertices','uv_changed_vertices',
                                          'additional_vertices','additional_textures','additional_draw_spans')},indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for field in ('original','candidate','metadata','bank','output'):
        parser.add_argument('--'+field,required=True,type=Path)
    parser.add_argument('--asset-root',type=Path,default=Path('build/playable/3ds/mkw-3ds'))
    parser.add_argument('--texture-root',type=Path,action='append')
    args=parser.parse_args()
    args.texture_root=args.texture_root or [Path('sd-card/3ds/mkw-3ds'),Path('build/roster')]
    require(not any(p.resolve().is_relative_to(args.output.resolve()) for p in
                    (args.original,args.candidate,args.metadata,args.bank)),'Output must not contain source inputs')
    run(args)


if __name__=='__main__':main()
