const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function providerSetupForm(status){
  const rows=status.providers??[],first=(status.providerOrder??[])[0];
  const field=(provider,id)=>'<label>'+provider+' API key '+(rows.find(r=>r.provider===provider.toLowerCase())?.credentialSaved?'· saved; leave blank to retain':'')+
    '<input id="'+id+'" type="password" autocomplete="off" spellcheck="false"></label>';
  return [
    '<p>Choose the preferred RTX 5090 marketplace. The helper checks compatible hosts and preserves spending limits. A reported available GPU still needs a successful startup.</p>',
    '<label>Provider order<select id="gpuProviderOrder"><option value="vast,lium" '+(first==='lium'?'':'selected')+'>Vast first · Lium backup</option><option value="lium,vast" '+(first==='lium'?'selected':'')+'>Lium first · Vast backup</option></select></label>',
    '<div class="grid-2"><label>Maximum US$/hour<input id="gpuProviderMaxRate" type="number" min=".01" max="100" step=".01" value="'+esc(status.maxHourlyUSD??1.19)+'"></label><label>Cloud working disk · GB<input id="gpuProviderDisk" type="number" min="25" max="1000" step="1" value="'+esc(status.workingDiskGB??25)+'"></label></div>',
    field('Vast','gpuVastKey'),field('Lium','gpuLiumKey'),
    '<p class="muted">Keys stay in the private helper and never enter project exports or browser storage. Saving settings does not rent a GPU. Each paid run needs its own total cap, including startup and transfers.</p>',
    '<div>'+rows.map(r=>'<p><strong>'+esc(r.provider==='vast'?'Vast.ai':'Lium')+'</strong> · '+(r.credentialSaved?'Key saved':'Account key needed')+' · '+(r.renderVerified?'Render test passed':'Render test pending')+(r.offers?' · '+r.offers.length+' matching offers checked':'')+(r.error?' · '+esc(r.error):'')+'</p>').join('')+'</div>',
    '<p class="muted">Automatic provider switching becomes available after a real render is verified. Current image generation and completed videos stay on their existing configuration.</p>'
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
