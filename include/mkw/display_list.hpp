#pragma once
#include <3ds.h>
#include <cstdint>
#include <cstring>
#include <vector>

namespace mkw {
// A static model's GPU command list (docs/o3ds-draw-pipeline.md, stage 2):
// its materials, uniforms and one draw per culling block, recorded by the
// ordinary drawNow once per fog state and replayed per frame. Replaying
// patches the frame's uniform values and the visible blocks' draw triggers
// in place and queues the list as its own GX command list. One copy per
// frame slot: while frames pipeline, the GPU may still read the previous
// frame's copy. Owned by the race's course state and destroyed with the
// GPU idle (every course release follows Renderer::waitForGpu).
struct DisplayList {
    struct Upload {uint32_t offset;uint16_t id,count;}; // C3D_UniformUpload
    struct Submesh {uint32_t firstDraw=0,draws=0,firstUpload=0,uploads=0;uint16_t bools=0;bool boolsDirty=false;};
    struct Copy {
        uint32_t* words=nullptr;uint32_t capacity=0,size=0; // words
        bool valid=false;
        float fog[8]{};                 // the renderer's retail fog state it was recorded for
        std::vector<Upload> uploads;    // float uniform runs, per submesh's first draw
        std::vector<uint32_t> triggers; // per draw: word offset of its DRAWELEMENTS header
        std::vector<uint32_t> counts;   // per draw: the index count its words hold, 0 when its trigger is disabled
        std::vector<Submesh> submeshes;
        Copy()=default;
        Copy(const Copy&)=delete;
        Copy& operator=(const Copy&)=delete;
        ~Copy(){if(words)linearFree(words);}
    };
    Copy copies[2];
    bool unsupported=false; // a material the list cannot carry: drawn directly from then on
    unsigned records=0,replays=0,mismatches=0,fallbacks=0;
    uint32_t bytes() const {return (copies[0].capacity+copies[1].capacity)*4;}
    DisplayList()=default;
    DisplayList(const DisplayList&)=delete;
    DisplayList& operator=(const DisplayList&)=delete;
};
}
