#pragma once
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include "mkw/texture_detail.hpp"

namespace mkw {
// Old 3DS course texture mip chains (docs/o3ds-texture-cache.md). The PICA200
// samples through 256 B of L1 per texture unit and 8 KB of shared L2; a
// minified texture without mipmaps misses on almost every pixel. Course
// textures keep their original level 0 byte for byte and gain levels 1..n from
// textures/o3ds/mips.pak (tools/cook_o3ds_course_textures.py --mode pack).
// Only a console ModelDetail treats as an Old 3DS uses them (New 3DS and
// unknown hardware never open the archive); QA builds turn them off with
// o3ds-texmips-off.txt, sample the nearest level with o3ds-texmips-nearest.txt
// and override the LOD bias with o3ds-texmips-bias.txt.
struct TextureMips {
    // The PICA200 picks one level from the most minified screen axis (no
    // anisotropic filtering), which over-blurs road edges seen at a grazing
    // angle; a small negative bias keeps their thin features (curbs, lines)
    // while distant surfaces still sample a level close to their size.
    static constexpr float DefaultLodBias=-0.5f;
    bool enabled=false,nearestLevel=false;float lodBias=DefaultLodBias;
    std::atomic<uint32_t> loaded{0},fallbacks{0},converted{0},levelBytes{0},alphaBytesDropped{0};
    static constexpr bool eligible(bool o3dsModels,bool disabled){return o3dsModels&&!disabled;}
    // Bias is clamped to [-2, 0]: never blurrier than the chain's own levels.
    void configure(bool o3dsModels,bool disabled,bool nearest,float bias=DefaultLodBias){
        enabled=eligible(o3dsModels,disabled);nearestLevel=enabled&&nearest;
        lodBias=bias==bias?std::min(0.f,std::max(-2.f,bias)):DefaultLodBias;
        loaded=0;fallbacks=0;converted=0;levelBytes=0;alphaBytesDropped=0;
    }
    // "textures/<name>.t3x" -> "<name>", the archive key; empty otherwise.
    static std::string key(const char* path){
        const std::string source=path?path:"";
        if(source.size()<=13||source.compare(0,9,"textures/")||source.compare(source.size()-4,4,".t3x"))return {};
        std::string name=source.substr(9,source.size()-13);
        if(name.find('/')!=std::string::npos||name.find('\\')!=std::string::npos||name.find("..")!=std::string::npos)return {};
        return name;
    }
    static constexpr const char* archivePath="textures/o3ds/mips.pak";
};
inline TextureMips& textureMips(){static TextureMips policy;return policy;}

// OMIP v1, one archive entry per original texture, little endian:
//   "OMIP", u32 version 1, u32 original T3X bytes, u32 original CRC32,
//   u8 format (12 ETC1 / 13 ETC1A4), u8 levels, u16 flags (bit 0: the
//   original is ETC1A4 whose every alpha nibble is 0xF; it becomes ETC1 with
//   its colour blocks copied verbatim), u32 tail bytes, u32 tail CRC32,
//   u32 zero, then levels 1..n in PICA order and the result format.
// Accepted only for the exact original T3X: one full-size, uncompressed ETC
// subtexture without mip levels. Level 0 always comes from the original.
struct MipVariant {
    uint32_t width=0,height=0,format=0,levels=0,level0Bytes=0,tailBytes=0;
    bool dropAlpha=false;
    uint32_t totalBytes()const{return level0Bytes+tailBytes;}
};
constexpr size_t MipHeaderBytes=32;
inline uint32_t mipLevelBytes(uint32_t width,uint32_t height,uint32_t format,uint32_t level){
    return (width>>level)*(height>>level)/(format==12?2:1);
}
inline bool readMipVariant(const uint8_t* original,size_t originalSize,const uint8_t* candidate,size_t candidateSize,MipVariant& out){
    out={};
    if(candidateSize<MipHeaderBytes||candidateSize>4u*1024*1024||std::memcmp(candidate,"OMIP",4))return false;
    uint32_t h[8];std::memcpy(h,candidate,sizeof h);
    const uint32_t format=h[4]&255,levels=(h[4]>>8)&255,flags=h[4]>>16;
    if(h[1]!=1||h[7]||flags&~1u||h[2]!=originalSize)return false;
    ModelTextureLayout source;
    if(!modelTextureLayout(original,originalSize,source)||source.levels)return false;
    const bool dropAlpha=flags&1;
    if(dropAlpha?source.format!=13||format!=12:format!=source.format)return false;
    if((!levels&&!dropAlpha)||levels>7||(source.width>>levels)<8||(source.height>>levels)<8)return false;
    uint32_t tail=0;
    for(uint32_t level=1;level<=levels;++level)tail+=mipLevelBytes(source.width,source.height,format,level);
    if(h[5]!=tail||candidateSize!=MipHeaderBytes+size_t(tail))return false;
    if(h[3]!=textureCrc32(original,originalSize)||h[6]!=textureCrc32(candidate+MipHeaderBytes,tail))return false;
    // ETC1A4 blocks are 8 alpha bytes then the 8-byte ETC1 colour block.
    const uint8_t* payload=original+21;
    if(dropAlpha)for(uint32_t block=0;block<source.bytes/16;++block)
        for(uint32_t i=0;i<8;++i)if(payload[block*16+i]!=0xff)return false;
    out.width=source.width;out.height=source.height;out.format=format;out.levels=levels;
    out.level0Bytes=mipLevelBytes(source.width,source.height,format,0);out.tailBytes=tail;out.dropAlpha=dropAlpha;
    return true;
}
// The whole chain (level 0 from the original, then the archive's levels) into
// destination, which holds variant.totalBytes().
inline void buildMipChain(const uint8_t* original,const uint8_t* candidate,const MipVariant& variant,uint8_t* destination){
    const uint8_t* payload=original+21;
    if(variant.dropAlpha)for(uint32_t block=0;block<variant.level0Bytes/8;++block)std::memcpy(destination+block*8,payload+block*16+8,8);
    else std::memcpy(destination,payload,variant.level0Bytes);
    std::memcpy(destination+variant.level0Bytes,candidate+MipHeaderBytes,variant.tailBytes);
}

// MPK1 index (tools/pack_menu_assets.py): "MPK1", u32 count, u32 namesBytes,
// count {u32 nameOffset, u32 offset, u32 size} sorted by name, then names.
struct MipArchiveIndex {
    struct Entry {uint32_t name,offset,size;};
    std::vector<Entry> entries;std::vector<char> names;
    // header: the first 12 bytes; table: count*12 bytes; names: namesBytes.
    static bool counts(const uint8_t* header,uint32_t& count,uint32_t& namesBytes){
        if(std::memcmp(header,"MPK1",4))return false;
        std::memcpy(&count,header+4,4);std::memcpy(&namesBytes,header+8,4);
        return count&&count<=65536&&namesBytes&&namesBytes<=(4u<<20);
    }
    bool parse(const uint8_t* table,uint32_t count,const char* nameBytes,uint32_t namesBytes,uint64_t fileSize){
        entries.resize(count);names.assign(nameBytes,nameBytes+namesBytes);
        if(names.back())return false;
        const uint64_t dataStart=12+uint64_t(count)*12+namesBytes;
        for(uint32_t i=0;i<count;++i){
            auto& e=entries[i];std::memcpy(&e,table+i*12,12);
            if(e.name>=namesBytes||e.offset<dataStart||uint64_t(e.offset)+e.size>fileSize)return false;
            if(i&&std::strcmp(names.data()+entries[i-1].name,names.data()+e.name)>=0)return false;
        }
        return true;
    }
    const Entry* find(const char* key)const{
        auto it=std::lower_bound(entries.begin(),entries.end(),key,[&](const Entry& e,const char* k){return std::strcmp(names.data()+e.name,k)<0;});
        return it!=entries.end()&&!std::strcmp(names.data()+it->name,key)?&*it:nullptr;
    }
};
}
namespace mkw {
class Texture;
// Runtime (src/texture_mips.cpp): when the archive holds a variant for this
// texture's path and it validates against the original bytes, loads the whole
// chain into LINEAR and returns true. False leaves the texture untouched and
// the caller loads the original as before. Thread-safe (asset loader thread).
bool loadTextureMips(const char* path,const std::vector<uint8_t>& original,Texture& texture);
}
