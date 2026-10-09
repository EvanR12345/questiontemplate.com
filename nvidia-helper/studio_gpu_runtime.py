"""Locate NVRTC already supplied by Torch/CUDA; never install another runtime."""
import ctypes
import ctypes.util
import os
from pathlib import Path


def nvrtc_candidates(torch_file, platform=None, find_library=ctypes.util.find_library):
    platform = platform or os.name
    package = Path(torch_file).resolve().parent
    folders = [package / 'lib', package.parent / 'nvidia' / 'cuda_nvrtc' / 'lib',
               package.parent / 'nvidia' / 'cuda_nvrtc' / 'bin']
    patterns = ('nvrtc64_*.dll',) if platform == 'nt' else ('libnvrtc.so', 'libnvrtc.so.*')
    candidates = []
    for folder in folders:
        for pattern in patterns:
            # Keep the matched library path; do not strip its version or assume CUDA 12.
            candidates.extend(str(path) for path in sorted(folder.glob(pattern), reverse=True)
                              if path.is_file())
    if platform != 'nt':
        found = find_library('nvrtc')
        if found:
            candidates.append(found)
        candidates.extend(('libnvrtc.so', 'libnvrtc.so.13', 'libnvrtc.so.12'))
    return list(dict.fromkeys(candidates))


def load_nvrtc(torch_file, platform=None, loader=ctypes.CDLL, find_library=ctypes.util.find_library):
    tried = []
    for candidate in nvrtc_candidates(torch_file, platform, find_library):
        try:
            return loader(candidate)
        except OSError:
            tried.append(candidate)
    detail = ', '.join(tried) if tried else 'no bundled NVRTC library found'
    raise RuntimeError('Native motion could not load the existing CUDA compiler (' + detail +
                       '). Check the installed Torch/CUDA runtime, or choose CPU compatibility. '
                       'No additional runtime was downloaded.')
