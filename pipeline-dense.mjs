// Scheduling assumptions only. No inference speeds, voice timings or prices.
export const denseActive=config=>config.policy==='current'&&config.productionGenerationStrategy==='dense';
export const denseLeaseRetained=config=>denseActive(config)&&config.productionDenseLeasePolicy==='retained-qc-v1'&&config.productionDenseHoldsGPUForQC===true;
export function denseLeasePair(policy,holds){
 return policy==='retained-qc-v1'&&holds===true||policy==='stopped-qc-v1'&&holds===false;
}
// A measured final-assembly interval is useful only for the exact proven
// project envelope. Another story/profile's receipt never supplies free credit.
export function densePrewarmMeasurement(config){
 const hash=/^[a-f0-9]{64}$/;
 if(!denseActive(config)||config.productionDensePrewarmVersion!==1||
    config.productionDensePrewarmEligible!==true||config.productionDensePrewarmMeasured!==true||
    !hash.test(config.productionDensePrewarmProof||'')||
    config.productionDensePrewarmProof!==config.productionDensePrewarmMeasuredProof)return null;
 const assembly=config.productionDensePrewarmAssemblySeconds,overhead=config.productionDensePrewarmProofSeconds;
 if(!Number.isFinite(assembly)||assembly<=0||assembly>120||!Number.isFinite(overhead)||overhead<0||
    overhead>120)return null;
 return {assemblySeconds:assembly,proofSeconds:overhead};
}
export function densePrewarmCredit(config,startupSeconds){
 const measured=densePrewarmMeasurement(config);
 if(!measured||!Number.isFinite(startupSeconds)||startupSeconds<=0)return 0;
 return Math.max(0,Math.min(measured.assemblySeconds,startupSeconds)-measured.proofSeconds);
}
export function denseCapability(profile){
 const p=profile?.denseImagePhase;
 return p?.version===1&&p.supported===true&&p.eligible===true&&p.allPlansFirst===true&&
  (p.leasePolicy===undefined&&p.holdsGPUForQC===undefined||denseLeasePair(p.leasePolicy,p.holdsGPUForQC))&&
  Number.isSafeInteger(p.generationTasks)&&p.generationTasks>=2&&p.generationTasks<=5&&
  Number.isSafeInteger(p.qcTaskSlots)&&p.qcTaskSlots>=1&&p.qcTaskSlots<=4&&
  Number.isSafeInteger(p.descriptorLimit)&&p.descriptorLimit>0&&
  Number.isSafeInteger(p.maxRepairRounds)&&p.maxRepairRounds>=0;
}
export function denseRoundCounts(config,extraAttempts){
 if(!extraAttempts)return [];
 const rounds=Number(config.repairRounds);
 if(!Number.isSafeInteger(rounds)||rounds<1||rounds>config.productionDenseMaxRepairRounds)
  throw Error('Assumed repair rounds must fit the saved per-shot retry ceiling. The ceiling is not an error rate.');
 return Array.from({length:Math.min(rounds,extraAttempts)},(_,i)=>
  Math.floor(extraAttempts*(i+1)/Math.min(rounds,extraAttempts))-Math.floor(extraAttempts*i/Math.min(rounds,extraAttempts)));
}
export function imageRentalPercent(result){
 const ratio=result.rentalSeconds>0?result.gpuWork/result.rentalSeconds:0;
 // Truncate display precision: nonzero billed setup must never round to 100%.
 return (Math.floor(Math.max(0,Math.min(1,ratio))*1000)/10).toFixed(1)+'%';
}
// Union wall time, not the sum of concurrent API calls. Only intervals inside
// the owned rental with active checks and no image delivery work count here.
export function heldQualityIdleSeconds(tasks,windows){
 let total=0;
 for(const window of windows){
  const events=[];
  for(const task of tasks){
   const kind=['vision','qcBoundary'].includes(task.kind)?'qc':['delivered','infer'].includes(task.kind)?'image':null;
   if(!kind)continue;
   const start=Math.max(window.start,task.start+(task.kind==='qcBoundary'?task.qcActiveAfter||0:0)),end=Math.min(window.end,task.end);
   if(end>start)events.push([start,kind,1],[end,kind,-1]);
  }
  events.sort((a,b)=>a[0]-b[0]);let at=window.start,qc=0,image=0;
  for(const [time,kind,change]of events){if(qc>0&&image===0)total+=time-at;at=time;if(kind==='qc')qc+=change;else image+=change;}
 }
 return total;
}
