#include "mkw/model_variant.hpp"
#include <cassert>
#include <limits>

namespace {
struct Vertex {float position[3],normal[3],uv[4];uint8_t color[4];};
struct Submesh {uint32_t first,count;int32_t texture;uint32_t material[8];};
static_assert(sizeof(Vertex)==44&&sizeof(Submesh)==44);
struct Reader {
    const std::vector<uint8_t>& bytes;size_t offset=0;
    bool read(void* out,size_t count){if(count>bytes.size()-offset)return false;std::memcpy(out,bytes.data()+offset,count);offset+=count;return true;}
    bool matches(const void* expected,size_t count){if(count>bytes.size()-offset||std::memcmp(expected,bytes.data()+offset,count))return false;offset+=count;return true;}
    bool atEnd()const{return offset==bytes.size();}
};
struct Allocation {
    std::vector<uint16_t> storage;uint16_t* data=nullptr;bool fail=false;
    std::vector<Vertex> vertexStorage;Vertex* vertexData=nullptr;
    bool allocate(uint32_t count){if(fail)return false;storage.resize(count);data=storage.data();return true;}
    bool allocateVertices(uint32_t count){if(fail)return false;vertexStorage.resize(count);vertexData=vertexStorage.data();return true;}
};
struct Fixture {
    uint32_t header[6]={0x4c444d47,3,6,9,2,1};
    std::vector<Submesh> originals{{0,6,0,{}},{6,3,0,{}}};
    std::vector<std::array<char,64>> names{1};
    Vertex vertices[6]{};
    uint16_t indices[9]={0,1,2,0,2,3,4,5,3};
    std::vector<uint8_t> bytes;
    static constexpr size_t Names=24+2*sizeof(Submesh),Vertices=Names+64,Indices=Vertices+6*sizeof(Vertex);
    Fixture(){
        std::memcpy(names[0].data(),"mario",6);
        for(unsigned i=0;i<6;++i){vertices[i].position[0]=float(i);vertices[i].color[3]=255;}
        uint32_t candidate[6];std::memcpy(candidate,header,sizeof header);candidate[3]=6;
        append(candidate,sizeof candidate);
        const Submesh spans[]={{0,3,0,{}},{3,3,0,{}}};append(spans,sizeof spans);
        append(names.data(),64);append(vertices,sizeof vertices);
        const uint16_t reduced[]={0,1,2,4,5,3};append(reduced,sizeof reduced);
    }
    void append(const void* source,size_t count){const auto* p=static_cast<const uint8_t*>(source);bytes.insert(bytes.end(),p,p+count);}
    bool accepts(const std::vector<uint8_t>& candidate,bool allocationFails=false,bool compact=false){
        Reader reader{candidate};Allocation allocation;allocation.fail=allocationFails;
        std::vector<Submesh> spans;std::vector<uint32_t> remap;uint32_t count=0;
        const bool result=mkw::readModelVariant(reader,header,sizeof(Submesh),originals,names,vertices,indices,allocation,spans,count,remap);
        if(result){
            assert(count==6&&spans.size()==2&&spans[1].first==3);
            if(compact){
                assert(allocation.storage==std::vector<uint16_t>({0,1,2,3,4,2}));
                assert(remap==std::vector<uint32_t>({0,2,3,4,5}));
                assert(allocation.vertexStorage.size()==5);
                for(size_t i=0;i<remap.size();++i)assert(!std::memcmp(&allocation.vertexStorage[i],&vertices[remap[i]],sizeof(Vertex)));
            }else {assert(allocation.storage==std::vector<uint16_t>({0,1,2,4,5,3}));assert(remap.empty());}
        }
        return result;
    }
};
void u32(std::vector<uint8_t>& bytes,size_t offset,uint32_t value){std::memcpy(bytes.data()+offset,&value,4);}
void u16(std::vector<uint8_t>& bytes,size_t offset,uint16_t value){std::memcpy(bytes.data()+offset,&value,2);}
}

int main(){
    Fixture fixture;
    assert(fixture.accepts(fixture.bytes));
    assert(!fixture.accepts(fixture.bytes,true));
    // Independent corruptions: source format/counts, material, texture name,
    // position/normal/UV/colour, index validity and authored-shape membership.
    for(size_t offset:{size_t(0),size_t(4),size_t(8),size_t(16),size_t(20),size_t(36),Fixture::Names,Fixture::Vertices,Fixture::Vertices+12,Fixture::Vertices+24,Fixture::Vertices+40}){
        auto bad=fixture.bytes;bad[offset]^=1;assert(!fixture.accepts(bad));
    }
    {auto bad=fixture.bytes;u32(bad,12,9);assert(!fixture.accepts(bad));} // no reduction
    {auto bad=fixture.bytes;u32(bad,24+sizeof(Submesh),4);assert(!fixture.accepts(bad));} // gap
    {auto bad=fixture.bytes;u32(bad,28,0);assert(!fixture.accepts(bad));} // erased shape
    {auto bad=fixture.bytes;u32(bad,28,0xffffffffu);assert(!fixture.accepts(bad));} // overflow
    {auto bad=fixture.bytes;u16(bad,Fixture::Indices,6);assert(!fixture.accepts(bad));} // out of bounds
    {auto bad=fixture.bytes;u16(bad,Fixture::Indices,4);assert(!fixture.accepts(bad));} // wrong shape
    {auto bad=fixture.bytes;bad.push_back(0);assert(!fixture.accepts(bad));}
    // Truncation at every possible position must leave the original usable.
    for(size_t size=0;size<fixture.bytes.size();++size){
        auto bad=fixture.bytes;bad.resize(size);assert(!fixture.accepts(bad));
    }
    assert(fixture.originals[1].first==6&&fixture.header[3]==9&&fixture.indices[0]==0);

    // Compacted vertices keep exact authored attributes and use a sorted,
    // strict source map so both animation bindings and ranges can be remapped.
    fixture.bytes.resize(Fixture::Vertices);u32(fixture.bytes,8,5);
    const uint32_t sourceIds[]={0,2,3,4,5};
    for(const uint32_t id:sourceIds)fixture.append(&fixture.vertices[id],sizeof(Vertex));
    const size_t compactIndices=fixture.bytes.size();
    const uint16_t compactTriangles[]={0,1,2,3,4,2};fixture.append(compactTriangles,sizeof compactTriangles);
    const size_t trailer=fixture.bytes.size();
    const uint32_t mapHeader[]={0x4d52334f,1,6,5};fixture.append(mapHeader,sizeof mapHeader);fixture.append(sourceIds,sizeof sourceIds);
    assert(fixture.accepts(fixture.bytes,false,true));
    assert(!fixture.accepts(fixture.bytes,true,true));
    for(size_t offset:{Fixture::Vertices,Fixture::Vertices+12,Fixture::Vertices+24,Fixture::Vertices+40,trailer,trailer+4,trailer+8,trailer+12}){
        auto bad=fixture.bytes;bad[offset]^=1;assert(!fixture.accepts(bad,false,true));
    }
    {auto bad=fixture.bytes;u32(bad,trailer+16+4,0);assert(!fixture.accepts(bad,false,true));} // duplicate
    {auto bad=fixture.bytes;u32(bad,trailer+16+4,6);assert(!fixture.accepts(bad,false,true));} // out of bounds
    {auto bad=fixture.bytes;u32(bad,trailer+16+4,1);assert(!fixture.accepts(bad,false,true));} // stale vertex bytes
    {auto bad=fixture.bytes;u16(bad,compactIndices,5);assert(!fixture.accepts(bad,false,true));} // compact bound
    {auto bad=fixture.bytes;u16(bad,compactIndices,3);assert(!fixture.accepts(bad,false,true));} // wrong authored shape
    {auto bad=fixture.bytes;bad.push_back(0);assert(!fixture.accepts(bad,false,true));}
    for(size_t size=0;size<fixture.bytes.size();++size){auto bad=fixture.bytes;bad.resize(size);assert(!fixture.accepts(bad,false,true));}
    // Lossless compaction can save skinning work without deleting a triangle.
    fixture.header[3]=6;fixture.originals[0].count=3;fixture.originals[1].first=3;
    const uint16_t weldedOriginal[]={0,2,3,4,5,3};std::memcpy(fixture.indices,weldedOriginal,sizeof weldedOriginal);
    assert(fixture.accepts(fixture.bytes,false,true));

    // O3RM v2: UV0 may be re-baked, a source vertex may repeat (UV seam) and a
    // slot may name its own re-baked texture. Nothing else may change.
    {
        Fixture v2;
        auto& b=v2.bytes;b.resize(Fixture::Vertices);u32(b,8,5);
        std::memcpy(b.data()+Fixture::Names,"mario_o3a1b2c3",15);
        const uint32_t ids[]={0,2,2,3,5};
        for(size_t i=0;i<5;++i){Vertex v=v2.vertices[ids[i]];if(i==2){v.uv[0]=0.25f;v.uv[1]=0.75f;}v2.append(&v,sizeof v);}
        const size_t uvAt=Fixture::Vertices+2*sizeof(Vertex)+24;
        const uint16_t tris[]={0,1,2,3,4,3};v2.append(tris,sizeof tris);
        const size_t trailer=b.size();
        const uint32_t mh[]={0x4d52334f,2,6,5};v2.append(mh,sizeof mh);v2.append(ids,sizeof ids);
        auto accept=[&](const std::vector<uint8_t>& bytes,bool wantTextures=true){
            Reader reader{bytes};Allocation allocation;std::vector<Submesh> spans;std::vector<uint32_t> remap;uint32_t count=0,format=0;
            std::vector<std::array<char,64>> textures;
            const bool ok=mkw::readModelVariant(reader,v2.header,sizeof(Submesh),v2.originals,v2.names,v2.vertices,v2.indices,
                                                allocation,spans,count,remap,wantTextures?&textures:nullptr,&format);
            if(ok)assert(format==2&&remap==std::vector<uint32_t>({0,2,2,3,5})&&!std::strcmp(textures[0].data(),"mario_o3a1b2c3"));
            return ok;
        };
        assert(accept(b));
        assert(!accept(b,false));                                    // caller cannot load re-baked textures
        {auto bad=b;bad[uvAt-24]^=1;assert(!accept(bad));}           // position changed
        {auto bad=b;bad[uvAt+8]^=1;assert(!accept(bad));}            // UV1 changed
        {auto bad=b;bad[uvAt+16]^=1;assert(!accept(bad));}           // colour changed
        {auto bad=b;std::memcpy(bad.data()+Fixture::Names,"luigi_o3a1b2c3",15);assert(!accept(bad));} // foreign texture
        {auto bad=b;std::memcpy(bad.data()+Fixture::Names,"mario_o3zz",11);assert(!accept(bad));}     // bad tag
        {auto bad=b;u32(bad,trailer+16+4,3);u32(bad,trailer+16+8,2);assert(!accept(bad));} // decreasing
        {auto bad=b;u32(bad,trailer+4,1);assert(!accept(bad));}      // v1 cannot rename or re-bake
        for(size_t size=0;size<b.size();++size){auto bad=b;bad.resize(size);assert(!accept(bad));}

        // O3RM v3: a record may also move (position only) with its bone-local skin position.
        auto v3=b;u32(v3,trailer+4,3);
        float moved=7.5f;std::memcpy(v3.data()+Fixture::Vertices+sizeof(Vertex),&moved,4);
        const uint32_t sp[]={0x5053334f,5};v3.insert(v3.end(),reinterpret_cast<const uint8_t*>(sp),reinterpret_cast<const uint8_t*>(sp)+8);
        for(int i=0;i<5;++i){const float p[3]={float(i),1.f,2.f};v3.insert(v3.end(),reinterpret_cast<const uint8_t*>(p),reinterpret_cast<const uint8_t*>(p)+12);}
        auto accept3=[&](const std::vector<uint8_t>& bytes,bool wantPositions=true){
            Reader reader{bytes};Allocation allocation;std::vector<Submesh> spans;std::vector<uint32_t> remap;uint32_t count=0,format=0;
            std::vector<std::array<char,64>> textures;std::vector<std::array<float,3>> positions;
            const bool ok=mkw::readModelVariant(reader,v2.header,sizeof(Submesh),v2.originals,v2.names,v2.vertices,v2.indices,
                                                allocation,spans,count,remap,&textures,&format,wantPositions?&positions:nullptr);
            if(ok)assert(format==3&&positions.size()==5&&positions[3][0]==3.f);
            return ok;
        };
        assert(accept3(v3));
        assert(!accept3(v3,false));                                  // caller must apply skin positions
        assert(!accept(b.size()<v3.size()?std::vector<uint8_t>(v3.begin(),v3.begin()+b.size()):v3)); // v3 header without block
        {auto bad=v3;bad[Fixture::Vertices+sizeof(Vertex)+12]^=1;assert(!accept3(bad));} // normal changed
        {auto bad=v3;bad[Fixture::Vertices+sizeof(Vertex)+40]^=1;assert(!accept3(bad));} // colour changed
        {auto bad=v3;u32(bad,b.size()+4,4);assert(!accept3(bad));}  // wrong count
        {auto bad=v3;const float nan=std::numeric_limits<float>::quiet_NaN();std::memcpy(bad.data()+b.size()+8,&nan,4);assert(!accept3(bad));}
        {auto bad=v3;bad.resize(b.size());assert(!accept3(bad));}   // missing O3SP
        {auto bad=v3;bad.push_back(0);assert(!accept3(bad));}
        {auto bad=b;std::memcpy(bad.data()+Fixture::Vertices+sizeof(Vertex),&moved,4);assert(!accept(bad));} // v2 cannot move
        struct Skin {float p[3],n[3];uint32_t matrix;};
        Skin skin[2]={{{0,0,0},{0,1,0},4},{{1,1,1},{1,0,0},5}};
        assert(mkw::applySkinPositions(skin,2,{}));
        assert(!mkw::applySkinPositions(skin,2,{{{9,9,9}}}));
        assert(mkw::applySkinPositions(skin,2,{{{9,8,7}},{{6,5,4}}})&&skin[1].p[2]==4&&skin[1].n[0]==1&&skin[1].matrix==5);
    }

    struct Binding {uint32_t bone;float position;};
    struct Range {uint32_t first,count;};
    Binding bindings[]={{0,0},{1,1},{2,2},{3,3},{4,4},{5,5},{6,6},{7,7}};
    Range ranges[]={{0,3},{2,4},{3,1},{6,2},{8,0}};
    assert(mkw::compactModelAnimation(bindings,8,ranges,5,{0,2,4,7}));
    assert(bindings[0].bone==0&&bindings[1].bone==2&&bindings[2].bone==4&&bindings[3].bone==7);
    assert(ranges[0].first==0&&ranges[0].count==2);
    assert(ranges[1].first==1&&ranges[1].count==2);
    assert(ranges[2].first==2&&ranges[2].count==0);
    assert(ranges[3].first==3&&ranges[3].count==1);
    assert(ranges[4].first==4&&ranges[4].count==0);
    Binding valid[]={{0,0},{1,1},{2,2}};Range validRange[]={{0,3}};
    for(const std::vector<uint32_t>& bad:std::vector<std::vector<uint32_t>>{{},{0,0},{1,0},{0,3}}){
        assert(!mkw::compactModelAnimation(valid,3,validRange,1,bad));assert(valid[1].bone==1&&validRange[0].count==3);
    }
    Range invalid[]={{2,2}};assert(!mkw::compactModelAnimation(valid,3,invalid,1,{0,2}));assert(valid[1].bone==1);
    // Repeated sources gather out of place; an in-place copy would read
    // already overwritten bindings once remap[i] < i.
    Binding repeat[]={{0,0},{1,1},{2,2},{3,3},{4,4},{5,5}};Range repeatRanges[]={{0,2},{2,4}};
    assert(!mkw::compactModelAnimation(repeat,6,repeatRanges,2,{1,1,3,5}));
    assert(mkw::compactModelAnimation(repeat,6,repeatRanges,2,{1,1,3,5},true));
    assert(repeat[0].bone==1&&repeat[1].bone==1&&repeat[2].bone==3&&repeat[3].bone==5);
    assert(repeatRanges[0].first==0&&repeatRanges[0].count==2&&repeatRanges[1].first==2&&repeatRanges[1].count==2);
    Binding later[]={{0,0},{1,1},{2,2},{3,3}};Range laterRange[]={{0,4}};
    assert(mkw::compactModelAnimation(later,4,laterRange,1,{0,0,0,3},true));
    assert(later[0].bone==0&&later[1].bone==0&&later[2].bone==0&&later[3].bone==3);
    assert(!mkw::compactModelAnimation(later,4,laterRange,1,{2,1},true));
}
