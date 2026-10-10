const esc=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const valid=n=>Number.isFinite(n)&&n>=0;
const number=n=>n.toLocaleString('en-CA');
export function lunaUsage(timings=[]){
  const stages=new Map();let missing=0,pending=0,costMissing=0;
  for(const row of timings){
    // Parent wall-time observations include child charges already. Count only
    // real adapter detail receipts, including paid failed attempts, once.
    if(!row.detail||row.provider!=='openai-luna'||row.reused)continue;
    if(row.usagePending)pending++;
    if(!valid(row.inputTokens)||!valid(row.tokens)){missing++;continue;}
    const stage=stages.get(row.stage)||{stage:row.stage,calls:0,input:0,output:0,cached:0,cost:0,workSeconds:0,costMissing:0,workMissing:0};
    stage.calls+=Number.isInteger(row.attempt)&&row.attempt>0?row.attempt:Math.max(1,row.latency?.attempts?.length||1);
    stage.input+=row.inputTokens;stage.output+=row.tokens;
    stage.cached+=valid(row.cachedInputTokens)?row.cachedInputTokens:0;
    stage.cost+=valid(row.estimatedUSD)?row.estimatedUSD:0;
    if(!valid(row.estimatedUSD)){costMissing++;stage.costMissing++;}
    stage.workSeconds+=valid(row.seconds)?row.seconds:0;
    if(!valid(row.seconds))stage.workMissing++;
    stages.set(row.stage,stage);
  }
  const rows=[...stages.values()].sort((a,b)=>b.input-a.input);
  return {rows,missing,pending,costMissing,...rows.reduce((a,r)=>({calls:a.calls+r.calls,input:a.input+r.input,
    output:a.output+r.output,cached:a.cached+r.cached,cost:a.cost+r.cost}),{calls:0,input:0,output:0,cached:0,cost:0})};
}
export function lunaUsageHTML(timings){
  const usage=lunaUsage(timings);
  return `<details data-ui="luna-usage"><summary>Luna usage · recorded requests</summary><p class="muted">Input counts every transmitted copy of story context, instructions, schemas and review plans. It is not the size of your story. This shows retained request history, including failures; reused results and parent totals are excluded. Output includes reasoning tokens once. API work sums call times; parallel calls overlap, so it is not elapsed delay.</p>${usage.calls?`<p>${number(usage.calls)} requests · ${number(usage.input)} input tokens · ${number(usage.output)} output tokens · ${number(usage.cached)} cached input tokens · $${usage.cost.toFixed(4)} ${usage.costMissing?"known API estimates only":"estimated API usage"}.</p><div class="evidence-table"><table><thead><tr><th>Pass</th><th>Requests</th><th>Input tokens</th><th>Output tokens</th><th>API work</th><th>Estimated API cost</th></tr></thead><tbody>${usage.rows.map(r=>`<tr><td>${esc(r.stage)}</td><td>${number(r.calls)}</td><td>${number(r.input)}</td><td>${number(r.output)}</td><td>${r.workMissing?(r.workSeconds?"≥"+r.workSeconds.toFixed(1)+"s (partial)":"Unknown"):r.workSeconds.toFixed(1)+"s"}</td><td>${r.costMissing?"≥":""}$${r.cost.toFixed(4)}${r.costMissing?" (partial)":""}</td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">No settled Luna request counts are recorded here yet.</p>'}${usage.missing||usage.pending||usage.costMissing?`<p class="muted">${usage.missing} records lack token totals; ${usage.pending} have unresolved usage; ${usage.costMissing} lack a cost estimate. These totals do not establish the final bill.</p>`:''}<p class="muted">Account invoices and free-traffic credits are separate. Older retained history may be incomplete; the private spending ledger remains the budget source of truth.</p></details>`;
}
