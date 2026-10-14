import test from 'node:test';
import assert from 'node:assert/strict';
import {providerSetupForm,providerSetupValues} from './studio-provider-setup.mjs';
test('saved credentials are represented by a boolean and empty password fields',()=>{
  const html=providerSetupForm({providers:[{provider:'vast',credentialSaved:true,renderVerified:false,error:'<error>'}]});
  assert.match(html,/type="password" autocomplete="off"/);
  assert.match(html,/Render test pending/);assert.match(html,/&lt;error&gt;/);
  assert.ok(!html.includes('localStorage'));assert.ok(!html.includes('value="SECRET"'));
});
test('blank keys preserve existing secrets and invalid limits fail before saving',()=>{
  const values={gpuProviderOrder:'vast,lium',gpuProviderMaxRate:'1.19',gpuProviderDisk:'25',gpuVastKey:'',gpuLiumKey:''};
  const doc={getElementById:id=>({value:values[id]})};
  const result=providerSetupValues(doc);assert.equal(result.vastApiKey,undefined);assert.equal(result.liumApiKey,undefined);
  assert.deepEqual(result.gpuProviderOrder,['vast','lium']);
  values.gpuProviderMaxRate='NaN';assert.throws(()=>providerSetupValues(doc),/finite/);
});
test('all three provider orders and separate workload proofs are visible',()=>{
  const html=providerSetupForm({providerOrder:['runpod','vast','lium'],providers:[{provider:'runpod',credentialSaved:true,imageVerified:true,renderVerified:false}]});
  assert.match(html,/Runpod → Vast.ai → Lium/);assert.match(html,/Image test passed/);assert.match(html,/Render test pending/);
  assert.match(html,/uncertain rental response/);assert.equal((html.match(/<option /g)??[]).length,6);
});
