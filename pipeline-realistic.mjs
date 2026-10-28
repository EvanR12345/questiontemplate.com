import {buildPlan} from './pipeline-engine.mjs?v=dense-image-phase-20261028';
import {selectGPUConfig} from './pipeline-config.mjs?v=dense-image-phase-20261028';

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
  const common=config.qcBenchmarkId?'Practical and Strict both review every image. This schedule uses the newer matching policy and API-slot measurements; initial review bills vary with tokens.':'Practical and Strict both review every image. They use the same initial review-count, time and cost assumptions here; this is not a measured equality of API bills. '+(config.qcHistoryMatches===false?'Unmatched saved timing is a reference assumption for this policy/slot setup.':'');
  if(config.qc==='off')return 'Visual review is Off. Technical file checks remain. '+common;
  if(config.qc==='sampled')return 'Sampled checks review only the configured subset. '+common;
  const seconds=config.qcSeconds||evidence.calibration.qcSeconds;
  return `${common} Planned checks: ${result.qcCount}, estimated $${result.qcUSD.toFixed(3)} at ${seconds.toFixed(2)}s per review before overlap. ${config.qcBenchmarkNote||''} ${config.qcCostManual?'Your manual QC price overrides the measured token cost. ':''}Practical repairs confirmed major defects; Strict can also block or repair smaller findings. Extra repairs, rechecks and manual-review waiting are not measured or included beyond the explicit extra-attempt allowance.`;
}
