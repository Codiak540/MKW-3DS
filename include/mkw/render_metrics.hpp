#pragma once
#include <cstdint>

#include "flush_batch.hpp"
#include "render_profile.hpp"
#ifdef __3DS__
#include <3ds.h>
#endif
namespace mkw {
struct RenderMetrics {
 uint32_t flushCalls=0,flushBytes=0,poseEvaluations=0,poseReuses=0,skinnedVertices=0;
 // Actual CPU position transforms, excluding shared-position/normal copies
 // and palette-only animation. Increment where the skin writes execute, so
 // deferred CPU skinning belongs to the render thread that performs it.
 uint32_t cpuSkinnedVertices=0;
 // Index references submitted with the GPU skin palette enabled, after
 // culling and per eye. This is not a vertex shader invocation count: the
 // PICA post-transform cache can reuse repeated references. The legacy
 // skinnedVertices above retains its original pose-level accounting.
 uint32_t gpuSkinVertexReferences=0;
 // Menu model timing split (QA profile only): skin, environment, draw.
 float skinMs=0,environmentMs=0,drawMs=0;
 // Race profile: job worker busy time (its jobs, folded in by finishAll).
 float workerMs=0;
};
// Per thread: JobWorker::finishAll folds the worker core's counts in.
inline thread_local RenderMetrics renderMetrics;
// Call only with CPU-written GPU-visible data. Static geometry and textures
// flush when loaded; dynamic ranges flush before their first submitted draw.
inline void flushGpuData(void* data,uint32_t bytes){
 if(!bytes)return;
 ++renderMetrics.flushCalls;renderMetrics.flushBytes+=bytes;
 if(flushBatch().add(data,bytes))return;
 const prof::Scope t(prof::FlushData);
 GSPGPU_FlushDataCache(data,u32(bytes));
}
}
