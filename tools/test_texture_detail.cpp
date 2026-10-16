#include <mkw/texture_detail.hpp>
#include <cassert>

namespace {
std::vector<uint8_t> texture(uint32_t width,uint32_t height,uint8_t format=12){
    const auto exponent=[](uint32_t value){uint32_t result=0;while(value>8){++result;value/=2;}return result;};
    const uint32_t bytes=width*height/(format==12?2:1);
    std::vector<uint8_t> data(21+bytes);data[0]=1;data[2]=uint8_t(exponent(width)|(exponent(height)<<3));data[3]=format;
    const uint16_t sub[]={uint16_t(width),uint16_t(height),0,1024,1024,0};std::memcpy(data.data()+5,sub,sizeof sub);
    const uint32_t raw=bytes<<8;std::memcpy(data.data()+17,&raw,4);return data;
}
std::vector<uint8_t> wrap(const std::vector<uint8_t>& original,const std::vector<uint8_t>& payload){
    const uint32_t header[]={0x5845544f,1,uint32_t(original.size()),mkw::textureCrc32(original.data(),original.size()),uint32_t(payload.size()),mkw::textureCrc32(payload.data(),payload.size())};
    std::vector<uint8_t> result(24+payload.size());std::memcpy(result.data(),header,24);std::memcpy(result.data()+24,payload.data(),payload.size());return result;
}
bool accepts(const std::vector<uint8_t>& original,const std::vector<uint8_t>& candidate){mkw::ModelTextureLayout a,b;return mkw::readTextureVariant(original,candidate,a,b);}
}
int main(){
    mkw::TextureDetail policy;
    for(unsigned bits=0;bits<32;++bits){
        const bool known=bits&1,new3ds=bits&2,qa=bits&4,full=bits&8,requested=bits&16;
        policy.loaded=3;policy.fallbacks=2;policy.sourceBytes=100;policy.candidateBytes=25;
        policy.configure(known,new3ds,qa,full,requested);
#ifdef MKW_RELEASE
        assert(!policy.enabled);
#else
        assert(policy.enabled==(known&&!new3ds&&qa&&!full&&requested));
#endif
        assert(policy.loaded==0&&policy.fallbacks==0&&policy.sourceBytes==0&&policy.candidateBytes==0);
    }
    assert(mkw::TextureDetail::variantPath("textures/course_00_000.t3x")=="textures/o3ds/course_00_000.otex");
    const char* invalidPaths[]={nullptr,"","textures/.t3x","textures/../a.t3x","textures/a/b.t3x","textures/a\\b.t3x","models/a.t3x","textures/a.png"};
    for(const char* invalid:invalidPaths)assert(mkw::TextureDetail::variantPath(invalid).empty());
    assert(mkw::textureCrc32(reinterpret_cast<const uint8_t*>("123456789"),9)==0xcbf43926);
    const auto original=texture(256,128),reduced=texture(128,64),candidate=wrap(original,reduced);
    assert(accepts(original,candidate));
    for(size_t i=0;i<candidate.size();++i){auto bad=candidate;bad.resize(i);assert(!accepts(original,bad));}
    for(size_t i=0;i<24;++i){auto bad=candidate;bad[i]^=1;assert(!accepts(original,bad));}
    {auto bad=candidate;bad.back()^=1;assert(!accepts(original,bad));}
    {auto bad=candidate;bad.push_back(0);assert(!accepts(original,bad));}
    {auto bad=original;bad.back()^=1;assert(!accepts(bad,candidate));}
    assert(!accepts(original,wrap(original,original)));
    assert(!accepts(original,wrap(original,texture(64,64))));
    assert(!accepts(original,wrap(original,texture(128,64,13))));
    const auto tiny=texture(32,32);assert(!accepts(tiny,wrap(tiny,texture(16,16))));
    for(size_t i:{size_t(0),size_t(1),size_t(2),size_t(3),size_t(4),size_t(5),size_t(9),size_t(17)}){
        auto bad=reduced;bad[i]^=0x80;assert(!accepts(original,wrap(original,bad)));
    }
}
