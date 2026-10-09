import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {directorStudyProjections,directorStudyHTML} from './pipeline-director-study.mjs';
const study=JSON.parse(fs.readFileSync(new URL('./pipeline-director-study.json',import.meta.url)));

test('director scaling keeps measured work, wall time and API costs separate without modifying evidence',()=>{
  const before=structuredClone(study),rows=directorStudyProjections(study,120);
  assert.equal(rows[0].projectedWallSeconds,study.wholePlans[0].wallSeconds*7200/1050.23);
  assert.equal(rows[0].projectedUSD,study.wholePlans[0].estimatedUSD*7200/1050.23);
  assert.deepEqual(study,before);assert.equal(rows.length,4);
  for(const minutes of [90,720,1440])assert.equal(directorStudyProjections(study,minutes).length,4);
  for(const minutes of [0,-1,NaN,Infinity])assert.throws(()=>directorStudyProjections(study,minutes));
});
test('published scalar evidence excludes private story, identity, asset and key material',()=>{
  const raw=JSON.stringify(study);
  for(const privateField of ['workFolder','sourceProjectId','frozenSourceSha256','openaiApiKey','runpodApiKey','draftPrompt','narrationSegment','referenceImages'])assert.equal(raw.includes(privateField),false);
  assert.equal(study.pendingUsage,false);assert.ok(study.spentUSD<=study.budgetUSD);
  assert.equal(study.wholePlans.every(a=>a.repetitions===1),true);
  assert.equal(study.promptBatches.every(a=>a.repetitions===4&&a.allMappingsComplete),true);
});
test('benchmark panel labels projections, varying plans and unchanged GPU calibration honestly',()=>{
  const html=directorStudyHTML(study,1440);
  for(const phrase of ['not a measured long production','different shot counts','not silently substituted','no images','24-hour completion','unchanged grounded draft'])assert.ok(html.includes(phrase));
  assert.ok(html.includes('1440 minutes'));
});

test('measured pass table retains actual work and receipts without pretending these are serial delays',()=>{
  const arm=study.wholePlans.find(a=>a.id==='parallel-2');
  assert.ok(Math.abs(arm.stages.reduce((n,s)=>n+s.workSeconds,0)-arm.workSeconds)<.01);
  assert.ok(Math.abs(arm.stages.reduce((n,s)=>n+s.estimatedUSD,0)-arm.estimatedUSD)<.000001);
  const html=directorStudyHTML(study);
  assert.ok(html.includes('parallel rows overlap'));
  for(const stage of arm.stages)assert.ok(html.includes(stage.stage));
  const altered=structuredClone(study);
  altered.wholePlans.find(a=>a.id==='parallel-2').stages[0].medianFirstTextSeconds=null;
  assert.ok(directorStudyHTML(altered).includes('Unknown'));
});
