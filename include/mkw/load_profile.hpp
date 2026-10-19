#pragma once
// QA: where race-load time goes, in system ticks accumulated per step and
// printed with each load stage (main.cpp). Scopes nest: model textures,
// checks and sidecars are parts of models; texture mips are part of model
// textures. Load paths only; never a per-frame path (each scope reads the tick
// twice).
#include <3ds.h>
#include <atomic>
namespace mkw::loadProfile {
enum Step {Models,ModelTextures,TextureMips,ModelChecks,Sidecars,Banks,SkinPrep,Count};
inline constexpr const char* names[Count]={"models","model textures","texture mips","model checks","sidecars","banks","skin prep"};
inline std::atomic<unsigned long long> ticks[Count];
#ifdef MKW_RELEASE
struct Scope {explicit Scope(Step){}};
#else
struct Scope {
    Step step;u64 start;
    explicit Scope(Step s):step(s),start(svcGetSystemTick()){}
    ~Scope(){ticks[step].fetch_add(svcGetSystemTick()-start,std::memory_order_relaxed);}
};
#endif
}
