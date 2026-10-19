#pragma once
#include <atomic>
#include <cstdint>
#include <string>

namespace mkw {
// Low-poly models (models/o3ds sidecars, cooked for the Old 3DS). A console
// positively identified as an Old or New 3DS uses them (both have the same
// GPU); unknown hardware keeps the original models. QA builds can force the
// originals for A/B comparisons with full-quality.txt or o3ds-models-off.txt.
struct ModelDetail {
    enum Hardware {Unknown,Old3DS,New3DS};
    Hardware hardware=Unknown;
    bool candidate=false;
    std::atomic<uint32_t> loaded{0},fallbacks{0};
    static constexpr bool eligible(bool known,[[maybe_unused]] bool new3ds,bool fullQuality,bool disabled){
        return known&&!fullQuality&&!disabled;
    }
    void configure(bool known,bool new3ds,bool fullQuality,bool disabled){
        hardware=!known?Unknown:new3ds?New3DS:Old3DS;
        candidate=eligible(known,new3ds,fullQuality,disabled);
    }
    const char* hardwareName()const{return hardware==Old3DS?"old3ds":hardware==New3DS?"new3ds":"unknown";}
    const char* profileName()const{return candidate?"o3ds_candidate":"original";}
    // Deliberately accepts only the authored flat model namespace. Animation,
    // collision and other companions always keep their existing source paths.
    static std::string variantPath(const char* path,bool rival=false){
        const std::string source=path?path:"";
        if(source.size()<=12||source.compare(0,7,"models/")||source.compare(source.size()-5,5,".gmdl"))return {};
        const std::string name=source.substr(7);
        if(name.find('/')!=std::string::npos||name.find('\\')!=std::string::npos||name.find("..")!=std::string::npos)return {};
        return (rival?"models/o3ds/rival/":"models/o3ds/")+name;
    }
};
inline ModelDetail& modelDetail(){static ModelDetail detail;return detail;}
}
