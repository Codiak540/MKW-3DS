#include <mkw/object_gpu_skin.hpp>
#include <cassert>

int main(){
    mkw::ObjectGpuSkin policy;
    assert(!policy.enabled);
    for(unsigned bits=0;bits<32;++bits){
        const bool known=bits&1,new3ds=bits&2,qa=bits&4,fullQuality=bits&8,requested=bits&16;
        policy.loaded=3;policy.fallbacks=2;policy.vertexReferences=123;
        policy.configure(known,new3ds,qa,fullQuality,requested);
#ifdef MKW_RELEASE
        assert(!policy.enabled);
#else
        assert(policy.enabled==(known&&!new3ds&&qa&&!fullQuality&&requested));
#endif
        assert(policy.loaded==0&&policy.fallbacks==0&&policy.vertexReferences==0);
    }
}
