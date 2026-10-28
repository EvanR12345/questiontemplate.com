import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DEFAULTS,buildPlan,schedule} from './pipeline-engine.mjs';
import {plannerSettings,profileSummary} from './studio-planner-profile.mjs';
const read=name=>JSON.parse(fs.readFileSync(new URL(name,import.meta.url),'utf8'));
const evidence=read('./pipeline-evidence.json');evidence.concurrency=read('./pipeline-concurrency.json');
evidence.directorCalibration=read('./pipeline-director-calibration.json');
test('adaptive worker metadata describes actual generation without crediting an unmeasured forecast speedup',()=>{
 const p={version:1,name:'Synthetic',revision:1,generationStrategy:'align',timedHistoryReady:false,
 settings:{voice:'am_michael',voiceEngine:'kokoro-82m',qcCheckLevel:'practical',automaticRepair:true,
 video:{width:1280,height:720,fps:30},image:{model:'flux2-klein-4b',width:1280,height:720,steps:4,guidance:1},
 director:{provider:'openai-luna',executionMode:'staged-lean',parallelism:4,reasoning:'Balanced',processingTier:'default',visualPacking:'small'}},
 intro:{enabled:true,duration:30,placement:'full_story_only'},introImages:5,imageTasks:5,
 imageExecution:{backend:'comfyui-api',gpuLanes:2,modelCopies:2,requestSlots:4,requiredGPU:'NVIDIA GeForce RTX 5090'},estimates:{}};
 const before=plannerSettings(DEFAULTS,p,evidence),prior=schedule(buildPlan(before.config,evidence));
 p.imageAdmission={policy:'verified-dual-qc-window-v1',base:5,maximumAfterVerification:8,requiresVerifiedWorkers:true,requiresMemoryHeadroom:true,forecastSpeedupApplied:false};
 const after=plannerSettings(DEFAULTS,p,evidence),current=schedule(buildPlan(after.config,evidence));
 assert.deepEqual(after.config,before.config);assert.deepEqual(current,prior);
 assert.match(after.warnings.join(' '),/No speedup from this change is applied to the forecast/);
 assert.match(profileSummary(p),/5 image\/check tasks; up to 8 after worker and RAM verification/);
});
