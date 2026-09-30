import { test } from 'node:test';
import assert from 'node:assert/strict';
import { splitText, countWords, pcm16, wavBlob, duration } from './audio-core.mjs';
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
