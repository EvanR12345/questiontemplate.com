const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=n=>n.toLocaleString('en-CA');
const money=n=>'$'+n.toFixed(4);
const time=n=>`${Math.floor(n/60)}m ${(n%60).toFixed(1)}s`;

export function chapterRequestHTML(data){
  if(!data)return '';
  const rows=data.conditions||[];
  return `<details><summary>One storyboard request per chapter · real tests</summary>
    <p>Same saved story, Luna Standard, cadence, 8 API slots and 24k output allowance. Both arms use whole-chapter source-fact groups. Full fidelity checks and bounded repairs remain enabled. Failed attempts are retained below; their elapsed time is time until rejection, not successful production speed.</p>
    <div class="evidence-table"><table><thead><tr><th>Condition</th><th>Outcome</th><th>Completed chapters</th><th>Shots saved</th><th>Elapsed</th><th>API requests</th><th>Reported input tokens</th><th>Reported output tokens</th><th>Settled API cost</th><th>Reserved unknown cost</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.condition)}</td><td>${esc(r.status)}${r.rejection?` · ${esc(r.rejection)}`:''}</td><td>${r.chapters.length} / 2</td><td>${num(r.shots)}</td><td>${time(r.seconds)}</td><td>${num(r.requests)}</td><td>${num(r.inputTokens)}</td><td>${num(r.outputTokens)}</td><td>${money(r.estimatedUSD)}</td><td>${money(r.reservedUSD||0)}</td></tr>`).join('')}</tbody></table></div>
    <p>“Hybrid” makes required story-change cuts mandatory and bounds the additional shots. Compare whole-chapter-hybrid with small-groups-hybrid to hold the response format constant. Earlier probes used different formats. The timed-out attempt has incomplete reported tokens and a retained cost reservation; it is excluded from successful speed comparisons. Fresh AI plans/facts can differ; this small study does not establish image quality or statistical significance.</p>
    <div class="evidence-table"><table><thead><tr><th>Condition</th><th>Storyboard calls</th><th>Storyboard input</th><th>Storyboard output</th><th>Storyboard active time</th><th>Storyboard API cost</th></tr></thead><tbody>${rows.map(r=>{const s=r.stages.find(s=>s.name==='Visual storyboard director');return s?`<tr><td>${esc(r.condition)}</td><td>${s.calls}</td><td>${num(s.inputTokens)}</td><td>${num(s.outputTokens)}</td><td>${time(s.activeSeconds)}</td><td>${money(s.estimatedUSD)}</td></tr>`:'';}).join('')}</tbody></table></div>
    <p class="caption">Storyboard active time merges measured call intervals, counting overlaps once and excluding gaps spent on other passes. It is not GPU execution or whole-video elapsed time. Fewer input tokens can reduce cost while a longer single output reduces parallelism. No production workflow, saved projects, audio or video was replaced.</p></details>`;
}

export function standardLunaHTML(data,minutes=120){
  if(!data)return '';
  if(!Number.isFinite(minutes)||minutes<=0||!Number.isFinite(data.sourceNarrationSeconds)||data.sourceNarrationSeconds<=0)
    throw Error('Positive measured narration and projection duration required.');
  const runs=data.runs||[];
  const rows=[data.baseline,...runs];
  const scale=minutes*60/data.sourceNarrationSeconds;
  return `<section aria-label="Standard Luna efficiency study"><h3>Standard-priced Luna · latest complete-plan tests</h3>
    <p>Same saved two-chapter source, character library, references and narration timings. No image generation. Lean plans retain source and full initial storyboard review, bounded repairs and the selected picture cadence. Repair verification checks changed shots and immediate neighbors with full source/state; it does not repeat the entire initial review. Lean source facts and repairs use at least medium reasoning; visual work uses the selected effort. No premium Fast API processing.</p>
    <div class="evidence-table"><table><thead><tr><th>Run</th><th>Elapsed</th><th>Requests</th><th>Input tokens</th><th>Output tokens</th><th>Shots</th><th>Estimated API cost</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.label)}</td><td>${time(r.seconds)}</td><td>${num(r.calls)}</td><td>${num(r.inputTokens)}</td><td>${num(r.outputTokens)}</td><td>${num(r.shots)}</td><td>${money(r.estimatedUSD)}</td></tr>`).join('')}</tbody></table></div>
    <p class="caption">${esc(data.baselineNote||'The baseline is a separate measured classic run, not a simultaneous control.')} Different generated plans/shot counts and small samples do not prove statistical significance, identical visual quality or a guaranteed 3× speedup. Failed prototypes do not count as successful speed samples. Existing project settings stay unchanged.</p>
    <details><summary>Director-only projections for ${esc(minutes)} minutes</summary><div class="evidence-table"><table><thead><tr><th>Run</th><th>Projected elapsed</th><th>Projected API cost</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.label)}</td><td>${time(r.seconds*scale)}</td><td>${money(r.estimatedUSD*scale)}</td></tr>`).join('')}</tbody></table></div><p class="caption">Linear extrapolations, not measured long productions. Excludes narration, images, vision QC, rentals, storage and rendering. Main planner calibration is unchanged; real compatible production history learns separately.</p></details>
    <p>Studio → Settings → Advanced AI settings → <strong>Lean Luna · Standard · experimental</strong> → 8 independent tasks. Review and Save before requesting new analysis. “Director reasoning: Fast” is low reasoning; “API processing: Fast” is a separately priced premium service.</p>
    <p><strong>${esc(data.recommendation||'Experimental opt-in; do not assume a reliable speed or quality gain.')}</strong> The identical-workflow repeat ${esc(data.repeat?.status||'has not been measured')}${data.repeat?` after ${time(data.repeat.seconds)}, completing ${data.repeat.completedChapters} / 2 chapters`:''}. Remaining major findings stop the plan, preserving previous work.</p>
    <details><summary>Why one complete plan still sent ${num(runs[0]?.inputTokens||0)} input tokens</summary><p class="caption">${num(runs[0]?.calls||0)} separate requests for casting, ordered source facts, independent visual groups, review and repair. A request sends the relevant source, state, instructions and response schema again; input is not just the story. This is the completed current candidate, separate from the earlier 3.35-million-token research total. Work durations overlap.</p><div class="evidence-table"><table><thead><tr><th>Pass</th><th>Requests</th><th>Input tokens</th><th>Output tokens</th><th>Call work</th><th>API cost</th></tr></thead><tbody>${(data.stages||[]).map(s=>`<tr><td>${esc(s.stage)}</td><td>${s.calls}</td><td>${num(s.inputTokens)}</td><td>${num(s.outputTokens)}</td><td>${time(s.workSeconds)}</td><td>${money(s.estimatedUSD)}</td></tr>`).join('')}</tbody></table></div></details>
    <details><summary>Latest study attempts · failures retained</summary><div class="evidence-table"><table><thead><tr><th>Attempt</th><th>Outcome</th><th>Elapsed</th><th>Completed chapters</th><th>Estimated API cost</th></tr></thead><tbody>${(data.attempts||[]).map(r=>`<tr><td>${esc(r.label)}</td><td>${esc(r.status)}</td><td>${time(r.seconds)}</td><td>${r.completedChapters} / 2</td><td>${money(r.estimatedUSD)}</td></tr>`).join('')}</tbody></table></div></details>
    ${chapterRequestHTML(data.chapterRequestStudy)}
    <p class="caption">Latest isolated study, including rejected work: ${money(data.spentUSD)} settled + ${money(data.reservedUSD||0)} reserved / ${money(data.capUSD)}; ${num(data.experiments)} experiments. ${data.pendingUsage?'Usage unresolved; do not treat this as a final total.':'No outstanding reservations.'} Recorded API usage estimates are separate from account invoices or free credits.</p></section>`;
}

export function lunaAuditHTML(data){
  if(!data)return '';
  const largest=Math.max(...data.stages.map(s=>s.inputTokens),1);
  return `<section aria-label="Complete Luna input token audit"><h3>Where all ${num(data.inputTokens)} input tokens went</h3>
    <p><strong>${num(data.requests)} API requests across ${data.experiments} experiments.</strong> This is the earlier closed research study, including failed and rejected prototypes. It is not one video's story size. Every recorded input token is assigned to one pass; ${num(data.unassignedTokens)} remain unassigned.</p>
    <div role="img" aria-label="Input tokens by director pass">${data.stages.map(s=>`<div class="compare-row"><div class="label">${esc(s.stage)}</div><div class="track"><i style="width:${s.inputTokens/largest*100}%;background:#89aa8f"><span>${num(s.inputTokens)}</span></i></div></div>`).join('')}</div>
    <div class="evidence-table"><table><thead><tr><th>Feature / pass</th><th>Requests</th><th>Input tokens</th><th>Output tokens</th><th>API work</th><th>API cost</th><th>If removed</th></tr></thead><tbody>${data.stages.map(s=>`<tr><td>${esc(s.stage)}</td><td>${num(s.calls)}</td><td>${num(s.inputTokens)} (${(s.inputTokens/data.inputTokens*100).toFixed(2)}%)</td><td>${num(s.outputTokens)}</td><td>${time(s.workSeconds)}</td><td>${money(s.estimatedUSD)}</td><td>${esc(s.removal)}</td></tr>`).join('')}</tbody></table></div>
    <p class="caption">API work sums call durations (${time(data.workSeconds)}); parallel calls overlap. Removing a pass does not necessarily save that amount of elapsed time. These are historical charges tied to each pass, not predictions of ablation savings. Removing validation may add later image-repair costs or allow wrong story facts.</p>
    <p class="caption">The API reports tokens for each whole request, not a token-by-token split between story, instructions, schemas, identity and review data. Old request bodies were not retained; their hashes cannot reconstruct them. Exact internal allocation cannot be recovered. New instrumentation stores component character counts, receipt IDs and call timings without raw story text; characters are not tokens.</p>
    <details data-luna-requests><summary>Every request · filterable audit of ${num(data.requests)} receipts</summary><p class="caption">Request numbers are anonymous. Stream time covers collection, not serial delay or feature-ablation savings. All returned tokens and charges remain accounted for, including failed experiments.</p><label>Director pass <select data-luna-pass><option value="">All passes</option>${data.stages.map(s=>`<option value="${esc(s.stage)}">${esc(s.stage)}</option>`).join('')}</select></label><label>Experiment <select data-luna-experiment><option value="">All experiments</option>${data.runs.map((r,i)=>`<option value="${i+1}">${esc(r.label)}</option>`).join('')}</select></label><div data-luna-request-table></div></details>
    <details><summary>All experiments · including failures</summary><div class="evidence-table"><table><thead><tr><th>Experiment</th><th>Outcome</th><th>Requests</th><th>Input tokens</th><th>Elapsed</th><th>API cost</th></tr></thead><tbody>${data.runs.map(r=>`<tr><td>${esc(r.label)}</td><td>${esc(r.status)}</td><td>${num(r.calls)}</td><td>${num(r.inputTokens)}</td><td>${time(r.seconds)}</td><td>${money(r.estimatedUSD)}</td></tr>`).join('')}</tbody></table></div></details>
    <p>Reconciled against the private ledger: ${num(data.outputTokens)} output tokens (reasoning included once), ${num(data.cachedInputTokens)} cached input tokens, ${money(data.estimatedUSD)} estimated API cost. Story text, credentials, account identifiers and references are excluded from this published audit.</p></section>`;
}

export function lunaRequestPage(data,{stage='',experiment='',page=0}={}){
  const filtered=(data.requestRows||[]).filter(r=>(!stage||r.feature===stage)&&(!experiment||r.experiment===Number(experiment)));
  const pages=Math.max(1,Math.ceil(filtered.length/25));
  const index=Math.max(0,Math.min(pages-1,Number.isInteger(page)?page:0));
  const rows=filtered.slice(index*25,index*25+25);
  const html=`<p role="status">${num(filtered.length)} requests · page ${index+1} / ${pages}</p><div class="evidence-table"><table><thead><tr><th>Request</th><th>Experiment</th><th>Pass</th><th>Tier</th><th>Input tokens</th><th>Output tokens</th><th>Cached input</th><th>Stream time</th><th>API cost</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${r.number}</td><td>${r.experiment}</td><td>${esc(r.feature)}</td><td>${esc(r.tier)}</td><td>${num(r.inputTokens)}</td><td>${num(r.outputTokens)}</td><td>${num(r.cachedInputTokens)}</td><td>${Number.isFinite(r.streamSeconds)?time(r.streamSeconds):'Unknown'}</td><td>${money(r.estimatedUSD)}</td></tr>`).join('')}</tbody></table></div><div class="toolbar"><button type="button" data-luna-prev ${index===0?'disabled':''}>Previous 25</button><button type="button" data-luna-next ${index===pages-1?'disabled':''}>Next 25</button></div>`;
  return {html,page:index,count:filtered.length};
}

export function mountLunaRequests(host,data){
  const details=host.querySelector('[data-luna-requests]');if(!details||!data)return;
  const pass=details.querySelector('[data-luna-pass]'),experiment=details.querySelector('[data-luna-experiment]');
  const table=details.querySelector('[data-luna-request-table]');let page=0;
  const render=()=>{const result=lunaRequestPage(data,{stage:pass.value,experiment:experiment.value,page});page=result.page;table.innerHTML=result.html;};
  details.addEventListener('toggle',()=>{if(details.open)render();});
  for(const field of [pass,experiment])field.addEventListener('change',()=>{page=0;render();});
  table.addEventListener('click',event=>{if(event.target.closest('[data-luna-next]')){page++;render();}
    else if(event.target.closest('[data-luna-prev]')){page--;render();}});
}
