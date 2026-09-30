import { test } from 'node:test';
import assert from 'node:assert/strict';
import { splitText, countWords, pcm16, wavBlob, duration } from './audio-core.mjs';
import { generateChecked } from './tts.worker.js';
test('long scripts retain every character with no fixed input cap', () => {
  const source = 'This is a complete sentence. Another one follows!\n'.repeat(30000);
  const chunks = [...splitText(source)];
  assert.equal(chunks.join(''), source);
  assert.ok(chunks.every(chunk => chunk.length <= 350));
  assert.equal(countWords(source), 240000);
});
test('unbroken words, Unicode, whitespace and paragraphs survive chunking', () => {
  for (const source of ['x'.repeat(9000), '😎'.repeat(600), '\n\n Hello.  A story!\n The end. ', '漢字 '.repeat(2000)]) {
    assert.equal([...splitText(source)].join(''), source);
  }
});
test('word counts, duration and little-endian PCM WAV metadata', async () => {
  assert.equal(countWords("It's Maya’s 2nd story."), 4);
  assert.equal(duration(60), '1:00'); assert.equal(duration(3601), '1:00:01');
  const pcm = pcm16(new Float32Array([-1, 0, 1, 2]));
  const view = new DataView(pcm); assert.equal(view.getInt16(0, true), -32768); assert.equal(view.getInt16(4, true), 32767);
  const blob = wavBlob([new Blob([pcm])], 4), wav = await blob.arrayBuffer(), header = new DataView(wav);
  assert.equal(blob.size, 52); assert.equal(header.getUint32(24, true), 24000); assert.equal(header.getUint32(40, true), 8);
  assert.equal(new TextDecoder().decode(wav.slice(0, 4)), 'RIFF');
});
test('multiple generated sentences advance progress once and retain whitespace', async () => {
  const source = '  The door opened.  Maya waited!\nThen she ran.  ';
  const engine = {
    async *stream(text) { for (const sentence of text.match(/[^.!?]+[.!?]?/g) || []) if (sentence.trim()) yield { text: sentence.trim(), phonemes: sentence, audio: { audio: new Float32Array(10) } }; },
    tokenizer(text) { return { input_ids: { dims: [1, text.length + 2] } }; },
  };
  const items = []; for await (const item of generateChecked(engine, source, {})) items.push(item);
  assert.equal(items.map(item => item.text).join(''), source);
  assert.equal(items.filter(item => item.audio).length, 3);
});
test('oversized phoneme sequences are retried without omitted text', async () => {
  const source = '1234567890'.repeat(100);
  const engine = {
    async *stream(text) { yield { text, phonemes: text, audio: { tokens: text.length * 4 } }; },
    tokenizer(text) { return { input_ids: { dims: [1, text.length * 4] } }; },
  };
  const items = []; for await (const item of generateChecked(engine, source, {})) items.push(item);
  assert.equal(items.map(item => item.text).join(''), source);
  assert.ok(items.every(item => item.audio.tokens <= 510));
});
