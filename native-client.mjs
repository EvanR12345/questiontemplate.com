// Fixed loopback destination: scripts and pairing keys never go to a remote API.
export const NATIVE_URL = 'http://127.0.0.1:8765';
export async function nativeRequest(path, key, body, fetcher = fetch) {
  if (!/^[a-f0-9]{64}$/.test(key || '')) throw new Error('Open the link printed by the NVIDIA helper, then click Connect NVIDIA.');
  let response;
  try {
    response = await fetcher(NATIVE_URL + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { Authorization: 'Bearer ' + key, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(path === '/synthesize' ? 180000 : 30000),
      cache: 'no-store', credentials: 'omit',
    });
  } catch { throw new Error('Cannot reach the NVIDIA helper. Keep its window open and allow this site’s Local network access permission in Chrome.'); }
  if (!response.ok) {
    let message = 'NVIDIA helper request failed (' + response.status + ')';
    try { message = (await response.json()).error || message; } catch {}
    throw new Error(message);
  }
  return response;
}
export async function nativeHealth(key, fetcher) {
  const info = await (await nativeRequest('/health', key, undefined, fetcher)).json();
  if (![1, 2].includes(info.protocol) || info.backend !== 'cuda' || info.sampleRate !== 24000 || !info.vocab) throw new Error('Update the NVIDIA helper using the website’s setup download.');
  return info;
}
export function nativeTokenizer(vocab) {
  return phonemes => {
    const supported = [...phonemes].filter(char => Object.hasOwn(vocab, char)).join('');
    if (!supported.trim()) throw new Error('This section has no supported speech sounds. Please spell out symbols.');
    return { input_ids: { dims: [1, [...supported].length + 2], phonemes: supported } };
  };
}
export async function nativeAudio(key, batch, voice, speed, fetcher) {
  const response = await nativeRequest('/synthesize', key, { phonemes: batch.ids.phonemes, voice, speed }, fetcher);
  if (response.headers.get('X-Sample-Rate') !== '24000') throw new Error('Unexpected NVIDIA audio sample rate.');
  const buffer = await response.arrayBuffer();
  if (!buffer.byteLength || buffer.byteLength % 4) throw new Error('Incomplete NVIDIA audio response. This section has not been saved.');
  const audio = new Float32Array(buffer);
  if (audio.some(value => !Number.isFinite(value))) throw new Error('NVIDIA engine returned invalid audio.');
  return { audio, sampling_rate: 24000 };
}
