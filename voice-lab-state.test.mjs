import test from 'node:test';
import assert from 'node:assert/strict';
import {auditionRequest,previewCosts,escapeHTML,parameterValue} from './voice-lab-state.mjs';
import {VOICE_DESIGNS,QWEN_PRESETS,REFERENCE_SCRIPT,preferredReference,jobVoiceName} from './voice-lab-library.mjs';
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
test('typed model settings preserve disabled sampling and reject string booleans',()=>{
  const control={id:'do_sample',label:'Sampling',type:'boolean',default:true};
  const catalog={...model,parameters:[control]};
  const request=auditionRequest(catalog,{mode:'design',description:'Clear',text:'Hello',language:'English',parameters:{do_sample:false}});
  assert.equal(request.parameters.do_sample,false);
  assert.throws(()=>parameterValue(control,'false'),/Sampling/);
  assert.throws(()=>parameterValue(control,0),/Sampling/);
});
test('Vox clone strategies travel as named enum values rather than numeric coercions',()=>{
  const control={id:'clone_strategy',label:'Clone strategy',type:'select',default:'auto',options:[{value:'auto',label:'Automatic'},{value:'prompt_reference',label:'Prompt + reference'}]};
  const request=auditionRequest({...model,parameters:[control]},{mode:'clone',text:'Hello',language:'English',referenceId:'ref',parameters:{clone_strategy:'prompt_reference'}});
  assert.equal(request.parameters.clone_strategy,'prompt_reference');
  assert.throws(()=>parameterValue(control,'0'),/Clone strategy/);
  assert.throws(()=>parameterValue({...model.parameters[0]},''),/Seed/);
});
test('reference recipes are distinct and audition labels resolve the actual saved reference',()=>{
  assert.equal(new Set(VOICE_DESIGNS.map(v=>v.description)).size,3);
  assert.ok(REFERENCE_SCRIPT.split(/\s+/).length>=55&&REFERENCE_SCRIPT.split(/\s+/).length<=75);
  assert.equal(preferredReference(24.99),false);assert.equal(preferredReference(25),true);assert.equal(preferredReference(30),true);assert.equal(preferredReference(30.01),false);
  assert.equal(jobVoiceName({referenceId:'r',description:VOICE_DESIGNS[0].description},[{id:'r',name:'Official reference'}]),'Official reference');
  assert.equal(jobVoiceName({description:VOICE_DESIGNS[1].description}),VOICE_DESIGNS[1].name);
  assert.equal(new Set(QWEN_PRESETS.map(voice=>voice.id)).size,9);
  assert.equal(jobVoiceName({mode:'preset',parameters:{speaker:'uncle_fu'}}),'Uncle Fu');
});
test('Drama character metadata travels only with voice design and keeps speaker labels bounded',()=>{
  const drama={id:'drama3',modes:[{id:'design',requiresDescription:true},{id:'clone',requiresReference:true}],languages:['Auto'],parameters:[]};
  const values={mode:'design',text:'A complete sentence.',language:'Auto',description:'Warm narrator',character:{name:'Captain',scene:'Quiet room'}};
  assert.deepEqual(auditionRequest(drama,values).character,{name:'Captain',scene:'Quiet room'});
  assert.equal('character' in auditionRequest(drama,{...values,mode:'clone',referenceId:'saved'}),false);
  assert.throws(()=>auditionRequest(drama,{...values,character:{name:'Captain:\nOther speaker'}}),/character/);
});
