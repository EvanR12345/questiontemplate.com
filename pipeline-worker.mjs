import {compareGPUPlans} from './pipeline-gpu-comparison.mjs?v=matched-production-20261029';
import {buildPlan,schedule} from './pipeline-engine.mjs?v=matched-production-20261029';
import {startupSearch} from './pipeline-detail-ui.mjs?v=matched-production-20261029';
self.onmessage=({data})=>{
 try{
  if(data.type==='plan'){
   const plan=buildPlan(data.config,data.evidence),result=schedule(plan,data.preferences);
   const serial=schedule(buildPlan({...plan.config,policy:'serial'},data.evidence));
   Object.assign(serial,sourceBurstSerialComparison(plan,result,serial));
   self.postMessage({type:'complete',plan,result,serial});
  }else if(data.type==='gpu-comparison'){
   self.postMessage({type:'complete',...compareGPUPlans(data.config,data.evidence)});
  }else if(data.type==='startup'){
   const search=startupSearch(data.config,data.evidence,true,data.searchPolicy);
   for(;;){const step=search.next();if(step.done){self.postMessage({type:'complete',values:step.value});break;}self.postMessage({type:'progress',message:step.value});}
  }else throw Error('Unknown planner computation');
 }catch(error){self.postMessage({type:'error',message:error.message});}
};
import {sourceBurstSerialComparison} from './pipeline-source-burst.mjs?v=matched-production-20261029';
