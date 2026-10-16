#pragma once
#include <cmath>

namespace mkw {
// Old 3DS opponent pose LOD. An opponent drawn smaller than `ratio` (its
// model radius over its distance from the camera; 0.05 is about 13 px of
// radius on the 240 px top screen at the race FOV) re-evaluates its skeletal
// poses on alternate frames, half of such racers on each frame, and draws
// the pose it already has in between: 15 Hz limb animation for small, far
// racers, as engines' animation update-rate LOD does. The kart itself (body
// transform, wheels) still moves every frame. Simulation never reads it.
struct PoseLod {
    bool enabled=false;
    float ratio=.05f;
    static constexpr float MinimumRatio=.005f,MaximumRatio=.2f;
    // QA may tune the ratio (o3ds-pose-lod.txt); invalid values keep the default.
    void configure(bool oldHardware,float requestedRatio=0){
        enabled=oldHardware;ratio=.05f;
        if(std::isfinite(requestedRatio)&&requestedRatio>=MinimumRatio&&requestedRatio<=MaximumRatio)ratio=requestedRatio;
    }
    // True when racer `racer` keeps its last pose on frame `frame`: enabled,
    // small on screen and on its off frame. NaN distances re-pose.
    bool keep(float radius,float distanceSquared,unsigned frame,unsigned racer)const{
        if(!enabled||!((frame+racer)&1u))return false;
        return radius*radius<ratio*ratio*distanceSquared;
    }
};
inline PoseLod& poseLod(){static PoseLod lod;return lod;}
}
