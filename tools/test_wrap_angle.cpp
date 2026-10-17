#include <mkw/wrap_angle.hpp>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <limits>

static uint32_t bits(float f){uint32_t u;std::memcpy(&u,&f,4);return u;}
int main(){
    constexpr float TwoPi=2*3.14159265358979323846f;
    // Every float in [-5pi, 5pi] (both signs, all exponents down to zero).
    uint64_t checked=0;
    for(uint32_t u=0;u<0x7f800000u;++u){
        float x;std::memcpy(&x,&u,4);
        if(x>2.5f*TwoPi)break;
        for(float v:{x,-x}){
            const float a=mkw::remainderTwoPi(v),b=std::remainder(v,TwoPi);
            assert(bits(a)==bits(b));++checked;
        }
    }
    for(float v:{std::numeric_limits<float>::infinity(),-std::numeric_limits<float>::infinity(),1e30f,-1e30f}){
        const float a=mkw::remainderTwoPi(v),b=std::remainder(v,TwoPi);
        assert((std::isnan(a)&&std::isnan(b))||bits(a)==bits(b));
    }
    assert(std::isnan(mkw::remainderTwoPi(std::numeric_limits<float>::quiet_NaN())));
    return checked>2000000000u?0:1;
}
