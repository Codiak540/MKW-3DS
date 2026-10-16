// Validate textures/o3ds/mips.pak with the exact runtime parser
// (include/mkw/texture_mips.hpp): every entry must be accepted against the
// original T3X the runtime loads (menu.pak, then race.pak, then the loose file),
// and its chain must start with the original level 0 (colour blocks for a
// conversion). Usage: validate_texture_mips MIPS.PAK ASSET_DIR
#include <mkw/texture_mips.hpp>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>

namespace {
struct Archive {
    std::ifstream file;mkw::MipArchiveIndex index;
    explicit Archive(const std::string& path,bool required){
        file.open(path,std::ios::binary|std::ios::ate);
        if(!file){if(required)throw std::runtime_error("cannot open "+path);return;}
        const uint64_t size=uint64_t(file.tellg());file.seekg(0);
        uint8_t header[12];uint32_t count=0,namesBytes=0;
        // menu.pak/race.pak hold more than OMIP's 65536 entries limit allows; read their counts directly.
        if(!file.read(reinterpret_cast<char*>(header),12)||std::memcmp(header,"MPK1",4))throw std::runtime_error("not MPK1 "+path);
        std::memcpy(&count,header+4,4);std::memcpy(&namesBytes,header+8,4);
        if(required&&!mkw::MipArchiveIndex::counts(header,count,namesBytes))throw std::runtime_error("bad counts "+path);
        std::vector<uint8_t> table(size_t(count)*12);std::vector<char> names(namesBytes);
        if(!file.read(reinterpret_cast<char*>(table.data()),table.size())||!file.read(names.data(),names.size())||
           !index.parse(table.data(),count,names.data(),namesBytes,size))throw std::runtime_error("bad index "+path);
    }
    bool read(const std::string& key,std::vector<uint8_t>& out){
        if(!file.is_open())return false;
        const auto* e=index.find(key.c_str());if(!e)return false;
        out.resize(e->size);file.clear();file.seekg(e->offset);
        return bool(file.read(reinterpret_cast<char*>(out.data()),out.size()));
    }
};
bool readLoose(const std::string& path,std::vector<uint8_t>& out){
    std::ifstream f(path,std::ios::binary|std::ios::ate);if(!f)return false;
    out.resize(size_t(f.tellg()));f.seekg(0);return bool(f.read(reinterpret_cast<char*>(out.data()),out.size()));
}
}
int main(int argc,char** argv){
    if(argc!=3){std::cerr<<"Usage: validate_texture_mips MIPS.PAK ASSET_DIR\n";return 2;}
    try{
        Archive mips(argv[1],true);const std::string root=argv[2];
        std::vector<std::unique_ptr<Archive>> packs;
        for(const char* name:{"/menu.pak","/race.pak"})packs.push_back(std::make_unique<Archive>(root+name,false));
        size_t passed=0,failed=0,converted=0;uint64_t before=0,after=0,levels=0;
        for(const auto& e:mips.index.entries){
            const std::string name=mips.index.names.data()+e.name,path="textures/"+name+".t3x";
            std::vector<uint8_t> original,candidate;bool found=false;
            for(auto& p:packs)if(p->read(path,original)){found=true;break;}
            if(!found)found=readLoose(root+"/"+path,original);
            mkw::MipVariant v;
            if(!found||!mips.read(name,candidate)||mkw::TextureMips::key(path.c_str())!=name||
               !mkw::readMipVariant(original.data(),original.size(),candidate.data(),candidate.size(),v)){
                ++failed;std::cerr<<"rejected "<<name<<(found?"":" (no original)")<<'\n';continue;
            }
            std::vector<uint8_t> chain(v.totalBytes());mkw::buildMipChain(original.data(),candidate.data(),v,chain.data());
            bool same=true;
            for(uint32_t block=0;block<v.level0Bytes/8;++block)
                same&=!std::memcmp(chain.data()+block*8,original.data()+21+block*(v.dropAlpha?16:8)+(v.dropAlpha?8:0),8);
            if(!same){++failed;std::cerr<<"level 0 differs "<<name<<'\n';continue;}
            ++passed;converted+=v.dropAlpha;levels+=v.levels;before+=original.size()-21;after+=v.totalBytes();
        }
        std::cout<<"Validated "<<passed<<" mip variants ("<<converted<<" ETC1A4->ETC1, "<<levels<<" levels); rejected "<<failed
                 <<". GPU bytes "<<before<<" -> "<<after<<".\n";
        return failed||!passed?1:0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 2;}
}
