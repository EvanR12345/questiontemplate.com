// Keep polling from replacing focused controls and closed, potentially huge histories.
const htmlCache = new WeakMap();
export function updateHTML(node, html) {
  if (!node || htmlCache.get(node) === html) return false;
  const opened = new Set([...node.querySelectorAll('details[data-ui][open]')].map(d => d.dataset.ui));
  node.innerHTML = html;
  htmlCache.set(node, html);
  for (const detail of node.querySelectorAll('details[data-ui]'))
    if (opened.has(detail.dataset.ui)) detail.open = true;
  return true;
}

export function queuePart(node, name) {
  let child = [...node.children].find(el => el.dataset.queuePart === name);
  if (!child) {
    child = node.ownerDocument.createElement('div');
    child.dataset.queuePart = name;
    node.append(child);
  }
  return child;
}

const escape = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time = n => `${Math.floor((n || 0)/60).toString().padStart(2,'0')}:${Math.floor((n || 0)%60).toString().padStart(2,'0')}`;
const seconds = n => Number.isFinite(n) && n >= 0 ? n : 0;

export function timingSummaryHTML(run) {
  const totals = new Map();
  for (const entry of run.timings || []) {
    if (entry.detail) continue;
    const stage = entry.stage || 'Unlabeled stage';
    const row = totals.get(stage) || {total:0, scopes:new Map()};
    const scope = entry.chapter ? 'Chapter '+entry.chapter : 'Project / intro';
    row.total += seconds(entry.seconds);
    row.scopes.set(scope, (row.scopes.get(scope) || 0) + seconds(entry.seconds));
    totals.set(stage, row);
  }
  let cost = '';
  if (run.costs) {
    const api = Number.isFinite(run.costs.apiEstimatedUSD) ? '$'+run.costs.apiEstimatedUSD.toFixed(4) : 'not recorded';
    const gpu = Number.isFinite(run.costs.gpuWindowEstimatedUSD) ? '$'+run.costs.gpuWindowEstimatedUSD.toFixed(4) : 'not recorded';
    cost = `<p class="muted">Estimated run cost: OpenAI ${api} · GPU window ${gpu}. Storage and account billing are reported separately.</p>`;
  }
  const receipt = run.completionReceipt;
  if (Number.isFinite(receipt?.balancePlusAPIEstimateUSD))
    cost += `<p class="muted">Completion snapshot, including earlier rejected work and account storage/settlement: $${receipt.balancePlusAPIEstimateUSD.toFixed(2)}. Recorded ${escape(new Date(receipt.verifiedAt*1000).toLocaleString())}. Retained storage continues billing while GPUs are stopped.</p>`;
  return `${cost}<p class="muted">Recorded stage wall time includes loading, retries and pauses. Reused assets take only their validation time. Director passes are details within directing; GPU rental overlaps generation. Do not add either to the stage totals again.</p><table><thead><tr><th>Stage</th><th>Total time</th><th>By chapter</th></tr></thead><tbody>${[...totals].map(([stage,row])=>`<tr><td>${escape(stage)}</td><td>${time(row.total)}</td><td>${[...row.scopes].map(([scope,n])=>escape(scope)+': '+time(n)).join('<br>')}</td></tr>`).join('')}</tbody></table>`;
}

export function timingPageHTML(timings, page = 0, size = 100) {
  const pages = Math.max(1, Math.ceil(timings.length/size));
  page = Math.min(pages-1, Math.max(0, page));
  const start = page*size, end = Math.min(timings.length, start+size);
  return {page, pages, html:`<p class="muted" role="status">${timings.length ? start+1 : 0}–${end} of ${timings.length} recorded entries. Download the timing report for the complete history.</p><div class="toolbar"><button data-timing-page="previous" ${page===0?'disabled':''}>Previous entries</button><button data-timing-page="next" ${page===pages-1?'disabled':''}>Next entries</button></div>${timings.slice(start,end).map(t=>`<p class="muted">${t.chapter?'Chapter '+escape(t.chapter)+' · ':''}${escape(t.stage)}${t.shot?' · '+escape(t.shot):''} · ${seconds(t.seconds).toFixed(1)} s${t.reused?' · reused':''}${t.status==='FAILED'?' · failed attempt':''}</p>`).join('')}`};
}

const histories = new WeakMap();
export function renderTimingHistory(target, projectId, run) {
  let state = histories.get(target);
  if (!state || state.project !== projectId || !target.querySelector('[data-timing-summary]')) {
    target.innerHTML = '<details data-ui="production-times"><summary>Costs and measured times</summary><div data-timing-summary></div><details data-ui="production-passes"><summary>Every stage and director pass</summary><div data-timing-entries></div></details></details>';
    state = {project:projectId, page:0};
    histories.set(target, state);
    state.outer = target.querySelector('[data-ui="production-times"]');
    state.inner = target.querySelector('[data-ui="production-passes"]');
    state.summary = target.querySelector('[data-timing-summary]');
    state.entries = target.querySelector('[data-timing-entries]');
    state.outer.addEventListener('toggle', ()=>state.refresh());
    state.inner.addEventListener('toggle', ()=>state.refresh());
    state.entries.addEventListener('click', event=>{
      const button = event.target.closest('[data-timing-page]');
      if (!button || button.disabled) return;
      state.page += button.dataset.timingPage==='next' ? 1 : -1;
      state.refresh();
    });
  }
  state.run = run;
  state.refresh = ()=>{
    if (!state.outer.open) return;
    const current = state.run;
    // Project refresh replaces the array; append-only live recording changes length.
    // No timing entry is read or formatted while the outer history is closed.
    const rows = current.timings || [];
    const costs = JSON.stringify([current.costs,current.completionReceipt]);
    if (state.rows !== rows || state.length !== rows.length || state.costs !== costs) {
      updateHTML(state.summary, timingSummaryHTML(current));
      state.rows = rows; state.length = rows.length; state.costs = costs;
    }
    if (state.inner.open) {
      if (state.pageRows !== rows || state.pageLength !== rows.length || state.shownPage !== state.page) {
        const result = timingPageHTML(rows,state.page);
        state.page = result.page;
        state.pageRows = rows; state.pageLength = rows.length; state.shownPage = state.page;
        updateHTML(state.entries,result.html);
      }
    }
  };
  state.refresh();
}
