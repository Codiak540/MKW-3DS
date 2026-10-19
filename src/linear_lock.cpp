#include <3ds.h>
#include <cstddef>

// libctru's LINEAR heap allocator is not thread-safe. The menu asset loader
// creates textures and model buffers on a background thread, so every LINEAR
// allocation in the program (including Citro3D's) is routed through one lock
// with -Wl,--wrap=linear*. A zero-initialized RecursiveLock is a valid,
// unlocked lock, so this is usable before static constructors run.
extern "C" {
void* __real_linearAlloc(size_t size);
void* __real_linearMemAlign(size_t size,size_t alignment);
void* __real_linearRealloc(void* mem,size_t size);
size_t __real_linearGetSize(void* mem);
void __real_linearFree(void* mem);
u32 __real_linearSpaceFree(void);
}
namespace {
RecursiveLock linearLock;
struct Guard {
 Guard(){RecursiveLock_Lock(&linearLock);}
 ~Guard(){RecursiveLock_Unlock(&linearLock);}
};
}
extern "C" {
void* __wrap_linearAlloc(size_t size){Guard g;return __real_linearAlloc(size);}
void* __wrap_linearMemAlign(size_t size,size_t alignment){Guard g;return __real_linearMemAlign(size,alignment);}
void* __wrap_linearRealloc(void* mem,size_t size){Guard g;return __real_linearRealloc(mem,size);}
size_t __wrap_linearGetSize(void* mem){Guard g;return __real_linearGetSize(mem);}
void __wrap_linearFree(void* mem){Guard g;__real_linearFree(mem);}
u32 __wrap_linearSpaceFree(void){Guard g;return __real_linearSpaceFree();}
}

// QA: counts file opens and the time spent opening (fopen is wrapped at link).
// Also the SD read requests stdio actually issues (FSFILE_Read, also wrapped)
// and their bytes: on a real card each request costs far more than a byte.
#include <cstdio>
#include <atomic>
namespace mkw {std::atomic<unsigned> fileOpens{0},fileOpenMisses{0},fileReads{0};std::atomic<unsigned long long> fileOpenTicks{0},fileReadBytes{0},fileReadTicks{0};}
extern "C" {
FILE* __real_fopen(const char* path,const char* mode);
FILE* __wrap_fopen(const char* path,const char* mode){
 const u64 start=svcGetSystemTick();FILE* file=__real_fopen(path,mode);
 mkw::fileOpens.fetch_add(1,std::memory_order_relaxed);mkw::fileOpenTicks.fetch_add(svcGetSystemTick()-start,std::memory_order_relaxed);
 if(!file)mkw::fileOpenMisses.fetch_add(1,std::memory_order_relaxed);
 return file;
}
#ifndef MKW_RELEASE
Result __real_FSFILE_Read(Handle handle,u32* bytesRead,u64 offset,void* buffer,u32 size);
Result __wrap_FSFILE_Read(Handle handle,u32* bytesRead,u64 offset,void* buffer,u32 size){
 const u64 start=svcGetSystemTick();const Result result=__real_FSFILE_Read(handle,bytesRead,offset,buffer,size);
 mkw::fileReads.fetch_add(1,std::memory_order_relaxed);mkw::fileReadBytes.fetch_add(size,std::memory_order_relaxed);
 mkw::fileReadTicks.fetch_add(svcGetSystemTick()-start,std::memory_order_relaxed);
 return result;
}
#endif
}
