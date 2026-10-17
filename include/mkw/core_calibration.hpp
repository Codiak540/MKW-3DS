#pragma once
#include <algorithm>
#include <array>
#include <cstdint>

namespace mkw {
// Old 3DS: where a race builds its GPU commands and runs its presentation
// jobs is decided on the console itself. The system core lends an
// application only part of its time, and how much is not documented, so
// each race times the candidate layouts on its own frames (the countdown:
// every racer in view) and keeps the one whose main-core work per presented
// frame is smallest. All layouts render and simulate identically; only which
// core does the work changes.
class CoreCalibration {
public:
    enum Mode : uint8_t {
        MainOnly,      // GPU commands and presentation jobs on the main core
        MainWorker,    // GPU commands on the main core, posing jobs on core 1/2
        RenderThread,  // the second-core render thread and job worker
        ModeCount
    };
    static constexpr unsigned Rounds=3,TrialFrames=8,SettleFrames=2;
    static constexpr const char* name(Mode m){return m==MainOnly?"main-only":m==MainWorker?"main+worker":"render-thread";}

    // fixed: no trials, always this mode (QA markers, New 3DS, unknown hardware).
    void reset(bool calibrate,Mode fixed){
        calibrating_=calibrate;chosen_=fixed;trial_=0;frame_=0;samples_={};count_=0;sums_={};
    }
    bool calibrating() const {return calibrating_;}
    Mode chosen() const {return chosen_;}
    // The layout the next presented frame uses.
    Mode mode() const {return calibrating_?Order[trial_%ModeCount]:chosen_;}
    // The main core's busy time for the presented frame just drawn in mode().
    // Returns true when this frame completed the calibration.
    bool frameDone(float busyMs){
        if(!calibrating_)return false;
        if(frame_>=SettleFrames&&count_<samples_.size())samples_[count_++]=busyMs;
        if(++frame_<TrialFrames)return false;
        // Median of the settled frames: a stray frame (item hit, effect
        // burst) cannot decide the layout.
        std::sort(samples_.begin(),samples_.begin()+count_);
        sums_[Order[trial_%ModeCount]]+=count_?samples_[count_/2]:0.f;
        frame_=0;count_=0;
        if(++trial_<Rounds*ModeCount)return false;
        Mode best=MainOnly;
        for(unsigned m=1;m<ModeCount;++m)if(sums_[m]<sums_[best])best=Mode(m);
        chosen_=best;calibrating_=false;
        return true;
    }
    float meanMs(Mode m) const {return sums_[m]/float(Rounds);}
private:
    static constexpr Mode Order[ModeCount]={MainOnly,MainWorker,RenderThread};
    bool calibrating_=false;
    Mode chosen_=MainOnly;
    unsigned trial_=0,frame_=0,count_=0;
    std::array<float,TrialFrames> samples_{};
    std::array<float,ModeCount> sums_{};
};
}
