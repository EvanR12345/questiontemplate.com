import test from 'node:test';
import assert from 'node:assert/strict';
import {auditionRequest,previewCosts,escapeHTML} from './voice-lab-state.mjs';
const model={id:'qwen3',modes:[{id:'clone',requiresReference:true},{id:'design',requiresDescription:true}],languages:['English'],parameters:[{id:'seed',label:'Seed',type:'integer',min:0,max:100,default:0}]};
test('cloning requires a private reference and supported language',()=>{
  assert.throws(()=>auditionRequest(model,{mode:'clone',text:'Hello',language:'English'}),/reference/);
  assert.throws(()=>auditionRequest(model,{mode:'clone',text:'Hello',language:'Dutch',referenceId:'r-1'}),/language/);
  assert.equal(auditionRequest(model,{mode:'clone',text:' Hello ',language:'English',referenceId:'r-1'}).text,'Hello');
});
test('design description and real parameter bounds are enforced',()=>{
  assert.throws(()=>auditionRequest(model,{mode:'design',text:'Hello',language:'English'}),/Describe/);
  assert.throws(()=>auditionRequest(model,{mode:'design',description:'Warm',text:'Hello',language:'English',parameters:{seed:1.1}}),/Seed/);
  assert.throws(()=>auditionRequest(model,{mode:'design',description:'Warm',text:'Hello',language:'English',parameters:{seed:101}}),/Seed/);
});
test('30 second projection scales generation and adds startup once',()=>{
  const cost=previewCosts({hourlyUSD:3.6,generationSeconds:10,audioSeconds:20,loadSeconds:5,startupSeconds:300});
  assert.equal(cost.warm30USD,.015);assert.equal(cost.modelLoad30USD,.02);assert.equal(cost.cold30USD,.32);
  assert.equal(previewCosts({hourlyUSD:3.6,generationSeconds:10,audioSeconds:20}).cold30USD,null);
  assert.equal(previewCosts({generationSeconds:10,audioSeconds:20}),null);
});
test('script and worker text is escaped for history markup',()=>assert.equal(escapeHTML('<audio onerror="x">'), '&lt;audio onerror=&quot;x&quot;&gt;'));
