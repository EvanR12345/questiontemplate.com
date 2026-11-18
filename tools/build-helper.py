"""Build the downloadable Windows helper and its local Studio frontend from Git sources."""
import argparse
import re
import subprocess
from tempfile import NamedTemporaryFile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
PROTECTED = {'python', 'models', 'studio-runtime', 'outputs', 'studio-config.json',
             'updates', 'backups'}
WEB_SUFFIXES = {'.css', '.html', '.js', '.mjs', '.json', '.bat'}
WEB_FILES = {'THIRD-PARTY.md', 'VOICE-MODELS.md', 'STUDIO-WORKSPACE-TOOLS.md',
             'STUDIO-RECOVERY-PERFORMANCE-20261018.md', 'LICENSE-KOKORO.txt',
             'LICENSE-MISAKI.txt', 'favicon.svg', 'favicon.ico', 'apple-touch-icon.png'}
# Keep the public research gallery photos online. Their original PNG files exceed 900 MB.
GALLERY_REF = re.compile(rb'((?:src|href)=")\./(pipeline-[a-z-]+-\d{8}/)')


def member(path):
    parts = path.parts
    if any(part.startswith('.') for part in parts):
        return None
    if parts[0] == 'nvidia-helper':
        if parts[1].lower() in PROTECTED:
            raise ValueError(f'Protected helper path {path}')
        return path
    if parts[0] == 'vendor' or (len(parts) == 1 and
            (path.name in WEB_FILES or path.suffix in WEB_SUFFIXES) and
            not path.name.endswith('.test.mjs') and path.name not in {'404.html', 'benchmark-exports.mjs'}):
        return Path('nvidia-helper/web') / path
    return None


def populate(output):
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT)
    with ZipFile(output, 'w') as archive:
        for filename in sorted(filter(None, tracked.decode('utf-8').split('\0'))):
            path = Path(filename)
            arcname = member(path)
            if arcname is None:
                continue
            source = ROOT / path
            if source.is_symlink():
                raise ValueError(f'Cannot bundle a symlink: {path}')
            data = source.read_bytes()
            if path.suffix == '.html' and path.name.startswith('pipeline'):
                data = GALLERY_REF.sub(rb'\1https://questiontemplate.com/\2', data)
            if b'\0' not in data and path.suffix not in {'.gz', '.ico', '.png'}:
                data = data.replace(b'\r\n', b'\n')
                if path.suffix in {'.ps1', '.bat'}:
                    data = data.replace(b'\n', b'\r\n')
            info = ZipInfo(arcname.as_posix())
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compress_type=ZIP_DEFLATED, compresslevel=9)


def build(output):
    with NamedTemporaryFile(dir=output.parent, prefix='helper-', suffix='.zip', delete=False) as tmp:
        temporary = Path(tmp.name)
    try:
        populate(temporary)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--output', type=Path, default=ROOT / 'nvidia-helper.zip')
    build(cli.parse_args().output)
