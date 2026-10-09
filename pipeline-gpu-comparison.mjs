import {buildPlan,schedule} from './pipeline-engine.mjs?v=render-research-20261009e';
import {gpuChoices,selectGPUConfig} from './pipeline-config.mjs?v=render-research-20261009e';
// Run in a worker. Return only chart/card fields, not every alternative's
// thousands of timeline operations and embedded calibration records.
export function compareGPUPlans(config,evidence){
  const comparisons=[],unavailable=[];
  for(const candidate of gpuChoices(evidence)){
    try{
      const c=selectGPUConfig(config,evidence,candidate.id),plan=buildPlan(c,evidence),result=schedule(plan);
      const m=plan.measurement;
      comparisons.push({g:plan.gpu,c,p:{measurement:m?{workers:m.workers,clientSlots:m.clientSlots,imagesPerSecond:m.imagesPerSecond}:null},r:{end:result.end,totalUSD:result.totalUSD}});
    }catch{unavailable.push({id:candidate.id,name:candidate.name});}
  }
  return {comparisons,unavailable};
}
