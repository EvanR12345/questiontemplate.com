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
