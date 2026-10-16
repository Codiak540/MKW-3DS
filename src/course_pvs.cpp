#include <mkw/course_pvs.hpp>
#include <mkw/render_metrics.hpp>
#include <mkw/renderer.hpp>
#include <mkw/visibility.hpp>
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <string>

namespace mkw {
namespace {
constexpr uint32_t NoSet=0xffffffffu;
struct Header {
    char magic[4];uint32_t version,fingerprint,vertexCount,indexCount,submeshCount,blockTriangles,blockCount;
    float originX,originZ,cell;uint32_t columnsX,columnsZ,setCount,setBytes;
};
static_assert(sizeof(Header)==60,"MPVS header");
uint32_t fnv(uint32_t h,uint32_t word){return (h^word)*0x01000193u;}
// tools/pvs/gmdl.py fingerprint(): counts, submesh spans, indices as
// little-endian u16 pairs (zero padded), position float bits.
uint32_t fingerprint(const Model& m){
    uint32_t h=0x811c9dc5u;
    h=fnv(h,m.vertexCount);h=fnv(h,m.indexCount);h=fnv(h,uint32_t(m.submeshes.size()));
    for(const auto& s:m.submeshes){h=fnv(h,s.first);h=fnv(h,s.count);}
    for(uint32_t i=0;i<m.indexCount;i+=2)h=fnv(h,uint32_t(m.indices[i])|(i+1<m.indexCount?uint32_t(m.indices[i+1])<<16:0u));
    for(uint32_t i=0;i<m.vertexCount;++i){
        uint32_t p[3];std::memcpy(p,&m.vertices[i].position,sizeof p);
        h=fnv(h,p[0]);h=fnv(h,p[1]);h=fnv(h,p[2]);
    }
    return h;
}
bool readAll(const char* path,std::vector<uint8_t>& out){
    FILE* f=std::fopen(path,"rb");if(!f)return false;
    std::fseek(f,0,SEEK_END);long size=std::ftell(f);std::fseek(f,0,SEEK_SET);
    bool ok=size>=long(sizeof(Header))&&size<(4l<<20);
    if(ok){out.resize(size_t(size));ok=std::fread(out.data(),1,out.size(),f)==out.size();}
    std::fclose(f);return ok;
}
}

bool CoursePvs::attach(Model& model,const char* part){
    *this={};
    char path[96];std::snprintf(path,sizeof path,"models/pvs/%s.pvs",part);
    std::vector<uint8_t> file;
    if(!readAll(path,file))return false;
    Header h;std::memcpy(&h,file.data(),sizeof h);
    if(std::memcmp(h.magic,"MPVS",4)||h.version!=1||h.vertexCount!=model.vertexCount||h.indexCount!=model.indexCount||
       h.submeshCount!=model.submeshes.size()||h.blockTriangles<16||h.blockTriangles>1024||!(h.cell>0)||
       h.setBytes!=(h.blockCount+7)/8||h.columnsX>4096||h.columnsZ>4096)return false;
    uint32_t blocks=0;
    for(const auto& s:model.submeshes){if(s.first%3||s.count%3)return false;const uint32_t stride=h.blockTriangles*3;blocks+=(s.count+stride-1)/stride;}
    const size_t triangles=h.indexCount/3,permBytes=(triangles*2+3)&~size_t(3),columns=size_t(h.columnsX)*h.columnsZ;
    const size_t need=sizeof h+permBytes+columns*sizeof(Column)+size_t(h.setCount)*h.setBytes;
    if(blocks!=h.blockCount||file.size()<need||file.size()>need+3||fingerprint(model)!=h.fingerprint)return false;
    std::vector<uint16_t> order(triangles);std::memcpy(order.data(),file.data()+sizeof h,triangles*2);
    columns_.resize(columns);std::memcpy(columns_.data(),file.data()+sizeof h+permBytes,columns*sizeof(Column));
    for(const auto& c:columns_)if(c.set!=NoSet&&(c.set>=h.setCount||!(c.yMin<=c.yMax)))return false;
    // Every submesh's local order must be a permutation of its triangles.
    std::vector<uint8_t> seen;
    for(const auto& s:model.submeshes){
        const uint32_t first=s.first/3,count=s.count/3;
        seen.assign(count,0);
        for(uint32_t j=0;j<count;++j){const uint16_t t=order[first+j];if(t>=count||seen[t])return false;seen[t]=1;}
    }
    // Validated: reorder, then rebuild the blocks over the new order.
    std::vector<uint16_t> authored;
    for(const auto& s:model.submeshes){
        const uint32_t first=s.first/3,count=s.count/3;bool identity=true;
        for(uint32_t j=0;j<count&&identity;++j)identity=order[first+j]==j;
        if(identity)continue;
        authored.assign(model.indices+s.first,model.indices+s.first+s.count);
        for(uint32_t j=0;j<count;++j)std::memcpy(model.indices+s.first+j*3,authored.data()+size_t(order[first+j])*3,3*sizeof(uint16_t));
    }
    flushGpuData(model.indices,model.indexCount*sizeof(uint16_t));
    model.visibility.clear();model.visibility.reserve(model.submeshes.size());
    for(const auto& s:model.submeshes)model.visibility.push_back(buildVisibility(model.vertices,model.indices,s.first,s.count,h.blockTriangles));
    masks_.assign(file.begin()+long(need-size_t(h.setCount)*h.setBytes),file.begin()+long(need));
    sectioned_=true;blocks_=h.blockCount;setBytes_=h.setBytes;sets_=h.setCount;
    columnsX_=h.columnsX;columnsZ_=h.columnsZ;originX_=h.originX;originZ_=h.originZ;cell_=h.cell;
    return true;
}

const uint8_t* CoursePvs::mask(Vec3 eye) const {
    if(!sectioned_){++misses;return nullptr;}
    const float x=(eye.x-originX_)/cell_,z=(eye.z-originZ_)/cell_;
    // Negated comparisons also reject NaN.
    if(!(x>=0.f&&z>=0.f&&x<float(columnsX_)&&z<float(columnsZ_))){++misses;return nullptr;}
    const Column& c=columns_[size_t(unsigned(z))*columnsX_+unsigned(x)];
    if(c.set==NoSet||!(eye.y>=c.yMin&&eye.y<=c.yMax)){++misses;return nullptr;}
    ++hits;return masks_.data()+size_t(c.set)*setBytes_;
}

void CourseSections::attach(std::vector<std::unique_ptr<Model>>& track,const char* list,bool enable){
    parts.clear();parts.resize(track.size());
    std::vector<std::string> names;
    if(enable)if(FILE* f=std::fopen(list,"r")){char name[64];while(std::fscanf(f,"%63s",name)==1)names.emplace_back(name);std::fclose(f);}
    for(size_t i=0;i<track.size();++i)
        if(names.size()!=track.size()||!parts[i].attach(*track[i],names[i].c_str()))track[i]->enableStaticCulling();
}
}
