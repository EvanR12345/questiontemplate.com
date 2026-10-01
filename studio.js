import { countWords, duration, wavBlob, SAMPLE_RATE, estimatedBytes, recordedSeconds } from './audio-core.mjs?v=long-fast-2';
import { beginSession, savePart, saveJob, loadSession } from './session-store.mjs?v=long-fast-2';
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
  updateCounts(); formatOutputs();
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
  for (const el of document.querySelectorAll('#generate,#sample,#studioVoice,#rate,#volume,#text,#clear,#importScript,#format,#bitrate,#engine,#restore,#resumeSaved,#pronunciation,[data-example],[data-voice]')) el.disabled = generating;
  $('#generationControls').hidden = !generating;
  if (generating) $('#performance').hidden = false;
  $('#cancel').disabled = !generating;
  $('#pauseGeneration').disabled = !generating;
  $('#bitrate').disabled = generating || $('#format').value === 'wav';
  renderHistory();
}
function selectVoice(id) {
  $('#studioVoice').value = id;
  $('#selectedVoice').textContent = $('#studioVoice').selectedOptions[0].textContent;
  document.querySelectorAll('[data-voice]').forEach(b => {
    const selected = b.dataset.voice === id;
    b.classList.toggle('active', selected); b.setAttribute('aria-pressed', String(selected));
  });
}
function revokeUrls() { urls.forEach(url => URL.revokeObjectURL(url)); urls = []; }
function urlFor(blob) { const url = URL.createObjectURL(blob); urls.push(url); return url; }
function recordingDuration() { return recordedSeconds(run.frames, run.bytes, run.format, run.bitrate); }
function performanceStats() {
  if (!run) return;
  const now = performance.now();
  $('#elapsed').textContent = duration((now - run.wallStart) / 1000);
  if (!run.generationStart) return;
  const active = Math.max(.001, (now - run.generationStart - run.pausedMs - (run.pauseStart ? now - run.pauseStart : 0)) / 1000);
  const audio = (run.frames - run.startFrames) / SAMPLE_RATE;
  $('#generationSpeed').textContent = audio ? (audio / active).toFixed(2) + '× realtime' : '—';
  const chars = run.processed - run.startOffset;
  $('#timeLeft').textContent = run.pauseStart ? 'Paused' : chars > 0 ? duration((run.text.length - run.processed) / chars * active) : 'Measuring…';
}
function updateRecording() {
  const seconds = recordingDuration();
  $('#actualDuration').textContent = duration(seconds);
  $('#actualWpm').textContent = seconds ? Math.round(run.words / seconds * 60).toLocaleString() : '—';
  $('#size').textContent = ((run.bytes + (run.format === 'wav' ? 44 : 0)) / 1e6).toFixed(1) + ' MB';
  performanceStats();
}
function jobData(complete = false) {
  return { text: run.text, voice: run.voice, voiceName: run.voiceName, speed: run.speed, volume: run.volume,
    format: run.format, bitrate: run.bitrate, engine: run.engine, pronunciation: run.pronunciation || '', processed: run.checkpoint || 0, complete, updated: Date.now() };
}
function storageFailure() {
  run.persist = false;
  $('#engineStatus').textContent = 'Checkpoint storage is unavailable or full. Download completed parts before closing this tab.';
}
function appendPart(part) {
  const blob = run.format === 'wav' ? wavBlob([part.blob], part.frames) : new Blob([part.blob], { type: 'audio/mpeg' });
  const url = urlFor(blob), index = run.parts.indexOf(part) + 1;
  const card = document.createElement('section'); card.className = 'part';
  const header = document.createElement('header'), title = document.createElement('h3'), download = document.createElement('a');
  title.textContent = (run.sample ? 'Voice preview' : 'Part ' + index) + ' · ' + duration(recordedSeconds(part.frames, part.blob.size, run.format, run.bitrate));
  download.textContent = '↓ Download ' + run.format.toUpperCase(); download.href = url;
  download.download = run.voice + '-part-' + String(index).padStart(3, '0') + '.' + run.format;
  header.append(title, download);
  const audio = document.createElement('audio'); audio.controls = true; audio.preload = 'none'; audio.src = url;
  card.append(header, audio); $('#parts').append(card);
}
async function finishPart(processed) {
  if (!run.partFrames) return;
  const part = { index: run.parts.length, blob: new Blob(run.current), frames: run.partFrames, processed };
  run.parts.push(part); run.current = []; run.partFrames = 0; run.checkpoint = processed;
  appendPart(part);
  if (run.persist) {
    try { await savePart(jobData(), part); } catch { storageFailure(); }
  }
}
function setFullDownload(partial = false) {
  if (!run.parts.length) return;
  try {
    const blob = run.format === 'wav' ? wavBlob(run.parts.map(part => part.blob), run.frames)
      : new Blob(run.parts.map(part => part.blob), { type: 'audio/mpeg' });
    const link = $('#downloadAll'); link.href = urlFor(blob);
    link.download = run.voice + '-' + (partial ? 'partial-' : '') + 'recording.' + run.format;
    link.textContent = '↓ Download full ' + run.format.toUpperCase(); link.setAttribute('aria-disabled', 'false');
  } catch { $('#recordingNote').textContent = 'Download individual parts; this recording exceeds the WAV format size limit.'; }
}
async function complete(message, partial = false) {
  $('#progress').hidden = true;
  if (run.frames) {
    setFullDownload(partial);
    $('#recordingNote').textContent = partial ? 'Partial recording. Completed sections are downloadable; resume to continue.'
      : run.sample ? 'A short sample of your script. Generate audio to record the entire script.'
      : run.words.toLocaleString() + ' words recorded · ' + run.voiceName + ' · ' + run.speed.toFixed(2) + '× speed. Actual WPM includes pauses.';
  } else $('#recordingNote').textContent = 'No audio generated yet.';
  if (run.persist) {
    try { await saveJob(jobData(!partial)); } catch { storageFailure(); }
  }
  $('#resumeSaved').hidden = !partial || run.processed >= run.text.length || run.sample;
  clearInterval(run.timer); updateRecording(); controls(false); status(message);
  await wakeLock?.release().catch(() => {}); wakeLock = null;
  if (run.sample && run.frames && !partial) {
    const audio = $('#parts audio');
    $('#downloads').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    try { await audio.play(); status('Voice preview ready. Generate audio to record your whole script.'); }
    catch { status('Voice preview ready. Press play below to listen.'); }
    try {
      const saved = await loadSession();
      if (saved?.parts.length) { $('#recoveryText').textContent = 'Your earlier recording is still saved.'; $('#recovery').hidden = false; }
    } catch {}
  }
}
function advance(processed) {
  run.processed = processed;
  while (!run.nextWord.done && run.nextWord.value.index + run.nextWord.value[0].length <= processed) {
    run.words++; run.nextWord = run.wordIterator.next();
  }
}
function fail(message) {
  worker?.terminate(); worker = null;
  // An abrupt worker failure may leave the last encoder buffer unfinished.
  // Only previously flushed parts are safe checkpoints.
  run.current = []; run.partFrames = 0;
  run.frames = run.parts.reduce((sum, part) => sum + part.frames, 0);
  run.bytes = run.parts.reduce((sum, part) => sum + part.blob.size, 0);
  resetWords(run.checkpoint || 0);
  void complete('Could not finish: ' + message + '. Completed parts are safe to download or resume.', true);
}
async function receive({ data }) {
  if (!busy || !run) return;
  if (data.type === 'loading') {
    const p = data.progress;
    status(p.status === 'progress' ? 'Loading model: ' + (p.file || 'file') + ' · ' + Math.round(p.progress || 0) + '%.' : 'Preparing the local voice engine…');
    return;
  }
  if (data.type === 'notice') { status(data.message); return; }
  if (data.type === 'ready') {
    run.ready = true; run.generationStart ||= performance.now();
    $('#engineStatus').textContent = (data.backend === 'webgpu' ? 'GPU acceleration' : 'CPU · smaller model') + ' · ' + run.format.toUpperCase() + (run.format === 'mp3' ? ' ' + run.bitrate + ' kbps mono' : '');
    status('Generating locally. Completed parts are saved automatically.'); return;
  }
  if (data.type === 'paused') { run.pauseStart ||= performance.now(); status('Generation paused. Resume whenever you are ready.'); return; }
  if (data.type === 'resumed') { if (run.pauseStart) run.pausedMs += performance.now() - run.pauseStart; run.pauseStart = 0; status('Generation resumed.'); return; }
  if (data.type === 'chunk' || data.type === 'progress') {
    advance(data.processed);
    if (data.type === 'chunk') {
      if (data.bytes.byteLength) { run.current.push(new Blob([data.bytes])); run.bytes += data.bytes.byteLength; }
      run.partFrames += data.frames; run.frames += data.frames;
    }
    const percent = Math.min(100, run.processed / run.text.length * 100);
    $('#progress').value = percent;
    status('Generating ' + Math.floor(percent) + '% · ' + duration(recordingDuration()) + ' recorded · ' + run.words.toLocaleString() + ' words.');
    updateRecording(); return;
  }
  if (data.type === 'partEnd') {
    await finishPart(data.processed);
    worker?.postMessage({ type: 'ack' }); return;
  }
  if (data.type === 'done') { run.checkpoint = run.processed; await complete(run.sample ? 'Voice sample ready. Press play below.' : 'Recording ready. Download the full track or individual parts.'); }
  if (data.type === 'canceled') await complete('Stopped safely. Completed audio is downloadable. Resume to continue.', true);
  if (data.type === 'error') await complete('Could not finish: ' + data.message + '. Completed sections are downloadable.', true);
}
function resetWords(processed = 0) {
  run.wordIterator = run.text.matchAll(/[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu);
  run.nextWord = run.wordIterator.next(); run.words = 0; advance(processed);
}
function initializeRun(job, parts = []) {
  stopPreview(); document.querySelectorAll('audio').forEach(audio => audio.pause()); revokeUrls();
  run = { ...job, frames: parts.reduce((sum, part) => sum + part.frames, 0), bytes: parts.reduce((sum, part) => sum + part.blob.size, 0),
    current: [], parts: [...parts], partFrames: 0, checkpoint: job.processed || 0, wallStart: performance.now(),
    generationStart: 0, pausedMs: 0, pauseStart: 0, persist: !job.sample, ready: false };
  run.startFrames = run.frames; run.startOffset = job.processed || 0;
  resetWords(job.processed || 0);
  $('#parts').replaceChildren(); $('#downloads').hidden = false; $('#downloadAll').removeAttribute('href'); $('#downloadAll').setAttribute('aria-disabled', 'true');
  $('#downloadAll').textContent = '↓ Download full ' + run.format.toUpperCase();
  $('#resumeSaved').hidden = true;
  parts.forEach(appendPart); updateRecording();
}
let wakeLock;
async function keepAwake() { try { wakeLock = await navigator.wakeLock?.request('screen'); } catch {} }
async function startWorker() {
  await keepAwake();
  run.timer = setInterval(performanceStats, 1000);
  $('#progress').hidden = false; $('#progress').value = run.processed / run.text.length * 100;
  $('#pauseGeneration').textContent = 'Ⅱ Pause generation'; run.pauseRequested = false;
  status('Preparing the local voice engine…');
  try {
    if (!worker) { worker = new Worker('./tts.worker.js?v=long-fast-3', { type: 'module' }); worker.onmessage = receive;
      worker.onerror = event => { event.preventDefault(); fail(event.message || 'Voice engine failed'); }; }
    worker.postMessage({ type: 'generate', text: run.text, voice: run.voice, speed: run.speed, volume: run.volume,
      format: run.format, bitrate: run.bitrate, engine: run.engine, pronunciation: run.pronunciation || '', offset: run.processed });
  } catch (error) { fail(error.message); }
}
async function generate(sample = false) {
  if (busy) return;
  const full = script.value.trim();
  if (!full || !countWords(full)) { status('Add some words to your script first.'); script.focus(); return; }
  if (!window.Worker || !window.WebAssembly) { status('Use a current browser for downloadable audio.'); return; }
  let value = full;
  if (sample) {
    const tokens = []; for (const match of full.matchAll(/\S+\s*/g)) { tokens.push(match[0]); if (tokens.length === 35) break; }
    value = tokens.join('').trim();
  }
  initializeRun({ text: value, voice: $('#studioVoice').value, voiceName: $('#studioVoice').selectedOptions[0].textContent,
    speed: Number($('#rate').value), volume: Number($('#volume').value), format: $('#format').value,
    bitrate: Number($('#bitrate').value), engine: $('#engine').value, pronunciation: $('#pronunciation').value, sample, processed: 0 });
  controls(true); remember(full); $('#recovery').hidden = true;
  $('#recordingNote').textContent = 'Parts appear as they finish. Raw audio is compressed immediately for compact downloads.';
  if (run.persist) { try { await beginSession(jobData()); } catch { storageFailure(); } }
  await startWorker();
}
async function resumeSaved() {
  if (busy || !run || run.processed >= run.text.length) return;
  document.querySelectorAll('audio').forEach(audio => audio.pause());
  run.startFrames = run.frames; run.startOffset = run.processed; run.wallStart = performance.now();
  run.generationStart = 0; run.pausedMs = run.pauseStart = 0; run.ready = false;
  $('#resumeSaved').hidden = true; $('#downloadAll').setAttribute('aria-disabled', 'true');
  controls(true); await startWorker();
}
async function restore() {
  if (busy) return;
  try {
    const saved = await loadSession(); if (!saved?.parts.length) { status('No completed parts are saved yet.'); return; }
    const { job, parts } = saved;
    script.value = job.text; $('#studioVoice').value = job.voice; selectVoice(job.voice);
    $('#rate').value = job.speed; $('#volume').value = job.volume; $('#format').value = job.format; $('#bitrate').value = job.bitrate; $('#engine').value = job.engine;
    $('#pronunciation').value = job.pronunciation || '';
    initializeRun(job, parts); outputs(); formatOutputs(); $('#recovery').hidden = true; setFullDownload(!job.complete);
    $('#resumeSaved').hidden = job.complete || job.processed >= job.text.length;
    $('#recordingNote').textContent = job.complete ? 'Saved recording restored. Your downloads are ready.' : 'Saved parts restored. Resume to generate the remaining text.';
    status('Saved recording restored.'); updateRecording();
  } catch { status('Saved recording could not be opened.'); }
}
function formatOutputs() {
  const format = $('#format').value, bitrate = Number($('#bitrate').value);
  $('#bitrate').disabled = busy || format === 'wav';
  const mb = seconds => (estimatedBytes(seconds, format, bitrate) / 1e6).toFixed(0);
  $('#sizeEstimate').textContent = '90–120 min ≈ ' + mb(5400) + '–' + mb(7200) + ' MB. Your script ≈ ' + mb(countWords(script.value) / (155 * Number($('#rate').value)) * 60) + ' MB.';
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
  const { splitText } = await import('./audio-core.mjs?v=long-fast-2');
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
let countTimer; script.addEventListener('input', () => { clearTimeout(countTimer); countTimer = setTimeout(() => { updateCounts(); formatOutputs(); }, 150); });
$('#generate').onclick = () => generate(); $('#sample').onclick = () => generate(true);
$('#importScript').onclick = () => $('#scriptFile').click();
$('#scriptFile').onchange = async event => {
  const file = event.target.files[0];
  if (!file || busy) return;
  try {
    const value = await file.text();
    if (busy) return;
    script.value = value; outputs(); status('Script imported. Choose a voice or preview it.'); script.focus();
  } catch { status('Could not read this file. Please paste its text instead.'); }
  finally { event.target.value = ''; }
};
$('#cancel').onclick = () => {
  if (!run.ready) { worker?.terminate(); worker = null; void complete('Stopped before generation. No completed audio was lost.', true); return; }
  worker?.postMessage({ type: 'cancel' }); $('#cancel').disabled = true; $('#pauseGeneration').disabled = true;
  status('Stopping after the current section and saving its audio…');
};
$('#pauseGeneration').onclick = () => {
  run.pauseRequested = !run.pauseRequested;
  worker?.postMessage({ type: run.pauseRequested ? 'pause' : 'resume' });
  $('#pauseGeneration').textContent = run.pauseRequested ? '▶ Resume generation' : 'Ⅱ Pause generation';
  status(run.pauseRequested ? 'Pausing after the current section…' : 'Resuming generation…');
};
$('#restore').onclick = restore; $('#resumeSaved').onclick = resumeSaved;
['format', 'bitrate', 'engine'].forEach(id => $('#' + id).onchange = formatOutputs);
document.addEventListener('visibilitychange', () => { if (busy && document.visibilityState === 'visible') void keepAwake(); });
loadSession().then(saved => {
  if (!busy && saved?.parts.length) {
    $('#recoveryText').textContent = 'A previous recording is saved on this device.';
    $('#recovery').hidden = false;
  }
}).catch(() => {});
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
selectVoice($('#studioVoice').value); outputs(); formatOutputs(); renderHistory();
