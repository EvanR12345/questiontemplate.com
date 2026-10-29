import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DEFAULTS,buildPlan,schedule} from './pipeline-engine.mjs';
import {plannerSettings} from './studio-planner-profile.mjs';
import {applySourceBurstProjection,sourceBurstEligible,sourceBurstSerialComparison} from './pipeline-source-burst.mjs';
import {executionLabel,generationSpeed} from './pipeline-config.mjs';
import {qualityConfig} from './pipeline-qc-forecast.mjs';
const read=name=>JSON.parse(fs.readFileSync(new URL(name,import.meta.url),'utf8'));
const base=read('./pipeline-evidence.json');base.concurrency=read('./pipeline-concurrency.json');base.directorCalibration=read('./pipeline-director-calibration.json');
const cadence=5.884425316359273,compatibility='1c27fa75e17e59833533216c7d5b64d9880e109147ede64cd9c47260f90bd7e0';
const image={provider:'comfyui',model:'flux2-klein-4b',workflow:'flux2-klein-4b-auto',steps:4,guidance:1,sampler:'euler',scheduler:'flux2',width:1280,height:720,referenceStrength:0.65};
const source=()=>({type:'single-source-inclusive-span-v1',version:1,samples:1,studyStatus:'STOPPED_NO_REPLAY',armStatus:'FAILED',standardReceiptsVerified:true,freshSamplerDecodeInferenceVerified:true,unknownRequests:0,pendingRequests:0,reservedUSD:0,rawFrozenInventoryVerified:true,sourceInventory:{chapter720:107,intro1344x768:5,total:112},evidenceSHA256:'a'.repeat(64),phasePreflightSHA256:'b'.repeat(64),qualityOutcome:{finalBlocking:30,finalNonblocking:82},qualityAllowsRender:false,humanQualityAccepted:false,qcCompatibilityHash:compatibility,imageSettings:{...image},initialChapter:{images:107,inclusiveSeconds:426.4183759689331},repairChapter:{images:38,inclusiveSeconds:370.8001606464386},intro:{images:5,inclusiveSeconds:33.39255356788635},repairQcSamples:38,repairQC:{type:'completed-repair-rechecks-cohort-v1',samples:38,reservedRepairAttempts:41,failedBeforeSubmission:3,partialAttemptCohort:true,componentTimingComplete:true,processingTier:'default',apiSlots:4,unknownRequests:0,measuredSeconds:704.485,measuredUSD:.014877413,secondsPerCheck:704.485/38,costUSDPerCheck:.014877413/38}});
function fixture(extra={}){
 const evidence=structuredClone(base);evidence.qcCalibration={version:1,timingScope:'api-call',cohorts:[],sourceBurstProjection:source()};
 const profile={version:1,name:'Synthetic',revision:1,generationStrategy:'dense',imageTasks:5,qcCompatibilityHash:compatibility,qcProcessingTier:'default',denseImagePhase:{version:1,supported:true,eligible:true,generationTasks:5,qcTaskSlots:4,descriptorLimit:8192,maxRepairRounds:1,allPlansFirst:true},settings:{unattendedProduction:true,automaticRepair:true,voice:'am_michael',voiceEngine:'kokoro-82m',qcCheckLevel:'practical',video:{width:1280,height:720,fps:30,renderer:'cpu'},image:{...image},director:{provider:'openai-luna',executionMode:'staged-lean',parallelism:4,processingTier:'default',reasoning:'Balanced',visualPacking:'small',cadencePerMinute:cadence}},imageExecution:{backend:'comfyui-api',gpuLanes:2,modelCopies:2,requestSlots:4,requiredGPU:'NVIDIA GeForce RTX 5090'},phaseOverlap:{audioLookahead:true,chapterRender:true},intro:{enabled:false},estimates:{}};
 const config={...plannerSettings({...DEFAULTS,minutes:120,chapters:14,cadence,batch:24},profile,evidence).config,...extra};
 return {evidence,profile,config};
}
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-7,`${a} != ${b}`);
test('actual plannerSettings metadata survives worker clone and applies separate n1 source projection',()=>{
 const {evidence,profile,config}=fixture(),before=structuredClone(profile);
 assert.deepEqual(config.sourceBurstImageSettings,image);
 const plan=buildPlan(structuredClone(config),structuredClone(evidence)),result=schedule(plan);
 assert.equal(plan.sourceBurstProjectionUsed,true);assert.equal(plan.imageCount,707);assert.equal(plan.attemptCount,707);
 assert.deepEqual(result.diagnostics,[]);assert.deepEqual(profile,before);
 assert.match(plan.sourceBurstProjectionWarning,/n=1/);assert.match(plan.sourceBurstProjectionWarning,/30 final blockers/);
 assert.match(executionLabel(plan),/Conditional/);assert.equal(generationSpeed(plan,evidence).conditionalSourceProjection,true);
});
test('counts checks dependencies resources voice and cold allocation are preserved',()=>{
 const {evidence,config}=fixture(),reference=structuredClone(evidence);delete reference.qcCalibration.sourceBurstProjection;
 const before=buildPlan(config,reference),after=buildPlan(config,evidence);
 assert.deepEqual(after.tasks.map(({id,kind,images,deps,resources})=>({id,kind,images,deps,resources})),before.tasks.map(({id,kind,images,deps,resources})=>({id,kind,images,deps,resources})));
 const voice=p=>p.tasks.filter(t=>t.kind==='voice').map(({id,duration})=>({id,duration}));assert.deepEqual(voice(after),voice(before));
 assert.equal(after.tasks.filter(t=>t.kind==='vision').reduce((n,t)=>n+t.images,0),707);
 const cold=after.tasks.find(t=>t.id==='cold');assert.equal(cold.duration,before.tasks.find(t=>t.id==='cold').duration);
 near(after.tasks.filter(t=>t.kind==='delivered').reduce((n,t)=>n+t.duration,0)+cold.duration,707*426.4183759689331/107);
 assert.equal(after.config.retries,0);
});
test('repair rental credits cold once and keeps explicit repair count and every recheck',()=>{
 const {evidence,config}=fixture({retries:10,repairRounds:1}),plan=buildPlan(config,evidence),result=schedule(plan);
 assert.equal(plan.sourceBurstProjectionUsed,true);assert.equal(plan.rentalWindows.length,2);assert.deepEqual(result.diagnostics,[]);
 const group=plan.tasks.find(t=>t.id==='repair1-images'),cold=plan.tasks.find(t=>t.id==='repair1-cold');
 near(group.duration+cold.duration,group.images*370.8001606464386/38);
 assert.equal(group.images,Math.ceil(707*.1));assert.equal(plan.sourceBurstProjection.coldDurationAllocations.length,2);
 assert.equal(plan.tasks.filter(t=>t.kind==='vision'&&t.id.startsWith('repair')).reduce((n,t)=>n+t.images,0),group.images);
 const rechecks=plan.tasks.filter(t=>t.kind==='vision'&&t.id.startsWith('repair'));
 near(rechecks.reduce((n,t)=>n+t.duration,0),group.images*704.485/38);
 near(result.qcUSD,707*plan.config.qcCost+group.images*.014877413/38);
 assert.ok(rechecks.every(t=>t.sourceBurstRepairQC===true));
});
test('intro keeps its own1344span and count instead of blending chapter720 rate',()=>{
 const {evidence,config}=fixture({intro:true,introImages:5}),plan=buildPlan(config,evidence);
 const intro=plan.tasks.filter(t=>t.kind==='delivered'&&t.chapter===0),cold=plan.tasks.find(t=>t.id==='cold');
 near(intro.reduce((n,t)=>n+t.duration,0)+cold.duration,33.39255356788635);
 assert.equal(intro.reduce((n,t)=>n+t.images,0),5);assert.equal(plan.sourceBurstProjectionUsed,true);
});
test('unsupported manual missing metadata or changed exact policy retains original two-round reference',()=>{
 for(const change of [f=>delete f.config.sourceBurstImageSettings,f=>f.config.sourceBurstImageSettings.steps=8,
  f=>f.config.sourceBurstImageSettings.guidance=2,f=>f.config.sourceBurstImageSettings.referenceStrength=.5,
  f=>f.config.qcCompatibilityHash='c'.repeat(64),f=>f.config.cadence=8,f=>f.config.qc='off',
  f=>f.config.productionRequestSlots=2,f=>f.config.productionDenseQCSlots=2,
  f=>{f.config.executionMode='resident';f.config.imageWorkers='1';},f=>f.config.encodingProfile='cached',
  f=>f.evidence.qcCalibration.sourceBurstProjection.pendingRequests=1,
  f=>f.evidence.qcCalibration.sourceBurstProjection.sourceInventory.chapter720=106,
  f=>f.evidence.qcCalibration.sourceBurstProjection.standardReceiptsVerified=false]){
  const f=fixture();change(f);assert.equal(sourceBurstEligible({config:{...f.config,qcEffectiveSlots:4}},f.evidence),null);
 }
});
test('invalid cold metadata fails back atomically rather than losing counts or time',()=>{
 const f=fixture(),without=structuredClone(f.evidence);delete without.qcCalibration.sourceBurstProjection;
 const plan=buildPlan(f.config,without);plan.tasks.find(t=>t.id==='cold').duration=Infinity;
 const snapshot=structuredClone(plan);assert.equal(applySourceBurstProjection(plan,f.evidence),plan);assert.deepEqual(plan,snapshot);
});
test('batch1 and24 keep equal inclusive work and counts with positive bounded cold allocation',()=>{
 const plans=[1,24].map(batch=>{const f=fixture({minutes:3,chapters:2,readyChapters:2,batch});return buildPlan(f.config,f.evidence);});
 const work=p=>p.tasks.filter(t=>t.kind==='cold'||t.kind==='delivered').reduce((n,t)=>n+t.duration,0);
 near(work(plans[0]),work(plans[1]));assert.equal(plans[0].imageCount,plans[1].imageCount);
 for(const p of plans){assert.equal(p.sourceBurstProjectionUsed,true);assert.ok(p.tasks.filter(t=>t.kind==='cold'||t.kind==='delivered').every(t=>t.duration>0));assert.deepEqual(schedule(p).diagnostics,[]);}
 assert.match(plans[0].tasks.find(t=>t.id==='cold').basis,/not an isolated cold measurement/);
});
test('manual QC price controls initial and repair dollars without replacing measured repair timing',()=>{
 const f=fixture({retries:10,repairRounds:1,qcCostManual:true,qcCost:.01}),plan=buildPlan(f.config,f.evidence),result=schedule(plan);
 near(result.qcUSD,result.qcCount*.01);
 const rows=plan.tasks.filter(t=>t.kind==='vision'&&t.id.startsWith('repair'));
 for(const row of rows){near(row.duration,row.images*704.485/38);near(row.sourceBurstQCUSD,row.images*.01);assert.match(row.basis,/manual per-check QC price/);}
});
test('n1 current versus old serial is explicitly unmatched with no same-work saving',()=>{
 const result={end:100},serial={end:200};
 const partial=sourceBurstSerialComparison({sourceBurstProjectionUsed:true},result,serial);
 assert.equal(partial.comparisonMatched,false);assert.equal(partial.comparisonSaving,null);
 assert.match(partial.comparisonNote,/different image timing basis/);
 assert.deepEqual(sourceBurstSerialComparison({},result,serial),{comparisonMatched:true,comparisonSaving:.5,comparisonNote:''});
});
test('settled-partial QC note separates measured repair work while legacy wording remains',()=>{
 const f=fixture(),row={id:'partial',status:'COMPLETE',timingScope:'api-call',unknownRequests:0,
  policy:'practical',apiSlots:4,qcCompatibilityHash:compatibility,processingTier:'default',checks:107,
  uniqueImages:107,secondsPerCheck:13.68,costUSDPerCheck:.00034,observedAt:1793249847,
  workload:'image-qc-pipeline',topology:{gpu:'5090',modelCopies:2,requestSlots:4,imageTasks:5},
  referenceImages:'up to 2',componentTimingComplete:true,studyStatus:'STOPPED_NO_REPLAY'};
 f.evidence.qcCalibration.cohorts=[row];
 const c={...f.config,qcEffectiveSlots:4};
 assert.match(qualityConfig(c,f.evidence).qcBenchmarkNote,/Measured repairs and rechecks are excluded/);
 assert.match(qualityConfig(c,f.evidence).qcBenchmarkNote,/quality-blocked/);
 delete row.componentTimingComplete;
 assert.match(qualityConfig(c,f.evidence).qcBenchmarkNote,/repairs, manual waiting and full-video completion are unmeasured/);
});
