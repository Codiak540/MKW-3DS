#include "mkw/texture_mips.hpp"
#include "mkw/renderer.hpp"
#include <cstdio>

namespace mkw {
namespace {
// textures/o3ds/mips.pak stays open for the session once a load asks for it:
// one SD open instead of one per course texture.
struct MipArchive {
    LightLock lock;bool tried=false;std::FILE* file=nullptr;MipArchiveIndex index;
    MipArchive(){LightLock_Init(&lock);}
    // With the lock held.
    bool open(){
        if(tried)return file;
        tried=true;
        std::FILE* f=std::fopen(TextureMips::archivePath,"rb");if(!f)return false;
        std::setvbuf(f,nullptr,_IONBF,0);
        uint8_t header[12];uint32_t count=0,namesBytes=0;long size=-1;
        bool ok=std::fseek(f,0,SEEK_END)==0&&(size=std::ftell(f))>0&&std::fseek(f,0,SEEK_SET)==0&&
            std::fread(header,1,12,f)==12&&MipArchiveIndex::counts(header,count,namesBytes);
        if(ok){
            std::vector<uint8_t> table(size_t(count)*12);std::vector<char> names(namesBytes);
            ok=std::fread(table.data(),1,table.size(),f)==table.size()&&std::fread(names.data(),1,names.size(),f)==names.size()&&
               index.parse(table.data(),count,names.data(),namesBytes,uint64_t(size));
        }
        if(!ok){std::fclose(f);index={};return false;}
        file=f;return true;
    }
};
MipArchive& archive(){static MipArchive value;return value;}
}
bool loadTextureMips(const char* path,const std::vector<uint8_t>& original,Texture& texture){
    auto& policy=textureMips();
    const std::string key=TextureMips::key(path);
    if(!policy.enabled||key.empty())return false;
    std::vector<uint8_t> candidate;bool found=false,read=false;
    {
        auto& a=archive();LightLock_Lock(&a.lock);
        if(a.open())if(const auto* e=a.index.find(key.c_str())){
            found=true;candidate.resize(e->size);
            read=std::fseek(a.file,long(e->offset),SEEK_SET)==0&&std::fread(candidate.data(),1,candidate.size(),a.file)==candidate.size();
        }
        LightLock_Unlock(&a.lock);
    }
    if(!found)return false; // Not a course texture: the original, silently.
    MipVariant variant;
    if(!read||!readMipVariant(original.data(),original.size(),candidate.data(),candidate.size(),variant)){
        policy.fallbacks.fetch_add(1,std::memory_order_relaxed);return false;
    }
    texture.reset();
    C3D_Tex& gpu=texture.gpu;
    if(!C3D_TexInitWithParams(&gpu,nullptr,{uint16_t(variant.width),uint16_t(variant.height),uint8_t(variant.levels),
                                             GPU_TEXCOLOR(variant.format),GPU_TEX_2D,false})){
        gpu={};policy.fallbacks.fetch_add(1,std::memory_order_relaxed);return false;
    }
    buildMipChain(original.data(),candidate.data(),variant,static_cast<uint8_t*>(gpu.data));
    C3D_TexFlush(&gpu);
    // As Texture::loadMemory; a chain samples between levels unless QA asks
    // for the nearest level only.
    C3D_TexSetFilter(&gpu,GPU_LINEAR,GPU_LINEAR);
    if(variant.levels){C3D_TexSetFilterMipmap(&gpu,policy.nearestLevel?GPU_NEAREST:GPU_LINEAR);C3D_TexSetLodBias(&gpu,policy.lodBias);}
    C3D_TexSetWrap(&gpu,GPU_REPEAT,GPU_REPEAT);
    texture.ready=true;
    policy.loaded.fetch_add(1,std::memory_order_relaxed);
    policy.levelBytes.fetch_add(variant.tailBytes,std::memory_order_relaxed);
    if(variant.dropAlpha){
        policy.converted.fetch_add(1,std::memory_order_relaxed);
        policy.alphaBytesDropped.fetch_add(variant.level0Bytes,std::memory_order_relaxed);
    }
    return true;
}
}
