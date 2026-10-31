import {describePlan} from './pipeline-engine.mjs?v=production-components-20261031';
import {denseActive,denseLeaseRetained} from './pipeline-dense.mjs?v=production-components-20261031';

export function plannerSourceState(project, requestedFollowing, applied) {
  const following=Boolean(requestedFollowing && applied?.project===project);
  return {project,following,requestedFollowing,revision:following?applied.revision:null};
}

export function productionModeNote(config) {
  if(denseActive(config))return `Installed Dense: all chapter plans, intro and references precede one initial image burst. ${config.productionDenseGenerationTasks} generation tasks supply the existing workers; up to ${config.productionDenseQCSlots} QC API slots run independently. ${denseLeaseRetained(config)?'One paid GPU lease continues through required checks and bounded repairs. QC-only waiting is billed idle time; repair rounds do not add another resume or model load.':'The GPU stops before the remaining QC tail; assumed repair rounds pay separate startup and stop intervals.'} ${config.productionRenderOverlap?'A chapter can render once all of its required QC and repairs pass, while unrelated chapters continue.':'Rendering waits for all required image work and reviews.'} Runtime keeps the saved per-shot retry ceilings. Setup/transfers/shutdown remain billed. Component timing is an unmatched reference for this phase until matching complete-production proof is selected.`;
  if(config.policy==='proposed')return 'Planning experiment: separates GPU work from retrieval and overlaps chapter rendering. This is not deployed production behavior.';
  if(config.policy!=='current')return 'Every task runs sequentially. This is the comparison baseline, not a speed recommendation.';
  const copies=config.productionModelCopies===2?2:1;
  return `Installed cloud overlap: Luna lookahead and QC share API slots. ${copies===2?'Two independent model copies share one GPU, with four bounded request slots.':'One model copy uses one image lane.'} Image work includes preparation through retrieval. ${config.productionRenderOverlap?'Each chapter can render after its required images and reviews finish while later chapters continue.':'Chapter rendering waits for all image work.'} ${config.productionAudioLookahead?'Narration lookahead is enabled.':'Narration waits for the preceding chapter handoff.'} Narration and native rendering share the local GPU lane; finish times remain projections.`;
}

export function gpuChoices(evidence) {
  const measured=(evidence.concurrency?.gpus||[]).filter(g=>
    ['profiles','productionProfiles','pipelineProfiles','hybridProfiles'].some(k=>g[k]?.length));
  return [...evidence.gpus,...measured.filter(g=>!evidence.gpus.some(old=>old.id===g.id))];
}

// Changing GPU must not inherit another GPU's execution method or rental attempt.
// Resolution, conditioning, model cadence, checks and all cost assumptions stay explicit.
export function selectGPUConfig(config,evidence,gpu) {
  // Fastest-worker selection is a benchmark comparison. Leaving the installed
  // one-queue constraint requires a proposed scenario, never an automatic
  // production change. Follow saved setup bypasses this selector.
  const base={...config,gpu,policy:config.policy==='current' && config.resolution!=='legacy'?'proposed':config.policy,
    measurementAttempt:'latest',imageWorkers:'best'};
  if(base.resolution==='legacy') {
    base.executionMode='resident';
    describePlan(base,evidence);
    return base;
  }
  const candidates=[];
  for(const executionMode of ['resident','pipeline','hybrid']) {
    const candidate={...base,executionMode};
    try {
      const plan=describePlan(candidate,evidence);
      candidates.push({config:candidate,rate:plan.measurement.imagesPerSecond});
    } catch { /* No completed compatible measurement is never replaced with an estimate. */ }
  }
  candidates.sort((a,b)=>b.rate-a.rate);
  if(!candidates.length)throw Error(`No completed ${base.resolution} / ${base.encodingProfile} measurement for this GPU. Choose another GPU or timing profile.`);
  return candidates[0].config;
}

export function executionLabel(plan) {
  if(plan.currentProductionComponentsUsed)return 'Quality-blocked current source components · retained QC · 2 copies / 4 slots';
  if(plan.productionCalibrationUsed)return 'One completed source observation · 2 model copies · 4 request slots';
  if(plan.sourceBurstProjectionUsed)return 'Conditional n=1 source-inclusive spans · 2 model copies · 4 request slots';
  const m=plan.measurement;
  if(!m)return 'Earlier serial profile';
  const slots=m.clientSlots||m.workers;
  return `${m.workers} model ${m.workers===1?'copy':'copies'} · ${slots} request ${slots===1?'slot':'slots'} · ${(m.imagesPerSecond*60).toFixed(1)} images/min`;
}

// Story image frequency is independent of generation throughput. Never feed this
// derived speed back into cadence, which would increase the work being compared.
export function generationSpeed(plan,evidence) {
  if(plan.currentProductionComponentsUsed)return {highest:null,current:60/plan.currentProductionComponents.initialSecondsPerImage,isFastest:false,label:executionLabel(plan),conditionalCurrentComponents:true};
  if(plan.productionCalibrationUsed)return {highest:null,current:60/plan.productionCalibration.initialSecondsPerImage,isFastest:false,label:executionLabel(plan),conditionalProductionProjection:true};
  if(plan.sourceBurstProjectionUsed)return {highest:null,current:60/plan.sourceBurstProjection.initialSecondsPerImage,isFastest:false,label:executionLabel(plan),conditionalSourceProjection:true};
  if(!plan.measurement)return {highest:null,current:null,isFastest:false,label:''};
  const fastest=describePlan(selectGPUConfig(plan.config,evidence,plan.config.gpu),evidence);
  const highest=fastest.measurement.imagesPerSecond*60,current=plan.measurement.imagesPerSecond*60;
  return {highest,current,isFastest:Math.abs(highest-current)<1e-8,label:executionLabel(fastest)};
}
