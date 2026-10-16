#pragma once
#include <mkw/math.hpp>
#include <cstdint>
#include <memory>
#include <vector>

namespace mkw {
class Model;
// Old 3DS track sectioning and precomputed visibility, one per course part
// (tools/build_o3ds_pvs.py writes models/pvs/<part>.pvs).
//
// attach() checks the sidecar against the geometry Model::load produced (an
// FNV-1a fingerprint of counts, submesh spans, indices and positions), then
// reorders the triangles of order-independent submeshes (opaque or alpha
// tested, depth writing, not blended) into spatial chunks and rebuilds the
// static-culling blocks with the sidecar's block size. Vertices, materials,
// submesh order and every blended submesh's triangle order are unchanged.
// mask() returns the potentially visible blocks for a camera position, or
// nullptr (draw everything) outside the sampled camera columns.
class CoursePvs {
public:
    // Without a valid sidecar the part keeps its authored order and the
    // ordinary static culling (Model::enableStaticCulling).
    bool attach(Model& model,const char* part);
    const uint8_t* mask(Vec3 eye) const;
    unsigned maskBytes() const {return setBytes_;}
    bool sectioned() const {return sectioned_;}
    unsigned blocks() const {return blocks_;}
    unsigned columns() const {return columnsX_*columnsZ_;}
    unsigned sets() const {return sets_;}
    // Frames that used a mask / drew everything since attach().
    mutable uint32_t hits=0,misses=0;
private:
    struct Column {float yMin,yMax;uint32_t set;};
    bool sectioned_=false;
    uint32_t blocks_=0,setBytes_=0,sets_=0,columnsX_=0,columnsZ_=0;
    float originX_=0,originZ_=0,cell_=1;
    std::vector<Column> columns_;
    std::vector<uint8_t> masks_;
};
// Course parts in list order; enable=false (New 3DS, unknown hardware, QA
// o3ds-pvs-off.txt) keeps the authored order and ordinary culling.
struct CourseSections {
    std::vector<CoursePvs> parts;
    // Reads the part names from the course list the parts were loaded from.
    void attach(std::vector<std::unique_ptr<Model>>& track,const char* list,bool enable);
    void clear(){parts.clear();}
};
}
