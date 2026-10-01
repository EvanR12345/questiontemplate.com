import { SAMPLE_RATE } from './audio-core.mjs?v=opus-1';
import { createEncoder, Application, Signal } from './vendor/opus/index.mjs';

// Ogg Opus pages use a 48 kHz granule clock even with 24 kHz input.
const crcTable = Uint32Array.from({ length: 256 }, (_, i) => {
  let crc = i << 24;
  for (let bit = 0; bit < 8; bit++) crc = (crc << 1) ^ (crc < 0 ? 0x04c11db7 : 0);
  return crc >>> 0;
});
function checksum(bytes) {
  let crc = 0;
  for (const byte of bytes) crc = (crc << 8) ^ crcTable[((crc >>> 24) ^ byte) & 255];
  return crc >>> 0;
}
function combine(arrays) {
  const result = new Uint8Array(arrays.reduce((n, a) => n + a.length, 0));
  let offset = 0;
  for (const array of arrays) { result.set(array, offset); offset += array.length; }
  return result;
}
function page(packets, flags, granule, serial, sequence) {
  const lacing = [];
  for (const packet of packets) {
    let remaining = packet.length;
    while (remaining >= 255) { lacing.push(255); remaining -= 255; }
    lacing.push(remaining);
  }
  if (lacing.length > 255) throw new Error('Opus page exceeds its segment limit.');
  const bytes = new Uint8Array(27 + lacing.length + packets.reduce((n, p) => n + p.length, 0));
  const view = new DataView(bytes.buffer);
  bytes.set(new TextEncoder().encode('OggS'));
  bytes[5] = flags;
  view.setBigUint64(6, BigInt(granule), true);
  view.setUint32(14, serial, true); view.setUint32(18, sequence, true);
  bytes[26] = lacing.length; bytes.set(lacing, 27);
  let offset = 27 + lacing.length;
  for (const packet of packets) { bytes.set(packet, offset); offset += packet.length; }
  view.setUint32(22, checksum(bytes), true);
  return bytes;
}

export async function createOpusEncoder(bitrate = 64) {
  const codec = await createEncoder({ sampleRate: SAMPLE_RATE, channels: 1,
    application: Application.Audio, signal: Signal.Voice, bitrate: bitrate * 1000,
    frameSize: SAMPLE_RATE / 50, complexity: 10, vbr: true, dtx: false, fec: false });
  const frame = new Float32Array(codec.frameSize), lookahead = codec.getLookahead();
  const preSkip = lookahead * 2;
  const serial = crypto.getRandomValues(new Uint32Array(1))[0];
  let sequence = 0, filled = 0, samples = 0, encoded = 0, segments = 0, closed = false;
  let packets = [];
  const head = new Uint8Array(19), hv = new DataView(head.buffer);
  head.set(new TextEncoder().encode('OpusHead')); head[8] = 1; head[9] = 1;
  hv.setUint16(10, preSkip, true); hv.setUint32(12, SAMPLE_RATE, true);
  const vendor = new TextEncoder().encode('QuestionTemplate / libopus');
  const tags = new Uint8Array(16 + vendor.length), tv = new DataView(tags.buffer);
  tags.set(new TextEncoder().encode('OpusTags')); tv.setUint32(8, vendor.length, true); tags.set(vendor, 12);
  let headers = [page([head], 2, 0, serial, sequence++), page([tags], 0, 0, serial, sequence++)];
  function emitPage(output, final = false) {
    output.push(page(packets, final ? 4 : 0, final ? samples * 2 + preSkip : encoded * 2, serial, sequence++));
    packets = []; segments = 0;
  }
  function encodeFrame(output) {
    const packet = codec.encodeFloat(frame);
    const count = Math.floor(packet.length / 255) + 1;
    // Keep the last page pending so final padding can always be trimmed.
    if (packets.length && (packets.length >= 50 || segments + count > 255)) emitPage(output);
    packets.push(packet); segments += count; encoded += frame.length; filled = 0;
  }
  return {
    encode(input, volume = 1) {
      if (closed) throw new Error('Opus encoder is already finalized.');
      const output = headers; headers = []; samples += input.length;
      for (let i = 0; i < input.length; i++) {
        frame[filled++] = Math.max(-1, Math.min(1, input[i] * volume));
        if (filled === frame.length) encodeFrame(output);
      }
      return combine(output);
    },
    flush() {
      if (closed) return new Uint8Array();
      closed = true;
      const output = headers; headers = [];
      try {
        if (filled) { frame.fill(0, filled); encodeFrame(output); }
        frame.fill(0);
        while (encoded < samples + lookahead) encodeFrame(output);
        emitPage(output, true);
        return combine(output);
      } finally { codec.free(); }
    },
  };
}
