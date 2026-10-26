"""Prepare persistent, isolated inference environments and pinned model caches.

No provisioning or paid API calls. Run on the explicitly selected GPU pod.
Downloads are explicit, resumable and kept on the mounted persistent volume.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / 'manifest.json').read_text())
QUOTA_ROOT = None
QUOTA_BYTES = 100_000_000_000
RESERVE_BYTES = 2_000_000_000
QUOTA_RACE_BUFFER_BYTES = 64 * 1024 * 1024


def quota_usage(root):
    # Network-volume df reports the shared backing filesystem, not this volume's
    # provisioned quota. GNU du counts hard-linked files once for the whole root.
    # During a package uninstall, du can see a directory entry disappear before
    # stat(). Retry only that exact ENOENT race, never permission or I/O errors.
    command = ['du', '-s', '-B1', str(root)]
    observed = []
    for attempt in range(3):
        result = subprocess.run(command, text=True, capture_output=True, timeout=60,
                                env=dict(os.environ, LC_ALL='C', LANG='C'))
        lines = result.stdout.strip().splitlines()
        match = re.fullmatch(r'([0-9]+)\t(.+)', lines[0]) if len(lines) == 1 else None
        valid_total = match is not None and match[2] == str(root)
        diagnostics = result.stderr.strip().splitlines()
        harmless = bool(diagnostics)
        for line in diagnostics:
            missing = re.fullmatch(r"du: cannot (?:access|stat|read directory) '(.+)': No such file or directory", line)
            if not missing or not Path(missing[1]).is_relative_to(root) or Path(missing[1]) == Path(root):
                harmless = False
                break
        if result.returncode == 0 and not diagnostics:
            if not valid_total:
                raise RuntimeError('Volume usage scan did not return a valid quota total.')
            return max([*observed, int(match[1])])
        if result.returncode != 1 or not harmless or not valid_total:
            raise subprocess.CalledProcessError(result.returncode or 1, command, output=result.stdout, stderr=result.stderr)
        observed.append(int(match[1]))
        if attempt < 2:
            time.sleep(.1)
    # Continuous unlink activity still produced three numeric totals. Use the
    # highest observed allocation plus extra headroom, retaining the separate
    # 2GB quota reserve. The next monitor pass measures again.
    return max(observed) + QUOTA_RACE_BUFFER_BYTES


def quota_guard(root, needed=0):
    used = quota_usage(root)
    remaining = max(0, QUOTA_BYTES - used)
    if remaining < needed + RESERVE_BYTES:
        raise RuntimeError('The owned voice volume is near its configured quota. Stop preparation, preserve model/env files, '
                           'and remove only reviewed rebuildable caches or approve a resize explicitly. '
                           f'Used={used} bytes, cap={QUOTA_BYTES}, required headroom={needed + RESERVE_BYTES}.')
    return used, remaining


def run(command, **kwargs):
    if QUOTA_ROOT is not None:
        quota_guard(QUOTA_ROOT)
    process = subprocess.Popen([str(x) for x in command], start_new_session=True, **kwargs)
    try:
        while True:
            try:
                code = process.wait(timeout=10)
                break
            except subprocess.TimeoutExpired:
                if QUOTA_ROOT is not None:
                    quota_guard(QUOTA_ROOT)
        if code:
            raise subprocess.CalledProcessError(code, command)
        if QUOTA_ROOT is not None:
            quota_guard(QUOTA_ROOT)
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        raise


def progress(root, model, stage, started):
    used, remaining = quota_guard(root)
    print(json.dumps({'model': model, 'stage': stage, 'elapsedSeconds': time.monotonic() - started,
                      'usedQuotaBytes': used, 'remainingQuotaBytes': remaining,
                      'configuredVolumeBytes': QUOTA_BYTES, 'at': time.time()}), flush=True)


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
    global QUOTA_ROOT, QUOTA_BYTES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='/workspace/questiontemplate-voice-lab')
    parser.add_argument('--model', choices=['all', *MANIFEST['models']], default='all')
    parser.add_argument('--volume-gb', type=float, default=100,
                        help='Owned network-volume quota in decimal GB; no automatic resize (default: 100).')
    parser.add_argument('--repair-runtime', action='store_true',
                        help='Reinstall selected runtime packages after an interrupted/quota-limited installation.')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    if not math.isfinite(args.volume_gb) or args.volume_gb <= 0:
        parser.error('Specify the owned network volume quota as positive decimal GB.')
    QUOTA_ROOT, QUOTA_BYTES = root, int(args.volume_gb * 1_000_000_000)
    # Venvs must not point at the CPU setup pod's ephemeral /root interpreter.
    # Keep uv's managed Python and hard-linked wheel cache on this same volume.
    os.environ.update(UV_PYTHON_INSTALL_DIR=str(root / 'python'), UV_PYTHON_PREFERENCE='only-managed',
                      UV_CACHE_DIR=str(root / 'cache' / 'uv'), UV_LINK_MODE='hardlink')
    if not shutil.which('uv') or not shutil.which('git') or not shutil.which('ffmpeg'):
        parser.error('Install uv, git and ffmpeg first. See README.md.')
    managed = list((root / 'python').glob('cpython-3.11*/bin/python3.11'))
    if managed:
        # Do not rerun uv's relocation/fixup over a working managed interpreter.
        # A quota-interrupted zero-byte sysconfig must be restored explicitly,
        # even when every model has an earlier successful preparation receipt.
        check = subprocess.run([str(managed[0]), '-c', 'import sysconfig; assert sysconfig.get_config_var("SOABI")'],
                               capture_output=True, timeout=20)
        if check.returncode:
            raise RuntimeError('Persistent managed Python is incomplete. Restore its exact distribution after freeing quota; existing environments were preserved.')
    selected = list(MANIFEST['models']) if args.model == 'all' else [args.model]
    prepared_models = [name for name in selected if not args.repair_runtime and reusable(root, name)]
    for name in prepared_models:
        print(json.dumps({'model': name, 'stage': 'reused-pinned-cache', 'prepared': True,
                          'inferenceVerified': False}), flush=True)
    selected = [name for name in selected if name not in prepared_models]
    if not selected:
        return
    quota_guard(root)
    tools = root / 'envs' / 'tools'
    if not managed:
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
        install = ['uv', 'pip', 'install', '--python', env / 'bin' / 'python', '--prerelease=allow']
        if args.repair_runtime:
            install.append('--reinstall')
        run([*install, '-e', requirement], cwd=checkout)
        # Catch Python dependency/API-import failures on the cheap setup machine,
        # before renting a GPU. Do not load weights or initialize CUDA here.
        imports = {'drama3': 'import sglang_omni', 'voxcpm2': 'from voxcpm import VoxCPM',
                   'qwen3': 'from qwen_tts import Qwen3TTSModel'}
        progress(root, name, 'cpu-import-check', start)
        import_env = os.environ.copy()
        import_env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        run([env / 'bin' / 'python', '-c', imports[name]], env=import_env)
        for model in MANIFEST['models'][name]:
            destination = root / 'models' / model['folder']
            present = sum(p.stat().st_size for p in destination.rglob('*') if p.is_file()) if destination.exists() else 0
            expected = model.get('bytes', 2_000_000_000)
            quota_guard(root, max(0, expected - present))
            progress(root, name, 'download-' + model['folder'], start)
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
                   'inferenceVerified': False, 'python': str(env / 'bin' / 'python'), 'runtimeReinstalled': args.repair_runtime}
        (prepared / (name + '.json')).write_text(json.dumps(receipt, indent=2))
        progress(root, name, 'prepared-not-inference-verified', start)
        print(json.dumps({'model': name, 'prepared': True, 'inferenceVerified': False}), flush=True)


if __name__ == '__main__':
    main()
