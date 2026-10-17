#pragma once
#include <mkw/renderer.hpp>
#include <mkw/render_metrics.hpp>
#include <mkw/render_profile.hpp>
#include <mkw/model_detail.hpp>
#include <mkw/render_quality.hpp>
#include <mkw/racer_distance.hpp>
#include <mkw/object_gpu_skin.hpp>
#include <mkw/texture_detail.hpp>
#include <mkw/texture_mips.hpp>
#include "build_identity.hpp"
#include "content_version.hpp"
#include <array>
#include <cstdio>
#include <iterator>
namespace mkw {
namespace profile_detail {
// Render-thread timing columns (render_profile.hpp). Slots below 32 are render command types
// (the Cmd* enum values); 32 and up are drawNow internals, which also log their call counts.
struct Column {unsigned slot;const char* name;};
inline constexpr Column Columns[]={
 {1,"cmd_begin"},{2,"cmd_end"},{3,"cmd_projection"},{4,"cmd_course_lighting"},{5,"cmd_retail_fog"},{6,"cmd_request_bloom"},
 {7,"cmd_apply_bloom"},{8,"cmd_bottom"},{9,"cmd_draw"},{10,"cmd_draw_at_screen"},{11,"cmd_menu_icon"},{12,"cmd_environment_uv"},
 {13,"cmd_uv1"},{14,"cmd_uv_offset"},{15,"cmd_clear_environment"},{16,"cmd_rigid"},{17,"cmd_skin"},{18,"cmd_overrides"},
 {19,"cmd_shadows"},{20,"cmd_sparks"},{21,"cmd_effects"},{22,"cmd_cinema"},{23,"cmd_fade"},{24,"cmd_sprite"},
 {25,"cmd_font_sprite"},{26,"cmd_material_quads"},{27,"cmd_text"},{28,"cmd_screen_depth"},
 {prof::Binder,"binder"},{prof::RetailLight,"retail_light"},{prof::RetailCombiners,"retail_combiners"},
 {prof::Cull,"cull"},{prof::PartCull,"part_cull"},{prof::WritesRun,"writes_run"},
{prof::DrawElements,"draw_elements"},{prof::EndSprites,"end_sprites"},{prof::FrameEnd,"frame_end"},
{prof::EffectUpload,"effect_upload"},{prof::EffectBatches,"effect_batches"},{prof::EffectBatchCount,"effect_batch_count"},
{prof::RacerPose,"racer_pose"},{prof::ObjectRecord,"object_record"},
{prof::ListRecord,"list_record"},{prof::ListReplay,"list_replay"},
{prof::WriteDriver,"write_driver"},{prof::WriteEscalator,"write_escalator"},{prof::WriteSkin,"write_skin"},{prof::FlushData,"flush_data"},
};
inline constexpr unsigned Count=unsigned(std::size(Columns));
}
class RuntimeProfile {
public:
 enum Stage {Simulation,UiPrepare,Wait,Course,Scenery,Opponents,Items,Player,Effects,Hud,Bottom,Submit,StageCount};
 // Simulation ticks split by system (sim_* columns), summed over the frame's ticks:
 // input and item/AI preparation, Kinoko's scene step, item/AI rules after it,
 // opponent presentation state, camera/flow/HUD/Lakitu, audio, the player's
 // presentation, kart effect emitters and the NW4R particle step.
 enum SimPart {SimPre,SimKinoko,SimRules,SimOpponents,SimRace,SimAudio,SimPlayer,SimEffects,SimParticles,SimPartCount};
 bool enabled()const{return on();}
 void addSim(SimPart part,uint64_t ticks){row_.sim[part]+=float(ticks*1000.0/SYSCLOCK_ARM11);}
 RuntimeProfile(){
  if(FILE* marker=std::fopen("sdmc:/3ds/mkw-3ds/performance.txt","r")){
   std::fclose(marker);file_=std::fopen("sdmc:/3ds/mkw-3ds/performance.csv","w");
   prof::enabled=file_!=nullptr;
   if(file_){
    // No trailing newline: the profiling columns follow.
    std::fputs("course,frame,rendered,physics_ticks,interval_ms,simulation_ms,ui_prepare_ms,wait_ms,course_ms,scenery_ms,opponents_ms,items_ms,player_ms,effects_ms,hud_ms,bottom_ms,submit_ms,previous_gpu_queue_ms,draws,triangles,culled_triangles,culled_submeshes,flush_calls,flush_bytes,pose_evaluations,pose_reuses,skinned_vertices,linear_free,command_usage,render_thread_ms,render_begin_wait_ms,record_wait_ms,worker_ms",file_);
    for(const auto& c:profile_detail::Columns){
     std::fprintf(file_,",%s_ms",c.name);
     if(c.slot>=32)std::fprintf(file_,",%s_calls",c.name);
    }
    std::fputs(",hardware,model_profile,quality_reduced,model_variants_loaded,model_variant_fallbacks,model_variant_triangles,model_variant_draws,stereo_wanted,race_frame_cap,build_id,content_id,version,racer_distance_enabled,racer_distance_limit,racer_distance_culled,cpu_skinned_vertices,gpu_skin_vertex_references,object_gpu_enabled,object_gpu_models_loaded,object_gpu_fallbacks,object_gpu_vertex_references,texture_enabled,texture_variants_loaded,texture_variant_fallbacks,texture_source_bytes,texture_candidate_bytes,sim_pre_ms,sim_kinoko_ms,sim_rules_ms,sim_opponents_ms,sim_race_ms,sim_audio_ms,sim_player_ms,sim_effects_ms,sim_particles_ms,texture_mips_enabled,texture_mips_nearest,texture_mips_loaded,texture_mips_fallbacks,texture_mips_level_bytes,gpu_pipeline\n",file_);
   }
  }
 }
 ~RuntimeProfile(){flush();if(file_)std::fclose(file_);}
 void begin(unsigned course,unsigned frame,unsigned ticks,float interval){
  if(!on())return;
  row_={};row_.course=course;row_.frame=frame;startTicks_=ticks;row_.interval=interval;
  stamp_=svcGetSystemTick();
 }
 void mark(Stage stage){if(!on())return;auto now=svcGetSystemTick();row_.ms[stage]=float((now-stamp_)*1000.0/SYSCLOCK_ARM11);stamp_=now;}
 void end(unsigned ticks,const Renderer& renderer,bool rendered=true){
  if(!on())return;
  row_.ticks=ticks-startTicks_;row_.rendered=rendered;
  row_.variantLoads=modelDetail().loaded.load(std::memory_order_relaxed);
  row_.variantFallbacks=modelDetail().fallbacks.load(std::memory_order_relaxed);
  row_.stereo=renderer.stereoWanted();
  row_.frameCap=modelDetail().hardware==ModelDetail::New3DS&&!row_.stereo?60:30;
  row_.reduced=renderQuality().reduced;
  row_.distanceCulled=racerDistance().culled.load(std::memory_order_relaxed);
  row_.objectGpuLoads=objectGpuSkin().loaded.load(std::memory_order_relaxed);
  row_.objectGpuFallbacks=objectGpuSkin().fallbacks.load(std::memory_order_relaxed);
  row_.objectGpuReferences=objectGpuSkin().vertexReferences.load(std::memory_order_relaxed);
  row_.textureLoads=textureDetail().loaded.load(std::memory_order_relaxed);
  row_.textureFallbacks=textureDetail().fallbacks.load(std::memory_order_relaxed);
  row_.textureSourceBytes=textureDetail().sourceBytes.load(std::memory_order_relaxed);
  row_.textureCandidateBytes=textureDetail().candidateBytes.load(std::memory_order_relaxed);
  row_.mipLoads=textureMips().loaded.load(std::memory_order_relaxed);
  row_.mipFallbacks=textureMips().fallbacks.load(std::memory_order_relaxed);
  row_.mipLevelBytes=textureMips().levelBytes.load(std::memory_order_relaxed);
  // Frame pipelining (Renderer::setPipelined): begin() waited for the frame
  // before the previous one; previous_gpu_queue_ms is that frame's GPU time.
  row_.pipeline=renderer.pipelined();
  if(rendered){row_.gpu=renderer.previousGpuMs;row_.draws=renderer.drawCalls;row_.triangles=renderer.triangles;
  row_.culledTriangles=renderer.culledTriangles;row_.culledSubmeshes=renderer.culledSubmeshes;
  row_.metrics=renderMetrics;row_.linearFree=linearSpaceFree();row_.command=renderer.commandUsage;
  row_.renderThread=renderer.renderThreadMs;row_.renderWait=renderer.beginWaitMs;row_.recordWait=renderer.recordWaitMs;
  row_.variantTriangles=renderer.modelVariantTriangles;row_.variantDraws=renderer.modelVariantDraws;
  // Same frame as render_thread_ms: the last one the render thread finished.
  const auto& p=prof::latest();
  for(unsigned c=0;c<profile_detail::Count;++c){
   const unsigned slot=profile_detail::Columns[c].slot;
   row_.profMs[c]=p.ms[slot];row_.profCalls[c]=p.calls[slot];
  }
  }
  rows_[count_++]=row_;if(count_==rows_.size())flush();
 }
private:
 struct Row {unsigned course=0,frame=0,ticks=0,rendered=0;float interval=0;std::array<float,StageCount> ms{};float gpu=0;unsigned draws=0,triangles=0,culledTriangles=0,culledSubmeshes=0;RenderMetrics metrics{};unsigned linearFree=0;float command=0,renderThread=0,renderWait=0,recordWait=0;
  unsigned variantLoads=0,variantFallbacks=0,variantTriangles=0,variantDraws=0,stereo=0,frameCap=0,reduced=0,distanceCulled=0;
  unsigned objectGpuLoads=0,objectGpuFallbacks=0,objectGpuReferences=0;
  unsigned textureLoads=0,textureFallbacks=0,textureSourceBytes=0,textureCandidateBytes=0;
  unsigned mipLoads=0,mipFallbacks=0,mipLevelBytes=0;
  unsigned pipeline=0;
  std::array<float,profile_detail::Count> profMs{};std::array<unsigned,profile_detail::Count> profCalls{};
  std::array<float,SimPartCount> sim{};};
 void flush(){
  if(!on())return;
  for(unsigned i=0;i<count_;++i){const auto& r=rows_[i];std::fprintf(file_,"%u,%u,%u,%u,%.4f",r.course,r.frame,r.rendered,r.ticks,r.interval);for(auto ms:r.ms)std::fprintf(file_,",%.4f",ms);
   std::fprintf(file_,",%.4f,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%.5f,%.4f,%.4f,%.4f,%.4f",r.gpu,r.draws,r.triangles,r.culledTriangles,r.culledSubmeshes,r.metrics.flushCalls,r.metrics.flushBytes,r.metrics.poseEvaluations,r.metrics.poseReuses,r.metrics.skinnedVertices,r.linearFree,r.command,r.renderThread,r.renderWait,r.recordWait,r.metrics.workerMs);
   for(unsigned c=0;c<profile_detail::Count;++c){
    std::fprintf(file_,",%.4f",r.profMs[c]);
    if(profile_detail::Columns[c].slot>=32)std::fprintf(file_,",%u",r.profCalls[c]);
   }
   std::fprintf(file_,",%s,%s,%u,%u,%u,%u,%u,%u,%u,%s,%s,%s,%u,%.1f,%u,%u,%u,%u,%u,%u,%u",modelDetail().hardwareName(),modelDetail().profileName(),r.reduced,r.variantLoads,r.variantFallbacks,r.variantTriangles,r.variantDraws,r.stereo,r.frameCap,MKW_BUILD_ID,MKW_CONTENT,MKW_VERSION,unsigned(racerDistance().enabled),racerDistance().distance,r.distanceCulled,r.metrics.cpuSkinnedVertices,r.metrics.gpuSkinVertexReferences,unsigned(objectGpuSkin().enabled),r.objectGpuLoads,r.objectGpuFallbacks,r.objectGpuReferences);
   std::fprintf(file_,",%u,%u,%u,%u,%u",unsigned(textureDetail().enabled),r.textureLoads,r.textureFallbacks,r.textureSourceBytes,r.textureCandidateBytes);
   for(auto ms:r.sim)std::fprintf(file_,",%.4f",ms);
   std::fprintf(file_,",%u,%u,%u,%u,%u,%u\n",unsigned(textureMips().enabled),unsigned(textureMips().nearestLevel),r.mipLoads,r.mipFallbacks,r.mipLevelBytes,r.pipeline);
  }
  std::fflush(file_);count_=0;
 }
 // Profiling is on only when performance.txt exists on the SD card, release builds
 // included: without it file_ stays null and every call returns immediately.
 bool on()const{return file_!=nullptr;}
 FILE* file_=nullptr;std::array<Row,120> rows_{};Row row_{};unsigned count_=0,startTicks_=0;uint64_t stamp_=0;
};
}
