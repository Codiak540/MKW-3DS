#pragma once
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <vector>

namespace mkw {
// O3RM v2 (Old 3DS low-poly characters) may re-bake UV0 and name a re-baked
// texture "<original>_o3<tag>". Everything else stays authored.
constexpr char ModelVariantTextureSuffix[]="_o3";
inline bool modelVariantTextureName(const char* original,const char* candidate){
    const size_t length=std::strlen(original),suffix=sizeof ModelVariantTextureSuffix-1;
    const size_t total=std::strlen(candidate);
    if(total<=length+suffix||total>=64||std::strncmp(candidate,original,length)||
       std::strncmp(candidate+length,ModelVariantTextureSuffix,suffix))return false;
    for(size_t i=length+suffix;i<total;++i){
        const char c=candidate[i];
        if(!((c>='0'&&c<='9')||(c>='a'&&c<='f')))return false;
    }
    return true;
}

// The loader and host rejection fixtures share this validator. Reader supplies
// read/matches/atEnd; Allocation supplies allocate(count), allocateVertices(count),
// uint16_t* data and Vertex* vertexData. Compact candidates append an O3RM
// source-vertex map; unchanged vertex streams retain the original disk layout.
//   v1: exact retained records, strictly increasing source map.
//   v2: records equal their source except UV0, nondecreasing source map (a
//       re-baked UV seam repeats its source vertex), optional re-baked textures
//       returned in `textures` (empty name = unchanged slot).
//   v3: as v2, and a record may also move (position only; normal, UV1 and
//       colour stay authored). An "O3SP" block then gives every compact vertex
//       its bone-local skin position, returned in `skinPositions`; the vertex
//       keeps its authored bone, so animation clips are unchanged.
// All output is staging: the caller commits it only after this returns true.
template<class Reader,class Vertex,class Submesh,class Allocation>
bool readModelVariant(Reader& candidate,const uint32_t* header,size_t record,
                      const std::vector<Submesh>& originals,
                      const std::vector<std::array<char,64>>& textureNames,
                      const Vertex* vertices,const uint16_t* indices,
                      Allocation& replacement,std::vector<Submesh>& spans,uint32_t& count,
                      std::vector<uint32_t>& remap,
                      std::vector<std::array<char,64>>* textures=nullptr,uint32_t* format=nullptr,
                      std::vector<std::array<float,3>>* skinPositions=nullptr){
    static_assert(sizeof(Vertex)==44,"UV0 offset assumes the 44-byte GMDL vertex");
    constexpr size_t Position=12,Uv0=24,Uv0Bytes=8;
    uint32_t ch[6];
    if(!candidate.read(ch,sizeof ch)||std::memcmp(ch,header,2*sizeof(uint32_t))||!ch[2]||ch[2]>header[2]||
       ch[4]!=header[4]||ch[5]!=header[5]||!ch[3]||ch[3]>header[3]||ch[3]%3||
       (ch[2]==header[2]&&ch[3]==header[3]))return false;
    const bool compact=ch[2]!=header[2];
    spans.resize(originals.size());
    uint32_t end=0;
    for(size_t i=0;i<spans.size();++i){
        auto& s=spans[i];const auto& original=originals[i];
        if(!candidate.read(&s,record)||s.first!=end||s.count>ch[3]-end||s.count%3||
           s.count>original.count||(original.count&&!s.count))return false;
        auto material=s;material.first=original.first;material.count=original.count;
        if(std::memcmp(&material,&original,record))return false;
        end+=s.count;
    }
    if(end!=ch[3])return false;
    std::vector<std::array<char,64>> names(textureNames.size());
    bool renamed=false;
    for(size_t i=0;i<names.size();++i){
        if(!candidate.read(names[i].data(),64)||!std::memchr(names[i].data(),0,64))return false;
        renamed|=std::memcmp(names[i].data(),textureNames[i].data(),64)!=0;
    }
    if(compact){
        if(!replacement.allocateVertices(ch[2])||!candidate.read(replacement.vertexData,size_t(ch[2])*sizeof(Vertex)))return false;
    }else if(!candidate.matches(vertices,size_t(header[2])*sizeof(Vertex)))return false;
    if(!replacement.allocate(ch[3])||!candidate.read(replacement.data,size_t(ch[3])*sizeof(uint16_t)))return false;
    remap.clear();
    uint32_t version=0;
    if(compact){
        uint32_t mh[4];
        if(!candidate.read(mh,sizeof mh)||std::memcmp(mh,"O3RM",4)||mh[1]<1||mh[1]>3||mh[2]!=header[2]||mh[3]!=ch[2])return false;
        version=mh[1];
        remap.resize(ch[2]);
        if(!candidate.read(remap.data(),remap.size()*sizeof(uint32_t)))return false;
        for(uint32_t i=0;i<ch[2];++i){
            if(remap[i]>=header[2]||(i&&(version==1?remap[i]<=remap[i-1]:remap[i]<remap[i-1])))return false;
            const auto* a=reinterpret_cast<const uint8_t*>(replacement.vertexData+i);
            const auto* b=reinterpret_cast<const uint8_t*>(vertices+remap[i]);
            const size_t fixedFrom=version==3?Position:0;
            if(version==1?std::memcmp(a,b,sizeof(Vertex)):
               (std::memcmp(a+fixedFrom,b+fixedFrom,Uv0-fixedFrom)||
                std::memcmp(a+Uv0+Uv0Bytes,b+Uv0+Uv0Bytes,sizeof(Vertex)-Uv0-Uv0Bytes)))return false;
            if(version>=2){
                float uv[2];std::memcpy(uv,a+Uv0,sizeof uv);
                if(!(uv[0]>=-64.f&&uv[0]<=64.f&&uv[1]>=-64.f&&uv[1]<=64.f))return false;
            }
            if(version==3){
                float position[3];std::memcpy(position,a,sizeof position);
                for(const float x:position)if(!(x>=-1e6f&&x<=1e6f))return false;
            }
        }
    }
    if(version==3){
        uint32_t sh[2];
        if(!skinPositions||!candidate.read(sh,sizeof sh)||std::memcmp(sh,"O3SP",4)||sh[1]!=ch[2])return false;
        skinPositions->resize(ch[2]);
        if(!candidate.read(skinPositions->data(),size_t(ch[2])*sizeof(std::array<float,3>)))return false;
        for(const auto& p:*skinPositions)for(const float x:p)if(!(x>=-1e6f&&x<=1e6f))return false;
    }else if(skinPositions)skinPositions->clear();
    // Only v2/v3 may rename a texture, and only to its own re-baked sibling.
    if(renamed){
        if(version<2||!textures)return false;
        for(size_t i=0;i<names.size();++i)
            if(std::memcmp(names[i].data(),textureNames[i].data(),64)&&
               !modelVariantTextureName(textureNames[i].data(),names[i].data()))return false;
    }
    if(!candidate.atEnd())return false;
    for(uint32_t i=0;i<ch[3];++i)if(replacement.data[i]>=ch[2])return false;
    // Preserve authored shape/material membership, which also prevents a stale
    // candidate from borrowing an index from a different skin/animation span.
    std::vector<uint8_t> member(header[2]);
    for(size_t i=0;i<spans.size();++i){
        std::fill(member.begin(),member.end(),0);
        const auto& original=originals[i];const auto& s=spans[i];
        for(uint32_t j=original.first;j<original.first+original.count;++j)member[indices[j]]=1;
        for(uint32_t j=s.first;j<s.first+s.count;++j)
            if(!member[compact?remap[replacement.data[j]]:replacement.data[j]])return false;
    }
    if(textures){
        textures->assign(names.size(),{});
        for(size_t i=0;i<names.size();++i)
            if(std::memcmp(names[i].data(),textureNames[i].data(),64))(*textures)[i]=names[i];
    }
    if(format)*format=version;
    count=ch[3];return true;
}

// Sorted source IDs preserve every authored contiguous shape range, including
// overlapping ranges and gaps. Validate before mutating a bank so malformed
// metadata cannot partially compact its bindings. `repeats` (O3RM v2) allows a
// source vertex to appear more than once; the gather is then done out of place.
template<class SourceVertex,class Range>
bool compactModelAnimation(SourceVertex* source,uint32_t sourceCount,Range* ranges,uint32_t shapeCount,
                           const std::vector<uint32_t>& remap,bool repeats=false){
    if(remap.empty()||remap.size()>sourceCount)return false;
    for(size_t i=0;i<remap.size();++i)
        if(remap[i]>=sourceCount||(i&&(repeats?remap[i]<remap[i-1]:remap[i]<=remap[i-1])))return false;
    for(uint32_t i=0;i<shapeCount;++i)
        if(ranges[i].first>sourceCount||ranges[i].count>sourceCount-ranges[i].first)return false;
    if(repeats){
        std::vector<SourceVertex> gathered(remap.size());
        for(size_t i=0;i<remap.size();++i)gathered[i]=source[remap[i]];
        std::copy(gathered.begin(),gathered.end(),source);
    }else for(size_t i=0;i<remap.size();++i)source[i]=source[remap[i]];
    for(uint32_t i=0;i<shapeCount;++i){
        auto& range=ranges[i];
        const auto first=std::lower_bound(remap.begin(),remap.end(),range.first);
        const auto end=std::lower_bound(first,remap.end(),range.first+range.count);
        range.first=uint32_t(first-remap.begin());range.count=uint32_t(end-first);
    }
    return true;
}

// O3RM v3: moved compact vertices keep their authored bone; only the bone-local
// position changes. Empty = nothing to apply. Called after compactModelAnimation.
template<class SourceVertex>
bool applySkinPositions(SourceVertex* source,uint32_t count,const std::vector<std::array<float,3>>& positions){
    if(positions.empty())return true;
    if(positions.size()!=count)return false;
    for(uint32_t i=0;i<count;++i)for(unsigned k=0;k<3;++k)source[i].p[k]=positions[i][k];
    return true;
}
}
