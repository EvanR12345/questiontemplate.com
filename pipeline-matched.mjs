import {buildPlan,schedule,DEFAULTS} from './pipeline-engine.mjs';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sec=v=>Number.isFinite(v)?`${v.toFixed(1)}s`:'Unmeasured';
const min=v=>Number.isFinite(v)?`${(v/60).toFixed(1)} min`:'Unmeasured';
const usd=v=>Number.isFinite(v)?`$${v.toFixed(3)}`:'Unmeasured';

export function projectMatched(mode,data,evidence,policy='proposed'){
  if(mode.continuousRounds!==2||!(mode.imagesPerMinute>0)||!Number.isFinite(mode.bothCopiesWarmSeconds))return null;
  const id='matched-'+mode.mode;
  const profile={attempt:'matched',resolution:'720p',encodingProfile:'fresh',workers:2,clientSlots:4,
    rounds:2,images:mode.continuousImages,imagesPerSecond:mode.imagesPerMinute/60,
    meanClientLatencySeconds:mode.warmDelivery.median,meanServerLatencySeconds:mode.handler.median};
  const e={...evidence,gpus:[...evidence.gpus,{id,name:`RTX 5090 · ${mode.mode}`}],concurrency:{gpus:[{id,name:`RTX 5090 · ${mode.mode}`,vramGB:32,
    profiles:[],productionProfiles:[],hybridProfiles:[profile],hardware:[{attempt:'matched',region:data.requestedRegion,
      computeContainerHourlyUSD:mode.hourlyUSD,startupSeconds:mode.bothCopiesWarmSeconds,
      downloadHashSeconds:0,firstWarmupClientSeconds:mode.warmDelivery.median}]}]}};
  const p=buildPlan({...DEFAULTS,gpu:id,policy,resolution:'720p',encodingProfile:'fresh',
    executionMode:'hybrid',measurementAttempt:'matched',qc:'off',retries:0},e);
  // Setup is one measured envelope, including the two first story outputs.
  for(const t of p.tasks)if(['models','health','cold'].includes(t.id))t.duration=.05;
  let included=2;
  for(const t of p.tasks)if(t.kind==='delivered'&&t.chapter>0&&included){
    const n=Math.min(included,t.images);t.duration=Math.max(.05,t.duration-n/profile.imagesPerSecond);included-=n;
  }
  const r=schedule(p);
  const stages=Object.fromEntries([...new Set(r.tasks.map(t=>t.kind))].map(kind=>[kind,r.tasks.filter(t=>t.kind===kind).reduce((n,t)=>n+t.duration,0)]));
  return {policy,finishedVideoSeconds:7200,images:p.imageCount,wallSeconds:r.end,
    rentalWindowSeconds:r.rentalSeconds,continuousWarmImagesSeconds:stages.delivered,
    setupSeconds:mode.bothCopiesWarmSeconds,stages,
    rentalWindowCostUpperUSD:r.gpuUSD,estimatedDirectorUSD:r.apiUSD,
    dailyStorageAllocationUSD:p.config.storageDaily,totalUpperUSD:r.totalUSD,
    diagnostics:r.diagnostics};
}

export function matchedHTML(data,evidence){
  const modes=data.modes||[];
  const table=modes.map(m=>`<tr><td>RTX 5090 · ${esc(m.mode)}</td><td>${m.savedImages}</td><td>${m.continuousRounds}/2</td><td>${sec(m.firstSavedSeconds)}</td><td>${sec(m.bothCopiesWarmSeconds)}</td><td>${m.continuousRounds===2?m.imagesPerMinute.toFixed(2):'Unmeasured'}</td><td>${sec(m.resumeFirstSeconds)}</td><td>${m.verifiedCopies?'2 proven':'Pending'}</td></tr>`).join('');
  const projections=modes.map(m=>({m,p:projectMatched(m,data,evidence),serial:projectMatched(m,data,evidence,'serial')})).filter(x=>x.p);
  const bars=projections.flatMap(({m,p,serial})=>[
    {name:`${m.mode} · image stage`,seconds:m.projectedImageStageSeconds,note:'Measured image rate + startup; projection for 709 outputs'},
    {name:`${m.mode} · full pipeline, serial`,seconds:serial.wallSeconds,note:'Historical director, audio and rendering estimates included'},
    {name:`${m.mode} · full pipeline, overlapped`,seconds:p.wallSeconds,note:'Dependency/resource scheduling projection, not a production stopwatch'}]);
  const telemetry=`<details class="evidence-block"><summary>Measured upload, encoding, diffusion, VAE, file saving and model setup</summary>${modes.filter(m=>m.savedImages>0).map(m=>`<h4>${esc(m.mode)}</h4><p>Warm client delivery median ${sec(m.warmDelivery?.median)}; worker handler median ${sec(m.handler?.median)}; reference upload inside worker ${sec(m.warmUploadSeconds?.median)}; laptop decode/save ${sec(m.localDecodeAndSaveSeconds?.median)}. Group throughput includes request preparation; per-request delivery begins after preparation.</p><div class="evidence-table"><table><thead><tr><th>Observed Comfy node</th><th>Median</th><th>Min–max</th><th>Samples</th></tr></thead><tbody>${Object.entries(m.nodeObservationSeconds||{}).map(([name,v])=>`<tr><td>${esc(name)}</td><td>${sec(v.median)}</td><td>${sec(v.min)}–${sec(v.max)}</td><td>${v.n}</td></tr>`).join('')}</tbody></table></div><p class="caption">${esc(m.nodeObservationLimit)}</p><div class="evidence-table"><table><thead><tr><th>Weight file</th><th>GB</th><th>Download</th><th>Hash verification</th><th>Discovery/link</th></tr></thead><tbody>${(m.modelSetup||[]).map(f=>`<tr><td>${esc(f.filename)}</td><td>${(f.bytes/1e9).toFixed(2)}</td><td>${sec(f.downloadSeconds)}</td><td>${sec(f.verificationSeconds)}</td><td>${sec((f.cacheDiscoverySeconds||0)+(f.linkSeconds||0))}</td></tr>`).join('')}</tbody></table></div><p class="caption">Model setup sits inside the measured startup envelope and is not added twice. Runtime: ${esc(m.runtime?.torch)} / CUDA ${esc(m.runtime?.cuda)} / ComfyUI ${esc(m.runtime?.comfyVersion)}.</p>`).join('')}</details>`;
  const scale=Math.max(1,...bars.map(b=>b.seconds));
  const chart=bars.length?`<section class="panel comparison"><h3>Time to produce a two-hour video · setup included</h3>${bars.map(b=>`<div class="compare-row"><div class="label">${esc(b.name)}<small>${esc(b.note)}</small></div><div class="track"><i style="width:${b.seconds/scale*100}%;background:#64826d"><span>${min(b.seconds)}</span></i></div><div class="cost">${min(b.seconds)}</div></div>`).join('')}</section>`:'';
  const production=projections.length?`<h3>Two-hour finished video · 704 story images + 5 intro images</h3><div class="evidence-table"><table><thead><tr><th>Mode</th><th>Continuous image stage incl. setup</th><th>Continuous image cloud estimate</th><th>All stages sequential</th><th>All stages overlapped</th><th>Overlapped rental-window cost upper estimate</th></tr></thead><tbody>${projections.map(({m,p,serial})=>`<tr><td>${esc(m.mode)}</td><td>${min(m.projectedImageStageSeconds)}</td><td>${usd(m.projectedContinuousImageUSD)}</td><td>${min(serial.wallSeconds)}</td><td>${min(p.wallSeconds)}</td><td>${usd(p.totalUpperUSD)}</td></tr>`).join('')}</tbody></table></div><p class="caption">Image throughput/setup are measured in this pilot. Luna/director, local voice, rendering, joining and shutdown use historical calibration/allowances. This is a scheduling projection, not a measured two-hour production. Rental-window upper estimates charge all scheduling gaps at the displayed GPU rate, add historical director API cost and a full $0.168 storage-day allocation. Serverless may bill less during gaps; repeated cold/FlashBoot recovery remains uncertain. No vision QC/replacements are included; technical image checks remain.</p><details class="evidence-block"><summary>Every stage and overlapping-time accounting</summary>${projections.map(({m,p})=>`<h4>${esc(m.mode)}</h4><p>Measured startup + both-copy loading + first two saved story images: ${min(p.setupSeconds)} (once). Remaining 707 images with upload, fresh encoding, inference, decode, delivery and saving included: ${min(p.continuousWarmImagesSeconds)}.</p><ul>${Object.entries(p.stages).filter(([k])=>!['delivered','boot','models','health','cold'].includes(k)).map(([k,v])=>`<li>${esc(k)}: ${min(v)} · historical estimate/allowance</li>`).join('')}</ul><p>Parallel stage totals overlap; their sum is not the full elapsed time. Projected elapsed: ${min(p.wallSeconds)}. GPU rental window: ${min(p.rentalWindowSeconds)}. Estimated director API: ${usd(p.estimatedDirectorUSD)}. Storage day: ${usd(p.dailyStorageAllocationUSD)}.</p>`).join('')}</details>`:'<p class="inline-note">No two-hour rate projection is published until both continuous rounds finish. Earlier 38-versus-12 figures used different configurations and cannot establish an intrinsic Pod/Serverless speed difference.</p>';
  return `<div class="schedulebar"><div><h2>Matched Pod vs Serverless · RTX 5090</h2><p class="caption">${esc(data.model)} · ${esc(data.resolution)} · 2 independent model copies / 4 request slots · Pod requested ${esc(data.requestedRegion)}${data.regionMatched===false?' · Serverless: any available region':''}</p></div></div><div class="inline-note">Status: ${esc(data.status)}. ${data.regionLimitation?esc(data.regionLimitation):''} ${usd(data.observedCumulativeDebitUSD)} observed cumulative pilot debit / $1 cap, including earlier failed attempts; billing may post late. ${data.failure?`Latest issue: ${esc(typeof data.failure==='string'?data.failure:JSON.stringify(data.failure))}.`:''}</div><div class="evidence-table"><table><thead><tr><th>Mode</th><th>Saved images</th><th>Complete rounds</th><th>First saved, incl. setup</th><th>Both copies warm</th><th>Warm images/min</th><th>First after 30s pause</th><th>Model copies</th></tr></thead><tbody>${table}</tbody></table></div>${chart}${production}${telemetry}<details class="evidence-block"><summary>Test conditions and limitations</summary><ul>${(data.limits||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul><p>${data.matchedUniqueShots||0} unique matched shots; ${data.identicalPNGUniqueShots||0} PNG-identical. No broad image-quality conclusion follows. Endpoint paused: ${data.cleanup?.endpointPaused?'yes':'not yet verified'}; newest Pod stopped: ${data.cleanup?.podStopped?'yes':'not yet verified'}.</p></details>`;
}
