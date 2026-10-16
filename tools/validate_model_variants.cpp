// Host check of real generated sidecars with the exact runtime validator.
// clang++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined \
//   -Iinclude tools/validate_model_variants.cpp -o build/validate_model_variants
// build/validate_model_variants ORIGINAL_MODELS_DIR CANDIDATE_MODELS_DIR
#include "mkw/model_variant.hpp"
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>

namespace {
namespace fs = std::filesystem;
struct Vertex { float values[10]; uint8_t color[4]; };
// The shared validator only names first/count; other material bytes stay raw.
// Zero initialization supplies the absent v4/v5 fields in older records.
struct Submesh { uint32_t first=0,count=0; std::array<uint8_t,84> material{}; };
static_assert(sizeof(Vertex)==44 && sizeof(Submesh)==92 && offsetof(Submesh,material)==8);
constexpr size_t MaxFileBytes=24+2048*92+512*64+65535*44+3000000*2+16+65535*4;

struct Reader {
    const std::vector<uint8_t>& bytes;
    size_t offset=0;
    bool read(void* out,size_t count) {
        if(count>bytes.size()-offset)return false;
        if(count)std::memcpy(out,bytes.data()+offset,count);
        offset+=count;return true;
    }
    bool matches(const void* expected,size_t count) {
        if(count>bytes.size()-offset)return false;
        if(count&&std::memcmp(expected,bytes.data()+offset,count))return false;
        offset+=count;return true;
    }
    bool atEnd()const { return offset==bytes.size(); }
};
struct Allocation {
    std::vector<uint16_t> storage;
    std::vector<Vertex> vertexStorage;
    uint16_t* data=nullptr;
    Vertex* vertexData=nullptr;
    bool allocate(uint32_t count) {
        storage.resize(count);data=storage.data();return true;
    }
    bool allocateVertices(uint32_t count) {vertexStorage.resize(count);vertexData=vertexStorage.data();return true;}
};
void require(bool condition,const char* message) {
    if(!condition)throw std::runtime_error(message);
}
std::vector<uint8_t> readFile(const fs::path& path) {
    require(fs::is_regular_file(path),"not a regular file");
    const auto size=fs::file_size(path);
    require(size>=24&&size<=MaxFileBytes,"file size outside GMDL bounds");
    std::ifstream file(path,std::ios::binary);
    require(bool(file),"cannot open file");
    std::vector<uint8_t> bytes(static_cast<size_t>(size));
    file.read(reinterpret_cast<char*>(bytes.data()),static_cast<std::streamsize>(size));
    require(bool(file)&&file.peek()==std::char_traits<char>::eof(),"file truncated or changed during read");
    return bytes;
}
template<class T> T materialField(const Submesh& submesh,size_t fileOffset) {
    T value;std::memcpy(&value,submesh.material.data()+fileOffset-8,sizeof value);return value;
}

struct Original {
    uint32_t header[6]{};
    size_t record=0;
    std::vector<Submesh> submeshes;
    std::vector<std::array<char,64>> names;
    std::vector<Vertex> vertices;
    std::vector<uint16_t> indices;
    explicit Original(const std::vector<uint8_t>& bytes) {
        Reader reader{bytes};
        require(reader.read(header,sizeof header),"truncated original header");
        require(std::memcmp(header,"GMDL",4)==0,"bad original magic");
        require(header[1]>=3&&header[1]<=5,"unsupported original version");
        require(header[2]>0&&header[2]<=65535&&header[3]>0&&header[3]<=3000000&&
                header[3]%3==0&&header[4]<=2048&&header[5]<=512,"invalid original counts");
        record=header[1]==5?92:header[1]==4?56:44;
        const size_t expected=24+size_t(header[4])*record+size_t(header[5])*64+
                              size_t(header[2])*sizeof(Vertex)+size_t(header[3])*sizeof(uint16_t);
        require(bytes.size()==expected,"original length does not match header");
        submeshes.resize(header[4]);
        for(auto& submesh:submeshes) {
            require(reader.read(&submesh,record),"truncated original submesh");
            require(submesh.first<=header[3]&&submesh.count<=header[3]-submesh.first&&
                    submesh.count%3==0,"original submesh out of bounds");
            for(const size_t field:{size_t(8),size_t(16)}) {
                const int32_t texture=materialField<int32_t>(submesh,field);
                require(texture>=-1&&texture<static_cast<int32_t>(header[5]),"original texture index out of bounds");
            }
            if(record==92) {
                const auto reflection=materialField<uint32_t>(submesh,56);
                const auto lighting=materialField<uint32_t>(submesh,44);
                const auto secondary=materialField<int32_t>(submesh,16);
                // Same file constraints as Model::load (retail::Menu is bit 21).
                require(reflection<=6&&reflection!=5&&(!reflection||(lighting&(1u<<21))),
                        "invalid original reflection mode");
                require(!reflection||reflection==3||secondary>=0,"missing original reflection texture");
                for(size_t offset=68;offset<92;offset+=4)
                    require(std::isfinite(materialField<float>(submesh,offset)),"nonfinite original reflection UV");
            }
        }
        names.resize(header[5]);
        for(auto& name:names) {
            require(reader.read(name.data(),name.size()),"truncated original texture name");
            const auto* end=static_cast<const char*>(std::memchr(name.data(),0,name.size()));
            require(end!=nullptr,"unterminated original texture name");
            const std::string_view value(name.data(),static_cast<size_t>(end-name.data()));
            require(value.find('/')==value.npos&&value.find('\\')==value.npos&&
                    value.find("..")==value.npos,"invalid original texture name");
        }
        vertices.resize(header[2]);indices.resize(header[3]);
        require(reader.read(vertices.data(),vertices.size()*sizeof(Vertex))&&
                reader.read(indices.data(),indices.size()*sizeof(uint16_t))&&reader.atEnd(),
                "truncated original geometry");
        for(const auto& vertex:vertices)
            for(unsigned n=0;n<3;++n)require(std::isfinite(vertex.values[n]),"nonfinite original position");
        for(const auto index:indices)require(index<header[2],"original vertex index out of bounds");
    }
};
}

int main(int argc,char** argv) {
    if(argc!=3&&argc!=4) {
        std::cerr<<"Usage: "<<argv[0]<<" ORIGINAL_MODELS_DIR CANDIDATE_MODELS_DIR [REBAKED_TEXTURES_DIR]\n";
        return 2;
    }
    try {
        const uint16_t endian=1;
        require(*reinterpret_cast<const uint8_t*>(&endian)==1,"little-endian host required");
        const fs::path originals=argv[1],candidates=argv[2];
        // Re-baked textures default to <package>/textures/o3ds beside models/o3ds.
        const fs::path rebakedDir=argc==4?fs::path(argv[3]):candidates.parent_path().parent_path()/"textures"/"o3ds";
        require(fs::is_directory(originals)&&fs::is_directory(candidates),"both inputs must be directories");
        std::vector<fs::path> files;
        for(const auto& entry:fs::directory_iterator(candidates))
            if(entry.path().extension()==".gmdl")files.push_back(entry.path());
        std::sort(files.begin(),files.end());
        require(!files.empty(),"no candidate .gmdl files found");
        uint64_t originalTriangles=0,candidateTriangles=0,originalVertices=0,candidateVertices=0;
        size_t passed=0,failed=0;
        for(const auto& path:files) {
            try {
                const auto originalBytes=readFile(originals/path.filename());
                const Original original(originalBytes);
                const auto candidateBytes=readFile(path);
                Reader reader{candidateBytes};Allocation allocation;
                std::vector<Submesh> spans;std::vector<uint32_t> remap;uint32_t count=0,format=0;
                std::vector<std::array<char,64>> rebaked;std::vector<std::array<float,3>> skinPositions;
                require(mkw::readModelVariant(reader,original.header,original.record,original.submeshes,
                        original.names,original.vertices.data(),original.indices.data(),allocation,spans,count,remap,
                        &rebaked,&format,&skinPositions),
                        "runtime readModelVariant rejected candidate");
                // Every re-baked texture the runtime would load must be staged next to the models.
                for(const auto& name:rebaked)if(name[0])
                    require(fs::is_regular_file(rebakedDir/(std::string(name.data())+".t3x")),
                            ("re-baked texture missing: "+std::string(name.data())).c_str());
                ++passed;originalTriangles+=original.header[3]/3;candidateTriangles+=count/3;
                originalVertices+=original.header[2];candidateVertices+=remap.empty()?original.header[2]:remap.size();
                std::cout<<"PASS "<<path.filename().string()<<": "<<original.header[3]/3<<" -> "<<count/3<<" triangles\n";
            } catch(const std::exception& error) {
                ++failed;std::cerr<<"FAIL "<<path.filename().string()<<": "<<error.what()<<'\n';
            }
        }
        std::cout<<"Validated "<<passed<<" sidecars; rejected "<<failed<<". Accepted triangles: "
                 <<originalTriangles<<" -> "<<candidateTriangles<<".\n";
        std::cout<<"Accepted vertices: "<<originalVertices<<" -> "<<candidateVertices<<".\n";
        std::cout<<"This validates runtime acceptance and source mapping, not visual quality or hardware performance.\n";
        return failed?1:0;
    } catch(const std::exception& error) {
        std::cerr<<"validate_model_variants: "<<error.what()<<'\n';return 1;
    }
}
