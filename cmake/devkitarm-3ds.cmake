# CMake toolchain file for the 3DS (devkitARM). Pass -DDKP_ROOT=<devkitPro dir>
# (mkw-build does), or set DEVKITPRO in the environment.
set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR armv6k)

if(NOT DKP_ROOT)
  if(DEFINED ENV{DEVKITPRO})
    set(DKP_ROOT "$ENV{DEVKITPRO}")
  else()
    set(DKP_ROOT "/opt/devkitpro")
  endif()
endif()
# MSYS-style paths from the Windows installer: /c/devkitPro -> C:/devkitPro
if(CMAKE_HOST_WIN32 AND DKP_ROOT MATCHES "^/([a-zA-Z])/(.*)$")
  set(DKP_ROOT "${CMAKE_MATCH_1}:/${CMAKE_MATCH_2}")
endif()
list(APPEND CMAKE_TRY_COMPILE_PLATFORM_VARIABLES DKP_ROOT)

set(_exe "")
if(CMAKE_HOST_WIN32)
  set(_exe ".exe")
endif()
set(_bin "${DKP_ROOT}/devkitARM/bin")
set(CMAKE_C_COMPILER   "${_bin}/arm-none-eabi-gcc${_exe}")
set(CMAKE_CXX_COMPILER "${_bin}/arm-none-eabi-g++${_exe}")
set(CMAKE_ASM_COMPILER "${_bin}/arm-none-eabi-gcc${_exe}")
set(CMAKE_AR           "${_bin}/arm-none-eabi-gcc-ar${_exe}" CACHE FILEPATH "")
set(CMAKE_RANLIB       "${_bin}/arm-none-eabi-gcc-ranlib${_exe}" CACHE FILEPATH "")
set(CMAKE_SIZE_UTIL    "${_bin}/arm-none-eabi-size${_exe}" CACHE FILEPATH "")

# The ARM11 core of the 3DS, as tools/build.py compiled it.
set(MKW_ARCH_FLAGS -march=armv6k -mtune=mpcore -mfloat-abi=hard -mtp=soft)
string(REPLACE ";" " " _arch "${MKW_ARCH_FLAGS}")
set(CMAKE_C_FLAGS_INIT   "${_arch}")
set(CMAKE_CXX_FLAGS_INIT "${_arch}")
set(CMAKE_ASM_FLAGS_INIT "${_arch}")

# Nothing here can run on the host.
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
set(CMAKE_FIND_ROOT_PATH "${DKP_ROOT}" "${DKP_ROOT}/libctru")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
