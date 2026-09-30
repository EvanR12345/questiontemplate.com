import { countWords, duration, wavBlob, SAMPLE_RATE, PART_SECONDS } from './audio-core.mjs';
const $ = selector => document.querySelector(selector);
const script = $('#text'), synth = window.speechSynthesis;
let worker, busy = false, run, urls = [], deviceVoices = [], previewId = 0, activeUtterance;
let history = [];
try { const saved = JSON.parse(localStorage.getItem('tts-history') || '[]'); if (Array.isArray(saved)) history = saved.filter(x => typeof x === 'string').slice(0, 6); } catch {}
const examples = {
  intro: 'You are not ready for what happens next. Today, we are testing the biggest challenge we have ever built!',
  ad: 'Meet the faster way to turn your ideas into clear, expressive audio. Try it in your browser today.',
  story: 'The lights went out at exactly midnight. That was when Maya heard three slow knocks at the door. She had been warned never to open it after dark. Tonight, someone on the other side knew her name.',
};
function status(message) { $('#status').textContent = message; }
function updateCounts() {
  const words = countWords(script.value), wpm = Math.round(155 * Number($('#rate').value));
  $('#count').textContent = script.value.length.toLocaleString();
  $('#words').textContent = words.toLocaleString();
  $('#targetWpm').textContent = wpm;
  $('#estimate').textContent = duration(words / wpm * 60);
}
function outputs() {
  $('#rateOut').textContent = Number($('#rate').value).toFixed(2) + '×';
  $('#pitchOut').textContent = Number($('#pitch').value).toFixed(2);
  $('#volumeOut').textContent = Math.round($('#volume').value * 100) + '%';
  updateCounts();
}
function renderHistory() {
  const box = $('#recent'); box.replaceChildren();
  if (!history.length) { box.textContent = 'Your recent scripts will appear here.'; return; }
  for (const value of history) {
    const button = document.createElement('button'), span = document.createElement('span'), label = document.createElement('small');
    span.textContent = value.slice(0, 120); label.textContent = 'Use again'; button.append(span, label);
    button.onclick = () => { if (!busy) { script.value = value; updateCounts(); script.focus(); } };
    button.disabled = busy; box.append(button);
  }
}
function remember(value) {
  history = [value, ...history.filter(x => x !== value)].slice(0, 6);
  try { localStorage.setItem('tts-history', JSON.stringify(history)); } catch {
    // Storage quotas never limit speech generation. Scripts remain available in this tab.
  }
  renderHistory();
}
function controls(generating) {
  busy = generating;
  for (const el of document.querySelectorAll('#generate,#sample,#studioVoice,#rate,#volume,#text,#clear,[data-example],[data-voice]')) el.disabled = generating;
  $('#cancel').disabled = !generating;
  renderHistory();
}
function selectVoice(id) {
  $('#studioVoice').value = id;
  document.querySelectorAll('[data-voice]').forEach(b => {
    const selected = b.dataset.voice === id;
    b.classList.toggle('active', selected); b.setAttribute('aria-pressed', String(selected));
  });
  if (id === 'am_puck') { $('#rate').value = 1.15; outputs(); }
}
function revokeUrls() { urls.forEach(url => URL.revokeObjectURL(url)); urls = []; }
function urlFor(blob) { const url = URL.createObjectURL(blob); urls.push(url); return url; }
function updateRecording() {
  const seconds = run.frames / SAMPLE_RATE;
  $('#actualDuration').textContent = duration(seconds);
  $('#actualWpm').textContent = seconds ? Math.round(run.words / seconds * 60).toLocaleString() : '—';
  $('#size').textContent = ((run.frames * 2 + 44) / 1e6).toFixed(1) + ' MB';
}
function finishPart() {
  if (!run.partFrames) return;
  const blobs = run.current, frames = run.partFrames, index = run.parts.length + 1;
  const blob = wavBlob(blobs, frames), url = urlFor(blob);
  run.parts.push({ blobs, frames }); run.current = []; run.partFrames = 0;
  const card = document.createElement('section'); card.className = 'part';
  const header = document.createElement('header'), title = document.createElement('h3'), download = document.createElement('a');
  title.textContent = (run.sample ? 'Voice preview' : 'Part ' + index) + ' · ' + duration(frames / SAMPLE_RATE);
  download.textContent = '↓ Download WAV'; download.href = url; download.download = `${run.voice}-part-${String(index).padStart(3, '0')}.wav`;
  header.append(title, download);
  const audio = document.createElement('audio'); audio.controls = true; audio.preload = 'metadata'; audio.src = url;
  card.append(header, audio); $('#parts').append(card);
}
function complete(message, partial = false) {
  finishPart(); controls(false);
  $('#progress').hidden = true;
  if (run.frames) {
    try {
      const blob = wavBlob(run.parts.flatMap(part => part.blobs), run.frames);
      const link = $('#downloadAll'); link.href = urlFor(blob);
      link.download = `${run.voice}-${partial ? 'partial-' : ''}recording.wav`;
      link.setAttribute('aria-disabled', 'false');
    } catch { $('#recordingNote').textContent = 'Download the individual parts; this recording exceeds the WAV format size limit.'; }
    if (partial) $('#recordingNote').textContent = 'Partial recording: only completed sections are included. You can download them below.';
    else if (run.sample) $('#recordingNote').textContent = 'A short sample of your script. Generate audio to record the entire script.';
    else $('#recordingNote').textContent = `${run.words.toLocaleString()} words recorded · ${run.voiceName} · ${run.speed.toFixed(2)}× speed. Actual WPM includes pauses.`;
  } else $('#recordingNote').textContent = 'No audio has been generated yet.';
  updateRecording(); status(message);
}
function fail(message) {
  worker?.terminate(); worker = null;
  complete('Could not finish generating. ' + message + ' Try again, or use Device preview.', true);
}
function receive({ data }) {
  if (!busy || !run) return;
  if (data.type === 'loading') {
    const p = data.progress;
    status(p.status === 'progress' ? `Loading voice model: ${p.file || 'file'} · ${Math.round(p.progress || 0)}%. First use may take a few minutes.` : 'Preparing the local voice engine…');
    return;
  }
  if (data.type === 'ready') { status('Generating locally… Keep this tab open.'); return; }
  if (data.type === 'chunk' || data.type === 'progress') {
    while (!run.nextWord.done && run.nextWord.value.index + run.nextWord.value[0].length <= data.processed) {
      run.words++; run.nextWord = run.wordIterator.next();
    }
    if (data.type === 'chunk') {
      run.current.push(new Blob([data.pcm])); run.partFrames += data.frames; run.frames += data.frames;
      if (run.partFrames >= PART_SECONDS * SAMPLE_RATE) finishPart();
    }
    const percent = Math.min(100, data.processed / run.text.length * 100);
    $('#progress').value = percent;
    status(`Generating ${Math.floor(percent)}% · ${duration(run.frames / SAMPLE_RATE)} recorded · ${run.words.toLocaleString()} words. Completed parts can be downloaded now.`);
    updateRecording(); return;
  }
  if (data.type === 'done') { $('#progress').value = 100; complete(run.sample ? 'Voice sample ready. Press play below.' : 'Recording ready. Play or download your audio below.'); }
  if (data.type === 'error') fail(data.message);
}
function generate(sample = false) {
  if (busy) return;
  const full = script.value.trim();
  if (!full || !countWords(full)) { status('Add some words to your script first.'); script.focus(); return; }
  if (!window.Worker || !window.WebAssembly) { status('This browser cannot run downloadable voices. Try a current desktop browser, or Device preview.'); return; }
  stopPreview(); document.querySelectorAll('audio').forEach(audio => audio.pause()); revokeUrls();
  let value = full;
  if (sample) {
    const tokens = [];
    for (const match of full.matchAll(/\S+\s*/g)) { tokens.push(match[0]); if (tokens.length === 35) break; }
    value = tokens.join('').trim();
  }
  const wordIterator = value.matchAll(/[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu);
  run = { text: value, voice: $('#studioVoice').value, voiceName: $('#studioVoice').selectedOptions[0].textContent,
    speed: Number($('#rate').value), sample, frames: 0, partFrames: 0, current: [], parts: [], words: 0, wordIterator, nextWord: wordIterator.next() };
  $('#parts').replaceChildren(); $('#downloads').hidden = false; $('#downloadAll').removeAttribute('href'); $('#downloadAll').setAttribute('aria-disabled', 'true');
  $('#recordingNote').textContent = 'Completed parts will appear here while the rest is generated.';
  $('#progress').hidden = false; $('#progress').value = 0;
  controls(true); updateRecording(); remember(full); status('Loading the local voice engine… First use needs a model download.');
  try {
    if (!worker) { worker = new Worker('./tts.worker.js?v=long-audio-1', { type: 'module' }); worker.onmessage = receive; worker.onerror = event => { event.preventDefault(); fail(event.message || 'The voice engine could not load. Check your connection.'); }; }
    worker.postMessage({ type: 'generate', text: value, voice: run.voice, speed: run.speed, volume: Number($('#volume').value) });
  } catch (error) { fail(error.message); }
}
function stopPreview() { previewId++; synth?.cancel(); activeUtterance = null; }
function loadVoices() {
  const selected = $('#voice').selectedOptions[0]?.textContent;
  deviceVoices = synth?.getVoices() || []; $('#voice').replaceChildren();
  if (!deviceVoices.length) $('#voice').append(new Option('Default device voice', ''));
  deviceVoices.forEach((voice, i) => $('#voice').append(new Option(voice.name + ' · ' + voice.lang, i)));
  const match = [...$('#voice').options].find(option => option.textContent === selected); if (match) match.selected = true;
}
async function devicePreview() {
  if (!synth) { status('Device preview is not supported in this browser.'); return; }
  const value = script.value.trim(); if (!value) { status('Type something first.'); return; }
  stopPreview(); const id = previewId;
  // Smaller utterances avoid browser speech engines dropping long scripts.
  const { splitText } = await import('./audio-core.mjs');
  const chunks = splitText(value);
  function next() {
    if (id !== previewId) return;
    const chunk = chunks.next(); if (chunk.done) { if (!busy) status('Device preview finished.'); return; }
    const utterance = new SpeechSynthesisUtterance(chunk.value); activeUtterance = utterance;
    const voice = deviceVoices[Number($('#voice').value)]; if (voice) utterance.voice = voice;
    utterance.rate = Number($('#rate').value); utterance.pitch = Number($('#pitch').value); utterance.volume = Number($('#volume').value);
    utterance.onend = next; utterance.onerror = event => { if (id === previewId && event.error !== 'canceled' && !busy) status('Device preview failed. Try another device voice.'); };
    synth.speak(utterance);
  }
  if (!busy) status('Playing device preview. This uses your device voice, not the downloadable studio voice.'); next();
}
let context;
function audioContext() { return context ||= new (window.AudioContext || window.webkitAudioContext)(); }
function tone(freq, length, type = 'sine', gain = .16, slide = 0, delay = 0) {
  const c = audioContext(), oscillator = c.createOscillator(), envelope = c.createGain(), now = c.currentTime + delay;
  oscillator.type = type; oscillator.frequency.setValueAtTime(freq, now);
  if (slide) oscillator.frequency.exponentialRampToValueAtTime(Math.max(30, freq + slide), now + length);
  envelope.gain.setValueAtTime(.001, now); envelope.gain.exponentialRampToValueAtTime(gain, now + .015); envelope.gain.exponentialRampToValueAtTime(.001, now + length);
  oscillator.connect(envelope).connect(c.destination); oscillator.start(now); oscillator.stop(now + length + .02);
}
function sound(name) {
  audioContext().resume();
  if (name === 'ding') { tone(660, .18); tone(990, .35, 'sine', .14, 0, .12); }
  if (name === 'pop') tone(180, .1, 'sine', .24, 500);
  if (name === 'laser') tone(1200, .4, 'sawtooth', .12, -1050);
  if (name === 'buzz') { tone(90, .35, 'square', .09); tone(110, .35, 'square', .06); }
  if (name === 'win') [523, 659, 784, 1046].forEach((freq, i) => tone(freq, .25, 'triangle', .13, 0, i * .11));
  if (name === 'whoosh') {
    const c = audioContext(), buffer = c.createBuffer(1, c.sampleRate * .48, c.sampleRate), data = buffer.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / data.length);
    const source = c.createBufferSource(), filter = c.createBiquadFilter(), gain = c.createGain(); filter.type = 'lowpass'; filter.frequency.value = 1600; gain.gain.value = .25;
    source.buffer = buffer; source.connect(filter).connect(gain).connect(c.destination); source.start();
  }
}
script.addEventListener('input', updateCounts);
$('#generate').onclick = () => generate(); $('#sample').onclick = () => generate(true);
$('#cancel').onclick = () => { worker?.terminate(); worker = null; complete('Canceled. Completed sections are available to download.', true); };
$('#preview').onclick = devicePreview;
$('#pause').onclick = () => { synth?.pause(); document.querySelectorAll('audio').forEach(audio => audio.pause()); if (!busy) status('Playback paused.'); };
$('#resume').onclick = () => { synth?.resume(); if (!busy) status('Device preview resumed. Use each recording’s play button for generated audio.'); };
$('#stop').onclick = () => { stopPreview(); document.querySelectorAll('audio').forEach(audio => { audio.pause(); audio.currentTime = 0; }); if (!busy) status('Playback stopped.'); };
$('#clear').onclick = () => { stopPreview(); script.value = ''; updateCounts(); script.focus(); };
$('#clearHistory').onclick = () => { history = []; try { localStorage.removeItem('tts-history'); } catch {} renderHistory(); };
document.querySelectorAll('[data-example]').forEach(button => button.onclick = () => { script.value = examples[button.dataset.example]; updateCounts(); });
document.querySelectorAll('[data-voice]').forEach(button => button.onclick = () => selectVoice(button.dataset.voice));
document.querySelectorAll('[data-sound]').forEach(button => button.onclick = () => sound(button.dataset.sound));
$('#studioVoice').onchange = () => selectVoice($('#studioVoice').value);
['rate', 'pitch', 'volume'].forEach(id => $('#' + id).oninput = outputs);
window.addEventListener('beforeunload', event => { if (busy) { event.preventDefault(); event.returnValue = ''; } });
if (synth) { loadVoices(); synth.onvoiceschanged = loadVoices; }
outputs(); renderHistory();
