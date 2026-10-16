#pragma once
#include <mkw/math.hpp>
#include <mkw/visibility.hpp>
#include <atomic>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <memory>
#include <new>
#include <vector>

namespace mkw {
class Model;
// Old 3DS precomputed visibility for course objects and opponent racers
// (tools/build_o3ds_object_pvs.py writes models/pvs/course_NN.opvs).
//
// The track PVS (course_pvs.hpp) hides blocks of course triangles. Objects
// and karts need a volumetric test instead: for each camera column the
// sidecar lists which region boxes (chunks around the drivable surfaces and
// boxes around object placements) the opaque course can hide completely.
// Something is skipped only when its current world bounds lie wholly inside
// a hidden box, so a mover outside every box, or a camera outside the
// sampled columns, draws exactly as before. load() validates everything;
// any mismatch (stale sidecar, other geometry, bad data) keeps it empty.
class ObjectPvs {
public:
    struct Header {
        char magic[4];uint32_t version,fingerprint,parts;
        float originX,originZ,cell;uint32_t columnsX,columnsZ;
        float lookupCell;uint32_t lookupX,lookupZ;
        uint32_t regionCount,setCount,setBytes,entryCount;
    };
    static_assert(sizeof(Header)==64,"MOPV header");
    struct Column {uint16_t set;int16_t yLow,yHigh;}; // y in units of 8, rounded inward
    static_assert(sizeof(Column)==6,"MOPV column");
    struct Region {float lo[3],hi[3];};
    static constexpr uint16_t NoSet=0xffff;
    static constexpr float YUnit=8.f;

    void clear(){*this=ObjectPvs{};}
    bool ready() const {return regions_!=nullptr;}
    unsigned regions() const {return header_.regionCount;}
    unsigned sets() const {return header_.setCount;}
    // Sidecar image plus the per-set hidden lists.
    std::size_t bytes() const {return regions_?size_+(std::size_t(header_.lookupX)*header_.lookupZ+1+header_.entryCount)*2:0;}

    // Takes the sidecar image; keeps it only when it is complete and matches
    // the loaded course (fingerprint, part count).
    bool load(std::unique_ptr<uint8_t[]> data,std::size_t size,uint32_t fingerprint,uint32_t parts){
        clear();
        if(!data||size<sizeof(Header))return false;
        Header h;std::memcpy(&h,data.get(),sizeof h);
        if(std::memcmp(h.magic,"MOPV",4)||h.version!=1||h.fingerprint!=fingerprint||h.parts!=parts)return false;
        if(!finite(h.originX)||!finite(h.originZ)||!(h.cell>0)||!finite(h.cell)||!(h.lookupCell>0)||!finite(h.lookupCell))return false;
        if(!h.columnsX||!h.columnsZ||h.columnsX>4096||h.columnsZ>4096||!h.lookupX||!h.lookupZ||h.lookupX>4096||h.lookupZ>4096)return false;
        if(!h.regionCount||h.regionCount>0xffff||!h.setCount||h.setCount>=NoSet||h.setBytes!=(h.regionCount+7)/8||h.entryCount>0xffff)return false;
        const std::size_t columns=std::size_t(h.columnsX)*h.columnsZ,lookups=std::size_t(h.lookupX)*h.lookupZ;
        std::size_t at=sizeof h;
        const std::size_t columnAt=at;at+=pad4(columns*sizeof(Column));
        const std::size_t regionAt=at;at+=std::size_t(h.regionCount)*sizeof(Region);
        const std::size_t offsetAt=at;at+=pad4((lookups+1)*2);
        const std::size_t entryAt=at;at+=pad4(std::size_t(h.entryCount)*2);
        const std::size_t setAt=at;at+=pad4(std::size_t(h.setCount)*h.setBytes);
        if(at!=size)return false;
        const uint8_t* base=data.get();
        for(std::size_t i=0;i<columns;++i){
            Column c;std::memcpy(&c,base+columnAt+i*sizeof c,sizeof c);
            if(c.set!=NoSet&&(c.set>=h.setCount||c.yLow>c.yHigh))return false;
        }
        for(uint32_t i=0;i<h.regionCount;++i){
            Region r;std::memcpy(&r,base+regionAt+std::size_t(i)*sizeof r,sizeof r);
            for(unsigned k=0;k<3;++k)if(!finite(r.lo[k])||!finite(r.hi[k])||r.lo[k]>r.hi[k])return false;
        }
        uint16_t previous=0;
        for(std::size_t i=0;i<=lookups;++i){
            uint16_t o;std::memcpy(&o,base+offsetAt+i*2,2);
            if((i==0&&o!=0)||o<previous||o>h.entryCount)return false;
            previous=o;
        }
        if(previous!=h.entryCount)return false;
        for(uint32_t i=0;i<h.entryCount;++i){uint16_t e;std::memcpy(&e,base+entryAt+std::size_t(i)*2,2);if(e>=h.regionCount)return false;}
        // Region boxes are read as floats in place: the image is 4-byte aligned.
        if(reinterpret_cast<uintptr_t>(base)%alignof(float))return false;
        // Per-set hidden lists (rebuilt by set() on a column change): sized now.
        std::unique_ptr<uint16_t[]> hiddenOffsets(new(std::nothrow) uint16_t[lookups+1]);
        std::unique_ptr<uint16_t[]> hiddenEntries(new(std::nothrow) uint16_t[h.entryCount?h.entryCount:1]);
        if(!hiddenOffsets||!hiddenEntries)return false;
        header_=h;size_=size;data_=std::move(data);
        columns_=data_.get()+columnAt;regions_=reinterpret_cast<const Region*>(data_.get()+regionAt);
        offsets_=data_.get()+offsetAt;entries_=data_.get()+entryAt;sets_=data_.get()+setAt;
        hiddenOffsets_=std::move(hiddenOffsets);hiddenEntries_=std::move(hiddenEntries);cachedSet_=nullptr;
        return true;
    }

    // The regions potentially visible from a camera position, or nullptr
    // (draw everything) outside the sampled columns. Call it on one thread,
    // before that frame's hidden() calls: a new set rebuilds the per-cell
    // lists of its hidden regions, which hidden() then reads.
    const uint8_t* set(Vec3 eye) const {
        if(!regions_)return nullptr;
        const float x=(eye.x-header_.originX)/header_.cell,z=(eye.z-header_.originZ)/header_.cell;
        // Negated comparisons also reject NaN.
        if(!(x>=0.f&&z>=0.f&&x<float(header_.columnsX)&&z<float(header_.columnsZ)))return nullptr;
        Column c;std::memcpy(&c,columns_+(std::size_t(unsigned(z))*header_.columnsX+unsigned(x))*sizeof c,sizeof c);
        if(c.set==NoSet||!(eye.y>=float(c.yLow)*YUnit&&eye.y<=float(c.yHigh)*YUnit))return nullptr;
        const uint8_t* s=sets_+std::size_t(c.set)*header_.setBytes;
        if(s!=cachedSet_){
            const std::size_t lookups=std::size_t(header_.lookupX)*header_.lookupZ;
            uint16_t out=0;
            for(std::size_t cell=0;cell<lookups;++cell){
                hiddenOffsets_[cell]=out;
                uint16_t first,last;std::memcpy(&first,offsets_+cell*2,2);std::memcpy(&last,offsets_+cell*2+2,2);
                for(unsigned i=first;i<last;++i){
                    uint16_t r;std::memcpy(&r,entries_+std::size_t(i)*2,2);
                    if(!(s[r>>3]&(1u<<(r&7))))hiddenEntries_[out++]=r;
                }
            }
            hiddenOffsets_[lookups]=out;cachedSet_=s;
        }
        return s;
    }

    // True only when world bounds b lie inside a region box that `set`
    // (from set()) does not contain. Nonfinite bounds are never hidden.
    // `contained` (tests, QA) forces the full scan and reports whether any
    // region holds b.
    bool hidden(const uint8_t* set,const Bounds& b,bool* contained=nullptr) const {
        if(!set||!regions_)return false;
        const float x=((b.minimum.x+b.maximum.x)*.5f-header_.originX)/header_.lookupCell;
        const float z=((b.minimum.z+b.maximum.z)*.5f-header_.originZ)/header_.lookupCell;
        if(!(x>=0.f&&z>=0.f&&x<float(header_.lookupX)&&z<float(header_.lookupZ)))return false;
        const std::size_t cell=std::size_t(unsigned(z))*header_.lookupX+unsigned(x);
        if(set==cachedSet_&&!contained){
            // Only this set's hidden regions of the cell.
            for(unsigned i=hiddenOffsets_[cell];i<hiddenOffsets_[cell+1];++i){
                const Region& g=regions_[hiddenEntries_[i]];
                if(b.minimum.x>=g.lo[0]&&b.minimum.y>=g.lo[1]&&b.minimum.z>=g.lo[2]&&
                   b.maximum.x<=g.hi[0]&&b.maximum.y<=g.hi[1]&&b.maximum.z<=g.hi[2])return true;
            }
            return false;
        }
        uint16_t first,last;std::memcpy(&first,offsets_+cell*2,2);std::memcpy(&last,offsets_+cell*2+2,2);
        for(unsigned i=first;i<last;++i){
            uint16_t r;std::memcpy(&r,entries_+std::size_t(i)*2,2);
            const Region& g=regions_[r];
            if(!(b.minimum.x>=g.lo[0]&&b.minimum.y>=g.lo[1]&&b.minimum.z>=g.lo[2]&&
                 b.maximum.x<=g.hi[0]&&b.maximum.y<=g.hi[1]&&b.maximum.z<=g.hi[2]))continue;
            if(contained)*contained=true;
            if(!(set[r>>3]&(1u<<(r&7))))return true;
        }
        return false;
    }

    // QA counters (any thread): tested and hidden objects and racers.
    struct Stats {std::atomic<uint32_t> objectTests{0},objectsHidden{0},racerTests{0},racersHidden{0};};
    mutable Stats stats;
    bool hiddenCounted(const uint8_t* set,const Bounds& b,bool racer) const {
        if(!set)return false;
        const bool h=hidden(set,b);
#ifndef MKW_RELEASE
        (racer?stats.racerTests:stats.objectTests).fetch_add(1,std::memory_order_relaxed);
        if(h)(racer?stats.racersHidden:stats.objectsHidden).fetch_add(1,std::memory_order_relaxed);
#else
        (void)racer;
#endif
        return h;
    }

    ObjectPvs()=default;
    ObjectPvs& operator=(ObjectPvs&& o) noexcept {
        header_=o.header_;size_=o.size_;data_=std::move(o.data_);columns_=o.columns_;regions_=o.regions_;
        offsets_=o.offsets_;entries_=o.entries_;sets_=o.sets_;
        hiddenOffsets_=std::move(o.hiddenOffsets_);hiddenEntries_=std::move(o.hiddenEntries_);cachedSet_=o.cachedSet_;
        for(auto* p:{&stats.objectTests,&stats.objectsHidden,&stats.racerTests,&stats.racersHidden})p->store(0,std::memory_order_relaxed);
        return *this;
    }
private:
    static bool finite(float v){return std::isfinite(v);}
    static std::size_t pad4(std::size_t n){return (n+3)&~std::size_t(3);}
    Header header_{};std::size_t size_=0;
    std::unique_ptr<uint8_t[]> data_;
    const uint8_t* columns_=nullptr;const Region* regions_=nullptr;
    const uint8_t* offsets_=nullptr;const uint8_t* entries_=nullptr;const uint8_t* sets_=nullptr;
    std::unique_ptr<uint16_t[]> hiddenOffsets_,hiddenEntries_;
    mutable const uint8_t* cachedSet_=nullptr;
};

// Course identity the sidecar was built for (tools/build_o3ds_object_pvs.py
// object_fingerprint): per part in list order its vertex, index and submesh
// counts; per submesh its triangle count and the 32-bit sum of per-triangle
// hashes, so reordering whole triangles (track PVS, vertex-cache pass)
// keeps it; then every position's float bits.
struct ObjectFingerprint {
    uint32_t h=0x811c9dc5u;
    static uint32_t fnv(uint32_t h,uint32_t word){return (h^word)*0x01000193u;}
    void add(uint32_t word){h=fnv(h,word);}
    static uint32_t triangles(const uint16_t* indices,uint32_t count){
        uint32_t sum=0;
        for(uint32_t i=0;i+2<count;i+=3)sum+=fnv(fnv(fnv(0x811c9dc5u,indices[i]),indices[i+1]),indices[i+2]);
        return sum;
    }
    void positions(const float* xyz,std::size_t stride,uint32_t count){
        for(uint32_t i=0;i<count;++i){uint32_t p[3];std::memcpy(p,reinterpret_cast<const uint8_t*>(xyz)+i*stride,sizeof p);add(p[0]);add(p[1]);add(p[2]);}
    }
};

inline ObjectPvs& objectPvs(){static ObjectPvs pvs;return pvs;}
// Reads models/pvs/course_NN.opvs for the parts loaded from `list`
// ("models/course_NN.list"); enable=false (New 3DS, unknown hardware, QA
// o3ds-objpvs-off.txt) or any mismatch leaves objectPvs() empty.
bool attachObjectPvs(const std::vector<std::unique_ptr<Model>>& track,const char* list,bool enable);
}
