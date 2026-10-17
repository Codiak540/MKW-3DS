#pragma once
// Preparatory OPOS v1 validator. No runtime call sites exist yet. This format is
// deliberately separate from O3RM: only positions/normals may change here.
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <numeric>
#include <vector>

namespace mkw { namespace opos {
struct Bytes { const uint8_t* data=nullptr; size_t size=0; };
inline uint32_t u32(const uint8_t* p) { return uint32_t(p[0])|(uint32_t(p[1])<<8)|(uint32_t(p[2])<<16)|(uint32_t(p[3])<<24); }
inline uint16_t u16(const uint8_t* p) { return uint16_t(p[0])|(uint16_t(p[1])<<8); }
inline float f32(const uint8_t* p) { uint32_t n=u32(p); float f; std::memcpy(&f,&n,4); return f; }
inline uint32_t crc32(Bytes b) {
    struct Table { uint32_t t[256]; Table(){for(uint32_t i=0;i<256;++i){uint32_t n=i;for(int j=0;j<8;++j)n=(n>>1)^(0xedb88320u&uint32_t(-int32_t(n&1)));t[i]=n;}} };
    static const Table table;
    uint32_t crc=0xffffffffu;
    for(size_t i=0;i<b.size;++i)crc=(crc>>8)^table.t[(crc^b.data[i])&255];
    return crc^0xffffffffu;
}
inline bool finite(const uint8_t* p,size_t count){for(size_t i=0;i<count;++i)if(!std::isfinite(f32(p+i*4)))return false;return true;}
struct Cursor {
    Bytes bytes; size_t at=0;
    const uint8_t* take(size_t n){if(n>bytes.size-at)return nullptr;const auto* p=bytes.data+at;at+=n;return p;}
    bool tag(const char* s){const auto* p=take(4);return p&&!std::memcmp(p,s,4);}
};
struct Model {
    Bytes bytes; uint32_t version=0,nv=0,ni=0,ns=0,nt=0,record=0;
    const uint8_t *spans=nullptr,*names=nullptr,*vertices=nullptr,*indices=nullptr;
    const uint8_t* vertex(uint32_t i)const{return vertices+size_t(i)*44;}
    uint32_t index(uint32_t i)const{return u16(indices+size_t(i)*2);}
    uint32_t first(uint32_t i)const{return u32(spans+size_t(i)*record);}
    uint32_t count(uint32_t i)const{return u32(spans+size_t(i)*record+4);}
    uint32_t material(uint32_t triangle)const{for(uint32_t i=0;i<ns;++i)if(triangle*3>=first(i)&&triangle*3<first(i)+count(i))return i;return ns;}
};
inline bool model(Bytes bytes,Model& out){
    if(bytes.size<24||bytes.size>10*1024*1024||std::memcmp(bytes.data,"GMDL",4))return false;
    Model m;m.bytes=bytes;m.version=u32(bytes.data+4);m.nv=u32(bytes.data+8);m.ni=u32(bytes.data+12);m.ns=u32(bytes.data+16);m.nt=u32(bytes.data+20);
    m.record=m.version==3?44:m.version==4?56:m.version==5?92:0;
    if(!m.record||!m.nv||m.nv>65535||!m.ni||m.ni>3000000||m.ni%3||!m.ns||m.ns>64||m.nt>512)return false;
    const size_t exact=24+size_t(m.ns)*m.record+size_t(m.nt)*64+size_t(m.nv)*44+size_t(m.ni)*2;
    if(exact!=bytes.size)return false; // No appended O3RM or unclaimed bytes.
    m.spans=bytes.data+24;m.names=m.spans+size_t(m.ns)*m.record;m.vertices=m.names+size_t(m.nt)*64;m.indices=m.vertices+size_t(m.nv)*44;
    uint32_t end=0;
    for(uint32_t i=0;i<m.ns;++i){
        const auto* s=m.spans+size_t(i)*m.record;
        const uint32_t count=u32(s+4),a=u32(s+8),b=u32(s+16);
        if(u32(s)!=end||count>m.ni-end||count%3||(a!=0xffffffffu&&a>=m.nt)||(b!=0xffffffffu&&b>=m.nt))return false;
        end+=count;
    }
    if(end!=m.ni)return false;
    for(uint32_t i=0;i<m.nv;++i)if(!finite(m.vertex(i),10))return false;
    for(uint32_t i=0;i<m.ni;++i)if(m.index(i)>=m.nv)return false;
    out=m;return true;
}
struct Bank {
    uint32_t nv=0,nm=0,ns=0,clips=0; size_t tail=0;
    const uint8_t *sources=nullptr,*ranges=nullptr,*palette=nullptr;
    const uint8_t* source(uint32_t i)const{return sources+size_t(i)*28;}
    uint64_t groups(uint32_t id)const{uint64_t result=0;for(uint32_t i=0;i<ns;++i){const auto* r=ranges+i*8;const uint32_t first=u32(r),count=u32(r+4);if(id>=first&&id-first<count)result|=uint64_t(1)<<i;}return result;}
};
inline bool bank(Bytes bytes,uint32_t poseClip,uint32_t poseFrame,Bank& out){
    if(bytes.size<28||bytes.size>12*1024*1024||std::memcmp(bytes.data,"DSKM",4))return false;
    const uint32_t version=u32(bytes.data+4),faces=u32(bytes.data+24);
    Bank b;b.nv=u32(bytes.data+8);b.nm=u32(bytes.data+12);b.clips=u32(bytes.data+16);b.ns=u32(bytes.data+20);
    if(version<2||version>5||!b.nv||b.nv>65535||!b.nm||b.nm>128||!b.clips||b.clips>64||!b.ns||b.ns>64||faces>64||poseClip>=b.clips)return false;
    Cursor r{bytes,28};uint32_t root=0;
    if(version==5){const auto* p=r.take(4);if(!p)return false;root=u32(p);if(root!=0xffffffffu&&root>=b.nm)return false;}
    b.sources=r.take(size_t(b.nv)*28);b.ranges=r.take(size_t(b.ns)*8);b.tail=r.at;
    if(!b.sources||!b.ranges)return false;
    for(uint32_t i=0;i<b.nv;++i)if(!finite(b.source(i),6)||u32(b.source(i)+24)>=b.nm)return false;
    for(uint32_t i=0;i<b.ns;++i){const auto* p=b.ranges+i*8;const uint32_t first=u32(p),count=u32(p+4);if(first>b.nv||count>b.nv-first)return false;}
    uint32_t totalFrames=0;
    auto clip=[&](size_t stride,uint32_t& frames)->const uint8_t*{
        const auto* h=r.take(44);if(!h)return nullptr;frames=u32(h+40);
        if(!frames||frames>2000||u32(h+32)!=frames||u32(h+36)>1)return nullptr;
        return r.take(size_t(frames)*stride);
    };
    const size_t stride=size_t(b.nm)*84+b.ns*4;
    for(uint32_t i=0;i<b.clips;++i){uint32_t n=0;const auto* p=clip(stride,n);if(!p)return false;totalFrames+=n;
        for(uint32_t f=0;f<n;++f)if(!finite(p+size_t(f)*stride,b.nm*21))return false;
        if(i==poseClip){if(poseFrame>=n)return false;b.palette=p+size_t(poseFrame)*stride;}}
    for(uint32_t i=0;i<faces;++i){uint32_t n=0;if(!clip(b.ns*4,n))return false;}
    if(version>=3){
        if(!r.tag("HLOC"))return false;const auto* h=r.take(8);if(!h)return false;const uint32_t nodes=u32(h),clips=u32(h+4);
        if(nodes>8||clips>2||bool(nodes)!=bool(clips))return false;const auto* bindings=r.take(nodes*8);if(!bindings)return false;
        for(uint32_t i=0;i<nodes;++i){const uint32_t matrix=u32(bindings+i*8),parent=u32(bindings+i*8+4);if(matrix>=b.nm||parent>=b.nm||matrix==parent)return false;
            for(uint32_t j=0;j<i;++j)if(matrix==u32(bindings+j*8)||matrix==u32(bindings+j*8+4))return false;}
        for(uint32_t i=0;i<clips;++i){const auto* c=r.take(44);if(!c)return false;const uint32_t n=u32(c+40);if(!n||n>2000||u32(c+32)!=n||u32(c+36)>1)return false;
            const size_t values=size_t(n+1)*nodes*21;const auto* p=r.take(values*4);if(!p||!finite(p,values))return false;}
    }
    if(version>=4){if(!r.tag("UVAN"))return false;const auto* h=r.take(4);if(!h)return false;const uint32_t n=u32(h);if(n>64)return false;
        for(uint32_t i=0;i<n;++i){uint32_t frames=0;const auto* p=clip(b.ns*24,frames);if(!p||!finite(p,size_t(frames)*b.ns*6))return false;}}
    if(version==5&&root==0xffffffffu){if(!r.tag("ROOT"))return false;const auto* h=r.take(4);if(!h||u32(h)!=b.clips)return false;const auto* p=r.take(size_t(totalFrames)*12);if(!p||!finite(p,size_t(totalFrames)*3))return false;}
    if(r.at!=bytes.size||!b.palette)return false;out=b;return true;
}
inline bool invertible(const uint8_t* p,unsigned stride){
    double a[3][3],c[3][3],norm=0;
    for(unsigned r=0;r<3;++r){double sum=0;for(unsigned j=0;j<3;++j){a[r][j]=f32(p+4*(r*stride+j));sum+=std::abs(a[r][j]);}norm=std::max(norm,sum);}
    for(unsigned r=0;r<3;++r)for(unsigned j=0;j<3;++j)c[r][j]=a[(r+1)%3][(j+1)%3]*a[(r+2)%3][(j+2)%3]-a[(r+1)%3][(j+2)%3]*a[(r+2)%3][(j+1)%3];
    const double det=a[0][0]*c[0][0]+a[0][1]*c[0][1]+a[0][2]*c[0][2];
    if(!std::isfinite(det)||std::abs(det)<1e-30)return false;double invNorm=0;
    for(unsigned r=0;r<3;++r){double sum=0;for(unsigned j=0;j<3;++j)sum+=std::abs(c[j][r]/det);invNorm=std::max(invNorm,sum);}
    return norm*invNorm<1e5;
}
inline bool roundtrip(const uint8_t* source,const uint8_t* vertex,const uint8_t* palette,double tolerance){
    const auto* m=palette+size_t(u32(source+24))*84;
    double pe=0,norm=0,targetNorm=0,n[3],target[3];
    for(unsigned r=0;r<3;++r){double position=f32(m+4*(r*4+3));n[r]=0;target[r]=f32(vertex+12+r*4);
        for(unsigned j=0;j<3;++j){position+=double(f32(m+4*(r*4+j)))*f32(source+4*j);n[r]+=double(f32(m+48+4*(r*3+j)))*f32(source+12+4*j);}
        const double delta=position-f32(vertex+r*4);pe+=delta*delta;norm+=n[r]*n[r];targetNorm+=target[r]*target[r];}
    if(!std::isfinite(pe)||pe>tolerance*tolerance||!std::isfinite(norm)||norm<=1e-24||targetNorm<=1e-24)return false;
    double error=0;for(unsigned r=0;r<3;++r){const double delta=n[r]/std::sqrt(norm)-target[r]/std::sqrt(targetNorm);error+=delta*delta;}
    return std::isfinite(error)&&error<=1e-8;
}
// Caller policy is authoritative. Never infer cap/eye lock intent from an
// untrusted OPOS payload. The offline writer records the policy in its receipt;
// a future loader must obtain the expected policy independently or decline OPOS.
// Both lists must be sorted/unique. Alpha material triangles are always locked.
struct Policy {
    const uint32_t* protectedTriangles=nullptr;size_t triangleCount=0;
    const uint32_t* lockedVertices=nullptr;size_t vertexCount=0;
};
struct Validated {
    Model candidate;Bank originalBank;
    const uint8_t *sourceIds=nullptr,*overrides=nullptr,*protectedTriangles=nullptr;
    uint32_t protectedCount=0,poseClip=0,poseFrame=0;
};
inline bool validate(Bytes wrapper,Bytes originalModel,Bytes originalBank,const Policy& policy,Validated& result,const char** error=nullptr){
    auto fail=[&](const char* message){if(error)*error=message;return false;};
    // These caps bound every subsequent allocation (largest scratch vector is
    // at most one byte per original triangle; no allocation uses unchecked data).
    if(wrapper.size<64||wrapper.size>16*1024*1024||originalModel.size>10*1024*1024||originalBank.size>12*1024*1024||!wrapper.data||!originalModel.data||!originalBank.data)return fail("wrapper/original bounds");
    const auto* h=wrapper.data;
    if(std::memcmp(h,"OPOS",4)||u32(h+4)!=1||u32(h+8)!=64||u32(h+56)!=1||u32(h+60))return fail("wrapper version/flags");
    if(originalModel.size!=u32(h+12)||originalBank.size!=u32(h+20)||crc32(originalModel)!=u32(h+16)||crc32(originalBank)!=u32(h+24))return fail("stale original identity");
    const uint32_t modelSize=u32(h+28),nv=u32(h+32),locks=u32(h+44),payload=u32(h+48);
    if(!nv||nv>65535||locks>1000000||uint64_t(modelSize)+uint64_t(nv)*32+uint64_t(locks)*8!=payload||payload!=wrapper.size-64||crc32({h+64,payload})!=u32(h+52))return fail("payload bounds/CRC");
    Model original,candidate;Bank authored;
    if(!model(originalModel,original)||!model({h+64,modelSize},candidate)||!bank(originalBank,u32(h+36),u32(h+40),authored))return fail("invalid model/bank");
    if(authored.nv!=original.nv||candidate.nv!=nv||candidate.version!=original.version||candidate.ns!=original.ns||candidate.nt!=original.nt||candidate.nv>original.nv||candidate.ni>original.ni||(candidate.nv==original.nv&&candidate.ni==original.ni))return fail("dimensions/reduction");
    if(std::memcmp(original.names,candidate.names,size_t(original.nt)*64))return fail("texture table changed");
    for(uint32_t i=0;i<original.ns;++i)if(candidate.count(i)>original.count(i)||(original.count(i)&&!candidate.count(i))||std::memcmp(original.spans+size_t(i)*original.record+8,candidate.spans+size_t(i)*candidate.record+8,original.record-8))return fail("material/span changed");
    const auto* ids=h+64+modelSize;const auto* overrides=ids+size_t(nv)*4;const auto* protectedPairs=overrides+size_t(nv)*28;
    std::vector<uint64_t> groups(original.nv);
    for(uint32_t i=0;i<original.nv;++i)groups[i]=authored.groups(i);
    for(uint32_t i=0;i<nv;++i){const uint32_t id=u32(ids+i*4);const auto* s=overrides+size_t(i)*28;
        if(id>=original.nv||(i&&id<=u32(ids+(i-1)*4))||!groups[id]||!finite(s,6)||u32(s+24)!=u32(authored.source(id)+24))return fail("source mapping/binding");
        // Version 1 permits only p/n edits. Texture coordinates and vertex colour
        // stay source-exact; a future UV-edit format needs separate review.
        if(std::memcmp(candidate.vertex(i)+24,original.vertex(id)+24,20))return fail("UV/colour changed");}
    std::vector<uint8_t> member(original.nv),used(nv);
    for(uint32_t s=0;s<original.ns;++s){std::fill(member.begin(),member.end(),0);
        for(uint32_t j=original.first(s);j<original.first(s)+original.count(s);++j)member[original.index(j)]=1;
        for(uint32_t j=candidate.first(s);j<candidate.first(s)+candidate.count(s);++j){const uint32_t v=candidate.index(j);if(!member[u32(ids+v*4)])return fail("material/source membership");used[v]=1;}}
    if(std::find(used.begin(),used.end(),0)!=used.end())return fail("unreferenced candidate vertex");
    double low[3],high[3];for(unsigned j=0;j<3;++j)low[j]=high[j]=f32(original.vertex(0)+j*4);
    for(uint32_t i=1;i<original.nv;++i)for(unsigned j=0;j<3;++j){const double v=f32(original.vertex(i)+j*4);low[j]=std::min(low[j],v);high[j]=std::max(high[j],v);}
    const double extent=std::max(1.,std::max(high[0]-low[0],std::max(high[1]-low[1],high[2]-low[2]))),tolerance=std::max(1e-5,extent*1e-6);
    for(uint32_t i=0;i<authored.nm;++i)if(!invertible(authored.palette+i*84,4)||!invertible(authored.palette+i*84+48,3))return fail("singular neutral matrix");
    for(uint32_t i=0;i<original.nv;++i)if(!roundtrip(authored.source(i),original.vertex(i),authored.palette,tolerance))return fail("original neutral roundtrip");
    for(uint32_t i=0;i<nv;++i)if(!roundtrip(overrides+size_t(i)*28,candidate.vertex(i),authored.palette,tolerance))return fail("candidate neutral roundtrip");
    if(policy.triangleCount>original.ni/3||policy.vertexCount>original.nv||(policy.triangleCount&&!policy.protectedTriangles)||(policy.vertexCount&&!policy.lockedVertices))return fail("policy bounds");
    std::vector<uint8_t> required(original.ni/3),claimed(candidate.ni/3);
    for(uint32_t s=0;s<original.ns;++s)if(u32(original.spans+size_t(s)*original.record+12)&3)for(uint32_t j=original.first(s)/3;j<(original.first(s)+original.count(s))/3;++j)required[j]=1;
    for(size_t i=0;i<policy.triangleCount;++i){const uint32_t t=policy.protectedTriangles[i];if(t>=required.size()||(i&&t<=policy.protectedTriangles[i-1]))return fail("policy triangles");required[t]=1;}
    size_t expected=0;for(uint8_t v:required)expected+=v;
    if(expected!=locks)return fail("protected triangle count");
    for(uint32_t i=0;i<locks;++i){const uint32_t a=u32(protectedPairs+i*8),b=u32(protectedPairs+i*8+4);
        if(a>=required.size()||b>=claimed.size()||!required[a]||claimed[b]||(i&&a<=u32(protectedPairs+(i-1)*8))||original.material(a)!=candidate.material(b))return fail("protected triangle mapping");
        bool equal=false;
        for(unsigned rotation=0;rotation<3&&!equal;++rotation){equal=true;for(unsigned j=0;j<3;++j){const uint32_t old=original.index(a*3+j),now=candidate.index(b*3+(j+rotation)%3);
            if(std::memcmp(original.vertex(old),candidate.vertex(now),44)||std::memcmp(authored.source(old),overrides+size_t(now)*28,28)||groups[old]!=groups[u32(ids+now*4)]){equal=false;break;}}}
        if(!equal)return fail("protected triangle bytes/binding");required[a]=0;claimed[b]=1;}
    // Hard-locked triangle-soup duplicates may weld, but an exactly equal
    // vertex/local binding with identical shape membership must survive.
    auto compare=[&](const uint8_t* av,const uint8_t* as,uint64_t ag,const uint8_t* bv,const uint8_t* bs,uint64_t bg){int c=std::memcmp(av,bv,44);if(!c)c=std::memcmp(as,bs,28);if(!c)c=ag<bg?-1:ag>bg?1:0;return c;};
    std::vector<uint32_t> order(nv);std::iota(order.begin(),order.end(),0);
    std::sort(order.begin(),order.end(),[&](uint32_t a,uint32_t b){return compare(candidate.vertex(a),overrides+size_t(a)*28,groups[u32(ids+a*4)],candidate.vertex(b),overrides+size_t(b)*28,groups[u32(ids+b*4)])<0;});
    for(size_t i=0;i<policy.vertexCount;++i){const uint32_t id=policy.lockedVertices[i];if(id>=original.nv||(i&&id<=policy.lockedVertices[i-1]))return fail("policy vertices");
        const auto found=std::lower_bound(order.begin(),order.end(),id,[&](uint32_t c,uint32_t o){return compare(candidate.vertex(c),overrides+size_t(c)*28,groups[u32(ids+c*4)],original.vertex(o),authored.source(o),groups[o])<0;});
        if(found==order.end()||compare(candidate.vertex(*found),overrides+size_t(*found)*28,groups[u32(ids+*found*4)],original.vertex(id),authored.source(id),groups[id]))return fail("locked source vertex lost/changed");}
    // Commit views only after every check. Input buffers must outlive this view.
    // The caller must still retain ORIGINAL authoredRanges and model submesh
    // first offsets for PAT lookup; compact ranges are only for source writes.
    result={candidate,authored,ids,overrides,protectedPairs,locks,u32(h+36),u32(h+40)};
    if(error)*error=nullptr;return true;
}
} } // namespace mkw::opos
