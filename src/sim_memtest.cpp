// QA hardware micro-benchmarks for the ARM11/VFP11 cost model
// (docs/o3ds-kinoko-hardware-checklist.md section 3): with o3ds-memtest.txt on
// the card the app measures, before the menu, FCRAM/L1 load latency by pointer
// chasing and the VFP11 latencies the model assumes, and writes
// memtest-result.json. Nothing here runs without the marker.
#include <mkw/sim_memtest.hpp>
#include <3ds.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <sys/stat.h>

namespace mkw {
namespace {
bool markerExists(const char* name){char path[96];snprintf(path,sizeof path,"sdmc:/3ds/mkw-3ds/%s",name);struct stat st;return stat(path,&st)==0;}

// A random cycle through `bytes` worth of 32-byte lines; returns ns per dependent load.
double chase(size_t bytes,uint32_t& seed){
    const size_t lines=bytes/32;
    auto* buffer=static_cast<uint32_t*>(malloc(bytes));
    if(!buffer)return -1;
    auto* order=static_cast<uint32_t*>(malloc(lines*sizeof(uint32_t)));
    if(!order){free(buffer);return -1;}
    for(size_t i=0;i<lines;++i)order[i]=uint32_t(i);
    for(size_t i=lines-1;i>0;--i){seed=seed*1664525u+1013904223u;size_t j=(seed>>8)%(i+1);uint32_t t=order[i];order[i]=order[j];order[j]=t;}
    for(size_t i=0;i<lines;++i)buffer[order[i]*8]=order[(i+1)%lines]*8;
    volatile uint32_t* p=buffer;uint32_t idx=0;
    const unsigned steps=bytes>=(1u<<20)?200000:1000000;
    for(unsigned i=0;i<4096;++i)idx=p[idx];   // warm
    const uint64_t start=svcGetSystemTick();
    for(unsigned i=0;i<steps;++i)idx=p[idx];
    const uint64_t end=svcGetSystemTick();
    buffer[0]=idx;free(order);free(buffer);
    return double(end-start)*1e9/SYSCLOCK_ARM11/steps;
}
double cyclesPer(uint64_t ticks,unsigned ops){return double(ticks)/ops;}
// VFPv2 has no float immediates: constants go through an integer register.
inline void setS(int reg,float value){uint32_t bits;memcpy(&bits,&value,4);
    switch(reg){
#define S(n) case n: asm volatile("fmsr s" #n ", %0"::"r"(bits)); break;
    S(0)S(1)S(2)S(3)S(4)S(5)S(6)S(8)S(9)S(10)S(11)S(12)S(13)S(14)S(15)S(16)S(17)S(18)S(19)S(24)S(25)S(26)S(27)
#undef S
    default: break;}
}
}

void runMemtest(){
    if(!markerExists("o3ds-memtest.txt"))return;
    uint32_t seed=0x12345678u;
    FILE* f=fopen("sdmc:/3ds/mkw-3ds/memtest-result.json","w");if(!f)return;
    fprintf(f,"{\n");
    for(size_t bytes:{size_t(8192),size_t(65536),size_t(1u<<20),size_t(8u<<20)}){
        double ns=chase(bytes,seed);fprintf(f," \"chase_ns_%u\": %.2f,\n",unsigned(bytes),ns);
    }
    constexpr unsigned N=100000;
    uint64_t t0,t1;
    // svcGetSystemTick itself
    t0=svcGetSystemTick();for(unsigned i=0;i<10000;++i)svcGetSystemTick();t1=svcGetSystemTick();
    fprintf(f," \"tick_call_cycles\": %.1f,\n",cyclesPer(t1-t0,10000));
    // dependent fmacs chain: s0 += s1 * s2, 16 per iteration
    setS(1,1.0f);setS(2,0.5f);setS(0,0.0f);
    t0=svcGetSystemTick();
    for(unsigned i=0;i<N/16;++i)asm volatile(
        "fmacs s0, s1, s2\n fmacs s0, s1, s2\n fmacs s0, s1, s2\n fmacs s0, s1, s2\n"
        "fmacs s0, s1, s2\n fmacs s0, s1, s2\n fmacs s0, s1, s2\n fmacs s0, s1, s2\n"
        "fmacs s0, s1, s2\n fmacs s0, s1, s2\n fmacs s0, s1, s2\n fmacs s0, s1, s2\n"
        "fmacs s0, s1, s2\n fmacs s0, s1, s2\n fmacs s0, s1, s2\n fmacs s0, s1, s2\n" ::: "s0","memory");
    t1=svcGetSystemTick();fprintf(f," \"fmacs_dep_cycles\": %.2f,\n",cyclesPer(t1-t0,N));
    // eight independent chains
    setS(8,0.0f);setS(9,0.0f);setS(10,0.0f);setS(11,0.0f);setS(12,0.0f);setS(13,0.0f);setS(14,0.0f);setS(15,0.0f);
    t0=svcGetSystemTick();
    for(unsigned i=0;i<N/16;++i)asm volatile(
        "fmacs s8, s1, s2\n fmacs s9, s1, s2\n fmacs s10, s1, s2\n fmacs s11, s1, s2\n"
        "fmacs s12, s1, s2\n fmacs s13, s1, s2\n fmacs s14, s1, s2\n fmacs s15, s1, s2\n"
        "fmacs s8, s1, s2\n fmacs s9, s1, s2\n fmacs s10, s1, s2\n fmacs s11, s1, s2\n"
        "fmacs s12, s1, s2\n fmacs s13, s1, s2\n fmacs s14, s1, s2\n fmacs s15, s1, s2\n" ::: "s8","s9","s10","s11","s12","s13","s14","s15","memory");
    t1=svcGetSystemTick();fprintf(f," \"fmacs_indep_cycles\": %.2f,\n",cyclesPer(t1-t0,N));
    // dependent divides and square roots
    setS(3,1.5f);setS(4,4.0f);
    t0=svcGetSystemTick();
    for(unsigned i=0;i<N/8;++i)asm volatile("fdivs s4, s4, s3\n fdivs s4, s4, s3\n fdivs s4, s4, s3\n fdivs s4, s4, s3\n fdivs s4, s4, s3\n fdivs s4, s4, s3\n fdivs s4, s4, s3\n fdivs s4, s4, s3\n" ::: "s4","memory");
    t1=svcGetSystemTick();fprintf(f," \"fdivs_cycles\": %.2f,\n",cyclesPer(t1-t0,N));
    setS(5,4.0f);
    t0=svcGetSystemTick();
    for(unsigned i=0;i<N/8;++i)asm volatile("fsqrts s5, s5\n fsqrts s5, s5\n fsqrts s5, s5\n fsqrts s5, s5\n fsqrts s5, s5\n fsqrts s5, s5\n fsqrts s5, s5\n fsqrts s5, s5\n" ::: "s5","memory");
    t1=svcGetSystemTick();fprintf(f," \"fsqrts_cycles\": %.2f,\n",cyclesPer(t1-t0,N));
    // compare of a fresh result, fmstat, conditional branch (never taken)
    t0=svcGetSystemTick();
    for(unsigned i=0;i<N/4;++i)asm volatile(
        "fadds s6, s1, s2\n fcmps s6, s3\n fmstat\n bmi 1f\n 1:\n"
        "fadds s6, s1, s2\n fcmps s6, s3\n fmstat\n bmi 2f\n 2:\n"
        "fadds s6, s1, s2\n fcmps s6, s3\n fmstat\n bmi 3f\n 3:\n"
        "fadds s6, s1, s2\n fcmps s6, s3\n fmstat\n bmi 4f\n 4:\n" ::: "s6","cc","memory");
    t1=svcGetSystemTick();fprintf(f," \"fcmp_branch_cycles\": %.2f,\n",cyclesPer(t1-t0,N));
    // short vectors: LEN = 4 (FPSCR bits 16-18 = 3); s8-s11 += s16-s19 * s24-s27
    {
        uint32_t fpscr=__builtin_arm_get_fpscr();
        setS(16,1.0f);setS(17,1.0f);setS(18,1.0f);setS(19,1.0f);setS(24,0.5f);setS(25,0.5f);setS(26,0.5f);setS(27,0.5f);
        __builtin_arm_set_fpscr((fpscr&~(7u<<16))|(3u<<16));
        t0=svcGetSystemTick();
        for(unsigned i=0;i<N/16;++i)asm volatile(
            "fmacs s8, s16, s24\n fmacs s12, s16, s24\n fmacs s8, s16, s24\n fmacs s12, s16, s24\n"
            "fmacs s8, s16, s24\n fmacs s12, s16, s24\n fmacs s8, s16, s24\n fmacs s12, s16, s24\n"
            "fmacs s8, s16, s24\n fmacs s12, s16, s24\n fmacs s8, s16, s24\n fmacs s12, s16, s24\n"
            "fmacs s8, s16, s24\n fmacs s12, s16, s24\n fmacs s8, s16, s24\n fmacs s12, s16, s24\n" ::: "s8","s9","s10","s11","s12","s13","s14","s15","memory");
        t1=svcGetSystemTick();
        __builtin_arm_set_fpscr(fpscr);
        fprintf(f," \"vector_fmacs_cycles\": %.2f,\n",cyclesPer(t1-t0,N));
    }
    fprintf(f," \"sysclock_hz\": %u\n}\n",unsigned(SYSCLOCK_ARM11));
    fclose(f);
}
}
