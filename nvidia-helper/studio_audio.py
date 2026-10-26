"""Opus-only audio assets. PCM travels in pipes/memory, never a WAV master.

Legacy recordings remain readable; new files publish atomically after encoding.
"""
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import struct

BITRATE = 128


def executable(ffmpeg=None):
    value = ffmpeg or shutil.which('ffmpeg')
    if not value:
        raise ValueError('Configure FFmpeg with libopus before generating audio.')
    return str(value)


def _flags():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


class OpusWriter:
    """Small wave-writer compatible interface, encoding signed PCM over stdin."""
    def __init__(self, target, ffmpeg=None):
        self.target = Path(target)
        self.ffmpeg = executable(ffmpeg)
        self.process = None
        self.frames = 0

    def __enter__(self):
        return self

    def setparams(self, params):
        channels, width, rate = params[:3]
        if width != 2 or channels != 1 or rate <= 0:
            raise ValueError('Narration encoder requires mono signed 16-bit PCM.')
        self.process = subprocess.Popen([self.ffmpeg, '-v', 'error', '-nostdin', '-y',
            '-f', 's16le', '-ar', str(rate), '-ac', '1', '-i', 'pipe:0',
            '-c:a', 'libopus', '-b:a', f'{BITRATE}k', '-vbr', 'on',
            '-application', 'audio', '-f', 'opus', str(self.target)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **_flags())

    def writeframes(self, data):
        if not self.process:
            raise RuntimeError('Set audio parameters before writing PCM.')
        self.process.stdin.write(data)
        self.frames += len(data) // 2

    writeframesraw = writeframes

    def __exit__(self, kind, value, traceback):
        try:
            if self.process:
                if kind:
                    self.process.kill()
                try:
                    self.process.stdin.close()
                except BrokenPipeError:
                    pass
                error = self.process.stderr.read()
                code = self.process.wait()
                self.process.stderr.close()
                if not kind and (code or not self.frames):
                    raise RuntimeError('Opus encoding failed; previous audio is preserved.')
        except Exception:
            self.target.unlink(missing_ok=True)
            raise
        finally:
            if kind:
                self.target.unlink(missing_ok=True)


def probe(path, ffmpeg=None):
    """Read format/decoded duration, including Opus pre-skip and end padding."""
    # Read-only compatibility for saved recordings; never creates a WAV file.
    if Path(path).suffix.lower() == '.wav':
        import wave
        with wave.open(str(path), 'rb') as legacy:
            return {'duration': legacy.getnframes()/legacy.getframerate(),
                    'sampleRate': legacy.getframerate(), 'channels': legacy.getnchannels(), 'codec': 'pcm_s16le'}
    if Path(path).suffix.lower() == '.opus':
        # Opus uses a fixed 48 kHz granule clock, regardless of input rate.
        # Read exact end padding/pre-skip without requiring another executable.
        first = True
        final = None
        serial = None
        with Path(path).open('rb') as source:
            while True:
                header = source.read(27)
                if not header:
                    break
                if len(header)!=27 or header[:5]!=b'OggS\0':
                    raise ValueError('Invalid or incomplete Opus audio.')
                table = source.read(header[26])
                if len(table)!=header[26]:raise ValueError('Incomplete Opus page.')
                payload = source.read(sum(table))
                if len(payload)!=sum(table):raise ValueError('Incomplete Opus audio.')
                current = header[14:18]
                if first:
                    if not header[5]&2 or len(payload)<19 or payload[:8]!=b'OpusHead':
                        raise ValueError('Expected an Opus recording.')
                    channels, skip = payload[9], struct.unpack('<H',payload[10:12])[0]
                    serial = current
                    first = False
                if current!=serial:raise ValueError('Expected one continuous Opus stream.')
                if header[5]&4:final=struct.unpack('<Q',header[6:14])[0]
        if first or final is None or final<=skip:
            raise ValueError('Incomplete or empty Opus recording.')
        return {'duration':(final-skip)/48000,'sampleRate':48000,'channels':channels,'codec':'opus'}
    ffmpeg = executable(ffmpeg)
    sibling = Path(ffmpeg).with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe')
    ffprobe = str(sibling) if sibling.is_file() else shutil.which('ffprobe')
    if not ffprobe:
        raise ValueError('Configure FFprobe alongside FFmpeg for audio validation.')
    result = subprocess.run([ffprobe, '-v', 'error', '-select_streams', 'a:0',
        '-show_entries', 'stream=codec_name,sample_rate,channels,duration,start_time:format=duration',
        '-of', 'json', str(path)], capture_output=True, timeout=60, **_flags())
    if result.returncode:
        raise ValueError('Audio could not be decoded; the original is preserved.')
    try:
        data = json.loads(result.stdout)
        stream = data['streams'][0]
        duration = float(stream.get('duration', data.get('format', {}).get('duration', 0)))
        # Ogg Opus duration includes its encoder pre-skip. FFmpeg decodes it away.
        if stream.get('codec_name') == 'opus':
            duration -= float(stream.get('start_time', 0))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError()
        return {'duration': duration, 'sampleRate': int(stream['sample_rate']),
                'channels': int(stream['channels']), 'codec': stream['codec_name']}
    except (ValueError, TypeError, KeyError, IndexError):
        raise ValueError('Invalid audio duration or stream metadata.') from None


def transcode(source, target, ffmpeg=None):
    """Convert an existing recording once; never deletes or rewrites the source."""
    target = Path(target)
    temporary = target.with_name(target.stem + '.partial.opus')
    try:
        result = subprocess.run([executable(ffmpeg), '-v', 'error', '-nostdin', '-y',
            '-i', str(source), '-vn', '-c:a', 'libopus', '-b:a', f'{BITRATE}k',
            '-vbr', 'on', '-ac', '1', '-f', 'opus', str(temporary)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=900, **_flags())
        if result.returncode:
            raise ValueError('Opus conversion failed; the original is preserved.')
        probe(temporary, ffmpeg)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
