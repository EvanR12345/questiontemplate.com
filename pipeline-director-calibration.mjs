// Read-only forecasts. Selecting a profile never edits a Studio project or calls an API.
export const DIRECTOR_PRICE_DATE='2026-10-10';
export const DIRECTOR_PRICE_URL='https://developers.openai.com/api/docs/models/gpt-6-luna';
export function directorForecast(config,evidence) {
  const id=config.directorProfile||'historical',tier=config.directorTier||'default';
  if(!['default','flex','fast'].includes(tier))throw Error('Choose Standard, Flex or Fast director processing.');
  const multiplier=tier==='flex'?.5:tier==='fast'?2:1;
  const flexFactor=Number(config.flexLatencyFactor??1);
  if(!Number.isFinite(flexFactor)||flexFactor<1)throw Error('Flex timing factor must be a finite number of at least one.');
  const manual=(config.directorCostMode||'manual')==='manual';
  if(!['manual','measured'].includes(config.directorCostMode||'manual'))throw Error('Choose measured or manual director cost.');
  if(id==='historical')return {id,label:'Earlier classic calibration',slots:config.apiSlots,
    secondsPerStorySecond:evidence.calibration.directorSeconds/evidence.calibration.narrationSeconds*(tier==='flex'?flexFactor:1),
    costPerTwoHours:manual?config.directorCost:.38798*multiplier,aggregated:false,
    scope:'Historical total allocated by assumed pass weights. Not a current isolated pass measurement.',tier,
    timingMeasured:tier==='default',priceMultiplier:multiplier};
  const data=evidence.directorCalibration;
  if(!data)throw Error('Latest director calibration unavailable. Choose the historical profile explicitly; no old timing is silently substituted.');
  const p=data.profiles.find(p=>p.id===id);
  if(!p||p.status!=='COMPLETE'||p.unknownRequests||p.completedChapters!==p.sourceChapters)
    throw Error('Director profile requires a fully completed, settled measurement.');
  if(!Number.isFinite(p.seconds)||p.seconds<=0||!Number.isFinite(data.sourceNarrationSeconds)||data.sourceNarrationSeconds<=0||!Number.isFinite(p.estimatedUSD)||p.estimatedUSD<0)
    throw Error('Incomplete director timing or cost evidence.');
  if(config.policy==='current'&&!p.installed)throw Error('This director format is a research prototype. Choose Proposed scheduling or an installed director profile.');
  // The measurement already contains its tested concurrency. Reserving that
  // capacity avoids applying an invented second speedup or overbooking visual QC.
  return {id,label:p.label,slots:p.slots,aggregated:true,tier,installed:p.installed,
    secondsPerStorySecond:p.seconds/data.sourceNarrationSeconds*(tier==='flex'?flexFactor:1),
    costPerTwoHours:manual?config.directorCost:p.estimatedUSD/data.sourceNarrationSeconds*7200*multiplier,
    sourceSeconds:data.sourceNarrationSeconds,sourceWords:data.sourceWords,sourceChapters:p.sourceChapters,
    measuredSeconds:p.seconds,measuredUSD:p.estimatedUSD,calls:p.calls,shots:p.shots,
    timingMeasured:tier==='default',priceMultiplier:multiplier,stages:p.stages||[],
    scope:p.scope+' Linear duration scaling from two chapters; different chapter sizes, cadence, repairs and rate limits can change it.'+
      (tier==='flex'?` Flex latency is unmeasured: ${flexFactor}× Standard is your assumption; temporary unavailability can add more delay.`:
       tier==='fast'?' Fast latency is unmeasured for this profile; Standard time is retained as a comparison reference, not a Fast measurement.':'')};
}

export function migrateDirectorForecast(config,saved=false,hasMoves=false) {
  if(Object.hasOwn(config,'directorProfile'))return {...config};
  if(hasMoves)return {...config,directorProfile:'historical',directorTier:'default',directorCostMode:'manual',flexLatencyFactor:1};
  // Keep an explicit old dollar override. The old built-in value is replaced
  // with receipt-derived pricing; manual timeline moves are handled by the UI.
  const manual=saved&&Math.abs(Number(config.directorCost)-.38798)>1e-8;
  return {...config,directorProfile:'lean-standard',directorTier:'default',
    directorCostMode:manual?'manual':'measured',flexLatencyFactor:1};
}
