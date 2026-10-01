// Shared, dependency-free audio helpers. No text is truncated.
export const SAMPLE_RATE = 24000;
export const PART_SECONDS = 5 * 60;
export function countWords(text) {
  let count = 0;
  for (const _ of text.matchAll(/[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu)) count++;
  return count;
}
export function* splitText(text, max = 350) {
  let offset = 0;
  while (offset < text.length) {
    let end = Math.min(offset + max, text.length);
    if (end < text.length) {
      const window = text.slice(offset, end);
      const sentences = [...window.matchAll(/[.!?]["'”’)]?\s+|\n+/g)];
      const last = sentences.at(-1);
      if (last && last.index > max / 3) end = offset + last.index + last[0].length;
      else {
        const spaces = [...window.matchAll(/\s+/g)];
        if (spaces.length) end = offset + spaces.at(-1).index + spaces.at(-1)[0].length;
      }
      // Keep UTF-16 surrogate pairs together.
      if (/[\uD800-\uDBFF]/.test(text[end - 1])) end--;
    }
    yield text.slice(offset, end);
    offset = end;
  }
}
export function pcm16(samples, volume = 1) {
  const buffer = new ArrayBuffer(samples.length * 2), view = new DataView(buffer);
  for (let i = 0; i < samples.length; i++) {
    const sample = Math.max(-1, Math.min(1, samples[i] * volume));
    view.setInt16(i * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
  }
  return buffer;
}
export function wavHeader(frames, sampleRate = SAMPLE_RATE) {
  const bytes = frames * 2;
  if (bytes > 0xffffffff - 36) throw new Error('Use individual parts for recordings larger than the WAV format allows.');
  const header = new ArrayBuffer(44), v = new DataView(header);
  const write = (offset, value) => [...value].forEach((c, i) => v.setUint8(offset + i, c.charCodeAt(0)));
  write(0, 'RIFF'); v.setUint32(4, bytes + 36, true); write(8, 'WAVE'); write(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true); write(36, 'data'); v.setUint32(40, bytes, true);
  return header;
}
export function wavBlob(blobs, frames) {
  return new Blob([wavHeader(frames), ...blobs], { type: 'audio/wav' });
}
export function duration(seconds) {
  const total = Math.round(seconds);
  return [Math.floor(total / 3600), Math.floor(total / 60) % 60, total % 60]
    .filter((_, i) => i > 0 || total >= 3600).map((n, i) => i ? String(n).padStart(2, '0') : String(n)).join(':');
}
export function estimatedBytes(seconds, format = 'mp3', bitrate = 48) {
  return format === 'wav' ? seconds * SAMPLE_RATE * 2 + 44 : seconds * bitrate * 1000 / 8;
}
export function recordedSeconds(frames, bytes, format, bitrate = 48) {
  // MP3 CBR output has no ID3/Xing tags. This includes codec padding, making
  // exported duration/WPM match the actual track rather than pre-encode PCM.
  return format === 'mp3' ? bytes * 8 / (bitrate * 1000) : frames / SAMPLE_RATE;
}
// Token validation happens before neural inference, so long numbers cannot
// silently lose words and no discarded/truncated audio is generated first.
export async function* prepareBatches(text, tokenizer, phonemize, language, maxTokens = 280) {
  if (!text.trim()) { yield { text, ids: null }; return; }
  const phonemes = await phonemize(text, language);
  const ids = tokenizer(phonemes, { truncation: false }).input_ids;
  if (ids.dims.at(-1) <= maxTokens) { yield { text, ids }; return; }
  if (text.length <= 2) throw new Error('Please spell out this unusually long symbol or number.');
  for (const chunk of splitText(text, Math.max(2, Math.floor(text.length / 2)))) {
    yield* prepareBatches(chunk, tokenizer, phonemize, language, maxTokens);
  }
}
