#pragma once
#include <atomic>
#include <cmath>
#include <cstdint>

namespace mkw {
// An independently measured QA experiment. The main thread configures this
// before any racer jobs start; its configuration then stays read-only while
// workers update only the atomic count of skipped opponent preparations.
struct RacerDistance {
    static constexpr float BaselineDistance=25000.f;
    static constexpr float MinimumDistance=1000.f;
    static constexpr float ReappearRatio=.9f;
    bool enabled=false;
    float distance=BaselineDistance;
    mutable std::atomic<uint32_t> culled{0};

    // Unknown hardware, New 3DS, invalid input and release builds retain the
    // original cutoff. The caller supplies a successfully identified Old 3DS.
    bool configure(bool confirmedOldHardware,float requestedDistance){
        enabled=false;distance=BaselineDistance;culled.store(0,std::memory_order_relaxed);
#ifndef MKW_RELEASE
        if(confirmedOldHardware&&std::isfinite(requestedDistance)&&
           requestedDistance>=MinimumDistance&&requestedDistance<=BaselineDistance){
            enabled=true;distance=requestedDistance;
        }
#else
        (void)confirmedOldHardware;(void)requestedDistance;
#endif
        return enabled;
    }

    // The state belongs to one opponent, never the local player. Hiding uses
    // the requested distance; returning 10% closer makes it visible again.
    // No sqrt or pose evaluation is needed to make this decision.
    bool visible(float distanceSquared,bool& hidden)const{
        if(!enabled){
            hidden=false;
            return !(distanceSquared>BaselineDistance*BaselineDistance);
        }
        // An invalid position should not latch an opponent invisibly. This
        // also preserves the original comparison's behavior for NaN values.
        if(std::isnan(distanceSquared)||distanceSquared<0){hidden=false;return true;}
        const float threshold=hidden?distance*ReappearRatio:distance;
        hidden=distanceSquared>threshold*threshold;
        if(hidden)culled.fetch_add(1,std::memory_order_relaxed);
        return !hidden;
    }
};
inline RacerDistance& racerDistance(){static RacerDistance policy;return policy;}
}
