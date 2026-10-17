"""Copy generated staging files without duplicating APFS blocks when possible.

Filesystem clones have independent contents: editing either file never changes
the other. Unsupported platforms/filesystems use an ordinary copy, not a link.
"""
import ctypes
import os
from pathlib import Path
import shutil
import stat
import sys


def _clone_file(source, destination):
    if sys.platform != 'darwin':
        return False
    try:
        clonefile = ctypes.CDLL(None, use_errno=True).clonefile
    except (AttributeError, OSError):
        return False
    # macOS SDK sys/clonefile.h: int clonefile(const char *, const char *,
    # uint32_t). CLONE_NOOWNERCOPY avoids requesting source ownership.
    clonefile.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32)
    clonefile.restype = ctypes.c_int
    return clonefile(os.fsencode(source), os.fsencode(destination), 0x0002) == 0


def stage_copy(source, destination):
    """Replace one generated destination with an independent copy of source.

    Source must be a regular file. Reject same-file paths, including symlink
    and hardlink aliases, before removing an existing staging destination.
    """
    source, destination = Path(source), Path(destination)
    if not stat.S_ISREG(source.stat().st_mode):
        raise shutil.SpecialFileError(f'Not a regular staging source: {source}')
    try:
        same_file = os.path.samefile(source, destination)
    except FileNotFoundError:
        same_file = False
    if same_file:
        raise shutil.SameFileError(f'{source} and {destination} are the same file')
    destination.unlink(missing_ok=True)
    if not _clone_file(source, destination):
        # A failed clone must not leave a partial destination for copyfile to
        # follow. Only this generated staging path is removed.
        destination.unlink(missing_ok=True)
        shutil.copyfile(source, destination)
    return destination
