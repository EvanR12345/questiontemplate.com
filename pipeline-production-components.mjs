// Source components remain useful even when the source cannot pass its quality
// gate. This path never represents a completed video or waives that gate.
export const CURRENT_COMPONENTS_ID='current-components-20261031';
const hash=x=>typeof x==='string'&&/^[a-f0-9]{64}$/.test(x);
const positive=x=>Number.isFinite(x)&&x>0;
const nonnegative=x=>Number.isFinite(x)&&x>=0;
const near=(a,b)=>Math.abs(a-b)<=1e-7*Math.max(1,Math.abs(a),Math.abs(b));
const imageKeys=['provider','model','workflow','steps','guidance','sampler','scheduler','width','height','referenceStrength'];
const imageSettings={provider:'comfyui',model:'flux2-klein-4b',workflow:'flux2-klein-4b-auto',steps:4,guidance:1,sampler:'euler',scheduler:'flux2',width:1280,height:720,referenceStrength:.65};
export function qualityBlockedProductionEvidence(evidence){
 const d=evidence?.productionCalibration,p=d?.partial;
 if(d?.version!==1||p?.version!==1||p.id!==CURRENT_COMPONENTS_ID||p.status!=='QUALITY_BLOCKED'||p.samples!==1||
  p.nativeStatus!=='STOPPED_NO_REPLAY'||p.rootStatus!=='STOPPED_RETAINED'||p.qualityAllowsRender!==false||p.blockingImages!==26||
  p.freshRenderVerified!==false||p.finalArchiveVerified!==false||p.freshVoiceCalls!==0||p.voiceEstimateChanged!==false||
  p.initialChapterImages!==105||p.introImages!==5||p.selectedImages!==110||p.repairImages!==36||p.maxAutomaticRepairRounds!==1||
  p.QCRequests!==146||p.QCEffort!=='low'||p.actualReturnedTiersStandard!==true||p.reservedUSD!==0||p.pendingRequests!==0||p.unknownImageOperations!==0||
  !positive(p.apiSettledUSD)||p.apiSettledUSD>.7||!hash(p.qcCompatibilityHash)||
  !positive(p.nativeElapsedSeconds)||!positive(p.rootElapsedSeconds)||p.rootElapsedSeconds<p.nativeElapsedSeconds||
  !p.proof||!['measurementSHA256','nativeResultSHA256','rootResultSHA256','sourceFreezeSHA256','rentalEventsSHA256'].every(k=>hash(p.proof[k])))return null;
 const lease=p.lease,i=p.images,b=p.boundaries,q=p.QC,s=p.startup;
 if(lease?.policy!=='retained-qc-v1'||lease.holdsGPUForQC!==true||lease.rentalWindows!==1||lease.shutdownConfirmed!==true||
  s?.cachedWeightsVerified!==true||s.modelCopies!==2||s.requestSlots!==4||!positive(s.seconds)||
  i?.cohortTimeBasis!=='nonoverlapping-wall-allocation'||i.includesCold!==true||i.includesLocalDelivery!==true||
  i.freshEncodingVerified!==true||i.freshInferenceVerified!==true||!positive(i.initialWallSeconds)||
  i.chapter?.images!==105||i.intro?.images!==5||i.repair?.images!==36||
  ![i.chapter.seconds,i.intro.seconds,i.repair.seconds].every(positive)||!near(i.chapter.seconds+i.intro.seconds,i.initialWallSeconds)||
  !['initialDispatchSeconds','initialQCActiveOffsetSeconds','repairAdmissionGapSeconds','repairQCActiveOffsetSeconds'].every(k=>nonnegative(b?.[k]))||
  !['initialQCCompletionSecondsFromImageStart','initialQCWallSeconds','initialQCTailSeconds','repairQCCompletionSecondsFromImageStart',
     'repairQCWallSeconds','repairQCTailSeconds','shutdownSecondsAfterLastRecheck'].every(k=>positive(b?.[k]))||
  !near(b.initialQCCompletionSecondsFromImageStart-b.initialQCActiveOffsetSeconds,b.initialQCWallSeconds)||
  !near(b.initialQCCompletionSecondsFromImageStart-i.initialWallSeconds,b.initialQCTailSeconds)||
  !near(b.repairQCCompletionSecondsFromImageStart-b.repairQCActiveOffsetSeconds,b.repairQCWallSeconds)||
  !near(b.repairQCCompletionSecondsFromImageStart-i.repair.seconds,b.repairQCTailSeconds))return null;
 for(const [key,n]of [['chapter',105],['intro',5],['repair',36]]){
  const c=q?.[key];
  if(c?.checks!==n||!positive(c.workSeconds)||c.workSeconds/n>3600||!nonnegative(c.USD)||c.USD/n>1||
   c.includesLaneWait!==false||c.processingTier!=='default'||c.effort!=='low')return null;
 }
 if(!near(d.components?.director?.USD+q.chapter.USD+q.intro.USD+q.repair.USD,p.apiSettledUSD)||
  p.decisionCounts?.passed!==46||p.decisionCounts.advisory!==38||p.decisionCounts.review!==26||
  p.blockerCategories?.clothesOnly!==19||p.blockerCategories.mixed!==4||p.blockerCategories.nonClothing!==3||
  p.archive?.unknownWrites!==1||p.archive.finalTailMeasured!==false||p.archive.retainedReserveUSD!==.04)return null;
 return p;
}
export function currentComponentsEligible(config,evidence,{ignoreSelection=false}={}){
 const p=qualityBlockedProductionEvidence(evidence),d=evidence?.productionCalibration,c=config;
 if(!p||!ignoreSelection&&c.productionCalibration!==p.id||c.policy!=='current'||c.productionGenerationStrategy!=='dense'||
  c.productionDenseVersion!==1||c.productionDenseSupported!==true||c.productionDenseEligible!==true||
  c.productionDenseLeasePolicy!==p.lease.policy||c.productionDenseHoldsGPUForQC!==true||
  c.gpu!=='5090'||c.resolution!=='720p'||c.encodingProfile!=='fresh'||c.executionMode!=='hybrid'||String(c.imageWorkers)!=='4'||
  c.productionModelCopies!==2||c.productionRequestSlots!==4||c.productionDenseGenerationTasks!==5||c.productionDenseQCSlots!==4||
  c.productionDenseMaxRepairRounds!==1||c.qcEffectiveSlots!==4||c.qc!=='practical'||c.directorTier!=='default'||c.qcProcessingTier!=='default'||
  c.qcCompatibilityHash!==p.qcCompatibilityHash||c.directorProfile!=='lean-overlap-standard-20261031'||c.directorConditionsMatch!==true||
  c.videoResolution!=='720p'||c.videoFps!==30||!Number.isFinite(c.cadence)||Math.abs(c.cadence-d.inputCadencePerMinute)>1e-12||
  !imageKeys.every(k=>d.imageSettings?.[k]===imageSettings[k]&&c.sourceBurstImageSettings?.[k]===imageSettings[k])||
  c.intro&&c.introImages!==5)return null;
 return p;
}
export function applyCurrentProductionComponents(plan,evidence){
 const p=currentComponentsEligible(plan.config,evidence);
 if(!p||plan.prewarm)return plan;
 const tasks=plan.tasks.map(t=>({...t,deps:[...t.deps],resources:{...t.resources}}));
 const find=id=>tasks.find(t=>t.id===id),add=task=>{tasks.push(task);return task.id;};
 const initial=tasks.filter(t=>t.kind==='delivered'&&!t.id.startsWith('repair'));
 if(!initial.length||!find('cold')||!find('health')||!find('stop'))return plan;
 if(plan.config.warmCache){
  find('boot').duration=p.startup.seconds-.1;find('models').duration=.05;find('health').duration=.05;
  for(const id of ['boot','models','health'])find(id).basis=`One measured cached two-copy preparation envelope, ${p.startup.seconds.toFixed(3)}s from replacement allocation intent to both processes ready. These chart allocations sum to the envelope once. Cold inference stays inside image delivery. A job-owned launcher must attach the retained cache; automatic native pool attachment is not wired.`;
 }
 const dispatch=add({id:'current-initial-dispatch',kind:'prepare',chapter:0,name:'Ready worker to first image admission',lane:'prepare',
  duration:p.boundaries.initialDispatchSeconds,deps:['health'],resources:{},priority:find('cold').priority-.1,
  basis:'Observed one-time gap between two-copy readiness and the first image execution; not multiplied by picture count.'});
 find('cold').deps=find('cold').deps.map(id=>id==='health'?dispatch:id);
 for(const t of initial){
  const cohort=t.chapter===0?p.images.intro:p.images.chapter;
  t.duration=t.images*cohort.seconds/cohort.images;
  t.basis=`Quality-blocked current source: allocated ${cohort.seconds.toFixed(3)}s / ${cohort.images} ${t.chapter===0?'intro':'chapter'} images. Cold inference, fresh encoding, retrieval and local saves are included. Intro overlaps chapters: these nonoverlapping allocations are coupled source components, not isolated benchmarks or a successful video.`;
 }
 // The actual source admitted chapter images before intro images. Keep that
 // order while retaining each chapter's own required review/render gates.
 const initialIds=new Set(initial.map(t=>t.id));
 const ordered=[...initial.filter(t=>t.chapter!==0),...initial.filter(t=>t.chapter===0)];
 let previousInitial=null;
 for(const t of ordered){t.deps=t.deps.filter(id=>!initialIds.has(id));if(previousInitial)t.deps.push(previousInitial);previousInitial=t.id;}
 // Cold work was not isolated in this source. A tiny visible chart allocation
 // is subtracted from the same delivery work, conserving its measured total.
 find('cold').duration=Math.min(.05,ordered[0].duration/2);ordered[0].duration-=find('cold').duration;
 find('cold').basis='Unisolated cold work remains inside the source image span. This chart allocation is subtracted equally from first delivery; no extra loading work is added.';
 const initialScale=initial.reduce((n,t)=>n+t.images,0)/110;
 const initialBoundary=add({id:'current-initial-qc-boundary',kind:'qcBoundary',chapter:0,name:'Initial QC completion boundary',lane:'qc',
  duration:p.boundaries.initialQCCompletionSecondsFromImageStart*initialScale,qcActiveAfter:p.boundaries.initialQCActiveOffsetSeconds*initialScale,
  deps:[dispatch],resources:{},priority:find('cold').priority,
  basis:`Measured source QC boundary: ${p.boundaries.initialQCWallSeconds.toFixed(3)}s wall, overlapping the ${p.images.initialWallSeconds.toFixed(3)}s image phase, with a ${p.boundaries.initialQCTailSeconds.toFixed(3)}s tail. This floor retains queue, transport and commit delays; API work runs concurrently below and is not added to this interval.`});
 for(const t of tasks.filter(t=>t.kind==='vision')){
  const q=t.id.startsWith('repair')?p.QC.repair:t.chapter===0?p.QC.intro:p.QC.chapter;
  t.duration=t.images*q.workSeconds/q.checks;
  t.productionQCUSD=t.images*(plan.config.qcCostManual===true?plan.config.qcCost:q.USD/q.checks);
  t.basis=`Current ${t.id.startsWith('repair')?'repair recheck':'initial Practical check'}: ${q.checks} settled Standard/low-effort calls, ${q.workSeconds.toFixed(3)}s API work excluding lane wait. Every projected image retains its check. Measured cohort completion boundaries separately retain runtime waiting without adding concurrent API durations again.`;
 }
 let previousBoundary=initialBoundary;
 const repairGroups=tasks.filter(t=>t.kind==='delivered'&&t.id.startsWith('repair'));
 for(const original of repairGroups){
  const prefix=original.id.replace(/images$/,''),checks=tasks.filter(t=>t.kind==='vision'&&t.id.startsWith(prefix));
  if(checks.reduce((n,t)=>n+t.images,0)!==original.images||!checks.length)return plan;
  const repairCount=original.images,scale=repairCount/36,base={...original,deps:[...original.deps]},previousDeps=[...original.deps,previousBoundary];
  const admission=add({id:prefix+'current-admission',kind:'prepare',chapter:0,name:'Repair phase admission',lane:'prepare',
   duration:p.boundaries.repairAdmissionGapSeconds,deps:[...new Set(previousDeps)],resources:{},priority:original.priority-.1,
   basis:'Observed one-time transition after the required initial reviews, on the same held worker. No resume, boot or model download is added.'});
  let previousImage=null;
  checks.forEach((check,index)=>{
   const task=index===0?original:{...base,id:base.id+'-part'+(index+1),resources:{...base.resources}};
   task.images=check.images;task.attempts=check.images;task.chapter=check.chapter;
   task.duration=check.images*p.images.repair.seconds/p.images.repair.images;
   task.deps=[previousImage||admission];task.priority=base.priority+index/10000;
   task.name=base.name+' · group '+(index+1);
   task.basis='Current quality-blocked repair cohort: 36 delivered repairs in 301.622s, including fresh encoding, inference and durable local delivery. Display groups expose the observed generation/recheck overlap on one lease; they do not change calls, quality or runtime limits.';
   if(index)tasks.push(task);
   check.deps=check.deps.map(id=>id===base.id?task.id:id);previousImage=task.id;
  });
  previousBoundary=add({id:prefix+'current-qc-boundary',kind:'qcBoundary',chapter:0,name:'Repair recheck completion boundary',lane:'qc',
   duration:p.boundaries.repairQCCompletionSecondsFromImageStart*scale,qcActiveAfter:p.boundaries.repairQCActiveOffsetSeconds*scale,
   deps:[admission],resources:{},priority:base.priority,
   basis:`Source repairs and their required rechecks overlapped; the last recheck finished ${p.boundaries.repairQCTailSeconds.toFixed(3)}s after the last repair image. This measured completion floor overlaps the API/check tasks rather than serially adding their full wall time.`});
 }
 find('stop').deps.push(previousBoundary);
 find('stop').duration=p.boundaries.shutdownSecondsAfterLastRecheck;
 find('stop').basis='Observed final-recheck to confirmed native shutdown interval, once on the retained lease. Later benchmark resumes and final resource deletion belong to separate study accounting.';
 const warning=`Current source was QUALITY-BLOCKED: 110 initial images, 36 repairs, 146 settled checks, 26 remaining blockers (19 clothing-only, 4 mixed, 3 other). Image/QC components now match retained-QC scheduling; full finish time remains unproven. This conditional schedule assumes accepted images, retains the compatible older render reference, and excludes the unmeasured fresh-video cloud-save tail. Later project/image archive recovery does not establish completed video production. The cached setup requires a job-owned launcher to attach the retained volume; automatic native pool attachment is not wired. The ${plan.config.retries}% extra-attempt allowance is a scenario, not a measured failure rate. Narration remains 12.3 minutes.`;
 return {...plan,tasks,currentProductionComponentsUsed:true,productionCalibrationUsed:false,sourceBurstProjectionUsed:false,
  currentProductionComponentsWarning:warning,currentProductionComponents:{id:p.id,samples:1,qualityBlocked:true,
   initialSecondsPerImage:p.images.chapter.seconds/p.images.chapter.images,measurementSHA256:p.proof.measurementSHA256,
   cachedStartupApplied:plan.config.warmCache,automaticCacheAttachment:false,finalRenderMeasured:false,finalArchiveMeasured:false},
  renderScope:plan.renderScope+' '+warning};
}
