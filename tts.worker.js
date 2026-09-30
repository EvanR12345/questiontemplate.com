import { splitText, pcm16, SAMPLE_RATE } from './audio-core.mjs';
let engine;
async function loadEngine() {
  if (!engine) {
    const { KokoroTTS } = await import('https://cdn.jsdelivr.net/npm/kokoro-js@1.2.1/dist/kokoro.web.js');
    engine = await KokoroTTS.from_pretrained('onnx-community/Kokoro-82M-v1.0-ONNX', {
      dtype: 'q8', device: 'wasm',
      progress_callback: progress => postMessage({ type: 'loading', progress }),
    });
  }
  return engine;
}
// Kokoro's public API truncates long token sequences. Check the exact phoneme
// sequence and recursively split/retry rather than ever exporting truncated audio.
export async function* generateChecked(tts, text, options) {
  if (!text.trim()) { yield { text, audio: null }; return; }
  let offset = 0;
  for await (const item of tts.stream(text, options)) {
    const start = text.indexOf(item.text, offset);
    if (start < 0) throw new Error('Could not align a speech section with the original script.');
    const end = start + item.text.length;
    const source = text.slice(offset, end);
    const tokens = tts.tokenizer(item.phonemes, { truncation: false }).input_ids.dims.at(-1);
    if (tokens > 510) {
      if (source.length < 2) throw new Error('This text cannot be pronounced safely. Please spell out unusual symbols.');
      for (const smaller of splitText(source, Math.max(2, Math.floor(source.length / 2)))) {
        yield* generateChecked(tts, smaller, options);
      }
    } else yield { text: source, audio: item.audio };
    offset = end;
  }
  if (offset < text.length) yield { text: text.slice(offset), audio: null };
}
if (typeof self !== 'undefined') self.onmessage = async ({ data }) => {
  if (data.type !== 'generate') return;
  try {
    const tts = await loadEngine();
    postMessage({ type: 'ready' });
    let processed = 0;
    for (const chunk of splitText(data.text)) {
      for await (const item of generateChecked(tts, chunk, { voice: data.voice, speed: data.speed })) {
        processed += item.text.length;
        if (!item.audio) { postMessage({ type: 'progress', processed }); continue; }
        if (item.audio.sampling_rate !== SAMPLE_RATE) throw new Error('Unexpected audio sample rate.');
        const pcm = pcm16(item.audio.audio, data.volume);
        postMessage({ type: 'chunk', pcm, frames: pcm.byteLength / 2, processed }, [pcm]);
      }
    }
    postMessage({ type: 'done' });
  } catch (error) {
    postMessage({ type: 'error', message: error.message || String(error) });
  }
};
