import test from 'node:test';
import assert from 'node:assert/strict';
import {selectedStrategy,strategyOptions,strategyDescription,forecastText,imageWorkEstimate} from './studio-generation-plan.mjs';
test('default align persists explicit choices and rejects silent provider fallbacks',()=>{
  assert.equal(selectedStrategy({}),'align');
  for(const id of ['align','fastest','legacy']) assert.equal(selectedStrategy({generationStrategy:id}),id);
  assert.deepEqual(strategyOptions('align',true),{generationStrategy:'align',overlap:true});
  assert.deepEqual(strategyOptions('fastest',true),{generationStrategy:'fastest',overlap:true});
  assert.deepEqual(strategyOptions('legacy',false),{generationStrategy:'legacy',overlap:false});
  assert.throws(()=>strategyOptions('align',false),/requires cloud/);
  assert.throws(()=>strategyOptions('invalid',true),/supported/);
});
test('live chapter estimates use matching fresh busy intervals once and retain unknown scope',()=>{
  const rows=[10,12,15].map(finished=>({stage:'Image + quality checks',chapter:1,finished,seconds:10,
    status:'COMPLETE',performanceProfile:{model:'klein'}}));
  assert.equal(imageWorkEstimate(rows,1,0,3).seconds,15);
  assert.equal(imageWorkEstimate(rows,1,11,3),null);
  assert.equal(imageWorkEstimate(rows.map(t=>({...t,paused:true})),1,0,3),null);
  assert.equal(imageWorkEstimate([...rows.slice(0,2),{...rows[2],performanceProfile:{model:'different'}}],1,0,3),null);
});
test('labels disclose actual dispatch, limited coverage and unknown estimates',()=>{
  assert.match(strategyDescription('align'),/plans the chapters first/);
  assert.match(strategyDescription('fastest'),/one cloud image lane/);
  assert.match(strategyDescription('align'),/rental start and stop remain external/);
  const text=forecastText({history:{observations:4,runs:1},forecast:{planningSeconds:null,readyImageSeconds:12,unknownStages:['AI directing']}});
  assert.match(text,/directing: unknown/);assert.match(text,/partial work/);assert.match(text,/not guarantees/);
});
