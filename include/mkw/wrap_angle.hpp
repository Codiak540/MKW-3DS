#pragma once
#include <cmath>

namespace mkw {
// std::remainder(x, 2*pi) for a float wheel phase, bit for bit. A roll
// phase stays near [-pi, pi], where the IEEE remainder is x, x - 2pi or
// x + 2pi; those differences are exact (Sterbenz) and ties at an odd multiple
// of pi round the quotient to even. Anything else uses the library.
inline float remainderTwoPi(float x){
    constexpr float TwoPi=2*3.14159265358979323846f,Pi=TwoPi*.5f;
    if(x>Pi){
        if(x<=2*TwoPi){
            const float r=x-TwoPi;
            if(r<Pi)return r;
            if(r==Pi)return -Pi; // x/2pi = 1.5: quotient 2
        }
    }else if(x<-Pi){
        if(x>=-2*TwoPi){
            const float r=x+TwoPi;
            if(r>-Pi)return r==0.f?-0.f:r; // a zero remainder keeps x's sign
            if(r==-Pi)return Pi; // x/2pi = -1.5: quotient -2
        }
    }else return x;
    return std::remainder(x,TwoPi);
}
}
