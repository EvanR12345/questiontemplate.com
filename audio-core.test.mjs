import { test } from 'node:test';
import assert from 'node:assert/strict';
import { splitText, countWords, pcm16, wavBlob, duration, prepareBatches, estimatedBytes, recordedSeconds } from './audio-core.mjs';
import { createEncoder, joinBytes } from './encode-audio.mjs';
import { pronunciationRules, speechText } from './pronunciation.mjs';
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
test('batching preserves multiple sentences and whitespace without per-sentence inference', async () => {
  const source = '  The door opened.  Maya waited!\nThen she ran.  ';
  const tokenizer = text => ({ input_ids: { dims: [1, text.length + 2] } });
  const items = []; for await (const item of prepareBatches(source, tokenizer, async text => text, 'a')) items.push(item);
  assert.equal(items.map(item => item.text).join(''), source);
  assert.equal(items.length, 1);
});
test('oversized phoneme sequences are split BEFORE inference without omitted text', async () => {
  const source = '1234567890'.repeat(100);
  const tokenizer = text => ({ input_ids: { dims: [1, text.length * 4] } });
  const items = []; for await (const item of prepareBatches(source, tokenizer, async text => text, 'a')) items.push(item);
  assert.equal(items.map(item => item.text).join(''), source);
  assert.ok(items.every(item => item.ids.dims.at(-1) <= 280));
});
test('90–120-minute sizes and MP3 duration include codec padding', () => {
  assert.equal(estimatedBytes(5400, 'mp3', 48), 32400000);
  assert.equal(estimatedBytes(7200, 'mp3', 48), 43200000);
  assert.equal(estimatedBytes(7200, 'wav'), 345600044);
  assert.equal(recordedSeconds(0, 43200000, 'mp3', 48), 7200);
});
test('streaming MP3 encoding retains frames between buffers and flushes final audio', async () => {
  const samples = Float32Array.from({ length: 24000 }, (_, i) => .25 * Math.sin(i * Math.PI * 2 * 220 / 24000));
  const encoder = await createEncoder('mp3', 48), blocks = [];
  for (let i = 0; i < samples.length; i += 1000) blocks.push(encoder.encode(samples.subarray(i, i + 1000)));
  blocks.push(encoder.flush()); const mp3 = joinBytes(blocks);
  assert.ok(mp3.length >= 6000 && mp3.length < 7000);
  assert.equal(mp3[0], 255); assert.equal(mp3[1] & 224, 224);
  assert.ok(recordedSeconds(samples.length, mp3.length, 'mp3', 48) >= 1);
});
test('pronunciation fixes are literal, whole-word, and retain sentence punctuation', () => {
  const rules = pronunciationRules('Maya = My uh\nAPI = A P I\nignored\nC++ = see plus plus');
  assert.equal(speechText('Maya uses API. Mayan is different!', rules), 'My uh uses A P I. Mayan is different!');
  assert.equal(speechText('C++ is useful', rules), 'see plus plus is useful.');
  assert.equal(speechText('The final word'), 'The final word.');
});
