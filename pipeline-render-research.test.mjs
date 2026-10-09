import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {candidateRows,researchHTML,sourceLink,mountRenderResearch} from './pipeline-render-research.mjs';
const data=JSON.parse(fs.readFileSync(new URL('./pipeline-render-research.json',import.meta.url)));
test('missing price sorts last without becoming free or changing source order',()=>{
  const original=JSON.stringify(data);
  const sorted=candidateRows(data,'price');
  assert.equal(sorted[0].id,'l4');
  assert.equal(sorted.at(-1).id,'5080');
  assert.equal(JSON.stringify(data),original);
});
test('encoding counts are capacity and never a fabricated timing calibration',()=>{
  assert.equal(candidateRows(data,'encoders')[0].id,'pro6000');
  const html=researchHTML(data);
  assert.match(html,/single H.264 stream/);
  assert.match(html,/No candidate changes/);
  assert.match(html,/Not verified/);
  assert.match(html,/89.93 fps/);
});
test('source links and names reject injection',()=>{
  assert.equal(sourceLink('javascript:alert(1)','<bad>'),'&lt;bad&gt;');
  assert.equal(sourceLink('https://key:secret@host/','text'),'text');
  assert.match(sourceLink('https://example.com/?x="','<bad>'),/noopener noreferrer/);
  const copy=structuredClone(data);copy.candidates[0].name='<script>bad</script>';
  assert.ok(!researchHTML(copy).includes('<script>'));
});
test('optional fetch failure cannot reject planner initialization',async()=>{
  const host={textContent:''};
  await mountRenderResearch(host,{fetcher:async()=>{throw Error('offline');}});
  assert.match(host.textContent,/remain usable/);
});
