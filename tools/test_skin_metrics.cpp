#include "mkw/skin_metrics.hpp"
#include <cassert>

int main(){
    mkw::SkinWorkReader reader;
    auto work=reader.consume({100,300});assert(work.cpuTransforms==100&&work.gpuReferences==300);
    work=reader.consume({100,300});assert(work.cpuTransforms==0&&work.gpuReferences==0);
    // Three render publications complete before the recording thread samples.
    work=reader.consume({180,1200});assert(work.cpuTransforms==80&&work.gpuReferences==900);
    // Menu/direct work is not added to these render-thread totals. Starting a
    // new deferred session discards its predecessor's unconsumed last frame.
    reader.discard({200,1500});
    work=reader.consume({200,1500});assert(work.cpuTransforms==0&&work.gpuReferences==0);
    work=reader.consume({240,1700});assert(work.cpuTransforms==40&&work.gpuReferences==200);
    // A restarted producer can reset its totals without unsigned underflow.
    work=reader.consume({7,19});assert(work.cpuTransforms==7&&work.gpuReferences==19);
    work=reader.consume({7,19});assert(work.cpuTransforms==0&&work.gpuReferences==0);
    // Long sessions accumulate beyond 32 bits without wrapping their baseline.
    constexpr uint64_t big=uint64_t(1)<<40;
    reader.discard({big,big});
    work=reader.consume({big+5,big+9});assert(work.cpuTransforms==5&&work.gpuReferences==9);
}
