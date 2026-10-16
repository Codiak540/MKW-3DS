#pragma once
#include <3ds.h>
#include <array>
#include <cstdint>
namespace mkw::prof {
    // Slots 0-31 are render command types; the rest are drawNow internals.
    enum Slot : unsigned {Binder=32,RetailLight,RetailCombiners,Cull,PartCull,WritesRun,
        DrawElements,EndSprites,FrameEnd,EffectUpload,EffectBatches,EffectBatchCount,WriteDriver,WriteEscalator,WriteSkin,FlushData,SlotCount};    struct Table {std::array<uint64_t,SlotCount> ticks{};std::array<uint32_t,SlotCount> calls{};};
    struct Latest {std::array<float,SlotCount> ms{};std::array<uint32_t,SlotCount> calls{};};
    inline Table& table(){static Table t;return t;}
    inline Latest& latest(){static Latest l;return l;} // read racily for the CSV; fine for diagnostics
    struct Scope {
        Slot slot;uint64_t start;
        explicit Scope(unsigned s):slot(Slot(s)),start(svcGetSystemTick()){}
        ~Scope(){auto& t=table();t.ticks[slot]+=svcGetSystemTick()-start;++t.calls[slot];}
    };
    inline void publish(){
        auto& t=table();auto& l=latest();
        for(unsigned i=0;i<SlotCount;++i){l.ms[i]=float(t.ticks[i]*1000.0/SYSCLOCK_ARM11);l.calls[i]=t.calls[i];}
        t=Table{};
    }
}