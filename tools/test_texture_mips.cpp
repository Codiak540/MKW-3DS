// Host tests for include/mkw/texture_mips.hpp (OMIP v1 and the MPK1 index):
// c++ -std=c++17 -Wall -Wextra -Werror -pedantic -Iinclude tools/test_texture_mips.cpp -o build/test_texture_mips && build/test_texture_mips
#include <mkw/texture_mips.hpp>
#include <cassert>
#include <cstdio>

namespace {
std::vector<uint8_t> texture(uint32_t width,uint32_t height,uint8_t format=12,uint8_t levels=0,uint8_t fill=0x5a){
    const auto exponent=[](uint32_t value){uint32_t result=0;while(value>8){++result;value/=2;}return result;};
    uint32_t bytes=0;for(uint32_t l=0;l<=levels;++l)bytes+=mkw::mipLevelBytes(width,height,format,l);
    std::vector<uint8_t> data(21+bytes);data[0]=1;data[2]=uint8_t(exponent(width)|(exponent(height)<<3));data[3]=format;data[4]=levels;
    const uint16_t sub[]={uint16_t(width),uint16_t(height),0,1024,1024,0};std::memcpy(data.data()+5,sub,sizeof sub);
    const uint32_t raw=bytes<<8;std::memcpy(data.data()+17,&raw,4);
    for(uint32_t i=0;i<bytes;++i)data[21+i]=uint8_t(fill+i*7);
    return data;
}
// An ETC1A4 texture whose alpha half of every block is 0xFF (unused alpha).
std::vector<uint8_t> opaqueEtc1a4(uint32_t width,uint32_t height){
    auto data=texture(width,height,13);
    for(uint32_t block=0;block<width*height/16;++block)std::memset(data.data()+21+block*16,0xff,8);
    return data;
}
std::vector<uint8_t> omip(const std::vector<uint8_t>& original,uint8_t format,uint8_t levels,uint16_t flags,uint32_t width,uint32_t height){
    uint32_t tail=0;for(uint32_t l=1;l<=levels;++l)tail+=mkw::mipLevelBytes(width,height,format,l);
    std::vector<uint8_t> out(mkw::MipHeaderBytes+tail);
    for(uint32_t i=0;i<tail;++i)out[mkw::MipHeaderBytes+i]=uint8_t(i*13+1);
    const uint32_t h[8]={0x50494d4f,1,uint32_t(original.size()),mkw::textureCrc32(original.data(),original.size()),
                         uint32_t(format)|uint32_t(levels)<<8|uint32_t(flags)<<16,tail,mkw::textureCrc32(out.data()+mkw::MipHeaderBytes,tail),0};
    std::memcpy(out.data(),h,sizeof h);return out;
}
bool accepts(const std::vector<uint8_t>& original,const std::vector<uint8_t>& candidate,mkw::MipVariant* variant=nullptr){
    mkw::MipVariant v;const bool ok=mkw::readMipVariant(original.data(),original.size(),candidate.data(),candidate.size(),v);
    if(variant)*variant=v;
    if(!ok)assert(v.width==0&&v.levels==0);
    return ok;
}
std::vector<uint8_t> archive(const std::vector<std::pair<std::string,std::vector<uint8_t>>>& files){
    std::vector<uint8_t> names,table,data;
    uint32_t start=12+uint32_t(files.size())*12;for(const auto& f:files)start+=uint32_t(f.first.size()+1);
    for(const auto& f:files){
        const uint32_t e[3]={uint32_t(names.size()),start+uint32_t(data.size()),uint32_t(f.second.size())};
        table.insert(table.end(),reinterpret_cast<const uint8_t*>(e),reinterpret_cast<const uint8_t*>(e)+12);
        names.insert(names.end(),f.first.begin(),f.first.end());names.push_back(0);
        data.insert(data.end(),f.second.begin(),f.second.end());
    }
    std::vector<uint8_t> out={'M','P','K','1'};
    const uint32_t c[2]={uint32_t(files.size()),uint32_t(names.size())};
    out.insert(out.end(),reinterpret_cast<const uint8_t*>(c),reinterpret_cast<const uint8_t*>(c)+8);
    out.insert(out.end(),table.begin(),table.end());out.insert(out.end(),names.begin(),names.end());out.insert(out.end(),data.begin(),data.end());
    return out;
}
bool parses(const std::vector<uint8_t>& file,mkw::MipArchiveIndex& index){
    uint32_t count=0,namesBytes=0;
    if(file.size()<12||!mkw::MipArchiveIndex::counts(file.data(),count,namesBytes))return false;
    if(file.size()<12+size_t(count)*12+namesBytes)return false;
    return index.parse(file.data()+12,count,reinterpret_cast<const char*>(file.data()+12+count*12),namesBytes,file.size());
}
}
int main(){
    mkw::TextureMips policy;
    for(unsigned bits=0;bits<8;++bits){
        const bool models=bits&1,off=bits&2,nearest=bits&4;
        policy.loaded=3;policy.fallbacks=2;policy.converted=1;policy.levelBytes=9;policy.alphaBytesDropped=4;
        policy.configure(models,off,nearest);
        assert(policy.enabled==(models&&!off));
        assert(policy.nearestLevel==(models&&!off&&nearest));
        assert(!policy.loaded&&!policy.fallbacks&&!policy.converted&&!policy.levelBytes&&!policy.alphaBytesDropped);
    }
    policy.configure(true,false,false);assert(policy.lodBias==mkw::TextureMips::DefaultLodBias);
    policy.configure(true,false,false,-1.f);assert(policy.lodBias==-1.f);
    policy.configure(true,false,false,0.75f);assert(policy.lodBias==0.f);
    policy.configure(true,false,false,-9.f);assert(policy.lodBias==-2.f);
    policy.configure(true,false,false,0.f/0.f);assert(policy.lodBias==mkw::TextureMips::DefaultLodBias);
    assert(mkw::TextureMips::key("textures/course_00_000.t3x")=="course_00_000");
    const char* invalidPaths[]={nullptr,"","textures/.t3x","textures/../a.t3x","textures/a/b.t3x","textures/a\\b.t3x","models/a.t3x","textures/a.png","textures/o3ds/a.t3x"};
    for(const char* invalid:invalidPaths)assert(mkw::TextureMips::key(invalid).empty());

    // ETC1 256x128 with levels 1..4 (down to 16x8).
    const auto original=texture(256,128);
    const auto candidate=omip(original,12,4,0,256,128);
    mkw::MipVariant v;
    assert(accepts(original,candidate,&v));
    assert(v.width==256&&v.height==128&&v.format==12&&v.levels==4&&!v.dropAlpha);
    assert(v.level0Bytes==256*128/2&&v.tailBytes==(128*64+64*32+32*16+16*8)/2);
    {
        std::vector<uint8_t> chain(v.totalBytes());mkw::buildMipChain(original.data(),candidate.data(),v,chain.data());
        assert(!std::memcmp(chain.data(),original.data()+21,v.level0Bytes));
        assert(!std::memcmp(chain.data()+v.level0Bytes,candidate.data()+mkw::MipHeaderBytes,v.tailBytes));
    }
    // Any truncation, extension, header bit or tail byte change is rejected.
    for(size_t i=0;i<candidate.size();++i){auto bad=candidate;bad.resize(i);assert(!accepts(original,bad));}
    {auto bad=candidate;bad.push_back(0);assert(!accepts(original,bad));}
    for(size_t i=0;i<mkw::MipHeaderBytes;++i)for(unsigned bit=0;bit<8;++bit){auto bad=candidate;bad[i]^=uint8_t(1u<<bit);assert(!accepts(original,bad));}
    for(size_t i=mkw::MipHeaderBytes;i<candidate.size();i+=97){auto bad=candidate;bad[i]^=1;assert(!accepts(original,bad));}
    // Bound to the exact original bytes.
    for(size_t i=0;i<original.size();i+=61){auto other=original;other[i]^=0x10;assert(!accepts(other,candidate));}
    {auto other=original;other.push_back(0);assert(!accepts(other,candidate));}
    // Levels: the whole chain stays at least 8x8; a 64x16 texture takes one level.
    assert(accepts(texture(64,16),omip(texture(64,16),12,1,0,64,16)));
    assert(!accepts(texture(64,16),omip(texture(64,16),12,2,0,64,16)));
    assert(accepts(texture(1024,1024),omip(texture(1024,1024),12,7,0,1024,1024)));
    assert(!accepts(texture(1024,1024),omip(texture(1024,1024),12,8,0,1024,1024)));
    // Nothing to add: no levels and no conversion.
    assert(!accepts(original,omip(original,12,0,0,256,128)));
    // Originals with their own levels, other formats, atlases or compression keep the original path.
    {const auto mipped=texture(256,128,12,2);assert(!accepts(mipped,omip(mipped,12,4,0,256,128)));}
    {auto rgba=texture(64,64);rgba[3]=0;assert(!accepts(rgba,omip(rgba,12,2,0,64,64)));}
    {auto atlas=texture(64,64);atlas[5]=32;assert(!accepts(atlas,omip(atlas,12,2,0,64,64)));}
    {auto compressed=texture(64,64);compressed[17]=0x11;assert(!accepts(compressed,omip(compressed,12,2,0,64,64)));}
    // A format change is only ETC1A4 -> ETC1, flagged, from opaque alpha.
    {
        const auto etc1a4=opaqueEtc1a4(128,64);
        const auto converted=omip(etc1a4,12,3,1,128,64);
        assert(accepts(etc1a4,converted,&v));
        assert(v.format==12&&v.dropAlpha&&v.levels==3&&v.level0Bytes==128*64/2);
        std::vector<uint8_t> chain(v.totalBytes());mkw::buildMipChain(etc1a4.data(),converted.data(),v,chain.data());
        for(uint32_t block=0;block<128*64/16;++block)assert(!std::memcmp(chain.data()+block*8,etc1a4.data()+21+block*16+8,8));
        assert(!std::memcmp(chain.data()+v.level0Bytes,converted.data()+mkw::MipHeaderBytes,v.tailBytes));
        // Conversion alone (no levels) is allowed for the smallest textures.
        assert(accepts(opaqueEtc1a4(8,8),omip(opaqueEtc1a4(8,8),12,0,1,8,8),&v)&&v.dropAlpha&&v.tailBytes==0&&v.level0Bytes==32);
        // One alpha nibble below 0xF anywhere: rejected (the CRC is recomputed so only alpha differs).
        for(uint32_t at:{0u,7u,128u*64u-16u,128u*64u-9u}){
            auto used=etc1a4;used[21+at]=0xfe;assert(!accepts(used,omip(used,12,3,1,128,64)));
        }
        // Without the flag the format must not change; with it the source must be ETC1A4.
        assert(!accepts(etc1a4,omip(etc1a4,12,3,0,128,64)));
        assert(!accepts(original,omip(original,12,4,1,256,128)));
        assert(!accepts(etc1a4,omip(etc1a4,13,3,1,128,64)));
        // ETC1A4 with its alpha kept may gain ETC1A4 levels.
        assert(accepts(etc1a4,omip(etc1a4,13,2,0,128,64),&v)&&v.format==13&&!v.dropAlpha&&v.level0Bytes==128*64);
    }
    // MPK1 index.
    {
        const auto file=archive({{"a_tex",{1,2,3}},{"b_tex",{4}},{"course_00",candidate}});
        mkw::MipArchiveIndex index;
        assert(parses(file,index));
        const auto* e=index.find("course_00");assert(e&&e->size==candidate.size()&&!std::memcmp(file.data()+e->offset,candidate.data(),e->size));
        assert(index.find("a_tex")&&index.find("b_tex")&&!index.find("c_tex")&&!index.find("course_0")&&!index.find(""));
        {auto bad=file;bad[0]='X';assert(!parses(bad,index));}
        {auto bad=file;auto shorter=bad;shorter.pop_back();assert(!parses(shorter,index));}
        {const auto unsorted=archive({{"b",{1}},{"a",{2}}});assert(!parses(unsorted,index));}
        {const auto duplicate=archive({{"a",{1}},{"a",{2}}});assert(!parses(duplicate,index));}
        {auto bad=file;const uint32_t far=uint32_t(file.size());std::memcpy(bad.data()+12+4,&far,4);assert(!parses(bad,index));}
        {auto bad=file;const uint32_t inHeader=4;std::memcpy(bad.data()+12+4,&inHeader,4);assert(!parses(bad,index));}
        {auto bad=file;const uint32_t name=1000;std::memcpy(bad.data()+12,&name,4);assert(!parses(bad,index));}
        {auto bad=file;const uint32_t zero=0;std::memcpy(bad.data()+4,&zero,4);assert(!parses(bad,index));}
    }
    std::puts("texture_mips: all checks passed");
    return 0;
}
