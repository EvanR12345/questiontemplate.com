const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function providerSetupForm(status){
  const rows=status.providers??[],selected=(status.providerOrder??['runpod','vast','lium']).join(',');
  const orders=[['runpod','vast','lium'],['runpod','lium','vast'],['vast','runpod','lium'],['vast','lium','runpod'],['lium','runpod','vast'],['lium','vast','runpod']];
  if(!orders.some(o=>o.join(',')===selected))orders.push(status.providerOrder);
  const names={runpod:'Runpod',vast:'Vast.ai',lium:'Lium'};
  const field=(provider,id)=>'<label>'+provider+' API key '+(rows.find(r=>r.provider===provider.toLowerCase())?.credentialSaved?'· saved; leave blank to retain':'')+
    '<input id="'+id+'" type="password" autocomplete="off" spellcheck="false"></label>';
  return [
    '<p>Choose the preferred RTX 5090 marketplace. The helper checks compatible hosts and preserves spending limits. A reported available GPU still needs a successful startup.</p>',
    '<label>Provider order<select id="gpuProviderOrder">'+orders.map(o=>'<option value="'+o.join(',')+'" '+(selected===o.join(',')?'selected':'')+'>'+o.map(p=>names[p]).join(' → ')+'</option>').join('')+'</select></label>',
    '<div class="grid-2"><label>Maximum US$/hour<input id="gpuProviderMaxRate" type="number" min=".01" max="100" step=".01" value="'+esc(status.maxHourlyUSD??1.19)+'"></label><label>Cloud working disk · GB<input id="gpuProviderDisk" type="number" min="25" max="1000" step="1" value="'+esc(status.workingDiskGB??25)+'"></label></div>',
    field('Vast','gpuVastKey'),field('Lium','gpuLiumKey'),
    '<p class="muted">Keys stay in the private helper and never enter project exports or browser storage. Saving settings does not rent a GPU. Each paid run needs its own total cap, including startup and transfers.</p>',
    '<p class="muted">Vast hosts above $0.001/GB for either transfer direction are excluded by default. Marketplace hourly quotes include working disk; stopped Vast disks keep billing until removed. Successful image tests do not automatically verify video rendering.</p>',
    '<div>'+rows.map(r=>'<p><strong>'+esc(names[r.provider]??r.provider)+'</strong> · '+(r.credentialSaved?'Key saved':'Account key needed')+' · '+(r.imageVerified?'Image test passed':'Image test pending')+' · '+(r.renderVerified?'Render test passed':'Render test pending')+(r.offers?' · '+r.offers.length+' matching offers checked':'')+(r.availability===false?' · No reported stock':'')+(r.error?' · '+esc(r.error):'')+'</p>'+(r.imageBenchmark?'<p class="muted">'+esc(r.imageBenchmark.images)+' public-reference test images · server median '+Number(r.imageBenchmark.warmServerMedianSeconds).toFixed(2)+'s · received median '+Number(r.imageBenchmark.warmClientMedianSeconds).toFixed(2)+'s · warmed group '+Number(r.imageBenchmark.completeCloudSavedImagesPerMinute).toFixed(2)+' images/min including cloud saving. Small sample; not a matched comparison against other providers or a production guarantee.</p>':'')).join('')+'</div>',
    '<p class="muted">Images and rendering each use only providers that passed that workload’s real test. Confirmed lack of capacity advances to the next provider. An uncertain rental response stops switching to prevent duplicate bills. All choices keep the same RTX 5090 and image model.</p>'
  ].join('');
}
export function providerSetupValues(document){
  const value=id=>document.getElementById(id).value.trim();
  const max=Number(value('gpuProviderMaxRate')),disk=Number(value('gpuProviderDisk'));
  if(!Number.isFinite(max)||max<.01||max>100)throw Error('Set a finite hourly ceiling.');
  if(!Number.isInteger(disk)||disk<25||disk>1000)throw Error('Choose 25–1000 GB of cloud working storage.');
  const result={gpuProviderOrder:value('gpuProviderOrder').split(','),gpuMaxHourlyUSD:max,gpuWorkingDiskGB:disk};
  for(const [id,key] of [['gpuVastKey','vastApiKey'],['gpuLiumKey','liumApiKey']]){
    const secret=value(id);
    if(secret)result[key]=secret;
  }
  return result;
}
