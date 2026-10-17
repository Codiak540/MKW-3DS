// Host test for include/mkw/object_pvs.hpp (Old 3DS object/racer visibility).
//   test_object_pvs                 unit tests (validation, lookup, containment)
//   test_object_pvs fingerprint     prints the fingerprint of the fixed model below
//   test_object_pvs query FILE FP PARTS ex ey ez lox loy loz hix hiy hiz
//                                   loads FILE, prints "loaded set hidden"
// tests/test_object_pvs.py builds sidecars with tools/build_o3ds_object_pvs.py
// and checks them through the query mode.
#include <mkw/object_pvs.hpp>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <random>
#include <vector>

using mkw::ObjectPvs;
using mkw::Vec3;
namespace {
struct Builder {
    ObjectPvs::Header h{};
    std::vector<ObjectPvs::Column> columns;
    std::vector<ObjectPvs::Region> regions;
    std::vector<uint16_t> offsets,entries;
    std::vector<uint8_t> sets;
    Builder(){
        std::memcpy(h.magic,"MOPV",4);h.version=1;h.fingerprint=0x1234;h.parts=1;
        h.originX=-1000;h.originZ=-1000;h.cell=1000;h.columnsX=2;h.columnsZ=2;
        h.lookupCell=2000;h.lookupX=1;h.lookupZ=1;
        // Region 0: a small box at the origin; region 1: a larger one around it.
        regions={{{-100,-100,-100},{100,100,100}},{{-900,-500,-900},{900,500,900}}};
        offsets={0,2};entries={0,1};
        h.regionCount=2;h.setBytes=1;h.setCount=2;h.entryCount=2;
        sets={0x00,0x02}; // set 0: nothing visible; set 1: region 1 visible
        // Columns: (0,0) set 0, (1,0) set 1, (0,1) no data, (1,1) set 0.
        columns={{0,-10,10},{1,-10,10},{ObjectPvs::NoSet,0,0},{0,-10,10}};
    }
    static void put(std::vector<uint8_t>& out,const void* p,std::size_t n){auto* b=static_cast<const uint8_t*>(p);out.insert(out.end(),b,b+n);}
    static void pad(std::vector<uint8_t>& out){while(out.size()%4)out.push_back(0);}
    std::vector<uint8_t> bytes() const {
        std::vector<uint8_t> out;put(out,&h,sizeof h);
        put(out,columns.data(),columns.size()*sizeof(ObjectPvs::Column));pad(out);
        put(out,regions.data(),regions.size()*sizeof(ObjectPvs::Region));
        put(out,offsets.data(),offsets.size()*2);pad(out);
        put(out,entries.data(),entries.size()*2);pad(out);
        put(out,sets.data(),sets.size());pad(out);
        return out;
    }
};
bool load(ObjectPvs& pvs,const std::vector<uint8_t>& bytes,uint32_t fp=0x1234,uint32_t parts=1){
    std::unique_ptr<uint8_t[]> data(new uint8_t[bytes.size()]);std::memcpy(data.get(),bytes.data(),bytes.size());
    return pvs.load(std::move(data),bytes.size(),fp,parts);
}
mkw::Bounds box(float lx,float ly,float lz,float hx,float hy,float hz){return {{lx,ly,lz},{hx,hy,hz}};}

void units(){
    ObjectPvs pvs;
    Builder b;
    assert(load(pvs,b.bytes())&&pvs.ready()&&pvs.regions()==2&&pvs.sets()==2);
    // Column lookup: inside the grid and the height interval (units of 8).
    const uint8_t* s0=pvs.set({-500,0,-500});
    const uint8_t* s1=pvs.set({500,0,-500});
    assert(s0&&s1&&s0!=s1);
    assert(pvs.set({-500,80,-500})&&!pvs.set({-500,81,-500})&&!pvs.set({-500,-81,-500}));
    assert(!pvs.set({-500,0,500}));                      // column without data
    assert(pvs.set({500,0,500})==s0);
    assert(!pvs.set({-1001,0,-500})&&!pvs.set({1000,0,-500})&&!pvs.set({-500,0,1000}));
    const float nan=std::numeric_limits<float>::quiet_NaN();
    assert(!pvs.set({nan,0,0})&&!pvs.set({0,nan,0})&&!pvs.set({0,0,nan}));
    // Containment: hidden only wholly inside a hidden region.
    assert(pvs.hidden(s0,box(-50,-50,-50,50,50,50)));
    assert(pvs.hidden(s0,box(-100,-100,-100,100,100,100)));    // touching the faces
    assert(pvs.hidden(s0,box(-500,-50,-50,50,50,50)));         // inside region 1 only
    assert(!pvs.hidden(s0,box(-950,-50,-50,50,50,50)));        // outside both
    assert(!pvs.hidden(s0,box(-50,-50,-50,50,600,50)));        // pokes out of region 1
    assert(pvs.hidden(s1,box(-50,-50,-50,50,50,50)));          // region 0 hidden in set 1
    assert(!pvs.hidden(s1,box(-500,-50,-50,50,50,50)));        // only region 1 holds it: visible
    assert(!pvs.hidden(nullptr,box(-50,-50,-50,50,50,50)));
    assert(!pvs.hidden(s0,box(nan,-50,-50,50,50,50)));
    assert(!pvs.hidden(s0,box(-50,-50,-50,50,nan,50)));
    assert(!pvs.hidden(s0,box(5000,0,5000,5010,10,5010)));    // outside the lookup grid
    // QA counters only count with a set.
    assert(pvs.hiddenCounted(s0,box(-50,-50,-50,50,50,50),false));
    assert(!pvs.hiddenCounted(nullptr,box(-50,-50,-50,50,50,50),true));
#ifndef MKW_RELEASE
    assert(pvs.stats.objectTests.load()==1&&pvs.stats.objectsHidden.load()==1&&pvs.stats.racerTests.load()==0);
#endif
    // The per-set hidden lists set() builds answer exactly as the full scan.
    {
        std::mt19937 rng(5);std::uniform_real_distribution<float> u(-1100,1100),e(0,700);
        for(const Vec3 eye:{Vec3{-500,0,-500},Vec3{500,0,-500},Vec3{500,0,500}}){
            const uint8_t* s=pvs.set(eye);assert(s);
            for(int i=0;i<4000;++i){
                const float cx=u(rng),cy=u(rng)*.3f,cz=u(rng),ex=e(rng),ey=e(rng),ez=e(rng);
                const auto b=box(cx-ex,cy-ey,cz-ez,cx+ex,cy+ey,cz+ez);
                bool contained=false;
                assert(pvs.hidden(s,b)==pvs.hidden(s,b,&contained));
            }
        }
    }
    assert(pvs.bytes()>b.bytes().size());
    pvs.clear();assert(!pvs.ready()&&!pvs.set({-500,0,-500})&&!pvs.hidden(s0,box(-50,-50,-50,50,50,50))&&pvs.bytes()==0);

    // Identity and every field: a mismatch keeps it empty.
    assert(!load(pvs,b.bytes(),0x1235)&&!pvs.ready());
    assert(!load(pvs,b.bytes(),0x1234,2)&&!pvs.ready());
    auto reject=[&](auto change){Builder c;change(c);ObjectPvs p;const bool ok=load(p,c.bytes());assert(!ok&&!p.ready());};
    reject([](Builder& c){c.h.magic[0]='X';});
    reject([](Builder& c){c.h.version=2;});
    reject([](Builder& c){c.h.cell=0;});
    reject([](Builder& c){c.h.cell=std::numeric_limits<float>::infinity();});
    reject([](Builder& c){c.h.originX=std::numeric_limits<float>::quiet_NaN();});
    reject([](Builder& c){c.h.lookupCell=-1;});
    reject([](Builder& c){c.h.columnsX=0;});
    reject([](Builder& c){c.h.columnsX=5000;});
    reject([](Builder& c){c.h.setBytes=2;});
    reject([](Builder& c){c.h.setCount=5;});              // size no longer matches
    reject([](Builder& c){c.columns[0].set=2;});           // set out of range
    reject([](Builder& c){c.columns[1].yLow=11;});         // empty interval
    reject([](Builder& c){c.regions[1].lo[1]=std::numeric_limits<float>::quiet_NaN();});
    reject([](Builder& c){c.regions[0].lo[0]=200;});       // min > max
    reject([](Builder& c){c.offsets={1,2};});              // must start at 0
    reject([](Builder& c){c.offsets={0,1};});              // must end at entryCount
    reject([](Builder& c){c.entries={0,2};});              // region out of range
    reject([](Builder& c){c.h.entryCount=3;c.entries={0,1,1};c.offsets={0,2};});
    {   // Truncated or extended images.
        Builder c;auto bytes=c.bytes();
        ObjectPvs p;
        auto shorter=bytes;shorter.pop_back();assert(!load(p,shorter));
        auto longer=bytes;longer.insert(longer.end(),4,0);assert(!load(p,longer));
        assert(!load(p,std::vector<uint8_t>(10,0)));
        assert(!p.load(nullptr,bytes.size(),0x1234,1));
    }
    {   // Offsets must not decrease.
        Builder c;c.h.lookupX=2;c.offsets={0,2,1};c.entries={0,1};
        ObjectPvs p;assert(!load(p,c.bytes()));
        c.offsets={0,1,2};assert(load(p,c.bytes()));
    }
    // Fingerprint: reordering whole triangles inside a submesh keeps it,
    // anything else changes it.
    const uint16_t a[]={0,1,2, 2,3,0, 4,5,6},r[]={4,5,6, 0,1,2, 2,3,0},rot[]={1,2,0, 2,3,0, 4,5,6},o[]={0,1,2, 2,3,0, 4,5,7};
    using F=mkw::ObjectFingerprint;
    assert(F::triangles(a,9)==F::triangles(r,9));
    assert(F::triangles(a,9)!=F::triangles(rot,9));
    assert(F::triangles(a,9)!=F::triangles(o,9));
    assert(F::triangles(a,6)!=F::triangles(a,9));
}

// Same model as tests/test_object_pvs.py FIXED_MODEL.
uint32_t fixedFingerprint(){
    const float positions[]={0,0,0, 100,0,0, 100,0,100, 0,0,100, 0,50,0};
    const uint16_t indices[]={0,1,2, 2,3,0, 0,1,4};
    const uint32_t spans[][2]={{0,6},{6,3}};
    mkw::ObjectFingerprint fp;fp.add(1);
    fp.add(5);fp.add(9);fp.add(2);
    for(const auto& s:spans){fp.add(s[1]/3);fp.add(mkw::ObjectFingerprint::triangles(indices+s[0],s[1]));}
    fp.positions(positions,12,5);
    return fp.h;
}
}

int main(int argc,char** argv){
    if(argc==2&&!std::strcmp(argv[1],"fingerprint")){std::printf("%08x\n",fixedFingerprint());return 0;}
    if(argc==14&&!std::strcmp(argv[1],"query")){
        FILE* f=std::fopen(argv[2],"rb");if(!f)return 2;
        std::vector<uint8_t> bytes;int c;while((c=std::fgetc(f))!=EOF)bytes.push_back(uint8_t(c));std::fclose(f);
        ObjectPvs pvs;
        const bool ok=load(pvs,bytes,uint32_t(std::strtoul(argv[3],nullptr,16)),uint32_t(std::atoi(argv[4])));
        float v[9];for(int i=0;i<9;++i)v[i]=std::strtof(argv[5+i],nullptr);
        const uint8_t* s=pvs.set({v[0],v[1],v[2]});
        std::printf("%d %d %d\n",int(ok),int(s!=nullptr),int(pvs.hidden(s,box(v[3],v[4],v[5],v[6],v[7],v[8]))));
        return 0;
    }
    units();
    std::puts("object pvs: ok");
    return 0;
}
