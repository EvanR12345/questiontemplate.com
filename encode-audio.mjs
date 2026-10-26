export function joinBytes(arrays) {
  const bytes = new Uint8Array(arrays.reduce((total, array) => total + array.byteLength, 0));
  let offset = 0;
  for (const array of arrays) { bytes.set(new Uint8Array(array.buffer, array.byteOffset, array.byteLength), offset); offset += array.byteLength; }
  return bytes;
}
export async function createEncoder(format = 'opus', bitrate = 128) {
  if (format !== 'opus') throw new Error('New audio recordings use Opus only.');
  const { createOpusEncoder } = await import('./opus-encoder.mjs?v=opus-1');
  return createOpusEncoder(bitrate);
}
