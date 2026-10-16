#pragma once
#include <atomic>
#include <cstdint>

namespace mkw {
// Separate from model reduction: the marker changes only weighted, lit
// course-object skinning. Configuration is fixed before asset workers start.
struct ObjectGpuSkin {
    bool enabled=false;
    std::atomic<uint32_t> loaded{0},fallbacks{0},vertexReferences{0};
    static constexpr bool eligible(bool known,bool new3ds,bool qa,bool fullQuality,bool requested){
#ifdef MKW_RELEASE
        return false;
#else
        return known&&!new3ds&&qa&&!fullQuality&&requested;
#endif
    }
    void configure(bool known,bool new3ds,bool qa,bool fullQuality,bool requested){
        enabled=eligible(known,new3ds,qa,fullQuality,requested);
        loaded.store(0,std::memory_order_relaxed);
        fallbacks.store(0,std::memory_order_relaxed);
        vertexReferences.store(0,std::memory_order_relaxed);
    }
};
inline ObjectGpuSkin& objectGpuSkin(){static ObjectGpuSkin policy;return policy;}
}
