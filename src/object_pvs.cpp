#include <mkw/object_pvs.hpp>
#include <mkw/renderer.hpp>
#include <cstdio>
#include <cstring>
#include <string>

namespace mkw {
bool attachObjectPvs(const std::vector<std::unique_ptr<Model>>& track,const char* list,bool enable){
    auto& pvs=objectPvs();pvs.clear();
    if(!enable||track.empty()||!list)return false;
    // "models/course_NN.list" -> "models/pvs/course_NN.opvs".
    const char* slash=std::strrchr(list,'/');const char* name=slash?slash+1:list;
    const char* dot=std::strrchr(name,'.');
    const std::size_t length=dot?std::size_t(dot-name):std::strlen(name);
    if(!length||length>48)return false;
    const std::string path="models/pvs/"+std::string(name,length)+".opvs";
    ObjectFingerprint fp;fp.add(uint32_t(track.size()));
    for(const auto& part:track){
        if(!part)return false;
        const Model& m=*part;
        fp.add(m.vertexCount);fp.add(m.indexCount);fp.add(uint32_t(m.submeshes.size()));
        for(const auto& s:m.submeshes){
            if(s.first>m.indexCount||s.count>m.indexCount-s.first)return false;
            fp.add(s.count/3);fp.add(ObjectFingerprint::triangles(m.indices+s.first,s.count));
        }
        if(m.vertexCount)fp.positions(&m.vertices[0].position.x,sizeof(Vertex),m.vertexCount);
    }
    FILE* f=std::fopen(path.c_str(),"rb");if(!f)return false;
    std::fseek(f,0,SEEK_END);const long size=std::ftell(f);std::fseek(f,0,SEEK_SET);
    bool ok=size>=long(sizeof(ObjectPvs::Header))&&size<(1l<<20);
    std::unique_ptr<uint8_t[]> data;
    if(ok){data.reset(new(std::nothrow) uint8_t[std::size_t(size)]);ok=data&&std::fread(data.get(),1,std::size_t(size),f)==std::size_t(size);}
    std::fclose(f);
    return ok&&pvs.load(std::move(data),std::size_t(size),fp.h,uint32_t(track.size()));
}
}
