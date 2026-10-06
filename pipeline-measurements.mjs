// Keep different hosts/attempts separate; never select one incomplete fast round.
export function latestProfiles(gpu,resolution,encodingProfile='cached',executionMode='resident',attempt='latest'){
  if(!['cached','fresh'].includes(encodingProfile))throw Error('Unknown conditioning profile.');
  if(!['resident','pipeline','hybrid'].includes(executionMode))throw Error('Unknown execution method.');
  const counts=new Map();
  const eligible=((executionMode==='hybrid'?gpu?.hybridProfiles:executionMode==='pipeline'?gpu?.pipelineProfiles:encodingProfile==='fresh'?gpu?.productionProfiles:gpu?.profiles)||[])
    .filter(p=>p.resolution===resolution&&(p.encodingProfile||'cached')===encodingProfile&&!p.postControl&&p.rounds>=2&&(attempt==='latest'||p.attempt===attempt));
  const latest=eligible.reduce((a,b)=>!a||(b.runStartedAt||0)>=(a.runStartedAt||0)?b:a,null);
  for(const p of eligible){
    if(p.attempt!==latest?.attempt)continue;
    const count=executionMode!=='resident'?p.clientSlots:p.workers;
    const previous=counts.get(count);
    if(!previous||(p.runStartedAt||0)>=(previous.runStartedAt||0))counts.set(count,p);
  }
  return [...counts.values()].sort((a,b)=>(executionMode!=='resident'?a.clientSlots-b.clientSlots:a.workers-b.workers));
}
