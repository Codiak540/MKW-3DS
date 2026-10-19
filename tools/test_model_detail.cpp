#include "mkw/model_detail.hpp"
#include <cassert>
#include <cstring>

int main(){
    using mkw::ModelDetail;
    // Every combination: a positively identified Old or New 3DS uses the
    // low-poly models, in QA and release alike; unknown hardware never does.
    for(unsigned mask=0;mask<16;++mask){
        const bool known=mask&1,new3ds=mask&2,full=mask&4,off=mask&8;
        const bool expected=known&&!full&&!off;
        assert(ModelDetail::eligible(known,new3ds,full,off)==expected);
        ModelDetail detail;detail.configure(known,new3ds,full,off);
        assert(detail.candidate==expected);
        assert(!(!known&&detail.candidate));
        assert(std::strcmp(detail.hardwareName(),!known?"unknown":new3ds?"new3ds":"old3ds")==0);
        assert(std::strcmp(detail.profileName(),expected?"o3ds_candidate":"original")==0);
    }
    assert(ModelDetail::variantPath("models/driver_mario.gmdl")=="models/o3ds/driver_mario.gmdl");
    assert(ModelDetail::variantPath("models/driver_mario.gmdl",true)=="models/o3ds/rival/driver_mario.gmdl");
    assert(ModelDetail::variantPath("models/o3ds/mario.gmdl",true).empty());
    const char* invalid[]={nullptr,"","models/.gmdl","models/../mario.gmdl","models/o3ds/mario.gmdl","models/mario.dskm","textures/mario.gmdl","models/mario\\body.gmdl"};
    for(const char* path:invalid)
        assert(ModelDetail::variantPath(path).empty());
}
