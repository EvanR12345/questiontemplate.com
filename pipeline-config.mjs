import {buildPlan} from './pipeline-engine.mjs?v=native-render-20261009b';

export function gpuChoices(evidence) {
  const measured=(evidence.concurrency?.gpus||[]).filter(g=>
    ['profiles','productionProfiles','pipelineProfiles','hybridProfiles'].some(k=>g[k]?.length));
  return [...evidence.gpus,...measured.filter(g=>!evidence.gpus.some(old=>old.id===g.id))];
}

// Changing GPU must not inherit another GPU's execution method or rental attempt.
// Resolution, conditioning, model cadence, checks and all cost assumptions stay explicit.
export function selectGPUConfig(config,evidence,gpu) {
  const base={...config,gpu,measurementAttempt:'latest',imageWorkers:'best'};
  if(base.resolution==='legacy') {
    base.executionMode='resident';
    buildPlan(base,evidence);
    return base;
  }
  const candidates=[];
  for(const executionMode of ['resident','pipeline','hybrid']) {
    const candidate={...base,executionMode};
    try {
      const plan=buildPlan(candidate,evidence);
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
  const fastest=buildPlan(selectGPUConfig(plan.config,evidence,plan.config.gpu),evidence);
  const highest=fastest.measurement.imagesPerSecond*60,current=plan.measurement.imagesPerSecond*60;
  return {highest,current,isFastest:Math.abs(highest-current)<1e-8,label:executionLabel(fastest)};
}
