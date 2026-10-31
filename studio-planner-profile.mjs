// Shared settings projection. No story, references, budgets or keys are copied.
import {qualityConfig} from './pipeline-qc-forecast.mjs?v=production-components-20261031';
import {denseCapability} from './pipeline-dense.mjs?v=production-components-20261031';
import {matchedDirectorProfile} from './pipeline-director-matched.mjs?v=production-components-20261031';
import {currentComponentsEligible,CURRENT_COMPONENTS_ID} from './pipeline-production-components.mjs?v=production-components-20261031';
export function plannerSettings(config, profile, evidence) {
  if(profile?.version!==1)throw Error('Update the helper to load saved production settings.');
  const s=profile.settings, v=s.video, d=s.director, i=s.image;
  const warnings=[], next={...config};
  // Read-only observed settings metadata, retained across worker structuredClone.
  // No control or saved production setting is changed by this projection.
  next.sourceBurstImageSettings=Object.fromEntries(['provider','model','workflow','steps','guidance','sampler','scheduler','width','height','referenceStrength'].map(k=>[k,i[k]]));
  next.productionGenerationStrategy=profile.generationStrategy||'align';
  next.productionDenseVersion=0;next.productionDenseSupported=false;next.productionDenseEligible=false;
  next.productionDenseQCSlots=0;next.productionDenseGenerationTasks=5;next.productionDenseDescriptorLimit=0;next.productionDenseMaxRepairRounds=0;
  next.productionDenseLeasePolicy='stopped-qc-v1';next.productionDenseHoldsGPUForQC=false;
  if(s.voiceCompute==='kaggle' || s.voiceEngine && s.voiceEngine!=='kokoro-82m')
    throw Error('Kaggle narration has no matching full-production calibration yet. Local Kokoro timings cannot estimate this voice. Use an audition and retain the existing planner scenario.');
  if(!['off','sampled','practical','strict'].includes(s.qcCheckLevel))throw Error('Unsupported saved visual check level.');
  next.qc=s.qcCheckLevel;
  next.sample=100/(s.qcSampleEvery||5);
  const resolution=(w,h)=>w===1280&&h===720?'720p':w===1920&&h===1080?'1080p':null;
  next.videoResolution=resolution(v.width,v.height);
  next.resolution=resolution(i.width,i.height);
  if(!next.videoResolution||!next.resolution||![24,30,60].includes(v.fps))throw Error('This project format has no matched planner timing. Its settings were not replaced with another format.');
  next.videoFps=v.fps;next.videoRenderer=v.renderer||'auto';
  next.fullAssemblyPolicy=profile.renderExecution?.fullAssemblyEligibility==='verified'&&profile.renderExecution?.fullAssemblyPolicy==='direct-concat-original-audio-v1'?'direct-concat-original-audio-v1':'legacy-two-pass-v1';
  if(profile.renderExecution?.fullAssemblyCapability==='direct-concat-original-audio-v1'&&next.fullAssemblyPolicy==='legacy-two-pass-v1')
    warnings.push('One-pass final assembly is installed. Its measured Join time requires verified local 720p30 and PCM 24 kHz mono sources; unknown or unsupported sources retain the previous Join estimate until their format is verified.');
  if(['cpu','native'].includes(profile.renderExecution?.backend)){
    next.videoRenderer=profile.renderExecution.backend;
    warnings.push(`Following the actual local helper renderer: ${next.videoRenderer==='native'?'NVIDIA GPU photo motion':'CPU compatibility'}. Auto does not choose a different renderer for this production run; short export timings remain projections for long videos.`);
    if(profile.renderExecution.frameProgressPolicy==='sampled-250ms-v1')
      warnings.push('Installed native rendering samples frame-display updates while checking pause and cancellation on every frame. Matched clip tests produced identical MP4 bytes and frames. This improvement is not multiplied into the full-video forecast; narration timing is unchanged.');
  }
  next.intro=profile.intro?.enabled===true;
  if(next.intro){
    if(profile.intro.placement==='every_chapter')throw Error('Per-chapter intros are not calibrated in this planner. Use a manual scenario; no full-story-only intro is silently substituted.');
    next.introSeconds=profile.intro.duration;next.introImages=profile.introImages||5;
  }
  // A retry ceiling is not a measured replacement percentage.
  if(!s.automaticRepair)next.retries=0;
  else warnings.push('Automatic repairs are enabled. Extra attempts remain your scenario allowance; a retry limit is not an error rate.');
  if(s.unattendedProduction)
    warnings.push('Full-video automation is enabled: confirmed major errors use bounded repairs within saved caps; minor or uncertain Practical findings remain warnings. Repair work is separate from the initial image burst; the final finish time depends on how many repairs are needed.');
  next.directorTier=d.processingTier||'default';
  const profiles=evidence.directorCalibration?.profiles||[];
  const matched=matchedDirectorProfile(config,profile,evidence);
  let id=matched?.id||(d.executionMode==='classic'?'classic-standard':d.executionMode==='staged-lean'?
    (d.compactCuts&&d.factBeatsMode==='storyboard'?'lean-compact-standard':d.factBeatsMode==='storyboard'?'lean-beats-standard':'lean-current-standard'):null);
  if(id&&profiles.some(p=>p.id===id))next.directorProfile=id;
  else warnings.push('This director workflow has no matching measured profile. Existing timing is a comparison reference.');
  next.directorActualSlots=d.parallelism||1;next.apiSlots=next.directorActualSlots;
  next.qcEffectiveSlots=next.apiSlots;
  const topology=profile.imageExecution;
  if(topology?.backend==='comfyui-api' && [1,2].includes(topology.gpuLanes)){
    next.productionModelCopies=topology.modelCopies||topology.gpuLanes;
    next.encodingProfile='fresh';
    next.productionRequestSlots=topology.requestSlots||(topology.gpuLanes===2?4:1);
    next.productionAudioLookahead=profile.phaseOverlap?.audioLookahead===true;
    next.productionRenderOverlap=profile.phaseOverlap?.chapterRender===true;
    next.policy='current';next.executionMode=topology.gpuLanes===2?'hybrid':'resident';next.imageWorkers=topology.gpuLanes===2?next.productionRequestSlots:1;next.measurementAttempt='latest';
    let resolvedProductionGPU=null;
    const required=profile.imageExecution.requiredGPU;
    if(required){
      const normalize=name=>String(name).replace(/\b(?:NVIDIA|GeForce)\b/gi,'').replace(/\s+/g,'').toLowerCase();
      const choices=[...(evidence.gpus||[]),...(evidence.concurrency?.gpus||[])];
      const matches=choices.filter(g=>normalize(g.name)===normalize(required));
      const ids=[...new Set(matches.map(g=>g.id))];
      if(ids.length===1){next.gpu=ids[0];resolvedProductionGPU=ids[0];}
      else warnings.push('The required production GPU has no unique matched benchmark. This selected GPU remains a comparison reference; production does not switch GPUs.');
    }
    const quote=topology.computeQuote;
    if(Number.isFinite(quote?.hourlyUSD)&&quote.hourlyUSD>0&&Number.isFinite(quote.observedAt)){
      if(resolvedProductionGPU){
        next.pricingBasis='quote';next.quotedGPUHourly=quote.hourlyUSD;next.quotedGPU=resolvedProductionGPU;
        if(quote.workingDiskIncluded)next.containerGB=0;
        warnings.push(`Using ${quote.provider??'the configured 5090'}'s last observed compute quote: $${quote.hourlyUSD.toFixed(2)}/hour, recorded ${new Date(quote.observedAt*1000).toLocaleString()}. ${quote.workingDiskIncluded?'Working disk is already included; it is not added twice. Shared storage remains separate.':'Container/shared storage remain separate.'} This is not a new availability or price guarantee.`);
        if(quote.provider&&quote.provider!=='runpod')warnings.push('The fallback marketplace can have different runtime and delivery speed. Until compatible production history is learned, Runpod benchmark timing remains a comparison reference, not measured fallback throughput.');
      }else warnings.push('The saved compute quote has no unique production GPU match; it was not applied. Existing explicit planning price assumptions remain.');
    }
    warnings.push(topology.gpuLanes===1?'Following production uses installed scheduling and one model-worker benchmark. It does not activate the proposed render overlap or multi-copy GPU experiment.':'Following production requests two independent model copies and four bounded request slots on one GPU. Startup must verify both private processes before any image is submitted; end-to-end speed remains a projection until a matching production run.');
  }
  if(profile.generationStrategy!=='dense'&&profile.imageTasks>3 && topology?.gpuLanes!==2)
    warnings.push(`Studio admits up to ${profile.imageTasks} image/check tasks while keeping one GPU image lane. Older three-task throughput does not measure this new pipeline; no speedup is assumed until matching production completes. A 28-call Luna QC-only pilot found no measured advantage for five tasks; complete image-generation throughput remains untested.`);
  if(profile.generationStrategy!=='dense'&&profile.imageAdmission?.maximumAfterVerification>profile.imageTasks)
    warnings.push(`Actual generation can admit up to ${profile.imageAdmission.maximumAfterVerification} image/check tasks only after both workers are verified and RAM headroom passes. Image requests and API calls keep their existing limits. No speedup from this change is applied to the forecast until matching production timing exists.`);
  else if(profile.generationStrategy!=='dense'&&profile.imageAdmission?.benchmarkStatus==='no-reliable-eight-task-gain')
    warnings.push('Production keeps five image/check tasks. Four matched 12-shot RTX 5090 rounds found no consistent speed gain at eight tasks (five averaged 56.6s; eight 57.6s). Generated images were identical and every image was checked. This short test does not recalibrate the full-video forecast.');
  if(topology?.backend==='comfyui-api' && topology.gpuLanes===1 && !topology.multipleModelCopiesVerified)
    warnings.push('The current production connection uses one ComfyUI queue. Faster multi-copy or hybrid GPU benchmarks are comparison targets, not verified throughput for this running production backend.');
  next.directorCostMode='measured';
  next.earlyGpu=false;next.gpuStartSeconds=0;
  if(profile.generationStrategy==='dense'){
    if(!denseCapability(profile)||topology?.backend!=='comfyui-api'||next.policy!=='current')
      throw Error('This saved Dense strategy requires an eligible managed cloud setup and an updated helper with dense-image capability. The retained planner scenario was not replaced.');
    const phase=profile.denseImagePhase;
    next.productionDenseVersion=phase.version;next.productionDenseSupported=true;next.productionDenseEligible=true;
    next.productionDenseQCSlots=phase.qcTaskSlots;next.productionDenseGenerationTasks=phase.generationTasks;
    next.productionDenseDescriptorLimit=phase.descriptorLimit;next.productionDenseMaxRepairRounds=phase.maxRepairRounds;
    next.productionDenseLeasePolicy=phase.leasePolicy||'stopped-qc-v1';next.productionDenseHoldsGPUForQC=phase.holdsGPUForQC===true;
    next.readyChapters=next.chapters;
    next.repairRounds=Math.min(config.repairRounds||1,Math.max(1,phase.maxRepairRounds));
    const held=next.productionDenseHoldsGPUForQC;
    warnings.push('Installed Dense plans every chapter and prepares intro/references before one initial GPU burst. '+(held?'The saved policy retains one paid GPU lease through required QC and bounded repairs; QC-only waiting bills as idle time, not image work. No extra resume or model reload is assumed per repair round.':'Generation does not wait for QC; the GPU stops before its remaining QC tail.')+' Startup, readiness, transfers and shutdown still bill, so image-pipeline coverage cannot be 100%. Complete-production finish time remains unmeasured; compatible component data and explicitly labelled source-based projections determine this scenario.');
    warnings.push(next.retries>0?`The ${next.retries}% extra-attempt allowance is spread across up to ${next.repairRounds} assumed repair rounds, not a measured defect rate. ${held?'Reviews and repairs stay on the same paid lease.':'Each nonempty round pays its own startup and shutdown before rechecks.'} Runtime retains each shot's saved ceiling of ${phase.maxRepairRounds} retries.`:
      `No repair rate is assumed. QC can require additional bounded repairs ${held?'on the same retained paid lease':'in separately billed bursts, each with startup and shutdown'}, up to the saved ${phase.maxRepairRounds} per-shot retry ceiling; their unknown work is excluded from this zero-extra-attempt scenario.`);
  }else if(profile.generationStrategy==='align'&&!profile.timedHistoryReady){
    next.readyChapters=next.chapters;
    warnings.push('Timed align has no complete matching production history; like Studio, this setup waits for all chapter plans before image dispatch. The 15-second alignment target is not a guaranteed finish.');
  }else if(profile.generationStrategy==='fastest')next.readyChapters=1;
  if(!matched&&(d.visualPacking&&d.visualPacking!=='small'||d.sourceVisualOverlap||d.reasoning!=='Balanced'))
    warnings.push('Director grouping, overlap or reasoning differs from the public calibration; its time and token cost are reference projections, not matching measurements.');
  const measured=profiles.find(p=>p.id===next.directorProfile);
  const actual=profile.directorCalibrationProfile;
  next.directorConditionsMatch=!!matched||!(d.visualPacking&&d.visualPacking!=='small'||d.sourceVisualOverlap||d.reasoning!=='Balanced');
  if(matched)warnings.push('Director time and token cost use one accepted full-source 17.5-minute sample at four Standard API slots. Two-hour finish time remains a projection. Other stages use their separately stated component evidence; the voice estimate is unchanged.');
  if(actual&&measured?.profileVersions){
    for(const field of ['factsVersion','planVersion','reviewHintsVersion','sourceTimingUnitsVersion'])
      if(actual[field]!==measured.profileVersions[field])next.directorConditionsMatch=false;
  }
  const reviewCapacity=measured?.conditions?.storyboardReviewCapacityVersion??measured?.profileVersions?.storyboardReviewCapacityVersion;
  if(actual&&(actual.storyboardReviewCapacityVersion!==undefined||reviewCapacity!==undefined)&&
     actual.storyboardReviewCapacityVersion!==reviewCapacity)next.directorConditionsMatch=false;
  const ownershipVersion=measured?.conditions?.ownershipResolutionVersion??measured?.profileVersions?.ownershipResolutionVersion;
  if(actual&&(actual.ownershipResolutionVersion!==undefined||ownershipVersion!==undefined)&&
     actual.ownershipResolutionVersion!==ownershipVersion)next.directorConditionsMatch=false;
  if(!next.directorConditionsMatch)warnings.push('Exact saved director settings/schema versions do not match this calibration. Its displayed time and token cost are comparison references; no measured speedup is assumed.');
  const observed=profile.directorObservation;
  next.directorObservedSecondsPerStorySecond=0;next.directorObservedCostPerTwoHours=0;next.directorObservedSamples=0;next.directorObservedRuns=0;
  if(observed&&Number.isFinite(observed.secondsPerStorySecond)&&observed.secondsPerStorySecond>0&&Number.isFinite(observed.costPerTwoHours)&&observed.costPerTwoHours>=0&&observed.samples>0){
    next.directorObservedSecondsPerStorySecond=observed.secondsPerStorySecond;
    next.directorObservedCostPerTwoHours=observed.costPerTwoHours;
    next.directorObservedSamples=observed.samples;next.directorObservedRuns=observed.runs;
    warnings.push('Director time and cost use matching current production history, scaled with measured narration. Small samples and different story structure remain uncertain.');
  }
  if(i.model!=='flux2-klein-4b'||i.steps!==4||i.guidance!==1)
    warnings.push('Image model/settings differ from the measured FLUX.2 Klein 4B / 4-step / guidance-1 benchmark. Its speed and rental cost are not calibrated for this setup.');
  const qc=profile.estimates?.visualQC;
  let qcFallbackWarning='';
  next.qcCompatibilityHash=profile.qcCompatibilityHash||'';
  next.qcProcessingTier=profile.qcProcessingTier||'';
  next.qcProductionTasks=profile.imageTasks||0;
  next.qcHistoryLatest=Number.isFinite(qc?.latest)?qc.latest:0;
  next.qcHistoryIdentity=profile.qcHistoryIdentity||null;
  next.qcHistorySeconds=Number.isFinite(qc?.seconds)?qc.seconds:0;
  next.qcHistoryCost=Number.isFinite(qc?.costUSD)?qc.costUSD:
    config.qcBenchmarkId&&Number.isFinite(config.qcHistoryCost)?config.qcHistoryCost:config.qcCost;
  next.qcCostManual=false;
  if(next.qc!=='off'&&Number.isFinite(qc?.seconds)&&qc.seconds>0){
    next.qcSeconds=qc.seconds;
    if(Number.isFinite(qc.costUSD)&&qc.costUSD>=0)next.qcCost=qc.costUSD;
  }else{next.qcSeconds=0;qcFallbackWarning=next.qc==='off'?'Visual checks are explicitly Off in this saved project; file checks remain.':'No matching visual-check history yet; historical QC time and the displayed per-image cost are estimates.';}
  warnings.push('Voice delivery, motion, overlays, transfers and long exports can differ from short benchmark conditions; total finish time remains a projection.');
  const resolved=qualityConfig(next,evidence);
  if(config.productionCalibration!=='current-production-20261031'&&currentComponentsEligible(resolved,evidence,{ignoreSelection:true})){
    resolved.productionCalibration=CURRENT_COMPONENTS_ID;
    warnings.push('Following current retained-QC image/check components from the quality-blocked source. Its 26 blockers prevented rendering; the conditional forecast keeps older compatible render timing and no final cloud-save measurement. It is not a completed-video ETA. Cached startup is applied only when the cached-weights scenario is selected; a job-owned launcher must attach the volume, because automatic native pool attachment is not wired.');
  }
  if(resolved.qcBenchmarkNote)warnings.push(resolved.qcBenchmarkNote);
  else if(qcFallbackWarning)warnings.push(qcFallbackWarning);
  return {config:resolved,warnings};
}

export function profileSummary(p) {
  const s=p.settings,v=s.video,i=s.image,d=s.director;
  const copies=p.imageExecution?.modelCopies||p.imageExecution?.gpuLanes||1;
  const window=p.generationStrategy==='dense'?(denseCapability(p)?`${p.denseImagePhase.generationTasks} generation tasks; ${p.denseImagePhase.qcTaskSlots} independent QC slots; plan first, ${p.denseImagePhase.holdsGPUForQC?'one paid lease through QC and repairs':'separate rental windows'}`:'Dense unavailable for this helper/setup'):
    p.imageAdmission?.maximumAfterVerification>p.imageTasks?`${p.imageTasks} image/check tasks; up to ${p.imageAdmission.maximumAfterVerification} after worker and RAM verification`:`${p.imageTasks||3} image/check tasks`;
  return `${p.name} · saved revision ${p.revision} · ${v.width}×${v.height}/${v.fps} fps · ${v.motionMode||'director'} motion · ${s.voice} / ${s.narrationDelivery||'standard'} · ${i.model}, ${i.steps} steps, guidance ${i.guidance} · ${s.qcCheckLevel} visual checks · ${s.unattendedProduction?'automatic completion and bounded repairs':'manual review policy'} · intro ${p.intro?.enabled?'On':'Off'} · ${d.parallelism||1} Luna tasks · ${window}, ${copies===1?'one GPU lane':copies+' independent model copies on one GPU'} · ${d.processingTier||'default'} processing`;
}
