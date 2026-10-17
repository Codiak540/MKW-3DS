// Host-only OPOS validation harness. No Nintendo SDK or runtime staging needed.
#include "mkw/position_variant.hpp"
#include <fstream>
#include <iostream>
#include <iterator>
#include <sstream>
#include <string>

static bool read(const char* path,size_t maximum,std::vector<uint8_t>& bytes){
    std::ifstream file(path,std::ios::binary|std::ios::ate);
    if(!file)return false;const auto size=file.tellg();
    if(size<0||uint64_t(size)>maximum)return false;
    bytes.resize(size_t(size));file.seekg(0);return bool(file.read(reinterpret_cast<char*>(bytes.data()),size));
}
int main(int argc,char** argv){
    if(argc!=5){std::cerr<<"usage: validate_position_variant original.gmdl original.dskm candidate.opos policy.txt\n"
        <<"Policy lines: t <original-global-triangle> or v <original-source-vertex>; sorted within each kind.\n";return 2;}
    std::vector<uint8_t> original,bank,wrapper,policyBytes;
    if(!read(argv[1],10*1024*1024,original)||!read(argv[2],12*1024*1024,bank)||!read(argv[3],16*1024*1024,wrapper)||!read(argv[4],2*1024*1024,policyBytes)){std::cerr<<"bounded file read failed\n";return 2;}
    std::vector<uint32_t> triangles,vertices;
    std::istringstream policyFile(std::string(policyBytes.begin(),policyBytes.end()));char kind;uint64_t value;
    while(policyFile>>kind){if(!(policyFile>>value)||value>0xffffffffu){std::cerr<<"invalid policy value\n";return 2;}
        if(kind=='t')triangles.push_back(uint32_t(value));else if(kind=='v')vertices.push_back(uint32_t(value));else return 2;}
    if(!policyFile.eof()||triangles.size()>1000000||vertices.size()>65535)return 2;
    mkw::opos::Policy policy{triangles.data(),triangles.size(),vertices.data(),vertices.size()};
    mkw::opos::Validated output;output.poseClip=0x12345678;output.candidate.nv=0xabcdef01;
    const char* error=nullptr;
    const bool ok=mkw::opos::validate({wrapper.data(),wrapper.size()},{original.data(),original.size()},{bank.data(),bank.size()},policy,output,&error);
    if(!ok){
        if(output.poseClip!=0x12345678||output.candidate.nv!=0xabcdef01){std::cerr<<"validator mutated output on failure\n";return 3;}
        std::cout<<"{\"accepted\":false,\"reason\":\""<<(error?error:"unspecified")<<"\",\"originalsPreserved\":true}\n";return 1;
    }
    std::cout<<"{\"accepted\":true,\"vertices\":"<<output.candidate.nv<<",\"triangles\":"<<output.candidate.ni/3
        <<",\"protectedTriangles\":"<<output.protectedCount<<",\"neutralClip\":"<<output.poseClip<<",\"neutralFrame\":"<<output.poseFrame
        <<",\"runtimeReady\":false,\"visualApproval\":false}\n";
}
