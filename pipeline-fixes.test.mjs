import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {plannerSettings} from './studio-planner-profile.mjs';
import {DEFAULTS,buildPlan,schedule} from './pipeline-engine.mjs';
import {startupCandidates,startupSearch} from './pipeline-detail-ui.mjs';
import {plannerSourceState} from './pipeline-config.mjs';
import {profileSummary} from './studio-planner-profile.mjs';

const read=name=>JSON.parse(fs.readFileSync(new URL(name,import.meta.url),'utf8'));
const evidence=read('./pipeline-evidence.json');
evidence.concurrency=read('./pipeline-concurrency.json');
evidence.directorCalibration=read('./pipeline-director-calibration.json');
const base={...DEFAULTS,minutes:30,chapters:3,cadence:8};
const profile=()=>({version:1,generationStrategy:'align',timedHistoryReady:true,
  settings:{voice:'am_michael',voiceCompute:'local',voiceEngine:'kokoro-82m',qcCheckLevel:'practical',qcSampleEvery:5,automaticRepair:false,
    video:{width:1280,height:720,fps:30,renderer:'cpu'},
    image:{model:'flux2-klein-4b',width:1280,height:720,steps:4,guidance:1},
    director:{executionMode:'staged-lean',parallelism:4,processingTier:'default',reasoning:'Balanced',visualPacking:'small'}},
  intro:{enabled:false},estimates:{},
  imageExecution:{backend:'comfyui-api',gpuLanes:2,modelCopies:2,requestSlots:4,requiredGPU:'NVIDIA GeForce RTX 5090'}});
const quoted=(included=true)=>{
  const value=profile();
  value.imageExecution.computeQuote={hourlyUSD:.69,observedAt:1791667350,provider:'runpod',workingDiskIncluded:included};
  return value;
};
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-7,`${a} != ${b}`);
const voice=plan=>plan.tasks.filter(t=>t.kind==='voice').map(t=>({id:t.id,duration:t.duration,resources:t.resources}));

test('saved compute quote reaches pricing for its uniquely resolved production GPU',()=>{
  const p=quoted(),before=structuredClone(p),value=plannerSettings(base,p,evidence);
  assert.equal(value.config.quotedGPU,'5090');assert.equal(value.config.containerGB,0);
  const price=buildPlan(value.config,evidence).gpu.pricing;
  assert.equal(price.basis,'quote');near(price.computeHourly,.69);near(price.containerHourly,0);
  assert.deepEqual(p,before);
});

test('saved quote replaces a stale GPU binding without dropping separately charged disk',()=>{
  const value=plannerSettings({...base,quotedGPU:'4090',quotedGPUHourly:9,pricingBasis:'quote',containerGB:20},quoted(false),evidence);
  assert.equal(value.config.quotedGPU,'5090');assert.equal(value.config.containerGB,20);
  const price=buildPlan(value.config,evidence).gpu.pricing;
  near(price.computeHourly,.69);near(price.containerHourly,20*.1/720);
});

test('unmatched or ambiguous GPU identity cannot apply the saved quote or remove disk',()=>{
  for(const mode of ['missing','ambiguous']){
    const p=quoted();let e=evidence;
    if(mode==='missing')p.imageExecution.requiredGPU='Unknown GPU';
    else e={...evidence,gpus:[...evidence.gpus,{...evidence.gpus.find(g=>g.id==='5090'),id:'other5090'}]};
    const value=plannerSettings(base,p,e);
    assert.equal(value.config.pricingBasis,'published');assert.equal(value.config.quotedGPU,'');assert.equal(value.config.containerGB,20);
    assert.match(value.warnings.join(' '),/quote has no unique production GPU match/);
  }
});

test('invalid or absent saved quote preserves explicit manual planning prices',()=>{
  for(const quote of [null,{hourlyUSD:0,observedAt:1},{hourlyUSD:-1,observedAt:1},{hourlyUSD:NaN,observedAt:1},{hourlyUSD:1,observedAt:Infinity}]){
    const p=profile();p.imageExecution.computeQuote=quote;
    const value=plannerSettings({...base,pricingBasis:'quote',quotedGPU:'5090',quotedGPUHourly:.81,containerGB:12},p,evidence).config;
    assert.equal(value.pricingBasis,'quote');assert.equal(value.quotedGPU,'5090');near(value.quotedGPUHourly,.81);assert.equal(value.containerGB,12);
  }
});

test('quote binding leaves every existing narration duration and demand unchanged',()=>{
  const unquoted=buildPlan(plannerSettings(base,profile(),evidence).config,evidence);
  const fixed=buildPlan(plannerSettings(base,quoted(),evidence).config,evidence);
  assert.deepEqual(voice(fixed),voice(unquoted));
  assert.equal(evidence.calibration.voiceSampleSeconds,135.515);
  assert.equal(evidence.calibration.voiceWallSeconds,13.467425699986052);
});

test('automatic current-policy startup search reapplies exactly its advertised schedule',()=>{
  const input=plannerSettings(base,profile(),evidence).config,before=structuredClone(input);
  const candidates=startupCandidates(input,evidence,'current');assert.ok(candidates.length>=3);
  const referenceVoice=voice(buildPlan(input,evidence));
  for(const candidate of candidates){
    assert.equal(candidate.plan.config.policy,'current');
    const applied=buildPlan({...input,readyChapters:candidate.readyChapters,gpuStartSeconds:candidate.gpuStartSeconds},evidence);
    const result=schedule(applied);
    near(result.end,candidate.result.end);near(result.rentalSeconds,candidate.result.rentalSeconds);near(result.totalUSD,candidate.result.totalUSD);
    assert.deepEqual(voice(applied),referenceVoice);assert.deepEqual(result.diagnostics,[]);
  }
  assert.deepEqual(input,before);
});

test('manual startup comparison intentionally retains proposed policy',()=>{
  const input=plannerSettings(base,profile(),evidence).config;
  assert.ok(startupCandidates(input,evidence).every(candidate=>candidate.plan.config.policy==='proposed'));
});

test('unsupported startup-search policies fail before calculation',()=>{
  for(const value of ['serial','typo','',null])assert.throws(()=>startupSearch(base,evidence,false,value).next(),/requires current or proposed/);
});

test('production worker forwards current policy while preserving the manual default',async()=>{
  let final;globalThis.self={postMessage:value=>{if(value.type!=='progress')final=value;}};
  try{
    await import('./pipeline-worker.mjs');
    const config=plannerSettings({...base,minutes:5,chapters:1},profile(),evidence).config;
    for(const searchPolicy of ['current',undefined]){
      final=null;self.onmessage({data:{type:'startup',config,evidence,...(searchPolicy?{searchPolicy}:{} )}});
      assert.equal(final.type,'complete');assert.ok(final.values.length>0);
      assert.ok(final.values.every(value=>value.plan.config.policy===(searchPolicy??'proposed')));
    }
  }finally{delete globalThis.self;}
});

test('automatic Follow loader sends the installed policy and applies the matching startup result',async()=>{
  const elements=new Map(),requests=[];
  const element=id=>{
    if(!elements.has(id))elements.set(id,{value:'',textContent:'',checked:true,
      replaceChildren(...options){this.value=options.find(value=>value.selected)?.value||options[0]?.value||'';}});
    return elements.get(id);
  };
  const p={...profile(),project:'fixture',name:'Fixture',revision:7};
  const context=vm.createContext({DEFAULTS,PRICING_DATE:'fixture',PRICING_URL:'fixture',VERSION:1,
    plannerSettings,profileSummary,plannerSourceState,buildPlan,
    fixtureEvidence:evidence,fixtureConfig:{...base,minutes:5,chapters:1},
    document:{getElementById:element},location:{search:''},localStorage:{getItem:()=>null},
    URLSearchParams,structuredClone,setTimeout:()=>0,clearTimeout:()=>{},
    Option:function(name,value,defaultSelected,selected){Object.assign(this,{name,value,selected});},
    pairingKey:()=> 'fixture-only',selectGPUConfig:config=>config,
    helperJson:async route=>route==='/studio/projects'?[{id:p.project,name:p.name}]:structuredClone(p),
    createWorkerRequest:()=>({run:async request=>{
      requests.push(request);
      return {values:startupCandidates(request.config,request.evidence,request.searchPolicy)};
    }}),
  });
  const source=fs.readFileSync(new URL('./pipeline-ui.mjs',import.meta.url),'utf8')
    .replace(/^import .*;\r?$/gm,'').replaceAll('import.meta.url',JSON.stringify(import.meta.url))
    .replace(/\nstart\(\);\s*$/,'');
  vm.runInContext(source+`
    evidence=fixtureEvidence;config=fixtureConfig;
    rebuild=async()=>{renderedGeneration=computeGeneration;};setControls=()=>{};
    globalThis.fixtureAPI={load:()=>loadStudioSettings({refresh:true,initial:true}),snapshot};`,context);
  await context.fixtureAPI.load();
  assert.equal(requests.length,1);assert.equal(requests[0].type,'startup');
  assert.equal(requests[0].config.policy,'current');assert.equal(requests[0].searchPolicy,'current');
  const applied=context.fixtureAPI.snapshot();assert.equal(applied.studioSource.following,true);
  assert.equal(applied.studioSource.revision,7);assert.equal(applied.config.policy,'current');
  const choices=startupCandidates(requests[0].config,evidence,'current');
  const aligned=choices.find(value=>value.balanced&&value.keepFastestVideo===false);
  assert.equal(applied.config.readyChapters,aligned.readyChapters);
  assert.equal(applied.config.gpuStartSeconds,aligned.gpuStartSeconds);
  near(schedule(buildPlan(applied.config,evidence)).end,aligned.result.end);
});
