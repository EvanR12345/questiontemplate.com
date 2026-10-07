import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {serverlessHTML} from './pipeline-serverless.mjs';

test('partial results and failures are honest and safely escaped',()=>{
  const html=serverlessHTML({model:'Klein',resolution:'720p',backend:'Comfy',cumulativeCapUSD:1,costs:{observedCumulativeDebitUSD:.3},attempts:[{attempt:4,status:'FAILED',completedImages:2,elapsedSeconds:8,cacheMode:'resident_fresh',failure:{message:'<img onerror=x>'},groups:[{gpu:'NVIDIA RTX 5090',pattern:'long_gap_resume',images:2,completedRounds:1,workerIds:['a'],delivery:{median:4,min:3,max:5},comfyExecution:{median:1},providerExecution:{median:2},queueAndStartup:{median:.1}}]}],limits:['Small sample']});
  assert.match(html,/30-second pause \/ resume/);
  assert.match(html,/\$0\.300/);
  assert.match(html,/Loaded models reused; encoding fresh/);
  assert.match(html,/FAILED/);
  assert.match(html,/&lt;img onerror=x&gt;/);
  assert.doesNotMatch(html,/<img onerror=x>/);
  assert.doesNotMatch(html,/Confirmed zero \/ resume<\/td>/);
});

test('no verified samples does not invent speeds',()=>{
  const html=serverlessHTML({model:'Klein',resolution:'720p',backend:'Comfy',cumulativeCapUSD:1,attempts:[],limits:[]});
  assert.match(html,/No verified output yet/);
  assert.match(html,/awaiting settlement/);
});

test('published pilot includes final results and keeps resume and startup distinct',()=>{
  const data=JSON.parse(readFileSync(new URL('./pipeline-serverless.json',import.meta.url),'utf8'));
  const last=data.attempts.find(a=>a.attempt===6);
  assert.equal(last.status,'COMPLETE');
  assert.equal(last.completedImages,13);
  assert.equal(data.attempts.reduce((n,a)=>n+a.completedImages,0),37);
  assert.equal(data.cacheOutputComparison.length,7);
  assert.ok(data.cacheOutputComparison.every(p=>p.identicalPixels));
  const resume=last.groups.find(g=>g.pattern==='long_gap_resume');
  assert.equal(resume.completedRounds,2);
  assert.equal(resume.firstAfterPauseDelivery.n,2);
  assert.equal(resume.resumeWorkerChangedFromWarmup,false);
  const h100=data.attempts.find(a=>a.attempt===4).groups.find(g=>g.pattern==='long_gap_resume');
  assert.equal(h100.completedRounds,1);
  assert.equal(h100.resumeWorkerChangedFromWarmup,true);
  const html=serverlessHTML(data);
  assert.match(html,/Initial startup · first saved image/);
  assert.match(html,/7 unique matched same-GPU shots/);
  assert.match(html,/44\.34s \(1 pause\)/);
  assert.match(html,/6\.80s \(2 pauses\)/);
  assert.match(html,/not a forced cold restart/);
  assert.match(html,/Images\/min in completed rounds/);
  assert.match(html,/EUR-IS-2/);
  assert.doesNotMatch(html,/undefined|NaN/);
  assert.ok(data.costs.observedCumulativeDebitUSD < data.cumulativeCapUSD);
  assert.deepEqual(data.costs.remainingOwnedEndpoints,[]);
  assert.deepEqual(data.costs.remainingOwnedTemplates,[]);
  const raw=JSON.stringify(data);
  assert.doesNotMatch(raw,/rpa_[A-Za-z0-9_-]{20,}|C:\\\\Users|"images":\s*\[\s*\{\s*"data"/);
});
