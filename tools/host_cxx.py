"""The host C++ compiler for the Old 3DS cook's small helper tools.

$CXX when set (it may carry a launcher, e.g. 'ccache g++'), else the first of
clang++, c++ and g++ found on PATH.
"""
import os
import shlex
import shutil


def host_cxx():
    if os.environ.get('CXX'):
        return shlex.split(os.environ['CXX'])
    for name in ('clang++', 'c++', 'g++'):
        if shutil.which(name):
            return [name]
    raise SystemExit("The Old 3DS cook needs a C++ compiler for its helper tools: install clang or g++ "
                     "(Linux: 'sudo apt install clang', macOS: 'xcode-select --install'), or set CXX.")
