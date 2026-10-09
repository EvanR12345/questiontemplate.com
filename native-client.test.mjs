import test from 'node:test';
import assert from 'node:assert/strict';
import { nativeRequest, nativeHealth, nativeAudio, nativeTokenizer, NATIVE_URL } from './native-client.mjs';
import { prepareBatches } from './audio-core.mjs';
const key = 'a'.repeat(64);

test('Audio accepts the shared Audio and Studio helper protocol', async () => {
  const info=await nativeHealth(key,async()=>new Response(JSON.stringify({protocol:2,backend:'cuda',sampleRate:24000,vocab:{h:1}})));
  assert.equal(info.protocol,2);
});

test('pairing validation prevents any unpaired network request', async () => {
  let requests = 0;
  await assert.rejects(nativeHealth('', () => { requests++; }), /Connect NVIDIA/);
  assert.equal(requests, 0);
});
test('requests stay on fixed loopback with ephemeral auth and no cookies', async () => {
  await nativeRequest('/prepare', key, {voice:'am_michael'}, async (url, options) => {
    assert.equal(url, NATIVE_URL + '/prepare');
    assert.equal(options.headers.Authorization, 'Bearer ' + key);
    assert.equal(options.credentials, 'omit');
    assert.equal(options.cache, 'no-store');
    assert.deepEqual(JSON.parse(options.body), {voice:'am_michael'});
    return new Response('{}');
  });
});
test('binary PCM retains sample values and rejects partial or invalid responses', async () => {
  const batch = {ids:{phonemes:'h'}};
  const fetcher = async () => new Response(new Float32Array([-.5,0,.5]), {headers:{'X-Sample-Rate':'24000'}});
  assert.deepEqual([...((await nativeAudio(key, batch, 'am_michael', 1, fetcher)).audio)], [-.5,0,.5]);
  await assert.rejects(nativeAudio(key, batch, 'am_michael', 1, async () => new Response(new Uint8Array([1]), {headers:{'X-Sample-Rate':'24000'}})), /Incomplete/);
  await assert.rejects(nativeAudio(key, batch, 'am_michael', 1, async () => new Response(new Float32Array([NaN]), {headers:{'X-Sample-Rate':'24000'}})), /invalid audio/);
});
test('oversized phonemes split before CUDA inference without losing source text', async () => {
  const text = ('A long sentence. ').repeat(100);
  const tokenizer = nativeTokenizer({'h':1, ' ':2});
  const batches = [];
  for await (const batch of prepareBatches(text, tokenizer, async source => 'h'.repeat(source.length), 'a', 280)) batches.push(batch);
  assert.equal(batches.map(b => b.text).join(''), text);
  assert.ok(batches.every(b => b.ids.dims[1] <= 280));
  assert.ok(batches.length > 1);
});
test('CUDA failure remains an explicit error rather than a silent CPU switch', async () => {
  await assert.rejects(nativeHealth(key, async () => new Response(JSON.stringify({protocol:1, backend:'wasm', sampleRate:24000, vocab:{}}))), /Update/);
  await assert.rejects(nativeRequest('/prepare', key, {}, async () => new Response(JSON.stringify({error:'CUDA ran out of memory'}), {status:503})), /CUDA ran out of memory/);
});

test('HTTP status survives safe JSON errors so reconnect distinguishes missing projects',async()=>{
  for(const status of [401,403,404,500])await assert.rejects(
    nativeRequest('/studio/project?id=test',key,undefined,async()=>new Response('{"error":"Request failed"}',{status})),
    error=>error.status===status&&error.message==='Request failed');
});

test('a typed missing-project error is preserved without mistaking arbitrary 404s for absence',async()=>{
  await assert.rejects(nativeRequest('/studio/project?id=test',key,undefined,async()=>new Response('{"error":"Missing","code":"PROJECT_NOT_FOUND"}',{status:404})),
    error=>error.status===404&&error.code==='PROJECT_NOT_FOUND');
});
