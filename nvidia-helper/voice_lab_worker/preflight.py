"""Check Drama's build tools on the inexpensive preparation machine."""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def _create_relative_link(link, target):
    link.symlink_to(os.path.relpath(target, link.parent), target_is_directory=target.is_dir())


def ensure_cuda_layout(root, driver_stub=None):
    """Repair only missing development links inside this owned CUDA toolkit.

    pip ships the runtime as libcudart.so.13, while SGLang's C++ linker invokes
    -lcudart and searches for libcudart.so. Runtime loading alone does not test
    this development-library layout.
    """
    root = Path(root).resolve()
    env = root / 'envs' / 'drama3'
    candidates = list(env.glob('lib/python*/site-packages/nvidia/cu13'))
    if len(candidates) != 1:
        raise RuntimeError('Find exactly one pinned Drama CUDA13 toolkit before launching inference.')
    toolkit = candidates[0].resolve()
    if not toolkit.is_relative_to(root):
        raise RuntimeError('The CUDA toolkit must be inside this owned lab environment.')
    libraries = toolkit / 'lib'
    if not libraries.is_dir() or not libraries.resolve().is_relative_to(toolkit):
        raise RuntimeError('The CUDA runtime library directory is missing or outside the owned toolkit.')
    alias_created = False
    alias = toolkit / 'lib64'
    if not alias.exists():
        if alias.is_symlink():
            raise RuntimeError('The CUDA lib64 link is broken; review it before repairing this toolkit.')
        _create_relative_link(alias, libraries)
        alias_created = True
    if not alias.is_dir() or not alias.resolve().is_relative_to(toolkit):
        raise RuntimeError('The CUDA lib64 layout points outside the owned toolkit.')
    runtime = libraries / 'libcudart.so.13'
    if not runtime.is_file():
        versions = [path for path in libraries.glob('libcudart.so.13.*') if path.is_file()]
        if len(versions) != 1:
            raise RuntimeError('The pinned CUDA13 shared runtime library is missing or ambiguous.')
        runtime = versions[0]
    if not runtime.resolve().is_relative_to(toolkit) or runtime.stat().st_size == 0:
        raise RuntimeError('The CUDA13 runtime library is empty or outside the owned toolkit.')
    runtime_links_created = []
    for directory in {libraries.resolve(), alias.resolve()}:
        link = directory / 'libcudart.so'
        if link.exists():
            if not link.is_file() or not link.resolve().is_relative_to(toolkit) or link.stat().st_size == 0:
                raise RuntimeError('The existing CUDA runtime development link is invalid.')
            continue
        if link.is_symlink():
            raise RuntimeError('The CUDA runtime development link is broken; review it before repair.')
        _create_relative_link(link, runtime)
        runtime_links_created.append(str(link.relative_to(toolkit)))
    stub = toolkit / 'lib64' / 'stubs' / 'libcuda.so'
    if not stub.parent.resolve().is_relative_to(toolkit):
        raise RuntimeError('The CUDA driver stub directory points outside the owned toolkit.')
    copied = False
    if not stub.exists():
        original = Path(driver_stub or '/usr/local/cuda/lib64/stubs/libcuda.so')
        if not original.is_file():
            raise RuntimeError('Install the NVIDIA driver development stub before renting a GPU.')
        stub.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, stub)
        copied = True
    if not stub.is_file() or not stub.resolve().is_relative_to(toolkit) or stub.stat().st_size == 0:
        raise RuntimeError('The CUDA driver development stub is empty or outside the owned toolkit.')
    if not (toolkit / 'include' / 'cuda_runtime.h').is_file() or not (toolkit / 'nvvm' / 'libdevice' / 'libdevice.10.bc').is_file():
        raise RuntimeError('The persistent CUDA development toolkit is incomplete.')
    return {'toolkit': toolkit, 'cudaLib64AliasCreated': alias_created,
            'cudaRuntimeDevelopmentLinksCreated': runtime_links_created, 'driverStubFromBaseImage': copied}


def compile_cuda_runtime_probe(toolkit, environment):
    """Compile and link a CUDA-runtime program on CPU; never execute it."""
    toolkit = Path(toolkit)
    with tempfile.TemporaryDirectory(prefix='qt-lab-cudart-check-') as directory:
        folder = Path(directory)
        source, object_file, binary = folder / 'probe.cu', folder / 'probe.o', folder / 'probe'
        source.write_text('#include <cuda_runtime_api.h>\nint main(){int devices=0; return static_cast<int>(cudaGetDeviceCount(&devices));}\n')
        subprocess.run([str(toolkit / 'bin' / 'nvcc'), '-std=c++20', '-arch=sm_90a', '-Xcompiler=-fPIC',
                        '-c', str(source), '-o', str(object_file)],
                       env=environment, check=True, capture_output=True, timeout=60)
        # Deliberately match SGLang's host-link step: -lcudart needs the unversioned
        # development name, even when loading libcudart.so.13 already succeeds.
        subprocess.run([environment.get('CXX', 'c++'), str(object_file), '-L' + str(toolkit / 'lib64'),
                        '-Wl,--no-as-needed', '-lcudart', '-o', str(binary)],
                       env=environment, check=True, capture_output=True, timeout=30)
    return {'cudaRuntimeCompileLink': True, 'cudaRuntimeProbeExecuted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='/workspace/questiontemplate-voice-lab')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    env = root / 'envs' / 'drama3'
    layout = ensure_cuda_layout(root)
    toolkit = layout.pop('toolkit')
    environment = os.environ.copy()
    environment.update(CUDA_HOME=str(toolkit), CUDA_PATH=str(toolkit), CUDACXX=str(toolkit / 'bin' / 'nvcc'))
    environment['PATH'] = str(toolkit / 'bin') + os.pathsep + str(env / 'bin') + os.pathsep + environment.get('PATH', '')
    environment['LD_LIBRARY_PATH'] = str(toolkit / 'lib') + os.pathsep + environment.get('LD_LIBRARY_PATH', '')
    nvcc = subprocess.check_output([str(toolkit / 'bin' / 'nvcc'), '--version'], text=True)
    ninja = subprocess.check_output([str(env / 'bin' / 'ninja'), '--version'], text=True).strip()
    source = b'#include <format>\nint main(){auto s=std::format("{}",1);return s.empty();}\n'
    with tempfile.TemporaryDirectory(prefix='qt-lab-cxxcheck-') as directory:
        subprocess.run([environment.get('CXX', 'c++'), '-std=c++20', '-x', 'c++', '-', '-o', str(Path(directory) / 'probe')],
                       input=source, check=True, capture_output=True, timeout=30, env=environment)
    probe = compile_cuda_runtime_probe(toolkit, environment)
    result = dict(nvccVersion=nvcc.splitlines()[-1], ninjaVersion=ninja, cpp20FormatCompile=True,
                  cudaLib64Layout=True, **layout, **probe, inferenceVerified=False, at=time.time())
    (root / 'prepared').mkdir(exist_ok=True)
    (root / 'prepared' / 'build-preflight.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
