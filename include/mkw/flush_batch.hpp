#pragma once
#include <3ds.h>
#include <algorithm>
#include <array>
#include <cstdint>
#include "mkw/render_profile.hpp"
namespace mkw {
    // Cache flushes inside a frame are deferred and merged: the GPU reads nothing until FrameEnd.
    class FlushBatch {
    public:
        static constexpr std::size_t MaxGap=8u<<20;
        static constexpr std::uintptr_t Line=32;        // ARM11 cache line
        static inline bool direct_=true;              // flip to false for merge-only A/B

        void begin(){active_=true;count_=0;}
        // false: not batching, the caller flushes now.
        bool add(const void* p,std::size_t n){
            if(!active_||!n)return false;
            const auto a=reinterpret_cast<std::uintptr_t>(p);
            if(count_==ranges_.size())commit();   // never drop a range
            ranges_[count_++]={a&~(Line-1),(a+n+Line-1)&~(Line-1)};
            return true;
        }

        static void issue(std::uintptr_t lo,std::uintptr_t hi){
            const prof::Scope t(prof::FlushData);
            const u32 size=u32(hi-lo);
            if(direct_){
                if(R_SUCCEEDED(svcFlushProcessDataCache(CUR_PROCESS_HANDLE,u32(lo),size)))return;
                direct_=false;   // not permitted for this process: use the GSP service from now on
            }
            GSPGPU_FlushDataCache(reinterpret_cast<const void*>(lo),size);
        }

        void commit() {
            if(!count_)return;
            std::sort(ranges_.begin(),ranges_.begin()+count_,[](const Range& a,const Range& b){return a.lo<b.lo;});
            Range cur=ranges_[0];
            for(unsigned i=1;i<count_;++i){
                if(ranges_[i].lo<=cur.hi+MaxGap)cur.hi=std::max(cur.hi,ranges_[i].hi);
                else{issue(cur.lo,cur.hi);cur=ranges_[i];}
            }
            issue(cur.lo,cur.hi);
            count_=0;
        }
        void end(){commit();active_=false;}
    private:
        struct Range{std::uintptr_t lo,hi;};
        std::array<Range,64> ranges_{};
        unsigned count_=0;
        bool active_=false;
    };
    // Per thread: loads on other threads between frames keep flushing immediately.
    inline FlushBatch& flushBatch(){static thread_local FlushBatch b;return b;}
}