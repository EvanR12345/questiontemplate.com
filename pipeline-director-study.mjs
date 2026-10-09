const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=value=>'$'+value.toFixed(4);
const duration=value=>{const seconds=Math.round(value);return `${Math.floor(seconds/60)}:${(seconds%60).toString().padStart(2,'0')}`;};

export function directorStudyProjections(study,minutes){
  if(!Number.isFinite(minutes)||minutes<=0||!Number.isFinite(study.sourceNarrationSeconds)||study.sourceNarrationSeconds<=0)
    throw Error('Director projection requires a positive duration and measured narration length.');
  return study.wholePlans.map(arm=>{
    if(!Number.isFinite(arm.wallSeconds)||arm.wallSeconds<=0||!Number.isFinite(arm.estimatedUSD)||arm.estimatedUSD<0)
      throw Error('Incomplete director evidence.');
    const scale=minutes*60/study.sourceNarrationSeconds;
    return {...arm,projectedWallSeconds:arm.wallSeconds*scale,projectedUSD:arm.estimatedUSD*scale};
  });
}

export function directorStudyHTML(study,minutes=120){
  const projected=directorStudyProjections(study,minutes);
  const max=Math.max(...study.wholePlans.map(a=>a.wallSeconds));
  const measured=study.wholePlans.find(a=>a.id==='parallel-2');
  const stages=[...(measured?.stages||[])].sort((a,b)=>b.workSeconds-a.workSeconds);
  return `<div class="section-head"><h2>Luna: measured directing improvements</h2><span class="badge">${esc(study.date)}</span></div>
    <p>${esc(study.model)} · ${esc(study.reasoning)} reasoning · ${esc(study.serviceTier)} processing. Two saved chapters, ${duration(study.sourceNarrationSeconds)} of narration. Audio was reused; no images or complete videos were generated in this test.</p>
    <h3>Complete chapter planning</h3><div class="evidence-table"><table><thead><tr><th>Setting</th><th>Observed elapsed</th><th>API work¹</th><th>API cost</th><th>Shots planned</th></tr></thead><tbody>${study.wholePlans.map(a=>`<tr><td>${esc(a.label)}</td><td>${duration(a.wallSeconds)}</td><td>${duration(a.workSeconds)}</td><td>${money(a.estimatedUSD)}</td><td>${a.shots}</td></tr>`).join('')}</tbody></table></div>
    <div role="img" aria-label="Measured director elapsed time comparison">${study.wholePlans.map(a=>`<div class="compare-row"><div class="label">${esc(a.label)}</div><div class="track"><i style="width:${a.wallSeconds/max*100}%;background:#89aa8f"><span>${duration(a.wallSeconds)}</span></i></div></div>`).join('')}</div>
    <p class="caption">¹ API work sums call durations; overlapping calls make it larger than elapsed time. One complete-plan run per setting. Luna chose different shot counts, so this does not establish statistical significance, identical quality, or the best slot count. Short supplements were faster in the fixed-batch test below; the complete concise plan took longer than the ordinary two-slot plan and contained more shots.</p>
    <details><summary>Where Luna spends time · measured two-task run</summary><p class="caption">Actual per-call records for these two chapters, rather than the main schedule's assumed pass weights. Work durations include response collection and validation; parallel rows overlap. Output tokens include reasoning tokens once. Unknown first-text timings stay unknown. Cached calls are excluded.</p><div class="evidence-table"><table><thead><tr><th>Pass</th><th>Calls</th><th>API work</th><th>API cost</th><th>Input tokens</th><th>Output tokens</th><th>Median first text</th></tr></thead><tbody>${stages.map(s=>`<tr><td>${esc(s.stage)}</td><td>${s.calls}</td><td>${s.workSeconds.toFixed(1)}s</td><td>${money(s.estimatedUSD)}</td><td>${s.inputTokens.toLocaleString('en-US')}</td><td>${s.outputTokens.toLocaleString('en-US')}</td><td>${Number.isFinite(s.medianFirstTextSeconds)?s.medianFirstTextSeconds.toFixed(2)+'s':'Unknown'}</td></tr>`).join('')}</tbody></table></div></details>
    <h3>Repeated identical prompt batches</h3><p class="caption">Three frozen 12-shot batches per round; four rounds per setting, with test order rotated. The short supplement is added to the unchanged grounded draft, preserving canonical identity and story constraints.</p><div class="evidence-table"><table><thead><tr><th>Setting</th><th>Median elapsed</th><th>Observed range</th><th>Mean API cost / round</th><th>Median supplement words</th></tr></thead><tbody>${study.promptBatches.map(a=>`<tr><td>${esc(a.label)}</td><td>${a.medianWallSeconds.toFixed(1)}s</td><td>${a.minWallSeconds.toFixed(1)}–${a.maxWallSeconds.toFixed(1)}s</td><td>${money(a.meanEstimatedUSD)}</td><td>${a.medianSupplementWords}</td></tr>`).join('')}</tbody></table></div>
    <details><summary>Scale director-only observations to ${esc(minutes)} minutes</summary><div class="evidence-table"><table><thead><tr><th>Setting</th><th>Projected directing time</th><th>Projected API cost</th></tr></thead><tbody>${projected.map(a=>`<tr><td>${esc(a.label)}</td><td>${duration(a.projectedWallSeconds)}</td><td>${money(a.projectedUSD)}</td></tr>`).join('')}</tbody></table></div><p class="caption">Linear extrapolation from two chapters, not a measured long production. Excludes narration, images, vision QC, retries outside these tests, rental setup/idle, storage and rendering. Chapter length, cadence, ambiguity, rate limits and output variation can change it. The main schedule retains its earlier calibration; these results are not silently substituted into GPU comparisons.</p></details>
    <h3>What can run together now</h3><p>${esc(study.installedBoundary)} Use Studio → Settings → Advanced AI settings → Independent Luna tasks, with Overlap cloud tasks enabled. Short visual-direction supplements are a separate optional setting.</p>
    <details><summary>Exploratory larger-context test · kept out of production defaults</summary><p>One chapter with up to ${study.largerGroupPilot.groupSentences} sentences per group took ${duration(study.largerGroupPilot.wallSeconds)}, versus ${duration(study.largerGroupPilot.comparisonParallel2Seconds)} with the ordinary group size. It planned ${study.largerGroupPilot.shots} shots instead of ${study.largerGroupPilot.comparisonParallel2Shots}. ${esc(study.largerGroupPilot.scope)}</p></details>
    <p><strong>Next opportunity:</strong> ${esc(study.nextCandidate)}</p><p class="caption">Batch can reduce API prices by 50%, but has a 24-hour completion window; it is not an immediate-speed setting. Output reduction and independent calls are the first targeted optimizations. <a href="https://developers.openai.com/api/docs/guides/batch" target="_blank" rel="noopener">Batch documentation</a> · <a href="https://developers.openai.com/api/docs/guides/latency-optimization" target="_blank" rel="noopener">Latency guidance</a></p>
    <p class="caption">All study requests together: ${money(study.spentUSD)} of ${money(study.budgetUSD)}. No unresolved usage; original saved story was preserved. No image-quality guarantee follows from valid JSON or faster text.</p>`;
}

export async function mountDirectorStudy(host,minutes=120){
  const response=await fetch(new URL('./pipeline-director-study.json',import.meta.url));
  if(!response.ok)throw Error('Director study unavailable.');
  const study=await response.json();host.innerHTML=directorStudyHTML(study,minutes);
  return {update(minutes){host.innerHTML=directorStudyHTML(study,minutes);}};
}
