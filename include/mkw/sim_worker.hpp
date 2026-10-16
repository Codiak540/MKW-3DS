#pragma once
#include <game/kart/KartObjectManager.hh>
#include <3ds.h>
#include <atomic>
#include <cstdint>
#include <cstdio>
namespace mkw {
// The second core's share of the kart step (docs/o3ds-kinoko-arm11.md, sections 5
// and 7). Kinoko's phased step hands each independent group of per-kart phases to
// run(); the main thread claims karts from the front of the group and the worker
// thread, on core 1 (core 2 on New 3DS), from the back, through one atomic
// counter, so any kart may run on either core and the result never depends on
// which did. The main thread never waits for an unclaimed kart: it runs it.
//
// QA markers (sdmc:/3ds/mkw-3ds/): o3ds-kinoko-reference.txt pins the retail
// order, o3ds-kinoko-phased.txt the reordered step on the main core only,
// o3ds-kinoko-parallel.txt the two-core step; otherwise each race times the
// phased and two-core steps during its countdown and keeps the faster.
// sim-hash.txt writes the per-tick simulation state hash to sim-hash.bin.
class SimWorker final : public Kinoko::Kart::KartObjectManager::PhaseRunner {
public:
    using Manager=Kinoko::Kart::KartObjectManager;
    enum class Policy : uint8_t { Reference, Phased, Parallel, Calibrate };
    bool start(bool qa,bool new3ds);
    void stop();
    bool running() const { return thread_!=nullptr; }
    void run(Manager& manager,Manager::Phase phase,uint32_t begin,uint32_t end) override;
    // Around every simulation tick on the main thread (timing, calibration, hashing).
    void beginTick();
    void endTick(unsigned physicsSteps,int raceStage);
    // Re-arm the countdown calibration for a new race.
    void beginRace();
    const char* summary(char* buf,size_t size);
    Policy policy() const { return policy_; }
private:
    static void entry(void* self);
    void loop();
    void applyMode(Manager::CalcMode mode);
    static uint32_t pack(uint32_t lo,uint32_t hi){return lo|(hi<<16);}
    Thread thread_=nullptr;
    LightEvent wake_{};
    std::atomic<uint32_t> claims_{0},done_{0};
    std::atomic<bool> quit_{false};
    Manager* manager_=nullptr;
    Manager::Phase phase_{};
    uint32_t fpscr_=0;
    Policy policy_=Policy::Calibrate;
    Manager::CalcMode chosen_=Manager::CalcMode::Parallel;
    // Calibration: 3 rounds of 2 modes x 8 ticks during the countdown.
    static constexpr unsigned CalibTicks=8,CalibRounds=3,CalibSettle=2;
    bool calibrating_=false;unsigned calibTrial_=0,calibTick_=0,calibCount_=0;
    uint64_t calibSamples_[CalibTicks]{};uint64_t calibSum_[2]{};bool calibDone_=false;
    // Mid-race guard (calibrate policy only): if the main core keeps waiting for a
    // kart the worker claimed (system-core preemption mid-kart), the race drops back
    // to the main-core step for good. Bit-identical either way.
    static constexpr uint64_t GuardWaitCycles=uint64_t(SYSCLOCK_ARM11/1000*0.3); // 0.3 ms per tick
    static constexpr unsigned GuardTicks=3,GuardWindow=60;
    uint64_t tickWaitCycles_=0;unsigned guardHits_=0,guardWindowTick_=0;bool guardTripped_=false;
    // Statistics (cycles from svcGetSystemTick).
    uint64_t tickStart_=0;
    uint64_t ticks_=0,tickCycles_=0,waitCycles_=0,maxWaitCycles_=0,groups_=0;
    uint64_t mainKarts_=0,workerKarts_=0;
    // QA hash stream.
    FILE* hashFile_=nullptr;uint64_t hashBuffer_[256];unsigned hashCount_=0;uint64_t hashTicks_=0;
};
SimWorker& simWorker();
}
