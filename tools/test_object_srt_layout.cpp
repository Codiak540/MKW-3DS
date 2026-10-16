#include <mkw/object_srt_layout.hpp>
#include <cassert>
#include <cstdint>
#include <limits>

int main() {
    using mkw::ObjectSrtLayout;
    using mkw::objectSrtLayout;
    ObjectSrtLayout out;
    // The real venice water bank: 24,000-frame loop, sample every 16 ticks,
    // one animated shape, including the final sample at frame 23,999.
    constexpr std::size_t VeniceBytes=4+1501*48;
    assert(objectSrtLayout(24000,16,1,8,VeniceBytes,out));
    assert(out.samples==1501&&out.sampleBytes==1501*48);
    assert(!objectSrtLayout(24000,16,1,8,VeniceBytes-1,out));
    assert(!objectSrtLayout(24000,16,1,8,3,out));
    assert(objectSrtLayout(1,64,1,1,52,out)&&out.samples==1);
    assert(objectSrtLayout(4,2,1,1,4+3*48,out)&&out.samples==3);
    assert(objectSrtLayout(5,2,1,1,4+3*48,out)&&out.samples==3);
    assert(!objectSrtLayout(0,16,1,1,VeniceBytes,out));
    assert(!objectSrtLayout(24000,0,1,1,VeniceBytes,out));
    assert(!objectSrtLayout(24000,65,1,1,VeniceBytes,out));
    assert(!objectSrtLayout(24000,16,2,1,VeniceBytes,out));
    assert(!objectSrtLayout((1u<<24)+1,16,0,0,0,out));
    assert(!objectSrtLayout(std::numeric_limits<unsigned>::max(),64,0,0,0,out));
    // A huge sampled payload must not wrap a 32-bit target's size_t budget.
    assert(!objectSrtLayout(1u<<24,1,256,256,std::numeric_limits<std::uint32_t>::max(),out));
    assert(objectSrtLayout(1u<<24,64,0,0,0,out)&&out.sampleBytes==0);
}
