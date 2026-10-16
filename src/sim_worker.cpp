#include <mkw/sim_worker.hpp>
#include <game/SimHash.hh>
#include <game/field/CollisionLane.hh>
#include <game/system/RaceManager.hh>
#include <algorithm>
#include <cstring>
#include <sys/stat.h>

namespace mkw {
namespace {
bool markerExists(const char* name){char path[96];snprintf(path,sizeof path,"sdmc:/3ds/mkw-3ds/%s",name);struct stat st;return stat(path,&st)==0;}
const char* modeName(Kinoko::Kart::KartObjectManager::CalcMode m){
    using M=Kinoko::Kart::KartObjectManager::CalcMode;
    return m==M::Reference?"reference":m==M::Phased?"phased":"parallel";
}
}
SimWorker& simWorker(){static SimWorker worker;return worker;}
void SimWorker::entry(void* self){static_cast<SimWorker*>(self)->loop();}

bool SimWorker::start(bool qa,bool new3ds){
    using M=Manager::CalcMode;
    policy_=Policy::Calibrate;
    if(qa){
        if(markerExists("o3ds-kinoko-reference.txt"))policy_=Policy::Reference;
        else if(markerExists("o3ds-kinoko-phased.txt"))policy_=Policy::Phased;
        else if(markerExists("o3ds-kinoko-parallel.txt"))policy_=Policy::Parallel;
        if(markerExists("sim-hash.txt")){hashFile_=fopen("sdmc:/3ds/mkw-3ds/sim-hash.bin","wb");hashCount_=0;hashTicks_=0;}
    }
    if(policy_==Policy::Reference){applyMode(M::Reference);return true;}
    if(policy_==Policy::Phased){applyMode(M::Phased);return true;}
    if(thread_)return true;
    LightEvent_Init(&wake_,RESET_ONESHOT);
    // The same VFP mode as the main thread (round to nearest, flush to zero, default
    // NaN), so a kart computes identical bits on either core.
    fpscr_=__builtin_arm_get_fpscr();
    if(!new3ds){u32 limit=0;APT_GetAppCpuTimeLimit(&limit);if(limit<80)APT_SetAppCpuTimeLimit(80);}
    s32 priority=0x30;svcGetThreadPriority(&priority,CUR_THREAD_HANDLE);
    thread_=threadCreate(entry,this,32*1024,std::clamp<s32>(priority+1,0x1a,0x3f),new3ds?2:1,false);
    if(!thread_){applyMode(M::Phased);policy_=Policy::Phased;return false;}
    Manager::SetPhaseRunner(this);
    chosen_=M::Parallel;
    applyMode(policy_==Policy::Parallel?M::Parallel:M::Phased);
    beginRace();
    return true;
}
void SimWorker::stop(){
    if(hashFile_){if(hashCount_)fwrite(hashBuffer_,sizeof(uint64_t),hashCount_,hashFile_);fclose(hashFile_);hashFile_=nullptr;hashCount_=0;}
    if(!thread_)return;
    Manager::SetPhaseRunner(nullptr);
    applyMode(Manager::CalcMode::Reference);
    quit_.store(true,std::memory_order_release);LightEvent_Signal(&wake_);
    threadJoin(thread_,U64_MAX);threadFree(thread_);thread_=nullptr;quit_.store(false);
}
void SimWorker::applyMode(Manager::CalcMode mode){Manager::SetCalcMode(mode);}
void SimWorker::beginRace(){
    calibrating_=policy_==Policy::Calibrate&&thread_!=nullptr;calibDone_=false;
    calibTrial_=0;calibTick_=0;calibCount_=0;calibSum_[0]=calibSum_[1]=0;
    guardHits_=0;guardWindowTick_=0;guardTripped_=false;
    if(calibrating_)applyMode(Manager::CalcMode::Phased);
}
// Publishes a group; both threads claim from one packed (lo, hi) counter.
void SimWorker::run(Manager& manager,Manager::Phase phase,uint32_t begin,uint32_t end){
    manager_=&manager;phase_=phase;
    done_.store(0,std::memory_order_relaxed);
    claims_.store(pack(begin,end),std::memory_order_release);
    LightEvent_Signal(&wake_);
    uint32_t ran=0;
    for(;;){
        uint32_t v=claims_.load(std::memory_order_acquire);
        const uint32_t lo=v&0xffff,hi=v>>16;
        if(lo>=hi)break;
        if(claims_.compare_exchange_weak(v,pack(lo+1,hi),std::memory_order_acq_rel)){
            manager.runPhase(phase,lo);
            done_.fetch_add(1,std::memory_order_acq_rel);
            ++ran;
        }
    }
    const uint32_t n=end-begin;
    if(done_.load(std::memory_order_acquire)<n){
        // The worker is finishing a kart it claimed; nothing else is left to run.
        const uint64_t waitStart=svcGetSystemTick();
        while(done_.load(std::memory_order_acquire)<n){}
        const uint64_t waited=svcGetSystemTick()-waitStart;
        waitCycles_+=waited;tickWaitCycles_+=waited;maxWaitCycles_=std::max(maxWaitCycles_,waited);
    }
    mainKarts_+=ran;workerKarts_+=n-ran;++groups_;
}
void SimWorker::loop(){
    __builtin_arm_set_fpscr(fpscr_);
    Kinoko::Field::CollisionLane::RegisterWorkerThread();
    for(;;){
        LightEvent_Wait(&wake_);
        if(quit_.load(std::memory_order_acquire))break;
        for(;;){
            uint32_t v=claims_.load(std::memory_order_acquire);
            const uint32_t lo=v&0xffff,hi=v>>16;
            if(lo>=hi)break;
            if(claims_.compare_exchange_weak(v,pack(lo,hi-1),std::memory_order_acq_rel)){
                manager_->runPhase(phase_,hi-1);
                done_.fetch_add(1,std::memory_order_acq_rel);
            }
        }
    }
    Kinoko::Field::CollisionLane::UnregisterWorkerThread();
}
void SimWorker::beginTick(){tickStart_=svcGetSystemTick();tickWaitCycles_=0;}
void SimWorker::endTick(unsigned,int raceStage){
    const uint64_t cycles=svcGetSystemTick()-tickStart_;
    ++ticks_;tickCycles_+=cycles;
    // The guard: three ticks within sixty that each waited more than 0.3 ms on the
    // worker mean the system core is taking it away mid-kart; stop using it.
    if(policy_==Policy::Calibrate&&!calibrating_&&calibDone_&&!guardTripped_&&Manager::GetCalcMode()==Manager::CalcMode::Parallel){
        if(++guardWindowTick_>GuardWindow){guardWindowTick_=0;guardHits_=0;}
        if(tickWaitCycles_>GuardWaitCycles&&++guardHits_>=GuardTicks){guardTripped_=true;chosen_=Manager::CalcMode::Phased;applyMode(chosen_);}
    }
    if(hashFile_){Kinoko::Hasher h;hashBuffer_[hashCount_++]=Kinoko::SimHash::race(h);++hashTicks_;
        if(hashCount_==256){fwrite(hashBuffer_,sizeof(uint64_t),hashCount_,hashFile_);fflush(hashFile_);hashCount_=0;}}
    if(!calibrating_)return;
    // Countdown stage only (every racer still, all in view); the modes are
    // bit-identical, so switching between ticks changes nothing but the time.
    using Stage=Kinoko::System::RaceManager::Stage;
    if(raceStage<int(Stage::Countdown))return;
    if(raceStage>int(Stage::Countdown)){
        // The race started before the trials ended: decide on what was measured.
        calibrating_=false;calibDone_=true;
        if(calibSum_[0]&&calibSum_[1])chosen_=calibSum_[1]<calibSum_[0]?Manager::CalcMode::Parallel:Manager::CalcMode::Phased;
        applyMode(chosen_);return;
    }
    const unsigned mode=calibTrial_%2; // 0 phased, 1 parallel
    if(calibTick_>=CalibSettle&&calibCount_<CalibTicks)calibSamples_[calibCount_++]=cycles;
    if(++calibTick_<CalibTicks)return;
    std::sort(calibSamples_,calibSamples_+calibCount_);
    calibSum_[mode]+=calibCount_?calibSamples_[calibCount_/2]:0;
    calibTick_=0;calibCount_=0;++calibTrial_;
    if(calibTrial_<CalibRounds*2){applyMode(calibTrial_%2?Manager::CalcMode::Parallel:Manager::CalcMode::Phased);return;}
    chosen_=calibSum_[1]<calibSum_[0]?Manager::CalcMode::Parallel:Manager::CalcMode::Phased;
    calibrating_=false;calibDone_=true;applyMode(chosen_);
}
const char* SimWorker::summary(char* buf,size_t size){
    const double toMs=1000.0/SYSCLOCK_ARM11;
    snprintf(buf,size,"sim worker: policy=%s mode=%s thread=%d ticks=%llu tick_ms=%.3f parallel_ticks=%u serial_ticks=%u main_karts=%llu worker_karts=%llu groups=%llu wait_ms=%.3f max_wait_us=%.1f calib_phased_ms=%.3f calib_parallel_ms=%.3f guard_tripped=%d hashed=%llu",
        policy_==Policy::Reference?"reference":policy_==Policy::Phased?"phased":policy_==Policy::Parallel?"parallel":"calibrate",
        modeName(Manager::GetCalcMode()),thread_!=nullptr,(unsigned long long)ticks_,ticks_?tickCycles_*toMs/ticks_:0.0,
        Manager::s_parallelTicks,Manager::s_serialTicks,(unsigned long long)mainKarts_,(unsigned long long)workerKarts_,(unsigned long long)groups_,
        waitCycles_*toMs,maxWaitCycles_*toMs*1000.0,calibSum_[0]*toMs/CalibRounds,calibSum_[1]*toMs/CalibRounds,int(guardTripped_),(unsigned long long)hashTicks_);
    return buf;
}
}
