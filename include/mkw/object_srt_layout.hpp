#pragma once
#include <cstddef>
#include <cstdint>

namespace mkw {
struct ObjectSrtLayout {
    unsigned samples=0;
    std::size_t sampleBytes=0;
};

// SRT periods can exceed 20,000 frames (Delfino Pier water uses 24,000),
// while sampled storage stays small. Bound by the payload actually available,
// and keep every integer frame/period exactly representable by sampleClip's
// float arithmetic. Do not relax CHR/PAT/CLR limits with this SRT-only check.
inline bool objectSrtLayout(unsigned frames,unsigned step,unsigned count,unsigned shapes,
                            std::size_t available,ObjectSrtLayout& result) {
    constexpr unsigned MaxExactFloatInteger=1u<<24;
    if(!frames||frames>MaxExactFloatInteger||!step||step>64||count>shapes)return false;
    const std::uint64_t samples=frames<=1?1:(std::uint64_t(frames)-1+step-1)/step+1;
    const std::uint64_t indexBytes=std::uint64_t(count)*4;
    const std::uint64_t sampleBytes=samples*count*48;
    if(indexBytes>available||sampleBytes>available-indexBytes)return false;
    result={unsigned(samples),std::size_t(sampleBytes)};
    return true;
}
}
