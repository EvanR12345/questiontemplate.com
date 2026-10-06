import {imageAccountingHTML} from './pipeline-detail-ui.mjs';
import {latestProfiles} from './pipeline-measurements.mjs';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function measuredRows(data,resolution,encodingProfile='cached',executionMode='resident'){
  return data.gpus.flatMap(g=>{
    const profiles=latestProfiles(g,resolution,encodingProfile,executionMode);
    if(!profiles.length)return [];
    const best=profiles.reduce((a,b)=>a.imagesPerSecond>b.imagesPerSecond?a:b);
    return [{...g,best,profiles}];
  }).sort((a,b)=>b.best.imagesPerSecond-a.best.imagesPerSecond);
}

function traceChart(profile){
  const trace=profile?.traces?.[0];if(!trace)return '';
  const slots=profile.clientSlots||profile.workers;
  const workers=profile.workers;
  const cloudEnd=Math.max(...trace.jobs.map(j=>j.serverEnd));
  const chart=(cloud)=>{
    const height=(cloud?workers:slots)*31+60;
    const end=cloud?cloudEnd:trace.wallSeconds,x=t=>80+t/end*520;
    const bars=trace.jobs.map(j=>{
      const start=cloud?j.serverStart:j.clientStart,stop=cloud?j.serverEnd:j.clientCommitted??j.clientSaved,y=30+(cloud?(j.serverWorker??(profile.executionMode==='pipeline'?0:j.worker)):j.worker)*31;
      const save=cloud?'':`<rect x="${x(j.saveStart)}" y="${y}" width="${Math.max(.7,x(j.saveEnd)-x(j.saveStart))}" height="22" fill="#b98b43"/>`;
      return `<g><title>Image ${j.index}: ${start.toFixed(3)}–${stop.toFixed(3)} seconds</title><rect x="${x(start)}" y="${y}" width="${Math.max(.7,x(stop)-x(start))}" height="22" fill="${cloud?'#64826d':'#8babc0'}"/>${save}</g>`;
    }).join('');
    return `<svg class="trace-chart" viewBox="0 0 650 ${height}" role="img" aria-label="${cloud?'Actual cloud execution intervals':'Actual laptop request and durable image save intervals'}">${[0,.25,.5,.75,1].map(p=>`<path d="M${80+p*520} 22V${height-22}" stroke="#e2e5df"/><text x="${80+p*520}" y="${height-5}" text-anchor="middle">${(end*p).toFixed(1)}s</text>`).join('')}${Array.from({length:cloud?workers:slots},(_,i)=>`<text x="4" y="${46+i*31}">${cloud?'Worker':'Client'} ${i+1}</text>`).join('')}${bars}</svg>`;
  };
  return `<div class="panel comparison"><h3>Actual trial: ${profile.workers} image workers, ${slots} request slots, ${trace.jobs.length} images</h3><p class="caption">First matched round · ${trace.wallSeconds.toFixed(2)}s until all receipts saved. Hover a bar for exact timings.</p><h4>Cloud execution — relative to first cloud operation</h4>${chart(true)}<h4>Laptop requests — relative to local dispatch</h4>${chart(false)}<p class="caption">Green: server operation. Blue: request through saved image and metadata receipt where recorded; older trials end at saved PNG. Amber: PNG encoding and durable saving. These are separate clock origins; their zero points are not asserted to align. Server overlap includes encoding, denoising, decoding and backend output, and does not count concurrent CUDA kernels.</p></div>`;
}

export function mountConcurrencyLab(host,data,onApply){
  let resolution='720p',encodingProfile='fresh',executionMode='resident',measurementAttempt='latest',rankBy='speed',selected=measuredRows(data,resolution,'fresh')[0]?.id||data.gpus[0].id,workerChoice='best';
  const draw=()=>{
    const ranked=measuredRows(data,resolution,encodingProfile,executionMode),max=Math.max(1,...ranked.map(g=>g.best.imagesPerSecond));
    if(rankBy==='cost')ranked.sort((a,b)=>a.best.warmUSDPerImage-b.best.warmUSDPerImage);
    const gpu=data.gpus.find(g=>g.id===selected),profiles=latestProfiles(gpu,resolution,encodingProfile,executionMode,measurementAttempt);
    const best=profiles.reduce((a,b)=>!a||b.imagesPerSecond>a.imagesPerSecond?b:a,null);
    const inspected=workerChoice==='best'?best:profiles.find(p=>(executionMode!=='resident'?p.clientSlots:p.workers)===Number(workerChoice))||best;
    const yMax=Math.max(.01,...profiles.map(p=>p.imagesPerSecond))*1.15;
    const x=n=>45+(n-1)/5*490,y=v=>190-v/yMax*150;
    const curve=profiles.map(p=>`${x(executionMode!=='resident'?p.clientSlots:p.workers)},${y(p.imagesPerSecond)}`).join(' ');
    const tableRows=profiles.map(p=>`<tr><td>${executionMode!=='resident'?p.clientSlots:p.workers}</td><td>${p.imagesPerSecond.toFixed(3)}</td><td>${Number.isFinite(p.speedupOverOneWorker)?p.speedupOverOneWorker.toFixed(2):'Unmeasured'}×</td><td>$${(p.warmUSDPerImage*1000).toFixed(2)}</td><td>${p.rounds} / ${p.images}</td><td>${p.observedRateRange.map(n=>n.toFixed(3)).join('–')}</td></tr>`).join('');
    host.innerHTML=`<div class="schedulebar"><div><h2>Measured single-GPU parallelism</h2><p class="caption">Same FLUX.2 Klein 4B weights and story shots. Every inference is fresh.</p></div><label>Output resolution<select data-resolution><option value="720p" ${resolution==='720p'?'selected':''}>720p · 1280 × 720</option><option value="1080p" ${resolution==='1080p'?'selected':''}>1080p · cropped from 1920 × 1088</option></select></label></div>
      <div class="inline-note">${ranked.length} GPU types measured at this resolution. $${data.rentalUpperUSD.toFixed(3)} booked experiment rental ceiling / $${data.costLedger?.capUSD ?? 5} cap. Active rentals and standing storage can add cost before the next receipt.</div>
      <div class="panel comparison"><h3>Delivered throughput and warm rental cost</h3>${ranked.length?ranked.map(g=>`<button class="compare-row lab-gpu" data-gpu="${esc(g.id)}"><div class="label">${esc(g.name)}<small>${g.best.workers} image workers${executionMode!=='resident'?' · '+g.best.clientSlots+' request slots':''} · ${g.vramGB} GB</small></div><div class="track"><i style="width:${g.best.imagesPerSecond/max*100}%;background:${g.id===selected?'#89aa8f':'#bdccba'}"><span>${g.best.imagesPerSecond.toFixed(3)} images/s</span></i></div><div class="cost">$${(g.best.warmUSDPerImage*1000).toFixed(2)}<small> / 1,000 images</small></div></button>`).join(''):'<p>No completed two-round measurements at this resolution yet. No pixel-scaling estimate is substituted.</p>'}</div>
      <div class="panel comparison"><div class="section-head"><h3>Worker scaling</h3><label>GPU<select data-selected>${data.gpus.map(g=>`<option value="${esc(g.id)}" ${g.id===selected?'selected':''}>${esc(g.name)}${g.profiles.length?'':' · untested'}</option>`).join('')}</select></label></div>
      ${profiles.length?`<svg class="scaling-chart" viewBox="0 0 580 225" role="img" aria-label="Delivered images per second by independent worker count"><path d="M45 25V190H545" fill="none" stroke="#c3c7c0"/><polyline points="${curve}" fill="none" stroke="#64826d" stroke-width="3"/>${[1,2,3,4,6].map(n=>`<text x="${x(n)}" y="212" text-anchor="middle">${n} ${executionMode!=='resident'?'slots':'workers'}</text>`).join('')}${profiles.map(p=>`<circle cx="${x(executionMode!=='resident'?p.clientSlots:p.workers)}" cy="${y(p.imagesPerSecond)}" r="5" fill="#49674f"/><text x="${x(executionMode!=='resident'?p.clientSlots:p.workers)}" y="${y(p.imagesPerSecond)-12}" text-anchor="middle">${p.imagesPerSecond.toFixed(3)}/s</text>`).join('')}</svg><div class="evidence-table"><table><thead><tr><th>${executionMode!=='resident'?'Request slots':'Workers'}</th><th>Images/s</th><th>Speedup</th><th>$/1,000 warm</th><th>Rounds / images</th><th>Observed rate range</th></tr></thead><tbody>${tableRows}</tbody></table></div>`:'<p>This GPU has no completed two-round profile for this resolution. It remains a candidate, not a measured recommendation.</p>'}
      <p class="caption">Best tested count is not the maximum possible count. Cost covers warm delivered throughput; boot, download, loading, rental idle, retries, API and video rendering are additional.</p>${profiles.length&&onApply?'<button class="button dark" data-apply>Use this measured setup in my schedule</button>':''}</div>
      ${profiles.length?`<label>Inspect an actual worker trial<select data-workers><option value="best" ${workerChoice==='best'?'selected':''}>Best measured count</option>${profiles.map(p=>`<option value="${executionMode!=='resident'?p.clientSlots:p.workers}" ${workerChoice===String(executionMode!=='resident'?p.clientSlots:p.workers)?'selected':''}>${executionMode!=='resident'?p.clientSlots+' slots · '+p.workers+' model copies':p.workers+' workers'}</option>`).join('')}</select></label>${traceChart(inspected)}`:''}
      <details class="evidence-block"><summary>Exact test conditions and limits</summary><ul>${data.conditions.map(s=>`<li>${esc(s)}</li>`).join('')}</ul></details>`;
    const overview=document.createElement('section');overview.className='panel comparison';
    const allModes=['resident','pipeline','hybrid'];
    const combinations=data.gpus.flatMap(g=>allModes.flatMap(mode=>latestProfiles(g,resolution,encodingProfile,mode).map(p=>({g,p,mode}))));
    const leaders=data.gpus.flatMap(g=>{const rows=combinations.filter(v=>v.g.id===g.id);return rows.length?[rows.reduce((a,b)=>a.p.warmUSDPerImage<b.p.warmUSDPerImage?a:b)]:[];}).sort((a,b)=>a.p.warmUSDPerImage-b.p.warmUSDPerImage);
    const maxRate=Math.max(.01,...leaders.map(v=>v.p.imagesPerSecond)),maxCost=Math.max(.01,...leaders.map(v=>v.p.warmUSDPerImage*1000));
    overview.innerHTML=`<h3>Lowest measured warm cost for each GPU</h3><p class="caption">Best completed setting across resident, pipeline and hybrid methods. Each point is its own measured rental host, not a price-adjusted or location-corrected score. Warm prices exclude cold startup and full video work.</p><svg class="scaling-chart" viewBox="0 0 660 255" role="img" aria-label="Measured delivery speed versus warm rental cost"><path d="M65 22V205H620" fill="none" stroke="#b5c5b8"/><text x="340" y="250" text-anchor="middle">Warm USD per 1,000 delivered images →</text><text x="8" y="18">Images / second ↑</text>${[0,.25,.5,.75,1].map(f=>`<text x="${65+555*f}" y="225" text-anchor="middle">$${(maxCost*f).toFixed(2)}</text><text x="57" y="${205-175*f}" text-anchor="end">${(maxRate*f).toFixed(2)}</text>`).join('')}${leaders.map((v,i)=>`<g><title>${esc(v.g.name)}: ${v.p.imagesPerSecond.toFixed(3)} images/s, $${(v.p.warmUSDPerImage*1000).toFixed(3)}/1000, ${v.mode}, ${esc(v.p.attempt)}</title><circle cx="${65+v.p.warmUSDPerImage*1000/maxCost*555}" cy="${205-v.p.imagesPerSecond/maxRate*175}" r="6" fill="#456850"/><text x="${74+v.p.warmUSDPerImage*1000/maxCost*555}" y="${198-v.p.imagesPerSecond/maxRate*175}">${i+1}</text></g>`).join('')}</svg><div class="evidence-table"><table><thead><tr><th>GPU</th><th>Method / copies / slots</th><th>Images/s</th><th>$/1,000 warm</th><th>Host</th><th></th></tr></thead><tbody>${leaders.map((v,i)=>`<tr><td>${i+1}. ${esc(v.g.name)}</td><td>${v.mode} · ${v.p.workers} / ${v.p.clientSlots||v.p.workers}</td><td>${v.p.imagesPerSecond.toFixed(3)}</td><td>$${(v.p.warmUSDPerImage*1000).toFixed(3)}</td><td>${esc(v.p.attempt)}</td><td><button class="button" data-leader="${i}">Inspect</button></td></tr>`).join('')}</tbody></table></div>`;
    host.querySelector('.panel.comparison').before(overview);
    overview.querySelectorAll('[data-leader]').forEach(b=>b.onclick=()=>{const v=leaders[Number(b.dataset.leader)];selected=v.g.id;executionMode=v.mode;measurementAttempt=v.p.attempt;workerChoice=String(v.mode==='resident'?v.p.workers:v.p.clientSlots);draw();});
    const profileControl=document.createElement('label');
    const ledger=data.costLedger;
    if(ledger){
      const costNote=document.createElement('p');costNote.className='inline-note';
      costNote.textContent=`Current conservative experiment ceiling: $${ledger.experimentConservativeUpperUSD.toFixed(3)} / $${ledger.capUSD}. Booked rentals $${ledger.bookedRentalUpperUSD.toFixed(3)}, unbooked rentals $${ledger.unbookedRentalUpperUSD.toFixed(3)}, standing storage $${ledger.standingStorageUpperUSD.toFixed(3)}. ${ledger.activeOrUnresolvedAllocations} active or unresolved allocation(s) at this snapshot. Not an invoice.`;
      host.querySelector('.schedulebar').after(costNote);
    }
    const hostNote=document.createElement('p');hostNote.className='caption';
    hostNote.textContent='Ranking uses the latest complete host per GPU. Inspect earlier rental hosts below; different hosts are never pooled into one scaling curve.';
    host.querySelector('.panel.comparison').prepend(hostNote);
    profileControl.innerHTML=`Conditioning<select data-conditioning><option value="fresh" ${encodingProfile==='fresh'?'selected':''}>Fresh prompt + reference encoding</option><option value="cached" ${encodingProfile==='cached'?'selected':''}>Reused conditioning · optimistic control</option></select>`;
    host.querySelector('.schedulebar').append(profileControl);
    profileControl.querySelector('select').onchange=e=>{encodingProfile=e.target.value;measurementAttempt='latest';workerChoice='best';draw();};
    const method=document.createElement('label');
    method.innerHTML=`Overlap method<select><option value="resident" ${executionMode==='resident'?'selected':''}>Independent image workers</option><option value="pipeline" ${executionMode==='pipeline'?'selected':''}>One worker · pipelined requests</option><option value="hybrid" ${executionMode==='hybrid'?'selected':''}>Multiple workers · buffered clients</option></select>`;
    host.querySelector('.schedulebar').append(method);
    method.querySelector('select').onchange=e=>{executionMode=e.target.value;measurementAttempt='latest';workerChoice='best';draw();};
    const available=(executionMode==='hybrid'?gpu.hybridProfiles:executionMode==='pipeline'?gpu.pipelineProfiles:encodingProfile==='fresh'?gpu.productionProfiles:gpu.profiles)||[];
    const attempts=[...new Set(available.filter(p=>p.resolution===resolution&&(p.encodingProfile||'cached')===encodingProfile&&!p.postControl&&p.rounds>=2).map(p=>p.attempt))].filter(Boolean);
    const archive=document.createElement('label');
    archive.innerHTML=`Inspect rental host<select><option value="latest" ${measurementAttempt==='latest'?'selected':''}>Latest complete host</option>${attempts.map(a=>`<option value="${esc(a)}" ${measurementAttempt===a?'selected':''}>${esc(a)}</option>`).join('')}</select>`;
    host.querySelector('.schedulebar').append(archive);
    archive.querySelector('select').onchange=e=>{measurementAttempt=e.target.value;workerChoice='best';draw();};
    const ranking=document.createElement('label');
    ranking.innerHTML=`Rank measured GPUs<select><option value="speed" ${rankBy==='speed'?'selected':''}>Fastest complete delivery</option><option value="cost" ${rankBy==='cost'?'selected':''}>Lowest warm cost per image</option></select>`;
    host.querySelector('.schedulebar').append(ranking);
    ranking.querySelector('select').onchange=e=>{rankBy=e.target.value;draw();};
    const inventory=document.createElement('details');
    inventory.className='evidence-block';
    const price=value=>Number.isFinite(value)?'$'+value.toFixed(2):'Unavailable';
    inventory.innerHTML=`<summary>All ${data.gpus.length} catalog GPUs: price, location and test coverage</summary><p class="caption">Stock snapshot: ${data.catalogSurveyAt?new Date(data.catalogSurveyAt*1000).toLocaleString():'not recorded'}. Catalog prices are quotes, not measured performance. Community pricing is shown separately; the current experiments use Secure Cloud.</p><div class="evidence-table"><table><thead><tr><th>GPU</th><th>VRAM GB</th><th>Secure / hour</th><th>Community / hour</th><th>Regions and stock</th><th>This conditioning / resolution</th></tr></thead><tbody>${data.gpus.map(g=>`<tr><td>${esc(g.name)}</td><td>${esc(g.vramGB)}</td><td>${price(g.catalog?.secureHourlyUSD)}</td><td>${price(g.catalog?.communityHourlyUSD)}</td><td>${g.catalog?.availableRegions?.length?g.catalog.availableRegions.map(r=>esc(r.dataCenterId)+' · '+esc(r.stockStatus)).join('<br>'):'No stock reported'}<br><small>${esc(g.catalog?.reason||'No catalog admission record')}</small></td><td>${latestProfiles(g,resolution,encodingProfile,executionMode).length?'Repeated measurements':'Unmeasured'}</td></tr>`).join('')}</tbody></table></div>`;
    host.append(inventory);
    if(inspected){
      const round=document.createElement('label');round.className='round-control';
      round.innerHTML=`Accounting round<select>${inspected.traces.map((t,i)=>`<option value="${i}">${esc(t.label)} · ${t.wallSeconds.toFixed(3)}s</option>`).join('')}</select>`;
      const accounting=document.createElement('div');accounting.innerHTML=imageAccountingHTML(inspected,gpu);
      host.append(round,accounting);round.querySelector('select').onchange=e=>accounting.innerHTML=imageAccountingHTML(inspected,gpu,Number(e.target.value));
      const hardware=gpu.hardware?.find(h=>h.attempt===inspected.attempt),connection=hardware?.connectionLatency;
      const context=document.createElement('p');context.className='caption';
      const millis=v=>Number.isFinite(v)?v.toFixed(0)+' ms':'unmeasured';
      context.textContent=`This trial: ${hardware?.region||'region unconfirmed'}; ${hardware?.regionEvidence||'no location evidence'}. ${connection?`${connection.clientContext}: TCP establishment median ${millis(connection.tcpConnect?.medianMs)}; tunneled application median ${millis(connection.tunneledApplicationRequest?.medianMs)}. Three idle samples per method, not pure ping. Location is a recorded variable.`:'Connection latency was not separately measured on this host.'}`;
      host.append(context);
      const telemetry=document.createElement('p');telemetry.className='caption';
      const device=inspected.deviceTelemetry;
      telemetry.textContent=device?.samples?`${device.samples} timed device samples. ${Object.entries(device.metrics).map(([name,v])=>`${name}: mean ${v.mean.toFixed(1)}, observed ${v.min.toFixed(1)}–${v.max.toFixed(1)}`).join('; ')}. ${device.scope}`:'Device utilization, power and clock samples are unavailable for this profile; no values are inferred.';
      host.append(telemetry);
    }
    host.querySelector('[data-resolution]').onchange=e=>{resolution=e.target.value;measurementAttempt='latest';workerChoice='best';draw();};
    host.querySelector('[data-selected]').onchange=e=>{selected=e.target.value;measurementAttempt='latest';draw();};
    host.querySelectorAll('[data-gpu]').forEach(b=>b.onclick=()=>{selected=b.dataset.gpu;measurementAttempt='latest';draw();});
    const apply=host.querySelector('[data-apply]');
    if(apply)apply.onclick=()=>onApply({gpu:selected,resolution,imageWorkers:'best',encodingProfile,executionMode,measurementAttempt});
    const workerSelect=host.querySelector('[data-workers]');
    if(workerSelect)workerSelect.onchange=e=>{workerChoice=e.target.value;draw();};
  };
  draw();
}
