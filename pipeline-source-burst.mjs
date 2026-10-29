// Conditional n=1 source-span projection. Does not change two-round selection,
// production settings, image counts, checks, or any task dependency/resource.
const CADENCE=5.884425316359273;
const HASH=/^[a-f0-9]{64}$/;
const positive=x=>Number.isFinite(x)&&x>0;
export function sourceBurstSerialComparison(plan,result,serial){
 return plan.sourceBurstProjectionUsed===true?
  {comparisonMatched:false,comparisonSaving:null,comparisonNote:'Sequential reference uses a different image timing basis and QC/rental topology; no same-work speed saving is claimed.'}:
  {comparisonMatched:true,comparisonSaving:1-result.end/serial.end,comparisonNote:''};
}
export function sourceBurstEligible(plan,evidence){
 const c=plan.config,d=evidence.qcCalibration?.sourceBurstProjection;
 if(!d||d.type!=='single-source-inclusive-span-v1'||d.version!==1||d.samples!==1||
  d.studyStatus!=='STOPPED_NO_REPLAY'||d.armStatus!=='FAILED'||
  d.standardReceiptsVerified!==true||d.freshSamplerDecodeInferenceVerified!==true||
  d.unknownRequests!==0||d.pendingRequests!==0||d.reservedUSD!==0||
  d.rawFrozenInventoryVerified!==true||d.sourceInventory?.chapter720!==107||
  d.sourceInventory?.intro1344x768!==5||d.sourceInventory?.total!==112||
  !HASH.test(d.evidenceSHA256||'')||!HASH.test(d.phasePreflightSHA256||'')||
  d.qualityOutcome?.finalBlocking!==30||d.qualityOutcome?.finalNonblocking!==82||
  d.qualityAllowsRender!==false||d.humanQualityAccepted!==false)return null;
 if(c.policy!=='current'||c.productionGenerationStrategy!=='dense'||
  c.productionDenseSupported!==true||c.productionDenseEligible!==true||
  c.productionDenseVersion!==1||c.gpu!=='5090'||c.resolution!=='720p'||
  c.executionMode!=='hybrid'||String(c.imageWorkers)!=='4'||c.encodingProfile!=='fresh'||
  c.productionModelCopies!==2||c.productionRequestSlots!==4||
  c.productionDenseGenerationTasks!==5||c.productionDenseQCSlots!==4||
  c.qc!=='practical'||c.qcEffectiveSlots!==4||c.directorTier!=='default'||
  c.qcProcessingTier!=='default'||c.qcCompatibilityHash!==d.qcCompatibilityHash||
  !HASH.test(d.qcCompatibilityHash||'')||!Number.isFinite(c.cadence)||Math.abs(c.cadence-CADENCE)>1e-12)return null;
 const s=c.sourceBurstImageSettings,expected=d.imageSettings;
 if(!s||!expected||Object.keys(expected).length!==10||
  !['provider','model','workflow','steps','guidance','sampler','scheduler','width','height','referenceStrength'].every(k=>
   Object.hasOwn(expected,k)&&s[k]===expected[k])||expected.height!==720||expected.referenceStrength!==0.65||
  expected.provider!=='comfyui'||expected.model!=='flux2-klein-4b'||
  expected.steps!==4||expected.guidance!==1||expected.width!==1280||
  expected.workflow!=='flux2-klein-4b-auto'||expected.sampler!=='euler'||expected.scheduler!=='flux2')return null;
 if(d.initialChapter?.images!==107||!positive(d.initialChapter.inclusiveSeconds)||
  d.repairChapter?.images!==38||!positive(d.repairChapter.inclusiveSeconds)||
  d.intro?.images!==5||!positive(d.intro.inclusiveSeconds))return null;
 const q=d.repairQC;
 if(d.repairQcSamples!==38||q?.type!=='completed-repair-rechecks-cohort-v1'||
  q.samples!==38||q.reservedRepairAttempts!==41||q.failedBeforeSubmission!==3||
  q.partialAttemptCohort!==true||q.componentTimingComplete!==true||q.processingTier!=='default'||
  q.apiSlots!==4||q.unknownRequests!==0||!positive(q.secondsPerCheck)||
  !Number.isFinite(q.costUSDPerCheck)||q.costUSDPerCheck<0||
  !positive(q.measuredSeconds)||!Number.isFinite(q.measuredUSD)||q.measuredUSD<0||
  Math.abs(q.secondsPerCheck*38-q.measuredSeconds)>1e-8||
  Math.abs(q.costUSDPerCheck*38-q.measuredUSD)>1e-8)return null;
 if(c.intro&&c.introImages!==5)return null;
 return d;
}

export function applySourceBurstProjection(plan,evidence){
 const d=sourceBurstEligible(plan,evidence);
 if(!d)return plan;
 const tasks=plan.tasks.map(t=>({...t}));
 const groups=tasks.filter(t=>t.kind==='delivered');
 if(!groups.length)return plan;
 for(const t of groups){
  const repair=t.id.startsWith('repair');
  const sample=repair?d.repairChapter:t.chapter===0?d.intro:d.initialChapter;
  if(!Number.isSafeInteger(t.images)||t.images<1)return plan;
  t.duration=t.images*sample.inclusiveSeconds/sample.images;
  t.basis=`Conditional n=1 source-inclusive span: ${sample.images} ${repair?'completed repair':t.chapter===0?'intro1344x768':'chapter720'} images in ${sample.inclusiveSeconds.toFixed(3)}s. Includes queued delivery, cold if present, transfers and local saves. Not replicated warm throughput. The source study failed with 30 final blockers; no render-ready or full-video completion claim.`;
 }
 // The retained cold task is an allocation inside the source-inclusive work,
 // not another charge on top. Credit duration only, never image counts.
 const allocations=[];
 for(const window of plan.rentalWindows){
  const prefix=window.phase==='repair'?window.boot.replace(/boot$/,''):'';
  const cold=tasks.find(t=>t.id===prefix+'cold');
  const first=groups.find(t=>prefix?t.id===prefix+'images':!t.id.startsWith('repair'));
  if(!cold||!first||!positive(cold.duration))return plan;
  const referenceCold=cold.duration,allocatedCold=Math.min(referenceCold,first.duration/2);
  if(!positive(allocatedCold)||allocatedCold<=1e-7)return plan;
  cold.duration=allocatedCold;first.duration-=allocatedCold;
  cold.basis=`${allocatedCold.toFixed(3)}s allocation inside the source-inclusive projection, not an isolated cold measurement. Bounded by the first display group's half-duration; counted by equal subtraction from that group.`;
  first.basis+=` ${allocatedCold.toFixed(3)}s allocated to this rental's Cold task is subtracted from this group; output count and total inclusive work are unchanged.`;
  allocations.push({cold:cold.id,delivery:first.id,referenceColdSeconds:referenceCold,creditedSeconds:allocatedCold});
 }
 for(const t of tasks.filter(t=>t.kind==='vision'&&t.id.startsWith('repair'))){
  if(!Number.isSafeInteger(t.images)||t.images<1)return plan;
  t.duration=t.images*d.repairQC.secondsPerCheck;
  t.sourceBurstRepairQC=true;t.sourceBurstQCUSD=t.images*(plan.config.qcCostManual===true?plan.config.qcCost:d.repairQC.costUSDPerCheck);
  t.basis=`Conditional repair scenario: ${d.repairQC.samples} completed repair rechecks measured ${d.repairQC.measuredSeconds.toFixed(3)}s API work and $${d.repairQC.measuredUSD.toFixed(9)} usage cost, excluding lane wait. This is the partial completed38of41attempt cohort; three failed before submission. Every projected repaired image is rechecked; no quality pass or repair success is guaranteed.`;
  if(plan.config.qcCostManual===true)t.basis+=' Your manual per-check QC price controls forecast dollars; measured repair timing is retained.';
 }
 const warning='Conditional single-source image projection (n=1): inclusive chapter/intro spans replace the small-pilot delivery rate. Cold/transfer/save are included once. Source study FAILED with 30 final blockers; zero-repair timing is conditional and is not a render-ready or full-video ETA. Different source/reference/repair burdens can change it.';
 return {...plan,tasks,sourceBurstProjectionUsed:true,sourceBurstProjectionWarning:warning,
  sourceBurstProjection:{type:d.type,samples:1,studyStatus:d.studyStatus,qualityOutcome:d.qualityOutcome,
   initialSecondsPerImage:d.initialChapter.inclusiveSeconds/d.initialChapter.images,
   repairSecondsPerImage:d.repairChapter.inclusiveSeconds/d.repairChapter.images,
   repairQcSamples:d.repairQcSamples,repairQC:d.repairQC,
   coldDurationAllocations:allocations,evidenceSHA256:d.evidenceSHA256},
  renderScope:plan.renderScope+' '+warning};
}
