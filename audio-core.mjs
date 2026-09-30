// Shared, dependency-free audio helpers. No text is truncated.
export const SAMPLE_RATE = 24000;
export const PART_SECONDS = 15 * 60;
export function countWords(text) {
  return (text.match(/[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu) || []).length;
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
  samples.forEach((value, i) => {
    const sample = Math.max(-1, Math.min(1, value * volume));
    view.setInt16(i * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
  });
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
