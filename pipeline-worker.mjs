import {buildPlan,schedule} from './pipeline-engine.mjs?v=render-research-20261009d';
import {startupSearch} from './pipeline-detail-ui.mjs?v=render-research-20261009d';
self.onmessage=({data})=>{
 try{
  if(data.type==='plan'){
   const plan=buildPlan(data.config,data.evidence),result=schedule(plan,data.preferences);
   const serial=schedule(buildPlan({...plan.config,policy:'serial'},data.evidence));
   self.postMessage({type:'complete',plan,result,serial});
  }else if(data.type==='startup'){
   const search=startupSearch(data.config,data.evidence,true);
   for(;;){const step=search.next();if(step.done){self.postMessage({type:'complete',values:step.value});break;}self.postMessage({type:'progress',message:step.value});}
  }else throw Error('Unknown planner computation');
 }catch(error){self.postMessage({type:'error',message:error.message});}
};
