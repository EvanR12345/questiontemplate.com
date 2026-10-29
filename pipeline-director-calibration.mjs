// Read-only forecasts. Selecting a profile never edits a Studio project or calls an API.
export const DIRECTOR_PRICE_DATE='2026-10-10';
export const DIRECTOR_PRICE_URL='https://developers.openai.com/api/docs/models/gpt-6-luna';
export function directorTimingLabel(director){
  if(director.timingMeasured)return 'Timing observation; long-project ETA is extrapolated.';
  if(director.conditionsMatch===false)return 'Saved director configuration differs from calibration; timing is a comparison reference.';
  if(director.slotsMatch===false)return 'API-slot count differs from calibration; timing is an unmeasured reference.';
  return 'Selected-tier timing is unmeasured; finish time is a scenario, not a measured ETA.';
}
export function directorProfileOptions(evidence){
  const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  return (evidence.directorCalibration?.profiles||[]).filter(p=>p.status==='COMPLETE'&&!p.unknownRequests&&p.completedChapters===p.sourceChapters)
    .map(p=>`<option value="${escape(p.id)}">${escape(p.label)}</option>`).join('')+
    '<option value="historical">Earlier classic calibration</option>';
}
export function directorForecast(config,evidence) {
  const id=config.directorProfile||'historical',tier=config.directorTier||'default';
  if(!['default','flex','fast'].includes(tier))throw Error('Choose Standard, Flex or Fast director processing.');
  const multiplier=tier==='flex'?.5:tier==='fast'?2:1;
  const flexFactor=Number(config.flexLatencyFactor??1);
  if(!Number.isFinite(flexFactor)||flexFactor<1)throw Error('Flex timing factor must be a finite number of at least one.');
  const manual=(config.directorCostMode||'manual')==='manual';
  if(!['manual','measured'].includes(config.directorCostMode||'manual'))throw Error('Choose measured or manual director cost.');
  if(Number.isFinite(config.directorObservedSecondsPerStorySecond)&&config.directorObservedSecondsPerStorySecond>0&&config.directorObservedSamples>0&&Number.isFinite(config.directorObservedCostPerTwoHours)&&config.directorObservedCostPerTwoHours>=0){
    return {id,label:'Matched current production history',slots:Number(config.directorActualSlots)||config.apiSlots,
      secondsPerStorySecond:config.directorObservedSecondsPerStorySecond,
      costPerTwoHours:manual?config.directorCost:config.directorObservedCostPerTwoHours,
      aggregated:true,tier,installed:true,timingMeasured:true,conditionsMatch:true,slotsMatch:true,tierMeasured:true,
      scope:`${config.directorObservedSamples} matching successful chapter observations across ${config.directorObservedRuns} run(s). Paused, reused, failed and unknown work excluded; fresh narration supplies the scale. This is an extrapolation, not a guarantee.`,stages:[],priceMultiplier:1};
  }
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
  const observed=p.tiers?.[tier];
  const slots=Number(config.directorActualSlots)||p.slots;
  const slotsMatch=slots===p.slots;
  const conditionsMatch=config.directorConditionsMatch!==false;
  const usable=observed?.status==='COMPLETE'&&observed.timingEligible&&observed.completedChapters===observed.sourceChapters&&!observed.unknownRequests;
  if(usable&&(!Number.isFinite(observed.seconds)||observed.seconds<=0||!Number.isFinite(observed.estimatedUSD)||observed.estimatedUSD<0))throw Error('Incomplete selected-tier measurement.');
  // The measurement already contains its tested concurrency. Reserving that
  // capacity avoids applying an invented second speedup or overbooking visual QC.
  return {id,label:p.label.replace('· Standard',tier==='flex'?'· Flex':tier==='fast'?'· Fast':'· Standard'),slots,aggregated:true,tier,installed:p.installed,
    includesHandoffCommitTime:p.includesHandoffCommitTime===true,
    secondsPerStorySecond:(usable?observed.seconds:p.seconds*(tier==='flex'?flexFactor:1))/data.sourceNarrationSeconds,
    costPerTwoHours:manual?config.directorCost:(usable?observed.estimatedUSD:p.estimatedUSD*multiplier)/data.sourceNarrationSeconds*7200,
    sourceSeconds:data.sourceNarrationSeconds,sourceWords:data.sourceWords,sourceChapters:p.sourceChapters,
    measuredSeconds:usable?observed.seconds:p.seconds,measuredUSD:usable?observed.estimatedUSD:p.estimatedUSD,calls:usable?observed.calls:p.calls,shots:usable?observed.shots:p.shots,
    timingMeasured:conditionsMatch&&slotsMatch&&(tier==='default'||usable),conditionsMatch,slotsMatch,tierMeasured:conditionsMatch&&slotsMatch&&usable,priceMultiplier:multiplier,stages:usable?observed.stages||[]:p.stages||[],
    scope:p.scope+' Linear duration scaling from two chapters; different chapter sizes, cadence, repairs and rate limits can change it.'+
      (slotsMatch?'':` Saved production uses ${slots} API slots; calibration used ${p.slots}. The schedule reserves the actual slots, but its director time remains an unmeasured reference; no concurrency speed multiplier is invented.`)+
      (conditionsMatch?'':' Saved grouping/overlap/schema settings differ from this measurement; time and token cost are comparison references.')+
      (usable?` ${observed.samples} compatible selected-tier timing sample(s). ${observed.scope}`:tier==='flex'?` Flex latency is unmeasured: ${flexFactor}× Standard is your assumption; temporary unavailability can add more delay.`:
       tier==='fast'?' Fast latency is unmeasured for this profile; Standard time is retained as a comparison reference, not a Fast measurement.':'')};
}

export function migrateDirectorForecast(config,saved=false,hasMoves=false) {
  if(Object.hasOwn(config,'directorProfile'))return {...config};
  if(hasMoves)return {...config,directorProfile:'historical',directorTier:'default',directorCostMode:'manual',flexLatencyFactor:1};
  // Keep an explicit old dollar override. The old built-in value is replaced
  // with receipt-derived pricing; manual timeline moves are handled by the UI.
  const manual=saved&&Math.abs(Number(config.directorCost)-.38798)>1e-8;
  return {...config,directorProfile:'lean-current-standard',directorTier:'default',
    directorCostMode:manual?'manual':'measured',flexLatencyFactor:1};
}
