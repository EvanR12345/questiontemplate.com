import {describePlan} from './pipeline-engine.mjs?v=production-parallel-20261012';

export function plannerSourceState(project, requestedFollowing, applied) {
  const following=Boolean(requestedFollowing && applied?.project===project);
  return {project,following,requestedFollowing,revision:following?applied.revision:null};
}

export function productionModeNote(config) {
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
  const m=plan.measurement;
  if(!m)return 'Earlier serial profile';
  const slots=m.clientSlots||m.workers;
  return `${m.workers} model ${m.workers===1?'copy':'copies'} · ${slots} request ${slots===1?'slot':'slots'} · ${(m.imagesPerSecond*60).toFixed(1)} images/min`;
}

// Story image frequency is independent of generation throughput. Never feed this
// derived speed back into cadence, which would increase the work being compared.
export function generationSpeed(plan,evidence) {
  if(!plan.measurement)return {highest:null,current:null,isFastest:false,label:''};
  const fastest=describePlan(selectGPUConfig(plan.config,evidence,plan.config.gpu),evidence);
  const highest=fastest.measurement.imagesPerSecond*60,current=plan.measurement.imagesPerSecond*60;
  return {highest,current,isFastest:Math.abs(highest-current)<1e-8,label:executionLabel(fastest)};
}
