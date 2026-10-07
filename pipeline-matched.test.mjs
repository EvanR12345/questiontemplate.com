import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {matchedHTML,projectMatched} from './pipeline-matched.mjs';
const evidence=JSON.parse(readFileSync(new URL('./pipeline-evidence.json',import.meta.url)));
const mode={mode:'pod',continuousRounds:2,continuousImages:24,imagesPerMinute:30,
  bothCopiesWarmSeconds:120,warmDelivery:{median:4},handler:{median:2},hourlyUSD:.99,
  savedImages:30,verifiedCopies:true,projectedImageStageSeconds:1542};
const data={status:'COMPLETE',requestedRegion:'EUR-NO-1',modes:[mode],limits:[]};
test('709 outputs, startup outputs counted once, and valid dependencies',()=>{
  const p=projectMatched(mode,data,evidence);
  assert.equal(p.images,709);
  assert.equal(p.finishedVideoSeconds,7200);
  assert.equal(p.continuousWarmImagesSeconds,707/.5);
  assert.equal(p.setupSeconds,120);
  assert.deepEqual(p.diagnostics,[]);
});
test('a partial round cannot generate a two-hour rate claim',()=>{
  assert.equal(projectMatched({...mode,continuousRounds:1},data,evidence),null);
  assert.match(matchedHTML({...data,modes:[{...mode,continuousRounds:1}]},evidence),/No two-hour rate projection/);
});
test('serial and overlap include all stages and label cost estimates',()=>{
  const p=projectMatched(mode,data,evidence),s=projectMatched(mode,data,evidence,'serial');
  assert.ok(s.wallSeconds>=p.wallSeconds);
  assert.ok(p.stages.render>3000&&p.stages.voice>500&&p.stages.prompt>0);
  assert.ok(p.totalUpperUSD>p.estimatedDirectorUSD);
  assert.match(matchedHTML(data,evidence),/not a measured two-hour production/);
});
test('provider text cannot inject markup',()=>{
  assert.ok(!matchedHTML({...data,status:'<script>bad</script>',modes:[]},evidence).includes('<script>'));
});
test('published partial dataset preserves receipts without leaking private inputs',()=>{
  const raw=readFileSync(new URL('./pipeline-matched.json',import.meta.url),'utf8');
  const d=JSON.parse(raw);
  assert.equal(d.status,'CAPACITY_BLOCKED');
  assert.equal(d.modes.find(m=>m.mode==='pod').savedImages,30);
  assert.equal(d.modes.find(m=>m.mode==='serverless').imagesPerMinute,null);
  assert.equal(d.cleanup.endpointPaused,true);
  assert.equal(d.cleanup.podStopped,true);
  assert.ok(d.observedCumulativeDebitUSD<d.cumulativeCapUSD);
  assert.ok(!/rpa_|STUDIO_POD_BEARER|private-frozen|C:\\\\Users|imagePath|\"prompt\"|\"seed\"/.test(raw));
});
