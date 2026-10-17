#pragma once
#include <cstdint>

namespace mkw {
// Monotonic render-thread totals travel with the latest publication. Reading
// the same frame twice or missing an intermediate publication loses no work.
struct SkinWorkTotals {uint64_t cpuTransforms=0,gpuReferences=0;};
class SkinWorkReader {
public:
    // Mode changes discard work from the session just drained (e.g. a race
    // before entering menus). Immediate/local frames keep their direct counts.
    void discard(SkinWorkTotals current){previous_=current;}
    SkinWorkTotals consume(SkinWorkTotals current){
        const SkinWorkTotals delta{
            current.cpuTransforms>=previous_.cpuTransforms?current.cpuTransforms-previous_.cpuTransforms:current.cpuTransforms,
            current.gpuReferences>=previous_.gpuReferences?current.gpuReferences-previous_.gpuReferences:current.gpuReferences};
        previous_=current;return delta;
    }
private:
    SkinWorkTotals previous_{};
};
}
