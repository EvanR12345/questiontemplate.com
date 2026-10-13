import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {plannerSettings,profileSummary} from './studio-planner-profile.mjs';
import {DEFAULTS,buildPlan,schedule} from './pipeline-engine.mjs';
import {directorForecast} from './pipeline-director-calibration.mjs';
const evidence=JSON.parse(fs.readFileSync(new URL('./pipeline-evidence.json',import.meta.url)));
evidence.directorCalibration=JSON.parse(fs.readFileSync(new URL('./pipeline-director-calibration.json',import.meta.url)));
evidence.concurrency=JSON.parse(fs.readFileSync(new URL('./pipeline-concurrency.json',import.meta.url)));
const profile=()=>({version:1,project:'p',name:'Test',revision:3,generationStrategy:'align',timedHistoryReady:false,
 settings:{qcCheckLevel:'practical',qcSampleEvery:5,automaticRepair:false,voice:'am_michael',
 video:{width:1280,height:720,fps:30,motionMode:'gentle'},image:{model:'flux2-klein-4b',width:1280,height:720,steps:4,guidance:1},
 director:{executionMode:'staged-lean',parallelism:4,processingTier:'default',reasoning:'Balanced',visualPacking:'small'}},intro:{enabled:false},estimates:{}});
test('production renderer uses the helper choice instead of auto picking a faster calibration',()=>{
 const p=profile();p.renderExecution={backend:'cpu',location:'local-helper'};
 const r=plannerSettings({...DEFAULTS,videoRenderer:'native'},p,evidence);
 assert.equal(r.config.videoRenderer,'cpu');assert.match(r.warnings.join(' '),/actual local helper renderer/);
 p.renderExecution.backend='native';assert.equal(plannerSettings(DEFAULTS,p,evidence).config.videoRenderer,'native');
});
test('following the configured verified 5090 uses its observed compute quote without renting',()=>{
 const p=profile();p.imageExecution={backend:'comfyui-api',gpuLanes:2,modelCopies:2,requestSlots:4,
  requiredGPU:'NVIDIA GeForce RTX 5090',computeQuote:{hourlyUSD:.69,observedAt:1791874064}};
 const r=plannerSettings(DEFAULTS,p,evidence);
 assert.equal(r.config.pricingBasis,'quote');assert.equal(r.config.quotedGPUHourly,.69);
 assert.match(r.warnings.join(' '),/last observed compute quote/);
 assert.match(r.warnings.join(' '),/not a new availability or price guarantee/);
});
test('default forecast includes every-image visual checks and their cost/time',()=>{
 assert.equal(DEFAULTS.qc,'practical');
 const checked=schedule(buildPlan(DEFAULTS,evidence)),off=schedule(buildPlan({...DEFAULTS,qc:'off'},evidence));
 assert.ok(checked.qcCount>0);assert.ok(checked.qcUSD>0);assert.equal(off.qcCount,0);assert.equal(off.qcUSD,0);
 assert.ok(checked.tasks.some(t=>t.kind==='vision'));assert.ok(!off.tasks.some(t=>t.kind==='vision'));
});
test('projection copies actual setup without changing targets or source data',()=>{
 const input={...DEFAULTS,retries:9,minutes:1440,chapters:80};const before=structuredClone(profile());
 const result=plannerSettings(input,before,evidence);
 assert.equal(result.config.qc,'practical');assert.equal(result.config.intro,false);assert.equal(result.config.videoFps,30);
 assert.equal(result.config.minutes,1440);assert.equal(result.config.chapters,80);assert.equal(result.config.cadence,input.cadence);
 assert.equal(result.config.directorActualSlots,4);assert.equal(result.config.retries,0);assert.equal(result.config.readyChapters,80);
 assert.deepEqual(before,profile());assert.equal(input.retries,9);assert.ok(!('budget' in result.config));
});
test('explicit Off stays Off; sampled maps the real one-in-N interval',()=>{
 const p=profile();p.settings.qcCheckLevel='off';assert.equal(plannerSettings(DEFAULTS,p,evidence).config.qc,'off');
 p.settings.qcCheckLevel='sampled';p.settings.qcSampleEvery=4;assert.equal(plannerSettings(DEFAULTS,p,evidence).config.sample,25);
});
test('matching QC history supplies per-image seconds and cost once',()=>{
 const p=profile();p.estimates.visualQC={seconds:6,costUSD:.0002,runs:2,samples:7};
 const c=plannerSettings(DEFAULTS,p,evidence).config;
 assert.equal(c.qcSeconds,6);assert.equal(c.qcCost,.0002);
 const result=schedule(buildPlan(c,evidence));assert.equal(result.qcUSD,result.qcCount*.0002);
 assert.equal(result.tasks.filter(t=>t.kind==='vision').reduce((n,t)=>n+t.duration,0),result.qcCount*6);
 p.estimates.visualQC={seconds:null,costUSD:null};assert.equal(plannerSettings(DEFAULTS,p,evidence).config.qcSeconds,0);
});
test('different slot counts reserve actual capacity without certifying calibration',()=>{
 const c=plannerSettings(DEFAULTS,profile(),evidence).config,d=directorForecast(c,evidence);
 assert.equal(d.slots,4);assert.equal(d.timingMeasured,false);assert.match(d.scope,/calibration used 8/);
});
test('unsupported formats reject instead of estimating another format',()=>{
 const p=profile();p.settings.video.fps=120;assert.throws(()=>plannerSettings(DEFAULTS,p,evidence),/no matched/);
 p.settings.video.fps=30;p.settings.image.width=512;assert.throws(()=>plannerSettings(DEFAULTS,p,evidence),/no matched/);
});
test('model mismatch and old helper formats are disclosed',()=>{
 const p=profile();p.settings.image.model='sd15';assert.match(plannerSettings(DEFAULTS,p,evidence).warnings.join(' '),/not calibrated/);
 assert.throws(()=>plannerSettings(DEFAULTS,{version:0},evidence),/Update/);
 assert.match(profileSummary(profile()),/practical visual checks/);
});
test('fastest starts at first chapter; per-chapter intro is not miscounted',()=>{
 const p=profile();p.generationStrategy='fastest';assert.equal(plannerSettings(DEFAULTS,p,evidence).config.readyChapters,1);
 p.intro={enabled:true,placement:'every_chapter',duration:15};assert.throws(()=>plannerSettings(DEFAULTS,p,evidence),/Per-chapter/);
});

test('new admission capacity is shown without claiming a measured speedup',()=>{
 const p=profile();p.imageTasks=5;
 const result=plannerSettings(DEFAULTS,p,evidence);
 assert.match(result.warnings.join(' '),/five|5 image\/check tasks/);
 assert.match(result.warnings.join(' '),/no speedup is assumed/);
 assert.match(result.warnings.join(' '),/28-call Luna QC-only pilot found no measured advantage/);
 assert.match(profileSummary(p),/5 image\/check tasks, one GPU lane/);
});

test('automatic completion shows repairs without inventing a measured replacement rate',()=>{
 const p=profile();p.settings.unattendedProduction=true;p.settings.automaticRepair=true;
 const result=plannerSettings({...DEFAULTS,retries:7},p,evidence);
 assert.equal(result.config.retries,7);
 assert.match(result.warnings.join(' '),/bounded repairs within saved caps/);
 assert.match(profileSummary(p),/automatic completion and bounded repairs/);
});

test('following a single ComfyUI production queue cannot retain proposed scheduling or a two-copy benchmark',()=>{
 const p=profile();p.imageExecution={backend:'comfyui-api',gpuLanes:1,multipleModelCopiesVerified:false};
 const result=plannerSettings({...DEFAULTS,policy:'proposed',executionMode:'hybrid',imageWorkers:'best'},p,evidence);
 assert.equal(result.config.policy,'current');assert.equal(result.config.executionMode,'resident');assert.equal(result.config.imageWorkers,1);
 assert.equal(result.config.minutes,DEFAULTS.minutes);assert.equal(result.config.chapters,DEFAULTS.chapters);
 assert.match(result.warnings.join(' '),/does not activate the proposed render overlap/);
 for(const readyChapters of [1,2,result.config.chapters]){
  const plan=buildPlan({...result.config,readyChapters},evidence),scheduled=schedule(plan);
  assert.deepEqual(scheduled.diagnostics,[]);assert.equal(plan.measurement.workers,1);
  assert.ok(plan.tasks.find(t=>t.id==='stop').deps.some(id=>id.endsWith('-vision')));
 }
});

test('following saved production maps the required 5090 instead of retaining another GPU scenario',()=>{
 const p=profile();p.imageExecution={backend:'comfyui-api',gpuLanes:1,requiredGPU:'NVIDIA GeForce RTX 5090'};
 const r=plannerSettings({...DEFAULTS,gpu:'a6000'},p,evidence);
 assert.equal(r.config.gpu,'5090');assert.equal(r.config.imageWorkers,1);
 p.imageExecution.requiredGPU='Unmeasured GPU';
 assert.match(plannerSettings(DEFAULTS,p,evidence).warnings.join(' '),/no unique matched benchmark/);
});

test('two-copy production keeps installed scheduling with actual phase overlap',()=>{
 const p=profile();p.imageExecution={backend:'comfyui-api',gpuLanes:2,modelCopies:2,requestSlots:4,requiredGPU:'NVIDIA GeForce RTX 5090'};
 p.phaseOverlap={audioLookahead:true,chapterRender:true};
 const r=plannerSettings({...DEFAULTS,gpu:'a6000'},p,evidence);
 assert.equal(r.config.gpu,'5090');assert.equal(r.config.policy,'current');assert.equal(r.config.executionMode,'hybrid');assert.equal(r.config.imageWorkers,4);
 assert.equal(r.config.productionAudioLookahead,true);assert.equal(r.config.productionRenderOverlap,true);
 const plan=buildPlan(r.config,evidence),run=schedule(plan);
 assert.deepEqual(run.diagnostics,[]);assert.equal(plan.measurement.workers,2);
 assert.ok(!plan.tasks.find(t=>t.id==='c2-voice').deps.includes('c1-handoff'));
 assert.ok(plan.tasks.find(t=>t.id==='c2-chapter').deps.includes('c1-handoff'));
 assert.ok(!plan.tasks.find(t=>t.id==='c1-render').deps.includes('c2-b1-vision'));
});

test('saved large or compact director settings cannot be certified by another schema calibration',()=>{
 const p=profile();p.settings.director.visualPacking='large';
 p.directorCalibrationProfile={factsVersion:4,planVersion:13,reviewHintsVersion:2};
 const c=plannerSettings(DEFAULTS,p,evidence).config;
 assert.equal(c.directorConditionsMatch,false);assert.equal(directorForecast(c,evidence).timingMeasured,false);
 assert.match(plannerSettings(DEFAULTS,p,evidence).warnings.join(' '),/Exact saved director/);
});

test('matching current history replaces reference director time and cost without another tier multiplier',()=>{
 const p=profile();p.directorObservation={secondsPerStorySecond:.12,costPerTwoHours:.08,samples:2,runs:1};
 const c=plannerSettings(DEFAULTS,p,evidence).config,d=directorForecast(c,evidence);
 assert.equal(d.secondsPerStorySecond,.12);assert.equal(d.costPerTwoHours,.08);assert.equal(d.slots,4);assert.equal(d.timingMeasured,true);
 assert.match(d.scope,/matching successful/);
});
