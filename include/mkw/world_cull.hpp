#pragma once
#include <mkw/visibility.hpp>
#include <array>
#include <cmath>

namespace mkw {
// Cheap early reject before the exact per-object frustum test, which builds a
// model-to-clip matrix (two 4x4 products) for every object every frame. The
// six clip planes are normalised in world space once per frame; an object is
// rejected only when its world AABB lies outside one plane by a margin (one
// unit plus 1e-4 of the magnitudes involved) far larger than the exact test's
// slack (ClipFrustum::intersects, 1e-5 relative). Anything near a plane goes
// on to the exact test, so the set of drawn objects is unchanged.
struct WorldCull {
    std::array<std::array<float,4>,6> planes{};
    bool valid=false;
    // m: row-major world-to-clip matrix (projection * view), as ClipFrustum.
    void reset(const float* m){
        valid=true;
        for(unsigned c=0;c<4;++c){
            planes[0][c]=m[12+c]+m[c];planes[1][c]=m[12+c]-m[c];
            planes[2][c]=m[12+c]+m[4+c];planes[3][c]=m[12+c]-m[4+c];
            planes[4][c]=m[12+c]+m[8+c];planes[5][c]=-m[8+c];
        }
        for(auto& p:planes){
            const float n=std::sqrt(p[0]*p[0]+p[1]*p[1]+p[2]*p[2]);
            if(!(n>0)||!std::isfinite(n)){valid=false;return;}
            for(auto& v:p)v/=n;
            for(float v:p)if(!std::isfinite(v)){valid=false;return;}
        }
    }
    // b: model-space bounds; t: row-major 3x4 model-to-world rows.
    bool outside(const Bounds& b,const float* t) const {
        if(!valid)return false;
        const float c[3]={(b.minimum.x+b.maximum.x)*.5f,(b.minimum.y+b.maximum.y)*.5f,(b.minimum.z+b.maximum.z)*.5f};
        const float h[3]={(b.maximum.x-b.minimum.x)*.5f,(b.maximum.y-b.minimum.y)*.5f,(b.maximum.z-b.minimum.z)*.5f};
        float wc[3],we[3];
        for(unsigned r=0;r<3;++r){
            const float* row=t+r*4;
            wc[r]=row[0]*c[0]+row[1]*c[1]+row[2]*c[2]+row[3];
            we[r]=std::fabs(row[0])*h[0]+std::fabs(row[1])*h[1]+std::fabs(row[2])*h[2];
        }
        return outsideBox(wc,we);
    }
    // World-space box given by centre and half extents.
    bool outsideBox(const float* wc,const float* we) const {
        if(!valid)return false;
        for(const auto& p:planes){
            const float centre=p[0]*wc[0]+p[1]*wc[1]+p[2]*wc[2];
            const float reach=std::fabs(p[0])*we[0]+std::fabs(p[1])*we[1]+std::fabs(p[2])*we[2];
            const float margin=1.f+1e-4f*(std::fabs(centre)+std::fabs(p[3])+reach);
            // NaN compares false: never rejects.
            if(centre+p[3]+reach< -margin)return true;
        }
        return false;
    }
};
// Course objects use the early reject (and the group and envelope tests built
// on it) on Old 3DS only (main.cpp); QA o3ds-objcull-off.txt turns it off.
inline bool& worldCullEnabled(){static bool on=false;return on;}
}
