"""Prepare persistent, isolated inference environments and pinned model caches.

No provisioning or paid API calls. Run on the explicitly selected GPU pod.
Downloads are explicit, resumable and kept on the mounted persistent volume.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / 'manifest.json').read_text())


def run(command, **kwargs):
    subprocess.run([str(x) for x in command], check=True, **kwargs)


def progress(root, model, stage, started):
    print(json.dumps({'model': model, 'stage': stage, 'elapsedSeconds': time.monotonic() - started,
                      'freeDiskBytes': shutil.disk_usage(root).free, 'at': time.time()}), flush=True)


def reusable(root, model):
    receipt = root / 'prepared' / (model + '.json')
    try:
        record = json.loads(receipt.read_text())
        return (record['source'] == MANIFEST['sources'][model] and record['models'] == MANIFEST['models'][model]
                and (root / 'envs' / model / 'bin' / 'python').is_file()
                and all((root / 'models' / m['folder'] / ('codec.pth' if m.get('allow') else 'config.json')).is_file()
                        for m in MANIFEST['models'][model]))
    except (OSError, ValueError, KeyError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='/workspace/questiontemplate-voice-lab')
    parser.add_argument('--model', choices=['all', *MANIFEST['models']], default='all')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    # Venvs must not point at the CPU setup pod's ephemeral /root interpreter.
    # Keep uv's managed Python and hard-linked wheel cache on this same volume.
    os.environ.update(UV_PYTHON_INSTALL_DIR=str(root / 'python'), UV_PYTHON_PREFERENCE='only-managed',
                      UV_CACHE_DIR=str(root / 'cache' / 'uv'), UV_LINK_MODE='hardlink')
    if not shutil.which('uv') or not shutil.which('git') or not shutil.which('ffmpeg'):
        parser.error('Install uv, git and ffmpeg first. See README.md.')
    selected = list(MANIFEST['models']) if args.model == 'all' else [args.model]
    prepared_models = [name for name in selected if reusable(root, name)]
    for name in prepared_models:
        print(json.dumps({'model': name, 'stage': 'reused-pinned-cache', 'prepared': True,
                          'inferenceVerified': False}), flush=True)
    selected = [name for name in selected if name not in prepared_models]
    if not selected:
        return
    # Full weights plus independent Torch/SGLang environments exceed a 70 GB volume.
    # This is a preparation safety margin, not a measured installation size.
    required = 90_000_000_000 if args.model == 'all' else (60_000_000_000 if args.model == 'drama3' else 20_000_000_000)
    if shutil.disk_usage(root).free < required and not all((root / 'prepared' / (m + '.json')).exists() for m in selected):
        parser.error('Insufficient free persistent disk for the selected models and isolated environments.')
    tools = root / 'envs' / 'tools'
    run(['uv', 'python', 'install', MANIFEST['python']])
    if not (tools / 'bin' / 'python').exists():
        run(['uv', 'venv', '--python', MANIFEST['python'], tools])
    run(['uv', 'pip', 'install', '--python', tools / 'bin' / 'python', 'huggingface-hub==2.3.0'])
    for name in selected:
        start = time.monotonic()
        progress(root, name, 'source-checkout', start)
        source = MANIFEST['sources'][name]
        checkout = root / 'sources' / name
        checkout.parent.mkdir(parents=True, exist_ok=True)
        if not (checkout / '.git').exists():
            checkout.mkdir(parents=True, exist_ok=True)
            run(['git', 'init', checkout])
            run(['git', '-C', checkout, 'remote', 'add', 'origin', source['url']])
            run(['git', '-C', checkout, 'fetch', '--depth', '1', 'origin', source['revision']])
            run(['git', '-C', checkout, 'checkout', '--detach', 'FETCH_HEAD'])
        actual = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != source['revision']:
            raise RuntimeError('Existing source checkout differs from the pinned manifest; preserve it and review explicitly.')
        env = root / 'envs' / name
        if not (env / 'bin' / 'python').exists():
            run(['uv', 'venv', '--python', MANIFEST['python'], env])
        requirement = str(checkout) + ('[' + source['extra'] + ']' if source.get('extra') else '')
        # Use the upstream uv overrides, including Drama3's protobuf compatibility pin.
        progress(root, name, 'runtime-install', start)
        run(['uv', 'pip', 'install', '--python', env / 'bin' / 'python', '--prerelease=allow', '-e', requirement], cwd=checkout)
        # Catch Python dependency/API-import failures on the cheap setup machine,
        # before renting a GPU. Do not load weights or initialize CUDA here.
        imports = {'drama3': 'import sglang_omni', 'voxcpm2': 'from voxcpm import VoxCPM',
                   'qwen3': 'from qwen_tts import Qwen3TTSModel'}
        progress(root, name, 'cpu-import-check', start)
        import_env = os.environ.copy()
        import_env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        run([env / 'bin' / 'python', '-c', imports[name]], env=import_env)
        for model in MANIFEST['models'][name]:
            progress(root, name, 'download-' + model['folder'], start)
            destination = root / 'models' / model['folder']
            download = ('import json,sys; from huggingface_hub import snapshot_download; '
                        'm=json.loads(sys.argv[1]); snapshot_download(repo_id=m["repo"],revision=m["revision"],'
                        'local_dir=sys.argv[2],allow_patterns=m.get("allow"))')
            run([tools / 'bin' / 'python', '-c', download, json.dumps(model), destination])
        freeze = subprocess.check_output(['uv', 'pip', 'freeze', '--python', str(env / 'bin' / 'python')], text=True)
        prepared = root / 'prepared'
        prepared.mkdir(exist_ok=True)
        (prepared / (name + '.freeze.txt')).write_text(freeze)
        receipt = {'model': name, 'source': source, 'models': MANIFEST['models'][name],
                   'preparedAt': time.time(), 'preparationSeconds': time.monotonic() - start,
                   'inferenceVerified': False, 'python': str(env / 'bin' / 'python')}
        (prepared / (name + '.json')).write_text(json.dumps(receipt, indent=2))
        progress(root, name, 'prepared-not-inference-verified', start)
        print(json.dumps({'model': name, 'prepared': True, 'inferenceVerified': False}), flush=True)


if __name__ == '__main__':
    main()
