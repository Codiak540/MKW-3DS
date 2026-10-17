#include <mkw/core_calibration.hpp>
#include <cassert>

int main(){
    using C=mkw::CoreCalibration;
    C c;
    // Pinned: no trials, the fixed layout throughout.
    c.reset(false,C::RenderThread);
    assert(!c.calibrating()&&c.mode()==C::RenderThread&&!c.frameDone(1.f));
    // Calibrating: rounds of main-only, main+worker, render-thread trials;
    // settle frames and a stray slow frame per trial do not decide.
    c.reset(true,C::MainOnly);
    const float cost[C::ModeCount]={20.f,24.f,43.f};
    unsigned frames=0;bool decided=false;
    while(!decided){
        const C::Mode m=c.mode();
        const unsigned inTrial=frames%C::TrialFrames;
        const float spike=inTrial==C::SettleFrames?90.f:0.f; // one outlier per trial
        const float settle=inTrial<C::SettleFrames?200.f:0.f;  // ignored settle frames
        decided=c.frameDone(cost[m]+spike+settle);++frames;
    }
    assert(frames==C::Rounds*C::ModeCount*C::TrialFrames);
    assert(!c.calibrating()&&c.chosen()==C::MainOnly&&c.mode()==C::MainOnly);
    assert(c.meanMs(C::MainOnly)==20.f&&c.meanMs(C::RenderThread)==43.f);
    // The second-core layouts win when they are faster.
    c.reset(true,C::MainOnly);
    const float faster[C::ModeCount]={30.f,26.f,18.f};
    for(decided=false;!decided;)decided=c.frameDone(faster[c.mode()]);
    assert(c.chosen()==C::RenderThread);
    return 0;
}
