export const STRATEGIES = [
  ['dense', 'Dense images · plan first · minimize rented waiting'],
  ['align', 'Timed · align Luna + images · 15s buffer'],
  ['fastest', 'Timed · keep fastest video'],
  ['legacy', 'Existing chapter-by-chapter workflow'],
];
export function selectedStrategy(project) {
  return STRATEGIES.some(([id]) => id === project?.generationStrategy) ? project.generationStrategy : 'align';
}
export function strategyOptions(value, eligible, legacyOverlap = false) {
  if (!STRATEGIES.some(([id]) => id === value)) throw Error('Choose a supported generation strategy.');
  if (value !== 'legacy' && !eligible) throw Error('Timed generation requires cloud ComfyUI images, Luna and an API spending cap. Choose the existing workflow to use another configuration.');
  return {generationStrategy: value, overlap: value !== 'legacy' || legacyOverlap};
}
export function strategyDescription(value,managed=false,modelCopies=1) {
  const detail = value === 'dense'
    ? `Prepares all chapter plans, intro and required references before one initial image burst. Up to five generation tasks keep the configured image workers supplied while QC uses its own bounded queue and up to four shared API slots. ${managed?'Studio confirms GPU shutdown before the QC tail.':'Dense requires verified managed GPU startup and shutdown; it is unavailable on an externally controlled rental.'} Eligible repairs run in additional bounded batches, each with paid startup and shutdown before rechecks, preserving your existing per-shot retry ceilings. Setup, transfers and shutdown prevent a 100% image-work/rental guarantee. Dense requires eligible managed cloud images, Luna and automatic production.`
    : value === 'align'
    ? 'Keeps chapter planning moving, then starts accepted images when matching timing history predicts the planning tail is covered, with a 15-second allowance. Without matching history it plans the chapters first. The allowance is a target, not a guaranteed finish.'
    : value === 'fastest'
      ? `Starts accepted chapter images immediately while Luna plans the next chapter. Chapters needing new character portraits wait for planning to finish, protecting Luna’s canonical inputs. Story facts and handoffs stay ordered; ${modelCopies===2?'two verified independent model copies on one GPU':'one cloud image lane'} and the selected API concurrency limit remain in force.`
      : 'Uses the existing workflow and your overlap checkbox. Image admission can wait before directing the next chapter.';
  if(value==='dense')return detail+' With chapter render overlap enabled, a chapter renders after its own required QC and repairs pass while unrelated chapters continue. Rental coverage and final finish are projections, not measured full-production guarantees.';
  return detail + (managed ? ' Studio starts the configured stopped GPU when images are admitted and confirms shutdown before final story assembly. Ready chapters can render earlier while cloud work continues. Keep the helper and laptop awake for its spending-window watchdog. Rental timing and costs are estimates until verified.' :
    ' GPU rental start and stop remain external. Choosing a timed image-dispatch strategy does not schedule a pod or guarantee lower rental cost.');
}
export function strategySummary(value,managed=false) {
  return (value==='dense'?(managed?'Plan everything first, generate all initial images in one supplied burst, stop the GPU, then finish QC. Additional bounded repair bursts have separate rental costs.':'Dense needs a supported managed GPU connection, cloud images, Luna and automatic production.'):value==='align' ? 'Align image dispatch with ongoing chapter planning using matching history. Without matching history, plan first.' :
    value==='fastest' ? 'Start images for each ready chapter while Luna plans the next.' : 'Keep the existing workflow and overlap setting.') +
    (managed ? ' Studio manages the configured GPU during image generation.' : ' GPU rental must be started and stopped separately.');
}
export function forecastText(report) {
  if (!report) return 'Check readiness to load timing history. Old estimates are retained; incompatible settings are never treated as new measurements.';
  const h = report.history || {}, f = report.forecast || {};
  const minutes = n => Number.isFinite(n) ? `${(n / 60).toFixed(1)} min` : 'unknown';
  return `${h.observations || 0} profiled observations retained across ${h.runs || 0} runs. Remaining narration + directing: ${minutes(f.planningSeconds)}. Accepted image work: ${minutes(f.readyImageSeconds)}. This is partial work, not total video ETA. ${f.unknownStages?.length ? 'Missing matching history: ' + f.unknownStages.join(', ') + '. ' : ''}Only successful fresh matching work calibrates timings. Failed, paused, reused and unresolved work stays in history. Observed ranges are not guarantees.`;
}

export function imageWorkEstimate(timings,chapter,after,remaining) {
  const rows=timings.filter(t=>t.stage==='Image + quality checks' && t.chapter===chapter &&
    t.finished-t.seconds>=after && t.status==='COMPLETE' && !t.paused && !t.reused && !t.usagePending &&
    Number.isFinite(t.finished) && t.seconds>0 && t.performanceProfile);
  if(rows.length<3 || new Set(rows.map(t=>JSON.stringify(t.performanceProfile))).size!==1)return null;
  let busy=0,end=-Infinity;
  for(const t of rows.toSorted((a,b)=>(a.finished-a.seconds)-(b.finished-b.seconds))){
    busy+=Math.max(0,t.finished-Math.max(t.finished-t.seconds,end));end=Math.max(end,t.finished);
  }
  return {seconds:busy/rows.length*Math.max(0,remaining),samples:rows.length,busySeconds:busy};
}
