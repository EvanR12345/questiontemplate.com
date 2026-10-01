import { pcm16, SAMPLE_RATE } from './audio-core.mjs?v=opus-1';
export function joinBytes(arrays) {
  const bytes = new Uint8Array(arrays.reduce((total, array) => total + array.byteLength, 0));
  let offset = 0;
  for (const array of arrays) { bytes.set(new Uint8Array(array.buffer, array.byteOffset, array.byteLength), offset); offset += array.byteLength; }
  return bytes;
}
export async function createEncoder(format = 'mp3', bitrate = 64) {
  if (format === 'wav') return { encode: (samples, volume) => new Uint8Array(pcm16(samples, volume)), flush: () => new Uint8Array() };
  if (format === 'opus') {
    const { createOpusEncoder } = await import('./opus-encoder.mjs?v=opus-1');
    return createOpusEncoder(bitrate);
  }
  if (format !== 'mp3') throw new Error('Unsupported download format.');
  const { Mp3Encoder } = await import('./vendor/lame.mjs?v=opus-1');
  const encoder = new Mp3Encoder(1, SAMPLE_RATE, bitrate);
  return {
    encode(samples, volume = 1) {
      const pcm = new Int16Array(pcm16(samples, volume)), output = [];
      for (let offset = 0; offset < pcm.length; offset += 1152) {
        const bytes = encoder.encodeBuffer(pcm.subarray(offset, offset + 1152));
        if (bytes.length) output.push(bytes);
      }
      return joinBytes(output);
    },
    flush: () => new Uint8Array(encoder.flush()),
  };
}
