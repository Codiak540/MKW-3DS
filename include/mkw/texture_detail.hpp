#pragma once
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace mkw {
struct TextureDetail {
    bool enabled=false;
    std::atomic<uint32_t> loaded{0},fallbacks{0},sourceBytes{0},candidateBytes{0};
    static constexpr bool eligible(bool known,bool new3ds,bool qa,bool fullQuality,bool requested){
#ifdef MKW_RELEASE
        (void)known;(void)new3ds;(void)qa;(void)fullQuality;(void)requested;
        return false;
#else
        return known&&!new3ds&&qa&&!fullQuality&&requested;
#endif
    }
    void configure(bool known,bool new3ds,bool qa,bool fullQuality,bool requested){
        enabled=eligible(known,new3ds,qa,fullQuality,requested);
        loaded=0;fallbacks=0;sourceBytes=0;candidateBytes=0;
    }
    static std::string variantPath(const char* path){
        const std::string source=path?path:"";
        if(source.size()<=13||source.compare(0,9,"textures/")||source.compare(source.size()-4,4,".t3x"))return {};
        const std::string name=source.substr(9,source.size()-13);
        if(name.find('/')!=std::string::npos||name.find('\\')!=std::string::npos||name.find("..")!=std::string::npos)return {};
        return "textures/o3ds/"+name+".otex";
    }
};
inline TextureDetail& textureDetail(){static TextureDetail policy;return policy;}

inline uint32_t textureCrc32(const uint8_t* data,size_t size){
    static const std::array<uint32_t,256> table=[](){
        std::array<uint32_t,256> values{};
        for(uint32_t i=0;i<256;++i){uint32_t v=i;for(unsigned bit=0;bit<8;++bit)v=(v>>1)^(0xedb88320u&uint32_t(-int32_t(v&1)));values[i]=v;}
        return values;
    }();
    uint32_t crc=~uint32_t(0);for(size_t i=0;i<size;++i)crc=table[(crc^data[i])&255]^(crc>>8);return ~crc;
}
struct ModelTextureLayout {uint32_t width=0,height=0,format=0,levels=0,bytes=0;};
// Only full-image, uncompressed ETC color maps are eligible. Atlas coordinates,
// alpha precision, shader data textures, and cubemaps never change here.
inline bool modelTextureLayout(const uint8_t* data,size_t size,ModelTextureLayout& out){
    if(size<21||data[0]!=1||data[1]!=0||(data[2]&0xc0)||(data[3]!=12&&data[3]!=13)||data[4]>7)return false;
    out.width=1u<<((data[2]&7)+3);out.height=1u<<(((data[2]>>3)&7)+3);out.format=data[3];out.levels=data[4];
    uint16_t sub[6];uint32_t encoded;std::memcpy(sub,data+5,sizeof sub);std::memcpy(&encoded,data+17,4);
    if(sub[0]!=out.width||sub[1]!=out.height||sub[2]!=0||sub[3]!=1024||sub[4]!=1024||sub[5]!=0||encoded&255)return false;
    uint32_t width=out.width,height=out.height,bytes=0;
    for(uint32_t level=0;level<=out.levels;++level){
        if(width<8||height<8)return false;
        bytes+=width*height/(out.format==12?2:1);width/=2;height/=2;
    }
    if(encoded>>8!=bytes||size!=21+size_t(bytes))return false;
    out.bytes=bytes;return true;
}
// OTEX v1: magic/version, original byte count/CRC32, payload byte count/CRC32,
// followed by a standard T3X. The caller imports only the validated payload.
inline bool readTextureVariant(const std::vector<uint8_t>& original,const std::vector<uint8_t>& candidate,
                               ModelTextureLayout& source,ModelTextureLayout& reduced){
    if(candidate.size()<24||candidate.size()>2u*1024*1024)return false;
    uint32_t header[6];std::memcpy(header,candidate.data(),sizeof header);
    if(std::memcmp(header,"OTEX",4)||header[1]!=1||header[2]!=original.size()||header[4]!=candidate.size()-24||
       header[3]!=textureCrc32(original.data(),original.size())||header[5]!=textureCrc32(candidate.data()+24,candidate.size()-24))return false;
    if(!modelTextureLayout(original.data(),original.size(),source)||
       !modelTextureLayout(candidate.data()+24,candidate.size()-24,reduced)||
       source.format!=reduced.format||source.levels<reduced.levels||std::max(source.width,source.height)<=32)return false;
    uint32_t scale=2;while(std::max(source.width,source.height)/scale>128)scale*=2;
    return reduced.width==std::max(uint32_t(8),source.width/scale)&&reduced.height==std::max(uint32_t(8),source.height/scale)&&reduced.bytes<source.bytes;
}
}
