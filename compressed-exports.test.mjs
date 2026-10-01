import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createEncoder, joinBytes } from './encode-audio.mjs';
import { recordedSeconds, estimatedBytes } from './audio-core.mjs';
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-codecs-'));
process.on('exit', () => fs.rmSync(directory, { recursive: true, force: true }));
const signal = length => Float32Array.from({length}, (_, i) => .2 * Math.sin(2 * Math.PI * 230 * i / 24000));
async function exportTrack(format, bitrate, samples, split = 1001) {
  const encoder = await createEncoder(format, bitrate), output = [];
  for (let i = 0; i < samples.length; i += split) output.push(encoder.encode(samples.subarray(i, i + split)));
  output.push(encoder.flush());
  return joinBytes(output);
}
function decode(bytes, extension) {
  const filename = path.join(directory, 'recording.' + extension); fs.writeFileSync(filename, bytes);
  return execFileSync('ffmpeg', ['-v','error','-i',filename,'-f','f32le','-ac','1','-ar','48000','pipe:1'], {maxBuffer:20e6});
}
test('Opus preserves exact decoded duration across irregular chunks and tiny/tail frames', async () => {
  for (const length of [1, 479, 480, 481, 24001, 72017]) {
    const bytes = await exportTrack('opus', 64, signal(length));
    assert.equal(decode(bytes, 'opus').byteLength / 8, length);
    assert.equal(recordedSeconds(length, bytes.length, 'opus', 64), length / 24000);
  }
});
test('all three Opus bitrate targets export decodable mono audio with bounded size', async () => {
  for (const bitrate of [64,128,256]) {
    const bytes = await exportTrack('opus', bitrate, signal(240007));
    assert.equal(decode(bytes, 'opus').byteLength / 8, 240007);
    assert.ok(bytes.length < estimatedBytes(11, 'opus', bitrate) * 1.5);
  }
});
test('full Opus export chains independent saved parts without missing or extra samples', async () => {
  const first = await exportTrack('opus', 64, signal(24001));
  const second = await exportTrack('opus', 64, signal(31003));
  assert.equal(decode(joinBytes([first,second]), 'opus').byteLength / 8, 55004);
});
test('MP3 really exports each requested bitrate, including MPEG-1 at 256 kbps', async () => {
  for (const bitrate of [64,128,256]) {
    const bytes = await exportTrack('mp3', bitrate, signal(24000));
    const filename=path.join(directory,'rate-'+bitrate+'.mp3');fs.writeFileSync(filename,bytes);
    const metadata=JSON.parse(execFileSync('ffprobe',['-v','error','-show_entries','stream=bit_rate,sample_rate,channels','-of','json',filename]));
    assert.equal(Number(metadata.streams[0].bit_rate), bitrate * 1000);
    assert.equal(metadata.streams[0].channels,1);
    assert.equal(Number(metadata.streams[0].sample_rate),bitrate===256?48000:24000);
    assert.ok(decode(bytes,'mp3').byteLength>=24000*4);
  }
});
