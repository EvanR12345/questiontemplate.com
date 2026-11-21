import {buildPlan} from './pipeline-engine.mjs?v=pipeline-review-20261121';
import {selectGPUConfig} from './pipeline-config.mjs?v=pipeline-review-20261121';

// An unmatched/unconnected saved project must not imply experimental overlap.
// This is a conservative comparison until Follow applies an actual topology.
export function realisticPlanningConfig(config,evidence){
  const next={...config,policy:'current',qc:'practical',executionMode:'resident',imageWorkers:'1',
    productionModelCopies:1,productionRequestSlots:1,productionAudioLookahead:false,
    productionRenderOverlap:false,directorActualSlots:config.directorProfile==='historical'?3:4,
    apiSlots:config.directorProfile==='historical'?3:4,measurementAttempt:'latest'};
  buildPlan(next,evidence);return next;
}

export function selectScenarioGPUConfig(config,evidence,gpu){
  if(config.policy!=='current')return selectGPUConfig(config,evidence,gpu);
  const dual=config.productionModelCopies===2&&config.productionRequestSlots===4;
  const next={...config,gpu,measurementAttempt:'latest',executionMode:dual?'hybrid':'resident',imageWorkers:dual?'4':'1'};
  buildPlan(next,evidence);return next;
}

export function reviewPolicyNote(config,result,evidence){
  const checks=result.tasks.filter(t=>t.kind==='vision');
  const componentRates=checks.some(t=>Number.isFinite(t.productionQCUSD)||t.sourceBurstRepairQC);
  let common='Practical and Strict both review every image.';
  if(componentRates)common+=' This schedule uses measured review components for the selected policy.';
  else if(config.qcBenchmarkId)common+=' This schedule uses matching policy and API-slot measurements.';
  else if(config.qcHistoryMatches)common+=' This schedule uses matching saved review history.';
  else common+=' Review time and cost use reference assumptions. Actual bills vary with tokens.';
  if(config.qc==='off')return 'Visual review is Off. Technical file checks remain. '+common;
  if(config.qc==='sampled')return 'Sampled checks review only the configured subset. '+common;
  const seconds=result.qcCount?checks.reduce((total,t)=>total+t.duration,0)/result.qcCount:config.qcSeconds||evidence.calibration.qcSeconds;
  const benchmarkNote=componentRates?'':config.qcBenchmarkNote||'';
  return `${common} The ${result.qcCount} planned checks cost an estimated $${result.qcUSD.toFixed(3)} and average ${seconds.toFixed(2)}s per review before overlap. ${benchmarkNote} ${config.qcCostManual?'The estimate uses your manual per-check price. ':''}Practical repairs confirmed major defects. Strict can also block smaller findings. The extra-attempt allowance determines how many retries enter this schedule. Manual-review waiting is not measured.`;
}
