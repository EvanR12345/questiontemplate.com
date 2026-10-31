// A completed saved-story observation is distinct from a warm GPU benchmark.
// This module is pure: it never runs a provider, changes a project or buys work.
import {qualityBlockedProductionEvidence} from './pipeline-production-components.mjs?v=production-components-20261031';
export const PRODUCTION_CALIBRATION_ID='current-production-20261031';
const hash=x=>typeof x==='string'&&/^[a-f0-9]{64}$/.test(x);
const positive=x=>typeof x==='number'&&Number.isFinite(x)&&x>0;
const nonnegative=x=>typeof x==='number'&&Number.isFinite(x)&&x>=0;
const count=x=>Number.isSafeInteger(x)&&x>0;
const near=(a,b)=>Math.abs(a-b)<=1e-7*Math.max(1,Math.abs(a),Math.abs(b));
const imageKeys=['provider','model','workflow','steps','guidance','sampler','scheduler','width','height','referenceStrength'];
const imageSettings={provider:'comfyui',model:'flux2-klein-4b',workflow:'flux2-klein-4b-auto',steps:4,guidance:1,sampler:'euler',scheduler:'flux2',width:1280,height:720,referenceStrength:.65};
const closed=x=>x&&positive(x.seconds)&&nonnegative(x.fixedSeconds)&&x.fixedSeconds<=x.seconds&&hash(x.evidenceSHA256);
const imageCohort=x=>closed(x)&&x.fixedSeconds<x.seconds&&count(x.images)&&x.freshEncodingVerified===true&&x.freshInferenceVerified===true&&x.includesCold===true&&x.includesLocalDelivery===true;
const reviewCohort=x=>x&&count(x.checks)&&positive(x.workSeconds)&&x.workSeconds/x.checks<=3600&&nonnegative(x.USD)&&x.USD/x.checks<=1&&x.includesLaneWait===false&&x.processingTier==='default'&&hash(x.evidenceSHA256);

export function completedProductionEvidence(evidence){
 const d=evidence?.productionCalibration,c=d?.completion;
 if(d?.version!==1||d.id!==PRODUCTION_CALIBRATION_ID||d.status!=='COMPLETE'||!c||
    c.rootStatus!=='COMPLETE'||c.nativeStatus!=='COMPLETE'||c.productionStatus!=='COMPLETE'||
    c.qualityAllowsRender!==true||c.blockingImages!==0||c.unknownRequests!==0||c.pendingRequests!==0||c.reservedUSD!==0||
    c.actualReturnedTiersStandard!==true||c.freshVoiceCalls!==0||c.voiceEstimateChanged!==false||
    !hash(c.rootResultSHA256)||!hash(c.nativeResultSHA256)||!hash(c.sourceFreezeSHA256)||
    !positive(d.sourceNarrationSeconds)||!positive(d.introSeconds)||!count(d.sourceChapters)||
    !positive(d.inputCadencePerMinute)||!hash(d.qcCompatibilityHash)||
    !nonnegative(c.apiSettledUSD)||c.apiSettledUSD>.7||
    !count(c.initialChapterImages)||!count(c.introImages)||c.selectedImages!==c.initialChapterImages+c.introImages||
    !Number.isSafeInteger(c.repairImages)||c.repairImages<0)return null;
 const a=c.archive,r=c.render,i=c.images,q=c.QC,director=d.components?.director,lease=c.lease;
 if(!lease||!hash(lease.evidenceSHA256)||lease.shutdownConfirmed!==true||
    !(lease.policy==='retained-qc-v1'&&lease.holdsGPUForQC===true&&lease.rentalWindows===1||
      lease.policy==='stopped-qc-v1'&&lease.holdsGPUForQC===false&&lease.rentalWindows===(c.repairImages?2:1)))return null;
 if(director?.status!=='COMPLETE'||director.actualReturnedTiersStandard!==true||director.pendingRequests!==0||director.reservedUSD!==0||
    !positive(director.seconds)||!nonnegative(director.USD)||!count(director.calls)||
    !hash(director.evidenceSHA256)||!hash(director.resultSHA256)||
    director.acceptedChapterShots!==c.initialChapterImages||director.introShots!==c.introImages)return null;
 if(a?.exactFinalRevisionVerified!==true||a.selectedImagesVerified!==c.selectedImages||
    !Number.isSafeInteger(a.finalRevision)||a.finalRevision<1||!/^\d+$/.test(a.finalGeneration||'')||
    !hash(a.finalVideoSHA256)||!hash(a.manifestSHA256)||a.activeWriters!==0||a.unknownWrites!==0||
    a.timingScope!=='post-native-critical-tail'||!closed(a.tail)||!positive(a.outputSeconds)||
    !nonnegative(a.costUSD)||a.costUSD>.04||!['usage-priced-estimate','invoice'].includes(a.costBasis)||
    a.storageClass!=='STANDARD'||a.uploadWorkers!==2)return null;
 if(r?.backend!=='native'||r.freshRenderVerified!==true||r.originalAudioVerified!==true||
    !closed(r.chapter)||r.chapter.fixedSeconds>=r.chapter.seconds||!positive(r.chapter.videoSeconds)||r.chapter.chapters!==d.sourceChapters||
    !closed(r.intro)||!positive(r.intro.videoSeconds)||!['cpu','native'].includes(r.intro.backend)||!closed(r.assembly)||!positive(r.assembly.videoSeconds)||
    !closed(r.probe)||!positive(r.probe.videoSeconds)||r.chapter.includesFinalAssembly!==false||
    !['direct-concat-original-audio-v1','legacy-two-pass-v1'].includes(r.assembly.policy))return null;
 if(!imageCohort(i?.chapter)||i.chapter.images!==c.initialChapterImages||
    !imageCohort(i.intro)||i.intro.images!==c.introImages||
    i.cohortTimeBasis!=='nonoverlapping-wall-allocation'||!positive(i.initialWallSeconds)||
    !near(i.chapter.seconds+i.intro.seconds,i.initialWallSeconds)||
    !closed(i.setup)||i.setup.cachedWeightsVerified!==true||i.setup.modelCopies!==2||i.setup.requestSlots!==4||
    i.setup.includesCold!==false||i.setup.scope!=='boot-models-health'||
    !reviewCohort(q?.chapter)||q.chapter.checks!==c.initialChapterImages||
    !reviewCohort(q.intro)||q.intro.checks!==c.introImages)return null;
 if(!near(r.chapter.videoSeconds,d.sourceNarrationSeconds)||!near(r.intro.videoSeconds,d.introSeconds)||
    !near(r.assembly.videoSeconds,d.sourceNarrationSeconds+d.introSeconds)||
    !near(r.probe.videoSeconds,r.assembly.videoSeconds)||!near(a.outputSeconds,r.assembly.videoSeconds))return null;
 if(c.repairImages&&(!imageCohort(i.repair)||i.repair.images!==c.repairImages||
    lease.policy==='stopped-qc-v1'&&(!closed(i.repairSetup)||i.repairSetup.includesCold!==false)||
    !reviewCohort(q.repair)||q.repair.checks!==c.repairImages))return null;
 if(director.USD+q.chapter.USD+q.intro.USD+(c.repairImages?q.repair.USD:0)>c.apiSettledUSD+1e-9)return null;
 return d;
}

export function currentProductionEligible(plan,evidence){
 const d=completedProductionEvidence(evidence),c=plan.config;
 if(!d||plan.prewarm||c.productionCalibration!==d.id||c.policy!=='current'||c.productionGenerationStrategy!=='dense'||
    c.productionDenseVersion!==1||!c.productionDenseSupported||!c.productionDenseEligible||
    c.productionDenseLeasePolicy!==d.completion.lease.policy||c.productionDenseHoldsGPUForQC!==d.completion.lease.holdsGPUForQC||
    c.gpu!=='5090'||c.resolution!=='720p'||c.encodingProfile!=='fresh'||c.executionMode!=='hybrid'||
    String(c.imageWorkers)!=='4'||c.productionModelCopies!==2||c.productionRequestSlots!==4||
    c.productionDenseGenerationTasks!==5||c.productionDenseQCSlots!==4||c.productionDenseMaxRepairRounds!==1||c.qcEffectiveSlots!==4||
    c.qc!=='practical'||c.directorTier!=='default'||c.qcProcessingTier!=='default'||c.warmCache!==true||
    c.videoRenderer!=='native'||c.videoResolution!=='720p'||c.videoFps!==30||
    c.fullAssemblyPolicy!==d.completion.render.assembly.policy||
    c.directorProfile!=='lean-overlap-standard-20261031'||c.directorConditionsMatch!==true||
    c.qcCompatibilityHash!==d.qcCompatibilityHash||!hash(d.qcCompatibilityHash)||
    !Number.isFinite(c.cadence)||Math.abs(c.cadence-d.inputCadencePerMinute)>1e-12||
    !imageKeys.every(k=>d.imageSettings?.[k]===imageSettings[k]&&c.sourceBurstImageSettings?.[k]===imageSettings[k])||
    c.intro&&c.introImages!==d.completion.introImages)return null;
 if(c.retries>0&&(!d.completion.repairImages||!imageCohort(d.completion.images.repair)||!reviewCohort(d.completion.QC.repair)))return null;
 return d;
}

export function applyCurrentProductionCalibration(plan,evidence){
 const d=currentProductionEligible(plan,evidence);
 if(!d)return plan;
 const c=d.completion,tasks=plan.tasks.map(t=>({...t,deps:[...t.deps]}));
 const groups=tasks.filter(t=>t.kind==='delivered');
 if(!groups.length)return plan;
 // Each rental owns a fixed startup envelope. Tiny positive chart allocations
 // keep the existing dependency graph while conserving the measured total.
 for(const window of plan.rentalWindows){
  const prefix=window.phase==='repair'?window.boot.replace(/boot$/,''):'';
  const setup=window.phase==='repair'?c.images.repairSetup:c.images.setup;
  const boot=tasks.find(t=>t.id===prefix+'boot'),models=tasks.find(t=>t.id===prefix+'models'),health=tasks.find(t=>t.id===prefix+'health');
  if(!boot||!models||!health||setup.seconds<=.1)return plan;
  boot.duration=setup.seconds-.1;models.duration=.05;health.duration=.05;
  for(const t of [boot,models,health])t.basis=`One observed cached startup envelope: ${setup.seconds.toFixed(3)}s, including boot, weight verification and two-copy readiness. Positive chart allocations sum to that envelope once. Cold inference is separate.`;
 }
 for(const t of groups){
  const cohort=t.id.startsWith('repair')?c.images.repair:t.chapter===0?c.images.intro:c.images.chapter;
  if(!cohort)return plan;
  t.duration=t.images*(cohort.seconds-cohort.fixedSeconds)/cohort.images;
  if(t.id.startsWith('repair')&&c.lease.holdsGPUForQC)t.duration+=cohort.fixedSeconds;
  t.basis=`One completed saved-story sample: ${cohort.images} ${t.id.startsWith('repair')?'repair':t.chapter===0?'intro 1344×768':'chapter 720p'} images. Variable delivery work ${(cohort.seconds-cohort.fixedSeconds).toFixed(3)}s excludes the fixed cold allowance, which ${t.id.startsWith('repair')&&c.lease.holdsGPUForQC?'stays in this repair group on the retained lease':'is counted separately once per rental'}. Preparation, encoding, inference, retrieval and local save are included once. Other stories remain projections.`;
 }
 for(const window of plan.rentalWindows){
  const prefix=window.phase==='repair'?window.boot.replace(/boot$/,''):'';
  const cold=tasks.find(t=>t.id===prefix+'cold'),own=groups.filter(t=>prefix?t.id.startsWith(prefix):!t.id.startsWith('repair'));
  const fixed=window.phase==='repair'?c.images.repair.fixedSeconds:c.images.chapter.fixedSeconds+c.images.intro.fixedSeconds;
  if(!cold||!own.length)return plan;
  // A receipt can have zero separately isolated loading work. Keep any
  // unseparated work inside its source rate, without adding it again here.
  const allocated=fixed||Math.min(.05,own[0].duration/2);
  if(!positive(allocated)||!fixed&&own[0].duration<=allocated)return plan;
  cold.duration=allocated;
  if(!fixed)own[0].duration-=allocated;
  cold.basis=fixed?`${fixed.toFixed(3)}s measured fixed loading inside the source image cohorts, removed from their variable rates and counted once per rental.`:'No isolated loading estimate: any unseparated work remains in the source-cohort rate. This positive chart allocation is subtracted from the first delivery group, adding no extra work.';
 }
 for(const t of tasks.filter(t=>t.kind==='vision')){
  const q=t.id.startsWith('repair')?c.QC.repair:t.chapter===0?c.QC.intro:c.QC.chapter;
  t.duration=t.images*q.workSeconds/q.checks;
  t.productionQCUSD=t.images*(plan.config.qcCostManual===true?plan.config.qcCost:q.USD/q.checks);
  t.basis=`Completed-source ${t.id.startsWith('repair')?'repair recheck':'initial review'} API work: ${q.workSeconds.toFixed(3)}s / ${q.checks} calls, excluding lane wait. Shared API slots supply overlap once. ${plan.config.qcCostManual?'Your manual per-check price is retained.':'Usage-derived check cost; no duplicate batch-wall multiplier.'}`;
 }
 let fixedRenderAdded=false;
 for(const t of tasks.filter(t=>t.kind==='render')){
  const r=t.chapter===0?c.render.intro:c.render.chapter;
  t.duration=t.videoSeconds*(r.seconds-r.fixedSeconds)/r.videoSeconds+(t.chapter===0||!fixedRenderAdded?r.fixedSeconds:0);
  if(t.chapter!==0)fixedRenderAdded=true;
  t.basis=`Actual ${t.chapter===0?'intro':'native chapter'} export component from one complete story. Final assembly is excluded here and counted only in Join; long-story variable work is extrapolated. ${t.chapter===0?'Intro retains its measured '+r.backend+' path.':'Original native quality/encoder settings are retained.'}`;
 }
 for(const [id,cohort]of [['join',c.render.assembly],['probe',c.render.probe]]){
  const task=tasks.find(t=>t.id===id);
  if(!task)return plan;
  task.duration=cohort.fixedSeconds+(cohort.seconds-cohort.fixedSeconds)*plan.config.minutes*60/cohort.videoSeconds;
  task.basis=`Completed-source ${id} component, with explicit fixed work once and variable work scaled by output seconds. Counted separately from chapter exports.`;
 }
 const archiveSeconds=c.archive.tail.fixedSeconds+(c.archive.tail.seconds-c.archive.tail.fixedSeconds)*plan.config.minutes*60/c.archive.outputSeconds;
 tasks.push({id:'archive-final',kind:'archive',chapter:0,name:'Confirm final cloud revision',lane:'finish',duration:archiveSeconds,
  deps:[tasks.some(t=>t.id==='decode')?'decode':'probe'],resources:{network:1,disk:.1},priority:tasks.length,
  basis:'Only the observed remaining post-native critical tail: drain, exact final revision, manifest and final-video proofs. Concurrent image/video uploads are already included in production and are not added again.'});
 const warning=`One complete ${((d.sourceNarrationSeconds+d.introSeconds)/60).toFixed(1)}-minute saved-story observation; narration was reused. Two-hour completion is a scheduling projection with the unchanged voice allowance, not a measured two-hour run or paired speedup. Source required ${c.repairImages} repairs; the selected ${plan.config.retries}% allowance is a scenario, not a predicted defect rate.`;
 return {...plan,tasks,productionCalibrationUsed:true,sourceBurstProjectionUsed:false,
  productionCalibrationWarning:warning,productionCalibration:{id:d.id,samples:1,rootResultSHA256:c.rootResultSHA256,
   initialSecondsPerImage:(c.images.chapter.seconds-c.images.chapter.fixedSeconds)/c.images.chapter.images,
   archiveCostBasis:c.archive.costBasis,leasePolicy:c.lease.policy},
  productionArchiveUSD:c.archive.costUSD*plan.config.minutes*60/c.archive.outputSeconds,
  renderScope:plan.renderScope+' '+warning};
}

export function productionCalibrationSummary(evidence,plan){
 const d=evidence?.productionCalibration;
 if(d?.version!==1||d.id!==PRODUCTION_CALIBRATION_ID)return 'Current production calibration is unavailable. Existing scenarios remain in place.';
 const director=d.components?.director,setup=d.components?.cachedSetup,partial=qualityBlockedProductionEvidence(evidence);
 const parts=[];
 if(director?.status==='COMPLETE'&&positive(director.seconds)&&nonnegative(director.USD))parts.push(`Current directing: ${director.seconds.toFixed(3)}s, $${director.USD.toFixed(6)}, ${director.calls} settled Standard calls for 17.5 minutes of saved narration.`);
 if(!partial&&setup?.status==='OBSERVED'&&positive(setup.seconds))parts.push(`Cached worker preparation: ${setup.seconds.toFixed(3)}s; this component excludes any later resume, additional process or inference loading.`);
 if(partial?.status==='QUALITY_BLOCKED')parts.push(`Fresh production stopped at its quality gate after ${partial.rootElapsedSeconds.toFixed(3)}s: 110 initial images, 36 repairs and 146 settled checks left 26 blockers. Cached replacement preparation to both workers ready was ${partial.startup.seconds.toFixed(3)}s, once; initial images took ${partial.images.initialWallSeconds.toFixed(3)}s. Initial QC overlapped generation and retained a ${partial.boundaries.initialQCTailSeconds.toFixed(3)}s tail. No fresh video exists. Later project/image archive recovery is a separate result and cannot establish completed video time.`);
 if(plan?.currentProductionComponentsUsed)parts.push(plan.currentProductionComponentsWarning);
 else if(plan?.productionCalibrationUsed)parts.push(plan.productionCalibrationWarning);
 else if(completedProductionEvidence(evidence))parts.push(plan?.config?.productionCalibration===d.id?'The selected current calibration does not match this setup; the retained schedule uses its stated reference measurements.':'Complete image/QC/render/archive evidence is available for an explicit matching cached two-copy scenario. Existing manual and historical scenarios are retained.');
 else parts.push('Image/QC/render/archive completion is not established. These components do not establish an end-to-end video time; the retained schedule uses its separately stated references.');
 parts.push('The 12.3-minute narration estimate is unchanged.');
 return parts.join(' ');
}
