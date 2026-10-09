const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function sourceLink(url, label) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) return esc(label);
    return `<a href="${esc(parsed.href)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`;
  } catch { return esc(label); }
}
export function candidateRows(data, sort='name') {
  const rows = (data.candidates || []).map(row=>({...row}));
  rows.sort((a,b)=>sort==='price'
    ? (Number.isFinite(a.hourlyUSD)?a.hourlyUSD:Infinity)-(Number.isFinite(b.hourlyUSD)?b.hourlyUSD:Infinity) || a.name.localeCompare(b.name)
    : sort==='encoders' ? b.encoders-a.encoders || a.name.localeCompare(b.name)
    : sort==='memory' ? b.vramGB-a.vramGB || a.name.localeCompare(b.name)
    : a.name.localeCompare(b.name));
  return rows;
}
export function researchHTML(data,sort='name') {
  const rows = candidateRows(data,sort);
  const comparison=data.applicationComparison;
  const bars=(comparison?.rows||[]).map(row=>{
    const max=Math.max(1,...row.values.map(v=>v.value));
    return `<h4>${esc(row.name)}</h4>${row.values.map(value=>`<div class="render-research-bar"><span>${esc(value.name)}</span><meter min="0" max="${max}" value="${value.value}" aria-label="${esc(value.name+' '+row.name)}"></meter><strong>${value.value.toFixed(2)} ${esc(row.unit)}</strong></div>`).join('')}`;
  }).join('');
  return `<div class="schedulebar"><div><h2>Video rendering · separate from image generation</h2><p class="caption">Primary-source research checked ${esc(data.checkedAt)}. Hardware capacity and editor benchmarks; no new Studio cloud export result.</p></div><label>Sort rendering candidates<select data-render-sort>${[['name','Name'],['price','Published Pod rate'],['encoders','Encoder engines'],['memory','VRAM']].map(([key,name])=>`<option value="${key}" ${key===sort?'selected':''}>${name}</option>`).join('')}</select></label></div>
    <div class="table-scroll"><table><thead><tr><th>GPU</th><th>VRAM</th><th>NVENC engines</th><th>Pod rate / hour</th><th>Reason and limits</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${sourceLink(row.source,row.name)}</td><td>${row.vramGB} GB</td><td>${row.encoders} · generation ${row.generation}</td><td>${Number.isFinite(row.hourlyUSD)?'$'+row.hourlyUSD.toFixed(2):'Not verified'}</td><td>${esc(row.role)}<br><small>${esc(row.caveat)}</small></td></tr>`).join('')}</tbody></table></div>
    <p class="caption">${esc(data.priceNote)} ${sourceLink(data.priceSource,'Check published prices')}</p>
    <details><summary>Actual editor encoding benchmarks and their limits</summary><p>${sourceLink(comparison?.source,comparison?.name)}</p>${bars}<p class="caption">${esc(comparison?.limitations)}</p></details>
    <div class="inline-note">A single H.264 stream does not automatically use every encoder. More VRAM does not establish faster export. No candidate changes the planner’s measured render timing. ${esc(data.testGate)}</div>
    <p class="caption">${data.sources.map(source=>sourceLink(source.url,source.name)).join(' · ')}</p>`;
}
export async function mountRenderResearch(host,{fetcher=fetch}={}) {
  host.textContent='Loading video-rendering research…';
  try {
    const response=await fetcher('./pipeline-render-research.json?v=render-research-20261009');
    if(!response.ok)throw Error('Research file could not load.');
    const data=await response.json();
    function draw(sort) {
      host.innerHTML=researchHTML(data,sort);
      host.querySelector('[data-render-sort]').onchange=event=>draw(event.target.value);
    }
    draw('name');
  } catch {
    host.textContent='Video-rendering research is unavailable. Existing image benchmarks and the planner remain usable.';
  }
}
