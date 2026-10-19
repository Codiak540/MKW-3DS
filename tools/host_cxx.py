"""The host C++ compiler for the Old 3DS cook's small helper tools.

Candidates: $CXX (it may carry a launcher, e.g. 'ccache g++'), clang++, c++
and g++ on PATH, then the pinned Zig (tools/release/toolchain.lock.json),
downloaded once into .toolchain/zig: a whole C++ toolchain with its own
standard library, so it needs no Visual Studio or Xcode. A candidate is used
only once it builds and runs a small test program (an LLVM install on Windows
without Visual Studio has clang++ but no standard library). With none working
(offline, say) the cook still finishes: it skips the build-time checks (the
game runs the same checks when it loads each file) and the visibility files
(tools/cook_o3ds_overlay.py).
"""
import functools
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / 'tools' / 'release' / 'toolchain.lock.json'
ZIG = ROOT / '.toolchain' / 'zig'
MISSING = 'no working C++ compiler, and the pinned Zig compiler could not be downloaded'
PROBE = r'''#include <cmath>
#include <cstdio>
#include <filesystem>
#include <thread>
int main(){int r=0;std::thread t([&]{r=int(std::sqrt(16.0));});t.join();
std::printf("%d\n",r);return std::filesystem::exists(".")?0:1;}
'''


def exe(path):
    """A helper tool's executable path (.exe on Windows)."""
    return Path(str(path) + ('.exe' if os.name == 'nt' else ''))


def compile_command(cxx, *args):
    # -pthread links the thread library on older Linux; Windows has none to link.
    return [*cxx, *args] + ([] if os.name == 'nt' else ['-pthread'])


def works(cxx):
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / 'probe.cpp', exe(Path(tmp) / 'probe')
        src.write_text(PROBE)
        try:
            built = subprocess.run(compile_command(cxx, '-std=c++20', '-O1', str(src), '-o', str(out)),
                                   capture_output=True, timeout=600)
            ran = built.returncode == 0 and subprocess.run([str(out)], capture_output=True, timeout=60)
            return bool(ran) and ran.returncode == 0 and ran.stdout.strip() == b'4'
        except (OSError, subprocess.SubprocessError):
            return False


def zig_key():
    system = {'Darwin': 'darwin', 'Linux': 'linux', 'Windows': 'windows'}.get(platform.system(), '')
    machine = platform.machine().lower()
    machine = {'amd64': 'x86_64', 'x64': 'x86_64', 'aarch64': 'arm64'}.get(machine, machine)
    return f'{system}-{machine}'


def zig():
    """['<zig>', 'c++'] from the pinned release (downloaded and checked once), or None."""
    lock = json.loads(LOCK.read_text())['zig']
    pin = lock['platforms'].get(zig_key())
    if not pin:
        return None
    root = ZIG / lock['version']
    binary = exe(root / 'zig')
    if not binary.is_file():
        sys.path.insert(0, str(ROOT / 'tools' / 'release'))
        from bootstrap_tools import fetch  # SHA-256 checked against the lock before use
        print(f"C++ compiler: downloading Zig {lock['version']} (once) ...", flush=True)
        try:
            fetch(pin['url'], pin['sha256'])  # kept in .toolchain/release/downloads until unpacked
        except (SystemExit, Exception) as error:
            print(f'  Zig download failed: {error}')
            return None
        archive = ROOT / '.toolchain' / 'release' / 'downloads' / pin['url'].rsplit('/', 1)[1]
        partial = ZIG / (lock['version'] + '.partial')
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(parents=True)
        try:
            if archive.suffix == '.zip':
                with zipfile.ZipFile(archive) as z:
                    z.extractall(partial)
            else:
                with tarfile.open(archive, 'r:xz') as t:
                    t.extractall(partial, **({'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}))
        finally:
            archive.unlink(missing_ok=True)  # the unpacked copy is what is kept
        # The archive holds one top folder (zig-<os>-<arch>-<version>).
        (top,) = [p for p in partial.iterdir() if p.is_dir()]
        shutil.rmtree(root, ignore_errors=True)
        top.rename(root)
        shutil.rmtree(partial, ignore_errors=True)
    return [str(binary), 'c++']


@functools.cache
def find_host_cxx():
    """A working compiler command, or None."""
    if os.environ.get('CXX'):
        candidates = [shlex.split(os.environ['CXX'])]
    else:
        candidates = [[name] for name in ('clang++', 'c++', 'g++') if shutil.which(name)]
    for cxx in candidates:
        if works(cxx):
            return cxx
    if candidates:
        print(f"C++ compiler: {', '.join(c[0] for c in candidates)} could not build a test program; using Zig")
    cxx = zig()
    return cxx if cxx and works(cxx) else None


def host_cxx():
    cxx = find_host_cxx()
    if not cxx:
        raise SystemExit(f'This tool needs a C++ compiler: {MISSING}.')
    return cxx
