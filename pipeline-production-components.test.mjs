import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DEFAULTS,buildPlan,schedule,importSnapshot} from './pipeline-engine.mjs';
import {qualityBlockedProductionEvidence,currentComponentsEligible,CURRENT_COMPONENTS_ID} from './pipeline-production-components.mjs';
import {completedProductionEvidence} from './pipeline-production-calibration.mjs';
import {plannerSettings} from './studio-planner-profile.mjs';
import {sourceBurstSerialComparison} from './pipeline-source-burst.mjs';
const read=name=>JSON.parse(fs.readFileSync(new URL(name,import.meta.url),'utf8'));
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-6,`${a} != ${b}`);
function fixture(extra={}){
 const evidence=read('./pipeline-evidence.json');
 for(const [k,file]of [['concurrency','pipeline-concurrency.json'],['directorCalibration','pipeline-director-calibration.json'],['qcCalibration','pipeline-qc-calibration.json'],['productionCalibration','pipeline-production-calibration.json']])evidence[k]=read('./'+file);
 const d=evidence.productionCalibration,p=d.partial;
 const config={...DEFAULTS,policy:'current',productionGenerationStrategy:'dense',productionDenseVersion:1,
  productionDenseSupported:true,productionDenseEligible:true,productionDenseGenerationTasks:5,productionDenseQCSlots:4,
  productionDenseDescriptorLimit:8192,productionDenseMaxRepairRounds:1,readyChapters:14,
  productionDenseLeasePolicy:'retained-qc-v1',productionDenseHoldsGPUForQC:true,
  productionModelCopies:2,productionRequestSlots:4,productionRenderOverlap:true,productionAudioLookahead:true,
  resolution:'720p',encodingProfile:'fresh',executionMode:'hybrid',imageWorkers:'4',warmCache:true,
  videoRenderer:'native',directorProfile:'lean-overlap-standard-20261031',directorCostMode:'measured',
  directorActualSlots:4,apiSlots:4,directorConditionsMatch:true,qc:'practical',qcCompatibilityHash:p.qcCompatibilityHash,
  qcProcessingTier:'default',qcProductionTasks:5,sourceBurstImageSettings:structuredClone(d.imageSettings),
  productionCalibration:CURRENT_COMPONENTS_ID,...extra};
 return {evidence,config,d,p};
}
const audio=p=>p.tasks.filter(t=>t.lane==='audio').map(({id,kind,duration,deps,resources})=>({id,kind,duration,deps,resources}));
test('published current components retain failed quality, archive uncertainty and no completed video',()=>{
 const f=fixture();assert.equal(qualityBlockedProductionEvidence(f.evidence),f.p);assert.equal(completedProductionEvidence(f.evidence),null);
 assert.equal(f.d.completion,null);assert.equal(f.p.blockingImages,26);assert.equal(f.p.repairImages,36);assert.equal(f.p.QCRequests,146);
 near(f.p.nativeElapsedSeconds,905.328);near(f.p.rootElapsedSeconds,907.047);near(f.p.images.initialWallSeconds,409.2936840057373);
 near(f.p.boundaries.initialQCTailSeconds,128.03232169151306);near(f.p.boundaries.repairQCTailSeconds,18.89706254005432);
 assert.equal(f.p.archive.unknownWrites,1);assert.equal(f.p.freshRenderVerified,false);
 near(f.d.studyBudget.accountedFinalUSD,1.7330712851841146);
 const accounting=f.d.studyBudget.finalAccounting;
 near(accounting.GPUAndDiskConservativeUpperUSD+accounting.APISettledUSD+accounting.CPUCachePreparationConservativeUSD+
  accounting.full48HourModelCacheLeaseReserveUSD+accounting.GoogleStorageAndTransferReserveUSD,f.d.studyBudget.accountedFinalUSD);
 assert.equal(f.d.finalProjectStatus,'REVIEW_REQUIRED');const models=f.d.componentBenchmarks.modelRouting;
 assert.equal(models.rows.length,9);assert.equal(models.enabled,false);assert.equal(models.productionQCNotRunForDiagnostics,true);
 assert.equal(models.noWarmMedianClaim,true);assert.ok(models.rows.every(row=>row.freshSamplerDecode===true));
 assert.equal(f.d.cacheIntegration.nativePoolAutomaticAttachmentWired,false);
 assert.equal(f.d.cacheIntegration.bootstrapVerifiesAndReusesAttachedCache,true);
 assert.equal(f.d.cloudRecovery.archiveRecovered,true);assert.equal(f.d.cloudRecovery.exactProjectRevision,2809);
 assert.equal(f.d.cloudRecovery.freshVideoProduced,false);assert.equal(f.d.cloudRecovery.originalUnknownRowUnchanged,true);
 assert.equal(f.d.cloudRecovery.selectedImageSHA256Matches,110);
 assert.equal(f.d.cloudRecovery.cleanup.status,'VERIFIED_OWNED_DELETED_QUARANTINE_RETAINED');
 assert.equal(f.d.cloudRecovery.cleanup.verifiedOwnedObjectsDeletedOrAbsent,345);
 assert.equal(f.d.cloudRecovery.cleanup.quarantinedUnknownRetained,true);
 assert.equal(f.d.componentBenchmarks.immutableArchiveTransport.liveCorrectnessPassed,true);
 assert.equal(f.d.componentBenchmarks.immutableArchiveTransport.newRecoverySpeedupClaim,false);
});
test('missing proof, unresolved image/API work, wrong tier and fabricated completion reject partial calibration',()=>{
 for(const change of [p=>p.status='COMPLETE',p=>p.qualityAllowsRender=true,p=>p.blockingImages=0,p=>p.pendingRequests=1,p=>p.reservedUSD=.01,
  p=>p.unknownImageOperations=1,p=>p.actualReturnedTiersStandard=false,p=>p.freshVoiceCalls=1,p=>p.voiceEstimateChanged=true,
  p=>delete p.proof.sourceFreezeSHA256,p=>p.lease.policy='stopped-qc-v1',p=>p.lease.holdsGPUForQC=false,p=>p.lease.rentalWindows=2,
  p=>p.lease.shutdownConfirmed=false,p=>p.QC.chapter.includesLaneWait=true,p=>p.QC.repair.effort='medium',
  p=>p.images.initialWallSeconds+=10,p=>p.boundaries.initialQCWallSeconds+=1,p=>p.apiSettledUSD+=.001]){
  const f=fixture();change(f.p);assert.equal(qualityBlockedProductionEvidence(f.evidence),null);
  assert.notEqual(buildPlan(f.config,f.evidence).currentProductionComponentsUsed,true);
 }
});
test('current partial image/QC timings require exact policy, quality, topology and source settings',()=>{
 for(const change of [c=>c.productionCalibration='reference',c=>c.qc='strict',c=>c.directorTier='fast',c=>c.productionRequestSlots=2,
  c=>c.productionDenseQCSlots=3,c=>c.productionDenseMaxRepairRounds=2,c=>c.directorConditionsMatch=false,
  c=>c.qcCompatibilityHash='b'.repeat(64),c=>c.sourceBurstImageSettings.referenceStrength=.5,c=>c.cadence=8]){
  const f=fixture();change(f.config);assert.equal(currentComponentsEligible(f.config,f.evidence),null);
 }
 const f=fixture();assert.equal(currentComponentsEligible({...f.config,productionDenseLeasePolicy:'stopped-qc-v1',productionDenseHoldsGPUForQC:false},f.evidence),null);
});
test('partial components preserve images, all checks, audio and compatible older render tasks',()=>{
 const f=fixture({retries:25}),before=structuredClone(f.evidence),p=buildPlan(f.config,f.evidence),old=buildPlan({...f.config,productionCalibration:'reference'},f.evidence),r=schedule(p);
 assert.equal(p.currentProductionComponentsUsed,true);assert.equal(p.productionCalibrationUsed,false);assert.equal(p.sourceBurstProjectionUsed,false);
 assert.equal(p.imageCount,old.imageCount);assert.equal(p.attemptCount,old.attemptCount);assert.equal(r.qcCount,p.attemptCount);
 assert.deepEqual(audio(p),audio(old));near(audio(buildPlan({...f.config,intro:false},f.evidence)).reduce((n,t)=>n+t.duration,0)/60,12.27555129689205);
 assert.deepEqual(p.tasks.filter(t=>['render','join','probe'].includes(t.kind)),old.tasks.filter(t=>['render','join','probe'].includes(t.kind)));
 assert.equal(p.tasks.filter(t=>t.kind==='archive').length,0);assert.deepEqual(f.evidence,before);assert.deepEqual(r.diagnostics,[]);
 const initial=r.tasks.filter(t=>t.kind==='delivered'&&!t.id.startsWith('repair'));
 assert.ok(Math.max(...initial.filter(t=>t.chapter!==0).map(t=>t.end))<=Math.min(...initial.filter(t=>t.chapter===0).map(t=>t.start)));
 assert.equal(sourceBurstSerialComparison(p,r,{end:r.end*2}).comparisonMatched,false);
 assert.match(p.currentProductionComponentsWarning,/QUALITY-BLOCKED/);assert.match(p.currentProductionComponentsWarning,/finish time remains unproven/);
 assert.equal(p.currentProductionComponents.automaticCacheAttachment,false);assert.match(p.currentProductionComponentsWarning,/automatic native pool attachment is not wired/);
});
test('measured fixed preparation is charged once, and unisolated cold work is conserved inside image delivery',()=>{
 for(const minutes of [60,120]){
  const f=fixture({minutes,retries:10}),p=buildPlan(f.config,f.evidence);
  near(['boot','models','health'].reduce((n,id)=>n+p.tasks.find(t=>t.id===id).duration,0),f.p.startup.seconds);
  assert.equal(p.rentalWindows.length,1);assert.equal(p.tasks.filter(t=>t.kind==='boot').length,1);
  const expected=p.tasks.filter(t=>t.kind==='delivered'&&!t.id.startsWith('repair')).reduce((n,t)=>n+t.images*(t.chapter===0?f.p.images.intro.seconds/5:f.p.images.chapter.seconds/105),0);
  const initial=p.tasks.filter(t=>t.kind==='delivered'&&!t.id.startsWith('repair')).reduce((n,t)=>n+t.duration,0)+p.tasks.find(t=>t.id==='cold').duration;
  near(initial,expected);assert.deepEqual(schedule(p).diagnostics,[]);
 }
 const f=fixture({warmCache:false}),p=buildPlan(f.config,f.evidence);assert.equal(p.currentProductionComponentsUsed,true);
 assert.equal(p.currentProductionComponents.cachedStartupApplied,false);assert.notEqual(p.tasks.find(t=>t.id==='boot').duration,f.p.startup.seconds-.1);
});
test('retained repair groups expose real recheck overlap without adding starts or duplicating repair work',()=>{
 const f=fixture({retries:35,batch:24}),p=buildPlan(f.config,f.evidence),r=schedule(p),repair=p.tasks.filter(t=>t.kind==='delivered'&&t.id.startsWith('repair'));
 assert.ok(repair.length>1);assert.equal(p.tasks.filter(t=>t.kind==='stop').length,1);assert.equal(r.rentalWindows.length,1);
 const repairs=p.attemptCount-p.imageCount;assert.equal(repair.reduce((n,t)=>n+t.images,0),repairs);
 near(repair.reduce((n,t)=>n+t.duration,0),repairs*f.p.images.repair.seconds/36);
 const imageEnd=Math.max(...r.tasks.filter(t=>t.kind==='delivered'&&t.id.startsWith('repair')).map(t=>t.end));
 assert.ok(r.tasks.some(t=>t.kind==='vision'&&t.id.startsWith('repair')&&t.start<imageEnd));
 const initialBoundary=r.tasks.find(t=>t.id==='current-initial-qc-boundary'),firstRepair=r.tasks.find(t=>t.id==='repair1-images');
 assert.ok(firstRepair.start>=initialBoundary.end);const stop=r.tasks.find(t=>t.id==='stop');
 assert.ok(r.tasks.filter(t=>['vision','qcBoundary','delivered'].includes(t.kind)).every(t=>t.end<=stop.start+1e-7));
 assert.ok(r.heldQCIdleSeconds>0);assert.ok(r.heldQCIdleSeconds<=r.gpuIdleSeconds+1e-7);assert.deepEqual(r.diagnostics,[]);
});
test('per-call QC work and cost coexist with completion floors without counting wall time as API work',()=>{
 const f=fixture({retries:10}),p=buildPlan(f.config,f.evidence),r=schedule(p);let cost=0;
 for(const t of p.tasks.filter(t=>t.kind==='vision')){
  const q=t.id.startsWith('repair')?f.p.QC.repair:t.chapter===0?f.p.QC.intro:f.p.QC.chapter;
  near(t.duration,t.images*q.workSeconds/q.checks);cost+=t.images*q.USD/q.checks;
 }
 near(r.qcUSD,cost);assert.ok(p.tasks.filter(t=>t.kind==='qcBoundary').every(t=>!t.images&&Object.keys(t.resources).length===0));
 assert.equal(r.qcCount,p.attemptCount);near(r.totalUSD,r.gpuUSD+r.directorUSD+r.qcUSD+r.storageUSD);
 const manual=buildPlan({...f.config,qcCostManual:true,qcCost:.01},f.evidence);near(schedule(manual).qcUSD,p.attemptCount*.01);
});
test('observed QC tail is a boundary floor, not serial QC wall added after image wall',()=>{
 const f=fixture({retries:0,batch:1}),p=buildPlan(f.config,f.evidence),r=schedule(p);
 const boundary=r.tasks.find(t=>t.id==='current-initial-qc-boundary'),dispatch=r.tasks.find(t=>t.id==='current-initial-dispatch');
 near(boundary.start,dispatch.end);near(boundary.duration,f.p.boundaries.initialQCCompletionSecondsFromImageStart*p.imageCount/110);
 const lastImage=Math.max(...r.tasks.filter(t=>t.kind==='delivered').map(t=>t.end));assert.ok(boundary.start<lastImage);
 assert.ok(boundary.end<lastImage+f.p.boundaries.initialQCWallSeconds*p.imageCount/110);
});
test('Follow selects matching current partial components while imports keep explicit historical scenarios',()=>{
 const f=fixture(),conditions=f.evidence.directorCalibration.profiles.find(p=>p.id==='lean-overlap-standard-20261031').conditions;
 const profile={version:1,name:'Synthetic saved setup',revision:1,generationStrategy:'dense',directorCalibrationProfile:structuredClone(conditions),
  denseImagePhase:{version:1,supported:true,eligible:true,allPlansFirst:true,generationTasks:5,qcTaskSlots:4,descriptorLimit:8192,maxRepairRounds:1,leasePolicy:'retained-qc-v1',holdsGPUForQC:true},
  settings:{unattendedProduction:true,automaticRepair:true,voice:'am_michael',voiceEngine:'kokoro-82m',qcCheckLevel:'practical',
   video:{width:1280,height:720,fps:30},image:structuredClone(f.d.imageSettings),director:{provider:'openai-luna',executionMode:'staged-lean',parallelism:4,processingTier:'default',reasoning:'Balanced',visualPacking:'small',sourceVisualOverlap:true}},
  qcCompatibilityHash:f.p.qcCompatibilityHash,qcProcessingTier:'default',imageTasks:5,
  imageExecution:{backend:'comfyui-api',gpuLanes:2,modelCopies:2,requestSlots:4,requiredGPU:'NVIDIA GeForce RTX 5090'},
  phaseOverlap:{audioLookahead:true,chapterRender:true},renderExecution:{backend:'native'},intro:{enabled:true,duration:30},estimates:{}};
 const followed=plannerSettings({...DEFAULTS,productionCalibration:'reference'},profile,f.evidence);
 assert.equal(followed.config.productionCalibration,CURRENT_COMPONENTS_ID);assert.match(followed.warnings.join(' '),/26 blockers/);
 assert.equal(buildPlan(followed.config,f.evidence).currentProductionComponentsUsed,true);
 const imported=importSnapshot({type:'studio-pipeline-plan',version:1,config:{...f.config,productionCalibration:'reference'}},f.evidence);
 assert.equal(imported.config.productionCalibration,'reference');assert.notEqual(buildPlan(imported.config,f.evidence).currentProductionComponentsUsed,true);
});
