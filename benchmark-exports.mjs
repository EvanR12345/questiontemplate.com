import fs from 'node:fs/promises';
import { performance } from 'node:perf_hooks';
import { createEncoder, joinBytes } from './encode-audio.mjs';
import { recordedSeconds, wavBlob } from './audio-core.mjs';
// One minute is really encoded. Replication exercises a complete two-hour
// export without pretending to benchmark two hours of neural synthesis.
const samples = Float32Array.from({ length: 24000 }, (_, i) => .25 * Math.sin(i * 2 * Math.PI * 220 / 24000));
const start = performance.now(), encoder = await createEncoder('mp3', 48), blocks = [];
for (let second = 0; second < 60; second++) blocks.push(encoder.encode(samples));
blocks.push(encoder.flush());
const minute = joinBytes(blocks), elapsed = (performance.now() - start) / 1000;
const blob = new Blob(Array(120).fill(new Blob([minute])), { type: 'audio/mpeg' });
await fs.writeFile(process.argv[2] || 'tts-export-120min.mp3', new Uint8Array(await blob.arrayBuffer()));
console.log(JSON.stringify({ encodedSeconds: 60, encodingSeconds: elapsed, encodingRealtime: 60 / elapsed, exportBytes: blob.size,
  exportSeconds: recordedSeconds(0, blob.size, 'mp3', 48), equivalentWavBytes: 7200 * 48000 + 44, heapMB: process.memoryUsage().heapUsed / 1e6 }));
