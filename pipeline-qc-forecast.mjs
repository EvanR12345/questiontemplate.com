// Read-only forecast selection. A benchmark is never a spending authorization.
export function qualityConfig(config,evidence){
  const next={...config};
  if(config.qcBenchmarkId){
    next.qcSeconds=Number.isFinite(config.qcHistorySeconds)?config.qcHistorySeconds:0;
    if(!config.qcCostManual&&Number.isFinite(config.qcHistoryCost))next.qcCost=config.qcHistoryCost;
  }
  next.qcBenchmarkId='';next.qcBenchmarkObservedAt=0;next.qcBenchmarkNote='';
  const slots=config.policy==='serial'?1:Number(config.qcEffectiveSlots??config.apiSlots);
  const identity=config.qcHistoryIdentity;
  const historyMatches=identity?.policy===config.qc&&identity.apiSlots===slots
    &&identity.processingTier===config.directorTier&&identity.compatibilityHash===config.qcCompatibilityHash;
  next.qcHistoryMatches=Boolean(historyMatches);
  if(!['practical','strict'].includes(config.qc))return next;
  const calibration=evidence.qcCalibration;
  if(calibration?.version!==1||calibration.timingScope!=='api-call'||!Array.isArray(calibration.cohorts))return next;
  const cohorts=calibration.cohorts.filter(row=>
    row&&row.status==='COMPLETE'&&row.timingScope==='api-call'&&row.unknownRequests===0
    &&row.policy===config.qc&&row.apiSlots===slots&&slots<=Number(config.qcProductionTasks||Infinity)
    &&typeof config.qcCompatibilityHash==='string'&&config.qcCompatibilityHash.length===64
    &&row.qcCompatibilityHash===config.qcCompatibilityHash
    &&row.processingTier===config.directorTier&&row.processingTier===config.qcProcessingTier
    &&config.resolution==='720p'&&row.checks>0&&row.uniqueImages>0
    &&Number.isFinite(row.secondsPerCheck)&&row.secondsPerCheck>0&&row.secondsPerCheck<=3600
    &&Number.isFinite(row.costUSDPerCheck)&&row.costUSDPerCheck>=0&&row.costUSDPerCheck<=1
    &&Number.isFinite(row.observedAt)&&row.observedAt>Number(historyMatches?config.qcHistoryLatest||0:0));
  const cohort=cohorts.sort((a,b)=>b.observedAt-a.observedAt)[0];
  if(!cohort)return next;
  next.qcSeconds=cohort.secondsPerCheck;
  if(!config.qcCostManual)next.qcCost=cohort.costUSDPerCheck;
  next.qcBenchmarkId=cohort.id;next.qcBenchmarkObservedAt=cohort.observedAt;
  next.qcBenchmarkNote=`QC forecast updated from ${new Date(cohort.observedAt*1000).toISOString().slice(0,10)}: ${cohort.checks} checks of ${cohort.uniqueImages} unique images, ${cohort.apiSlots} actual API slots, ${cohort.referenceImages} reference image. Initial API-call time and usage cost only; repairs, manual waiting and full-video completion are unmeasured.`;
  return next;
}
