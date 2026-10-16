# cmake -DSHBIN=<in.shbin> -DOUT=<shader_data.hpp> -P embed_shader.cmake
# The shader binary as a C array, as tools/build.py wrote build/shader_data.hpp.
file(READ "${SHBIN}" _hex HEX)
string(REGEX REPLACE "([0-9a-f][0-9a-f])" "0x\\1," _bytes "${_hex}")
file(WRITE "${OUT}" "#pragma once\n#include <cstdint>\nalignas(4) static const uint8_t mkw_shader[]={${_bytes}};\n")
