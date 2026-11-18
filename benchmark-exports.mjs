import fs from 'node:fs/promises';
import { performance } from 'node:perf_hooks';
import { PART_SECONDS, SAMPLE_RATE } from './audio-core.mjs';
import { createEncoder } from './encode-audio.mjs';

const seconds = 2 * 60 * 60;
const bitrate = 128;
const signal = Float32Array.from({ length: SAMPLE_RATE },
  (_, i) => .25 * Math.sin(i * 2 * Math.PI * 220 / SAMPLE_RATE));
const file = await fs.open(process.argv[2] || 'tts-export-120min.opus', 'w');
const start = performance.now();
let parts = 0;
let exportBytes;
try {
  for (let offset = 0; offset < seconds; offset += PART_SECONDS) {
    const encoder = await createEncoder('opus', bitrate);
    const length = Math.min(PART_SECONDS, seconds - offset);
    for (let i = 0; i < length; i++) await file.writeFile(encoder.encode(signal));
    await file.writeFile(encoder.flush());
    parts++;
  }
  exportBytes = (await file.stat()).size;
} finally {
  await file.close();
}
const elapsedSeconds = (performance.now() - start) / 1000;
console.log(JSON.stringify({ format: 'opus', bitrateKbps: bitrate, parts, encodedSeconds: seconds,
  exportSeconds: seconds, elapsedSeconds, realtime: seconds / elapsedSeconds, exportBytes }));
