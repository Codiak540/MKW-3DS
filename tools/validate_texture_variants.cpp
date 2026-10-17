// Validate cooked OTEX files with the exact runtime parser. Input TSV contains
// verified original T3X path, tab, candidate OTEX path; paths may contain spaces.
#include <mkw/texture_detail.hpp>
#include <fstream>
#include <iostream>
#include <stdexcept>

std::vector<uint8_t> read(const std::string& path){
    std::ifstream file(path,std::ios::binary|std::ios::ate);
    if(!file)throw std::runtime_error("cannot open "+path);
    const auto size=file.tellg();
    if(size<0||size>2*1024*1024)throw std::runtime_error("invalid file size "+path);
    std::vector<uint8_t> data(static_cast<size_t>(size));file.seekg(0);
    if(!file.read(reinterpret_cast<char*>(data.data()),size)||file.peek()!=std::char_traits<char>::eof())throw std::runtime_error("truncated file "+path);
    return data;
}
int main(int argc,char** argv){
    if(argc!=2){std::cerr<<"Usage: validate_texture_variants PAIRS.tsv\n";return 2;}
    std::ifstream pairs(argv[1]);if(!pairs)return 2;
    std::string line;size_t passed=0,failed=0;uint64_t originalBytes=0,candidateBytes=0;
    while(std::getline(pairs,line)){
        try{
            const auto tab=line.find('\t');if(tab==line.npos||line.find('\t',tab+1)!=line.npos)throw std::runtime_error("invalid TSV row");
            const auto original=read(line.substr(0,tab)),candidate=read(line.substr(tab+1));mkw::ModelTextureLayout a,b;
            if(!mkw::readTextureVariant(original,candidate,a,b))throw std::runtime_error("runtime rejected "+line.substr(tab+1));
            ++passed;originalBytes+=a.bytes;candidateBytes+=b.bytes;
        }catch(const std::exception& e){++failed;std::cerr<<e.what()<<'\n';}
    }
    std::cout<<"Validated "<<passed<<" texture candidates; rejected "<<failed<<". GPU bytes across the asset set "<<originalBytes<<" -> "<<candidateBytes<<".\n";
    return failed||!passed?1:0;
}
