import { splitText, prepareBatches, SAMPLE_RATE, PART_SECONDS } from './audio-core.mjs?v=speech-3';
import { phonemize } from './phonemize.mjs?v=speech-3';
import { loadLexicon } from './english-phonemes.mjs?v=english-2';
import { createEncoder } from './encode-audio.mjs?v=opus-1';
import { pronunciationRules, speechText } from './pronunciation.mjs?v=speech-3';
import { nativeHealth, nativeRequest, nativeTokenizer, nativeAudio } from './native-client.mjs?v=queue-1';
import { streamSynthesis } from './synthesis-pipeline.mjs?v=overlap-1';
let engine, backend, currentMode, running = false, canceled = false, paused = false, resumePause, acknowledge;
const send = data => postMessage(data);
async function discardEngine() {
  try { await engine?.model.dispose(); } catch {}
  engine = null; backend = null;
}
async function loadEngine(mode = 'auto', forceCpu = false) {
  if (engine && currentMode === mode && (!forceCpu || backend === 'wasm')) return engine;
  await discardEngine(); currentMode = mode;
  const { KokoroTTS } = await import('https://cdn.jsdelivr.net/npm/kokoro-js@1.2.1/dist/kokoro.web.js');
  let gpu = false;
  if (mode !== 'cpu' && !forceCpu && navigator.gpu) {
    try { const adapter = await navigator.gpu.requestAdapter(); gpu = !!adapter && (adapter.info?.isFallbackAdapter ?? adapter.isFallbackAdapter) !== true; } catch {}
  }
  if (mode === 'gpu' && !gpu && !forceCpu) throw new Error('GPU acceleration is unavailable. Select Automatic or Smaller model.');
  const options = { progress_callback: progress => send({ type: 'loading', progress }) };
  if (gpu) {
    try { engine = await KokoroTTS.from_pretrained('onnx-community/Kokoro-82M-v1.0-ONNX', { ...options, device: 'webgpu', dtype: 'fp32' }); backend = 'webgpu'; }
    catch (error) { if (mode === 'gpu') throw error; send({ type: 'notice', message: 'GPU could not start. Switching to the smaller CPU model.' }); }
  }
  if (!engine) { engine = await KokoroTTS.from_pretrained('onnx-community/Kokoro-82M-v1.0-ONNX', { ...options, device: 'wasm', dtype: 'q8' }); backend = 'wasm'; }
  return engine;
}
async function gate() {
  // Inference can resolve through microtasks without yielding to incoming
  // worker messages. Give pause/cancel a task boundary between sections.
  await new Promise(resolve => setTimeout(resolve, 0));
  if (paused && !canceled) {
    send({ type: 'paused' });
    await new Promise(resolve => { resumePause = resolve; });
    resumePause = null; send({ type: 'resumed' });
  }
  return !canceled;
}
async function runJob(data) {
  running = true; canceled = paused = false;
  let encoder, partFrames = 0, processed = data.offset || 0, failure;
  const emit = (bytes, frames = 0) => {
    const buffer = bytes.byteOffset === 0 && bytes.byteLength === bytes.buffer.byteLength
      ? bytes.buffer : bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    postMessage({ type: 'chunk', bytes: buffer, frames, processed }, [buffer]);
  };
  const closePart = async () => {
    if (!partFrames || !encoder) return;
    emit(encoder.flush());
    const saved = new Promise(resolve => { acknowledge = resolve; });
    send({ type: 'partEnd', processed });
    await saved; acknowledge = null;
    partFrames = 0; encoder = null;
  };
  try {
    let tts, nativeInfo;
    if (data.engine === 'native') {
      await discardEngine();
      nativeInfo = await nativeHealth(data.nativeKey);
      await nativeRequest('/prepare', data.nativeKey, { voice: data.voice });
      backend = 'cuda';
      tts = { tokenizer: nativeTokenizer(nativeInfo.vocab) };
    } else tts = await loadEngine(data.engine);
    const language = data.voice[0];
    await loadLexicon(language);
    send({ type: 'ready', backend, gpu: nativeInfo?.gpu });
    const rules = pronunciationRules(data.pronunciation);
    const pronounce = (text, language, final) => phonemize(speechText(text, rules, final), language);
    async function* batches() {
      let position = processed;
      for (const chunk of splitText(data.text.slice(processed), 420)) {
        position += chunk.length;
        yield* prepareBatches(chunk, tts.tokenizer, pronounce, language, 280, position === data.text.length);
      }
    }
    const render = async batch => {
      let audio;
      try { audio = data.engine === 'native'
        ? await nativeAudio(data.nativeKey, batch, data.voice, data.speed)
        : await engine.generate_from_ids(batch.ids, { voice: data.voice, speed: data.speed }); }
      catch (error) {
        if (backend !== 'webgpu' || data.engine === 'gpu') throw error;
        send({ type: 'notice', message: 'GPU generation failed. Continuing this section on CPU.' });
        await loadEngine(data.engine, true);
        send({ type: 'backend', backend });
        audio = await engine.generate_from_ids(batch.ids, { voice: data.voice, speed: data.speed });
      }
      return audio;
    };
    for await (const { batch, audio } of streamSynthesis(batches(), render, {
      gate, prefetch: data.engine === 'native', canPrefetch: () => !paused && !canceled,
    })) {
      if (!batch.ids) { processed += batch.text.length; send({ type: 'progress', processed }); continue; }
      if (audio.sampling_rate !== SAMPLE_RATE) throw new Error('Unexpected audio sample rate.');
      encoder ||= await createEncoder(data.format, data.bitrate);
      const bytes = encoder.encode(audio.audio, data.volume);
      processed += batch.text.length; partFrames += audio.audio.length;
      emit(bytes, audio.audio.length);
      if (partFrames >= PART_SECONDS * SAMPLE_RATE) await closePart();
    }
  } catch (error) { failure = error.message || String(error); }
  try { await closePart(); } catch (error) { failure ||= error.message; }
  send({ type: failure ? 'error' : canceled ? 'canceled' : 'done', message: failure, processed });
  running = false;
}
if (typeof self !== 'undefined') self.onmessage = ({ data }) => {
  if (data.type === 'generate' && !running) void runJob(data);
  if (data.type === 'ack') acknowledge?.();
  if (data.type === 'pause') paused = true;
  if (data.type === 'resume') { paused = false; resumePause?.(); }
  if (data.type === 'cancel') { canceled = true; paused = false; resumePause?.(); }
};
