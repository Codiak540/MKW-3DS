// Host test for include/mkw/world_cull.hpp: the early reject never rejects
// anything the exact per-object test (ClipFrustum) keeps, and does reject
// most boxes that are clearly off screen.
#include <mkw/world_cull.hpp>
#include <cassert>
#include <cstdio>
#include <limits>
#include <random>

namespace {
void multiply(const float* a,const float* b,float* out){
    for(int r=0;r<4;++r)for(int c=0;c<4;++c){float s=0;for(int k=0;k<4;++k)s+=a[r*4+k]*b[k*4+c];out[r*4+c]=s;}
}
// Row-major perspective mapping view depth n..f to clip z 0..-w (PICA), so
// ClipFrustum and WorldCull see the same planes as in the game.
void perspective(float fovy,float aspect,float n,float f,float* m){
    const float t=1.f/std::tan(fovy*.5f);
    const float p[16]={t/aspect,0,0,0, 0,t,0,0, 0,0,f/(f-n),f*n/(f-n), 0,0,-1,0};
    for(int i=0;i<16;++i)m[i]=p[i];
}
void lookAt(const float* eye,const float* yaw,float* m){
    const float c=std::cos(*yaw),s=std::sin(*yaw);
    // Rotation about y then translation by -eye.
    const float r[16]={c,0,-s,0, 0,1,0,0, s,0,c,0, 0,0,0,1};
    const float t[16]={1,0,0,-eye[0], 0,1,0,-eye[1], 0,0,1,-eye[2], 0,0,0,1};
    multiply(r,t,m);
}
}

int main(){
    std::mt19937 rng(11);
    std::uniform_real_distribution<float> u(-1,1);
    unsigned rejected=0,outsideExact=0,trials=0;
    for(int frame=0;frame<400;++frame){
        float proj[16],view[16],clip[16];
        perspective(.96f+.3f*u(rng),400.f/240.f,10.f,400000.f,proj);
        const float eye[3]={20000*u(rng),3000*u(rng),20000*u(rng)},yaw=3.14159f*u(rng);
        lookAt(eye,&yaw,view);multiply(proj,view,clip);
        mkw::WorldCull cull;cull.reset(clip);assert(cull.valid);
        for(int i=0;i<500;++i){
            // Random affine model matrix: rotation-ish, scale, translation near the camera.
            float model[16]={0};
            const float a=3.14159f*u(rng),sc=std::exp(1.5f*u(rng));
            const float ca=std::cos(a),sa=std::sin(a);
            const float rows[16]={ca*sc,0,sa*sc,eye[0]+30000*u(rng), .2f*u(rng)*sc,sc,0,eye[1]+3000*u(rng), -sa*sc,0,ca*sc,eye[2]+30000*u(rng), 0,0,0,1};
            for(int k=0;k<16;++k)model[k]=rows[k];
            float total[16];multiply(clip,model,total);
            const float e=500*std::fabs(u(rng))+1;
            mkw::Bounds b{{-e+100*u(rng),-e,-e},{e,e+100*std::fabs(u(rng)),e}};
            const bool exact=mkw::ClipFrustum(total).intersects(b);
            const bool early=cull.outside(b,model);
            ++trials;outsideExact+=!exact;rejected+=early;
            assert(!(early&&exact));
        }
    }
    // Nonfinite input never rejects.
    {mkw::WorldCull cull;float proj[16];perspective(1.f,1.6f,10.f,1e5f,proj);cull.reset(proj);
     const float nan=std::numeric_limits<float>::quiet_NaN();const float rows[12]={1,0,0,nan, 0,1,0,0, 0,0,1,0};
     assert(!cull.outside({{-1,-1,-1},{1,1,1}},rows));
     const float far[12]={1,0,0,0, 0,1,0,0, 0,0,1,1e6f};assert(cull.outside({{-1,-1,-1},{1,1,1}},far));}
    {mkw::WorldCull cull;float bad[16]={0};cull.reset(bad);assert(!cull.valid&&!cull.outside({{0,0,0},{1,1,1}},bad));}
    std::printf("world cull: ok, %u trials, exact rejects %u, early rejects %u (%.1f%%)\n",trials,outsideExact,rejected,100.*rejected/std::max(outsideExact,1u));
    return 0;
}
