import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DEFAULTS,buildPlan,schedule,importSnapshot} from './pipeline-engine.mjs';
import {matchedDirectorProfile} from './pipeline-director-matched.mjs';
import {completedProductionEvidence,currentProductionEligible,productionCalibrationSummary} from './pipeline-production-calibration.mjs';
const read=name=>JSON.parse(fs.readFileSync(new URL(name,import.meta.url),'utf8'));
const hash='a'.repeat(64),near=(a,b)=>assert.ok(Math.abs(a-b)<1e-7,`${a} != ${b}`);
const timing=(seconds,fixedSeconds=0,extra={})=>({seconds,fixedSeconds,evidenceSHA256:hash,...extra});
const image=(images,seconds,fixedSeconds)=>timing(seconds,fixedSeconds,{images,freshEncodingVerified:true,freshInferenceVerified:true,includesCold:true,includesLocalDelivery:true});
const review=(checks,workSeconds,USD)=>({checks,workSeconds,USD,includesLaneWait:false,processingTier:'default',evidenceSHA256:hash});
function fixture(extra={}){
 const evidence=read('./pipeline-evidence.json');
 for(const [key,name]of [['concurrency','pipeline-concurrency.json'],['directorCalibration','pipeline-director-calibration.json'],['qcCalibration','pipeline-qc-calibration.json'],['productionCalibration','pipeline-production-calibration.json']])evidence[key]=read('./'+name);
 const d=evidence.productionCalibration;d.status='COMPLETE';d.qcCompatibilityHash=evidence.qcCalibration.sourceBurstProjection.qcCompatibilityHash;
 d.completion={rootStatus:'COMPLETE',nativeStatus:'COMPLETE',productionStatus:'COMPLETE',qualityAllowsRender:true,blockingImages:0,
  unknownRequests:0,pendingRequests:0,reservedUSD:0,apiSettledUSD:.09,actualReturnedTiersStandard:true,freshVoiceCalls:0,voiceEstimateChanged:false,
  rootResultSHA256:hash,nativeResultSHA256:hash,sourceFreezeSHA256:hash,initialChapterImages:105,introImages:5,selectedImages:110,repairImages:11,
  lease:{policy:'stopped-qc-v1',holdsGPUForQC:false,rentalWindows:2,shutdownConfirmed:true,evidenceSHA256:hash},
  images:{cohortTimeBasis:'nonoverlapping-wall-allocation',initialWallSeconds:228,chapter:image(105,215,5),intro:image(5,13,3),repair:image(11,27,5),
   setup:timing(75.405,0,{cachedWeightsVerified:true,modelCopies:2,requestSlots:4,includesCold:false,scope:'boot-models-health'}),
   repairSetup:timing(30,0,{includesCold:false})},
  QC:{chapter:review(105,1260,.035),intro:review(5,50,.002),repair:review(11,165,.005)},
  render:{backend:'native',freshRenderVerified:true,originalAudioVerified:true,
   chapter:timing(110,5,{videoSeconds:1050.23,chapters:2,includesFinalAssembly:false}),
   intro:timing(8,1,{videoSeconds:30,backend:'cpu'}),assembly:timing(5,.1,{videoSeconds:1080.23,policy:'legacy-two-pass-v1'}),
   probe:timing(2,.1,{videoSeconds:1080.23})},
  archive:{exactFinalRevisionVerified:true,selectedImagesVerified:110,finalRevision:500,finalGeneration:'123456',
   finalVideoSHA256:hash,manifestSHA256:hash,activeWriters:0,unknownWrites:0,timingScope:'post-native-critical-tail',
   tail:timing(4,1),outputSeconds:1080.23,costUSD:.001,costBasis:'usage-priced-estimate',storageClass:'STANDARD',uploadWorkers:2}};
 const config={...DEFAULTS,policy:'current',productionGenerationStrategy:'dense',productionDenseVersion:1,
  productionDenseSupported:true,productionDenseEligible:true,productionDenseGenerationTasks:5,productionDenseQCSlots:4,
  productionDenseDescriptorLimit:8192,productionDenseMaxRepairRounds:1,readyChapters:14,
  productionModelCopies:2,productionRequestSlots:4,productionRenderOverlap:true,productionAudioLookahead:true,
  resolution:'720p',encodingProfile:'fresh',executionMode:'hybrid',imageWorkers:'4',warmCache:true,intro:false,
  videoRenderer:'native',fullAssemblyPolicy:'legacy-two-pass-v1',directorProfile:'lean-overlap-standard-20261031',
  directorCostMode:'measured',directorActualSlots:4,apiSlots:4,directorConditionsMatch:true,qc:'practical',
  qcCompatibilityHash:d.qcCompatibilityHash,qcProcessingTier:'default',qcProductionTasks:5,
  sourceBurstImageSettings:structuredClone(d.imageSettings),productionCalibration:d.id,...extra};
 return {evidence,config,d};
}
const voices=p=>p.tasks.filter(t=>t.lane==='audio').map(({id,kind,duration,deps,resources})=>({id,kind,duration,deps,resources}));

test('published observations are components only and cannot become a completed forecast',()=>{
 const data=read('./pipeline-production-calibration.json');
 assert.equal(data.status,'QUALITY_BLOCKED_COMPONENTS_ONLY');assert.equal(completedProductionEvidence({productionCalibration:data}),null);
 assert.equal(data.components.director.seconds,309.922);assert.equal(data.components.director.USD,.044775205);
 const note=productionCalibrationSummary({productionCalibration:data});
 assert.match(note,/309\.922s/);assert.match(note,/135\.361s/);assert.match(note,/completion is not established/);assert.match(note,/12\.3/);
});
test('failed or stale render metadata, quality blockers and unresolved charges cannot qualify',()=>{
 for(const change of [d=>d.status='RUNNING',d=>d.completion.rootStatus='STOPPED_RETAINED',d=>d.completion.nativeStatus='STOPPED_NO_REPLAY',
  d=>d.completion.productionStatus='RUNNING',d=>d.completion.qualityAllowsRender=false,d=>d.completion.blockingImages=1,
  d=>d.completion.selectedImages=0,d=>d.completion.unknownRequests=1,d=>d.completion.pendingRequests=1,d=>d.completion.reservedUSD=.01,
  d=>d.completion.actualReturnedTiersStandard=false,d=>d.completion.render.freshRenderVerified=false,
  d=>delete d.completion.rootResultSHA256,d=>d.completion.archive.exactFinalRevisionVerified=false,
  d=>d.completion.archive.selectedImagesVerified=109,d=>d.completion.archive.activeWriters=1,d=>d.completion.archive.unknownWrites=1,
  d=>delete d.completion.archive.finalVideoSHA256,d=>d.completion.archive.storageClass='ARCHIVE']){
  const f=fixture();change(f.d);assert.equal(completedProductionEvidence(f.evidence),null);
  assert.notEqual(buildPlan(f.config,f.evidence).productionCalibrationUsed,true);
 }
});
test('incomplete timing, mixed cohorts and lane-wait QC measurements fail closed',()=>{
 for(const change of [d=>d.completion.images.chapter.fixedSeconds=215,d=>d.completion.images.chapter.seconds=Infinity,
  d=>d.completion.images.intro.images=6,d=>d.completion.images.setup.includesCold=true,d=>d.completion.images.setup.modelCopies=1,
  d=>d.completion.images.initialWallSeconds=225,d=>d.completion.images.cohortTimeBasis='summed-overlapping-spans',
  d=>d.completion.QC.chapter.includesLaneWait=true,d=>d.completion.QC.repair.checks=10,
  d=>d.completion.render.chapter.includesFinalAssembly=true,d=>d.completion.render.assembly.videoSeconds=1050.23,
  d=>d.completion.archive.timingScope='all-uploads-summed']){
  const f=fixture();change(f.d);assert.equal(completedProductionEvidence(f.evidence),null);
 }
});
test('exact compatible opt-in uses the new path once and preserves narration, counts and graph',()=>{
 const f=fixture(),before=structuredClone(f.evidence),reference=structuredClone(f.evidence);delete reference.productionCalibration;delete reference.qcCalibration.sourceBurstProjection;
 const old=buildPlan(f.config,reference),p=buildPlan(f.config,f.evidence),r=schedule(p);
 assert.equal(p.productionCalibrationUsed,true);assert.equal(p.sourceBurstProjectionUsed,false);assert.equal(p.imageCount,707);
 assert.deepEqual(voices(p),voices(old));near(voices(p).reduce((n,t)=>n+t.duration,0)/60,12.27555129689205);
 assert.deepEqual(p.tasks.filter(t=>t.kind!=='archive').map(({id,deps,resources,images})=>({id,deps,resources,images})),old.tasks.map(({id,deps,resources,images})=>({id,deps,resources,images})));
 assert.equal(r.qcCount,p.imageCount);assert.equal(p.tasks.filter(t=>t.kind==='archive').length,1);assert.deepEqual(r.diagnostics,[]);assert.deepEqual(f.evidence,before);
});
test('manual and historical scenarios retain their chosen references and IDs',()=>{
 const f=fixture({productionCalibration:'reference'}),p=buildPlan(f.config,f.evidence);
 assert.notEqual(p.productionCalibrationUsed,true);assert.equal(p.sourceBurstProjectionUsed,true);
 const imported=importSnapshot({type:'studio-pipeline-plan',version:1,config:f.config,preferences:{'c1-voice':{notBefore:500}}},f.evidence);
 assert.equal(imported.config.productionCalibration,'reference');assert.equal(imported.preferences['c1-voice'].notBefore,500);
 const old=buildPlan({...f.config,directorProfile:'lean-overlap-standard'},f.evidence);assert.equal(old.director.id,'lean-overlap-standard');
});
test('cached setup, policy, encoding, effort compatibility and native assembly must match',()=>{
 for(const change of [c=>c.warmCache=false,c=>c.qc='strict',c=>c.encodingProfile='cached',c=>c.directorConditionsMatch=false,
  c=>c.directorProfile='lean-overlap-standard',c=>c.videoRenderer='cpu',c=>c.fullAssemblyPolicy='direct-concat-original-audio-v1',
  c=>c.qcCompatibilityHash='b'.repeat(64),c=>c.sourceBurstImageSettings.steps=8,c=>c.cadence=8,
  c=>c.productionDenseMaxRepairRounds=2,c=>c.productionDenseQCSlots=3]){
  const f=fixture();change(f.config);assert.notEqual(buildPlan(f.config,f.evidence).productionCalibrationUsed,true);
 }
});
test('completion evidence must prove the same lease policy and confirmed shutdown',()=>{
 for(const change of [d=>delete d.completion.lease,d=>d.completion.lease.policy='unknown',
  d=>d.completion.lease.holdsGPUForQC=true,d=>d.completion.lease.rentalWindows=1,
  d=>d.completion.lease.shutdownConfirmed=false,d=>delete d.completion.lease.evidenceSHA256]){
  const f=fixture();change(f.d);assert.equal(completedProductionEvidence(f.evidence),null);
  assert.notEqual(buildPlan(f.config,f.evidence).productionCalibrationUsed,true);
 }
 const f=fixture({productionDenseLeasePolicy:'retained-qc-v1',productionDenseHoldsGPUForQC:true});
 assert.notEqual(buildPlan(f.config,f.evidence).productionCalibrationUsed,true);
 assert.notEqual(buildPlan(f.config,f.evidence).sourceBurstProjectionUsed,true);
});
test('a proven retained lease uses one startup and keeps repair loading inside that lease',()=>{
 const f=fixture({productionDenseLeasePolicy:'retained-qc-v1',productionDenseHoldsGPUForQC:true,retries:10});
 f.d.completion.lease={policy:'retained-qc-v1',holdsGPUForQC:true,rentalWindows:1,shutdownConfirmed:true,evidenceSHA256:hash};
 delete f.d.completion.images.repairSetup;
 assert.equal(completedProductionEvidence(f.evidence),f.d);
 const p=buildPlan(f.config,f.evidence),r=schedule(p),repair=p.tasks.find(t=>t.id==='repair1-images');
 assert.equal(p.productionCalibrationUsed,true);assert.equal(p.productionCalibration.leasePolicy,'retained-qc-v1');
 assert.equal(p.rentalWindows.length,1);assert.equal(p.tasks.filter(t=>t.kind==='boot').length,1);
 near(repair.duration,repair.images*2+5);assert.deepEqual(r.diagnostics,[]);assert.ok(r.heldQCIdleSeconds>0);
 const old=buildPlan({...f.config,productionCalibration:'reference'},f.evidence);assert.deepEqual(voices(p),voices(old));
 f.d.completion.lease.rentalWindows=2;assert.equal(completedProductionEvidence(f.evidence),null);
});
test('fixed startup and loading are conserved once per rental rather than multiplied by pictures',()=>{
 for(const minutes of [60,120]){
  const f=fixture({minutes,retries:10}),p=buildPlan(f.config,f.evidence);
  for(const [prefix,seconds,cold]of [['',75.405,8],['repair1-',30,5]]){
   near(['boot','models','health'].reduce((n,id)=>n+p.tasks.find(t=>t.id===prefix+id).duration,0),seconds);
   near(p.tasks.find(t=>t.id===prefix+'cold').duration,cold);
  }
  const images=p.tasks.filter(t=>t.kind==='delivered'&&!t.id.startsWith('repair'));
  near(images.reduce((n,t)=>n+t.duration,0),p.imageCount*2);
  assert.deepEqual(schedule(p).diagnostics,[]);
 }
});
test('already-warm loading chart allocation subtracts exactly the same work from delivery',()=>{
 const f=fixture();for(const name of ['chapter','intro'])f.d.completion.images[name].fixedSeconds=0;
 const p=buildPlan(f.config,f.evidence),work=p.tasks.filter(t=>t.kind==='delivered').reduce((n,t)=>n+t.duration,0)+p.tasks.find(t=>t.id==='cold').duration;
 near(work,p.imageCount*215/105);assert.ok(p.tasks.find(t=>t.id==='cold').duration>0);
});
test('fully fixed startup and archive components remain fixed when output length changes',()=>{
 for(const minutes of [60,120]){
  const f=fixture({minutes});f.d.completion.images.setup.fixedSeconds=75.405;f.d.completion.archive.tail.fixedSeconds=4;
  const p=buildPlan(f.config,f.evidence);assert.equal(p.productionCalibrationUsed,true);
  near(p.tasks.find(t=>t.id==='archive-final').duration,4);
  near(['boot','models','health'].reduce((n,id)=>n+p.tasks.find(t=>t.id===id).duration,0),75.405);
 }
});
test('initial, intro and repair QC use API work and their own costs with shared slots once',()=>{
 const f=fixture({intro:true,retries:10}),p=buildPlan(f.config,f.evidence),r=schedule(p);
 let expected=0;
 for(const t of p.tasks.filter(t=>t.kind==='vision')){
  const q=t.id.startsWith('repair')?f.d.completion.QC.repair:t.chapter===0?f.d.completion.QC.intro:f.d.completion.QC.chapter;
  near(t.duration,t.images*q.workSeconds/q.checks);expected+=t.images*q.USD/q.checks;assert.equal(t.resources.api,1);
 }
 near(r.qcUSD,expected);assert.equal(p.capacities.api,4);assert.deepEqual(r.diagnostics,[]);
});
test('explicit manual QC price applies to every review without changing its measured work',()=>{
 const a=fixture({retries:10}),b=fixture({retries:10,qcCostManual:true,qcCost:.01});
 const p=buildPlan(a.config,a.evidence),manual=buildPlan(b.config,b.evidence),r=schedule(manual);
 assert.deepEqual(manual.tasks.filter(t=>t.kind==='vision').map(t=>t.duration),p.tasks.filter(t=>t.kind==='vision').map(t=>t.duration));near(r.qcUSD,r.qcCount*.01);
});
test('archive contributes only one post-native critical tail and a separate priced cost',()=>{
 const f=fixture(),p=buildPlan(f.config,f.evidence),r=schedule(p),tail=p.tasks.find(t=>t.id==='archive-final');
 assert.deepEqual(tail.deps,['probe']);near(tail.duration,1+3*7200/1080.23);near(r.archiveUSD,.001*7200/1080.23);
 near(r.totalUSD,r.gpuUSD+r.directorUSD+r.qcUSD+r.archiveUSD+r.storageUSD);
 const b=fixture();b.d.completion.archive.tail.seconds+=10;near(schedule(buildPlan(b.config,b.evidence)).end-r.end,10*7200/1080.23);
});
test('native exports and final assembly are separate with fixed chapter work only once',()=>{
 const f=fixture(),p=buildPlan(f.config,f.evidence),renders=p.tasks.filter(t=>t.kind==='render');
 near(renders.reduce((n,t)=>n+t.duration,0),7200*105/1050.23+5);
 near(p.tasks.find(t=>t.id==='join').duration,.1+4.9*7200/1080.23);
});
test('Follow may select a newer exact director component while explicit older IDs remain valid',()=>{
 const f=fixture(),latest=f.evidence.directorCalibration.profiles.find(p=>p.id==='lean-overlap-standard-20261031');
 const profile={directorCalibrationProfile:structuredClone(latest.conditions)};
 assert.equal(matchedDirectorProfile(f.config,profile,f.evidence),latest);
 assert.equal(buildPlan({...f.config,directorProfile:'lean-overlap-standard'},f.evidence).director.measuredSeconds,322.73399999993853);
 const changed=structuredClone(latest);changed.id='unmatched-new';changed.observedAt+=1;changed.conditions.reasoning='different';f.evidence.directorCalibration.profiles.push(changed);
 assert.equal(matchedDirectorProfile(f.config,profile,f.evidence),latest);
});
