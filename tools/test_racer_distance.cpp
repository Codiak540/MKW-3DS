#include <mkw/racer_distance.hpp>
#include <cassert>
#include <cmath>
#include <initializer_list>
#include <limits>

int main(){
    using mkw::RacerDistance;
    RacerDistance policy;
    bool hidden=true;
    const auto square=[](float value){return value*value;};
    assert(!policy.enabled&&policy.distance==25000.f);
    assert(policy.visible(square(25000.f),hidden)&&!hidden);
    assert(!policy.visible(square(25001.f),hidden)&&!hidden);
    assert(policy.visible(square(24999.f),hidden)&&!hidden);
    assert(policy.culled.load()==0);
    // A baseline pass has no hysteresis and clears previous experimental state.
    hidden=true;
    assert(policy.visible(square(24000.f),hidden)&&!hidden);

    for(float invalid:{0.f,999.f,25001.f,std::numeric_limits<float>::infinity(),
                       -std::numeric_limits<float>::infinity(),std::numeric_limits<float>::quiet_NaN()}){
        assert(!policy.configure(true,invalid));
        assert(!policy.enabled&&policy.distance==25000.f);
    }
    assert(!policy.configure(false,5000.f));
    assert(!policy.enabled&&policy.distance==25000.f);

#ifdef MKW_RELEASE
    assert(!policy.configure(true,5000.f));
    assert(!policy.enabled&&policy.distance==25000.f);
#else
    assert(policy.configure(true,5000.f));
    assert(policy.enabled&&policy.distance==5000.f);
    hidden=false;
    assert(policy.visible(square(5000.f),hidden)&&!hidden);
    assert(!policy.visible(square(5001.f),hidden)&&hidden);
    assert(!policy.visible(square(4999.f),hidden)&&hidden);
    assert(!policy.visible(square(4501.f),hidden)&&hidden);
    assert(policy.culled.load()==3);
    assert(policy.visible(square(4500.f),hidden)&&!hidden);
    assert(policy.visible(square(4900.f),hidden)&&!hidden);
    // Each opponent has independent hysteresis state.
    bool secondHidden=false;
    assert(!policy.visible(square(6000.f),hidden)&&hidden);
    assert(policy.visible(square(4900.f),secondHidden)&&!secondHidden);
    assert(policy.visible(square(100.f),hidden)&&!hidden); // teleport/re-entry
    hidden=true;
    assert(policy.visible(std::numeric_limits<float>::quiet_NaN(),hidden)&&!hidden);
    assert(policy.visible(-1.f,hidden)&&!hidden);
    assert(!policy.visible(std::numeric_limits<float>::infinity(),hidden)&&hidden);
    assert(!policy.configure(false,5000.f));
    assert(policy.culled.load()==0);
    assert(policy.visible(square(24000.f),hidden)&&!hidden);
    assert(policy.configure(true,1000.f));
    assert(policy.configure(true,25000.f));
#endif
}
