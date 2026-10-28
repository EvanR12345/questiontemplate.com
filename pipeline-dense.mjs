// Scheduling assumptions only. No inference speeds, voice timings or prices.
export const denseActive=config=>config.policy==='current'&&config.productionGenerationStrategy==='dense';
export function denseCapability(profile){
 const p=profile?.denseImagePhase;
 return p?.version===1&&p.supported===true&&p.eligible===true&&p.allPlansFirst===true&&
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
