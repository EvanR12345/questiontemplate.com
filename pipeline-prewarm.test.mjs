import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DEFAULTS,buildPlan,schedule} from './pipeline-engine.mjs';
import {densePrewarmCredit,densePrewarmMeasurement} from './pipeline-dense.mjs';
import {plannerSettings} from './studio-planner-profile.mjs';
const read=name=>JSON.parse(fs.readFileSync(new URL(name,import.meta.url),'utf8'));
const evidence=read('./pipeline-evidence.json');
evidence.concurrency=read('./pipeline-concurrency.json');
evidence.directorCalibration=read('./pipeline-director-calibration.json');
const profile={version:1,name:'Synthetic exact-envelope test',revision:1,generationStrategy:'dense',
 denseImagePhase:{version:1,supported:true,eligible:true,generationTasks:5,qcTaskSlots:4,descriptorLimit:8192,maxRepairRounds:2,allPlansFirst:true},
 settings:{unattendedProduction:true,automaticRepair:true,voice:'am_michael',voiceEngine:'kokoro-82m',qcCheckLevel:'practical',
 video:{width:1280,height:720,fps:30,renderer:'cpu'},image:{width:1280,height:720,model:'flux2-klein-4b',steps:4,guidance:1},
 director:{provider:'openai-luna',executionMode:'staged-lean',parallelism:4,processingTier:'default',reasoning:'Balanced',visualPacking:'small'}},
 imageExecution:{backend:'comfyui-api',gpuLanes:2,modelCopies:2,requestSlots:4,requiredGPU:'NVIDIA GeForce RTX 5090'},
 phaseOverlap:{audioLookahead:true,chapterRender:true},intro:{enabled:false},estimates:{}};
const config=()=>plannerSettings({...DEFAULTS,minutes:120,chapters:14,batch:24},profile,evidence).config;
const measured=()=>({productionDensePrewarmVersion:1,productionDensePrewarmEligible:true,productionDensePrewarmMeasured:true,
 productionDensePrewarmProof:'a'.repeat(64),productionDensePrewarmMeasuredProof:'a'.repeat(64),
 productionDensePrewarmAssemblySeconds:20,productionDensePrewarmProofSeconds:2});
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-6,`${a} != ${b}`);
const find=(r,id)=>r.tasks.find(t=>t.id===id);

test('no blanket startup credit: absent, foreign, ineligible, unmeasured or malformed proof gives zero',()=>{
 const base=config();assert.equal(densePrewarmCredit(base,120.499),0);
 for(const change of [{productionDensePrewarmEligible:false},{productionDensePrewarmMeasured:false},
  {productionDensePrewarmMeasuredProof:'b'.repeat(64)},{productionDensePrewarmProof:''},
  {productionDensePrewarmAssemblySeconds:Infinity},{productionDensePrewarmAssemblySeconds:121},
  {productionDensePrewarmProofSeconds:-1}]){
  const c={...base,...measured(),...change};assert.equal(densePrewarmMeasurement(c),null);
  const p=buildPlan(c,evidence);assert.deepEqual(find(schedule(p),'boot').deps,['dense-ready']);
 }
});

test('matched envelope shifts startup by measured remaining assembly and subtracts proof overhead, never cold or checks',()=>{
 const c=config(),base=buildPlan(c,evidence),before=schedule(base);
 const p=buildPlan({...c,...measured()},evidence),after=schedule(p);
 const first=p.tasks.find(t=>t.kind==='delivered').id;
 near(find(before,first).start-find(after,first).start,18);
 near(before.rentalSeconds,after.rentalSeconds);near(before.totalUSD,after.totalUSD);
 assert.ok(find(after,'boot').start<find(after,'dense-ready').end);
 assert.ok(find(after,'cold').start>=find(after,'dense-ready').end);
 for(const t of after.tasks.filter(t=>t.kind==='delivered'))assert.ok(t.deps.includes('dense-ready'));
 assert.equal(after.qcCount,before.qcCount);assert.equal(p.imageCount,base.imageCount);
 assert.deepEqual(p.tasks.filter(t=>t.kind==='voice'),base.tasks.filter(t=>t.kind==='voice'));
 assert.deepEqual(after.diagnostics,[]);
});

test('startup beating assembly produces paid idle in the rental instead of free utilization',()=>{
 const c={...config(),warmCache:true},before=schedule(buildPlan(c,evidence));
 const p=buildPlan({...c,...measured(),productionDensePrewarmAssemblySeconds:100},evidence),after=schedule(p);
 const startup=['boot','models','health'].reduce((n,id)=>n+find(before,id).duration,0);
 near(after.rentalSeconds-before.rentalSeconds,100-startup);
 const first=p.tasks.find(t=>t.kind==='delivered').id;
 near(find(before,first).start-find(after,first).start,startup-2);
 assert.ok(after.totalUSD>before.totalUSD);assert.deepEqual(after.diagnostics,[]);
});

test('delaying the final plan moves the startup trigger with it and retains every cold/image gate',()=>{
 const p=buildPlan({...config(),...measured()},evidence),before=schedule(p);
 const last=p.tasks.filter(t=>t.lane==='director').at(-1);
 const after=schedule(p,{[last.id]:{notBefore:find(before,last.id).start+60}});
 near(find(after,'dense-ready').end-find(before,'dense-ready').end,60);
 near(find(after,'boot').start-find(before,'boot').start,60);
 assert.ok(find(after,'cold').start>=find(after,'dense-ready').end);
 assert.deepEqual(after.diagnostics,[]);
});
