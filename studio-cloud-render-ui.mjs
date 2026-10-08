const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const terminal=new Set(['COMPLETE','FAILED','CANCELLED','NOT_STARTED']);
const mounts=new WeakMap();
export function cloudRenderForm(p){
  return `<div class="section-box"><h3>Render in Google Cloud</h3><p class="muted">Uses your saved narration, artwork and finishing settings. Completed videos stay in private Google storage. This control becomes available after the cloud worker has been deployed and tested.</p><label>Video version<select id="cloudRenderDestination"><option value="youtube" ${p.settings.engagement?.exportDestination==='patreon'?'':'selected'}>YouTube</option><option value="patreon" ${p.settings.engagement?.exportDestination==='patreon'?'selected':''}>Patreon</option></select></label><div class="toolbar"><button id="cloudRenderStart" disabled>Render full story in cloud</button><button id="cloudRenderRefresh" disabled>Refresh rendering</button><button id="cloudRenderCancel" disabled>Cancel cloud render</button></div><p id="cloudRenderStatus" role="status">Checking cloud renderer…</p><div id="cloudRenderResults"></div><p class="muted">Progress normally updates about every four seconds. An MP4 becomes playable when encoding and saving finish. Your saved chapter-split settings also apply. Rendering uses trial compute credits beyond any free allowance.</p></div>`;
}
export class CloudRenderClient{
  constructor(project,api,newId=()=>crypto.randomUUID().replaceAll('-','')){
    this.project=project;this.api=api;this.newId=newId;this.pending=null;this.state={status:'NOT_STARTED'};this.busy=false;this.version=0;
  }
  async refresh(){
    if(this.busy)return this.state;
    const version=++this.version;
    const state=await this.api('cloud-render',{project:this.project.id,operation:'status'});
    if(version===this.version)this.state=state;return this.state;
  }
  async start(destination){
    if(this.busy||!terminal.has(this.state.status))throw Error('A cloud render is already active. Refresh its progress.');
    if(!['youtube','patreon'].includes(destination))throw Error('Choose an export version.');
    // Preserve the exact nonce after a lost reply; never submit a new paid request automatically.
    this.pending??={id:this.newId(),revision:this.project.revision,destination};
    if(this.pending.destination!==destination)throw Error('Refresh the pending render before changing its version.');
    this.busy=true;
    const version=++this.version;
    try{
      const result=await this.api('cloud-render',{project:this.project.id,operation:'start',options:this.pending});
      if(version===this.version)this.state=result;this.pending=null;return this.state;
    }finally{this.busy=false;}
  }
  async cancel(){
    if(!this.state.id||terminal.has(this.state.status))return this.state;
    const version=++this.version;
    const result=await this.api('cloud-render',{project:this.project.id,operation:'cancel',options:{id:this.state.id}});
    if(version===this.version)this.state=result;
    return this.state;
  }
}
export async function wireCloudRender({p,api,action,active,open,enabled}){
  const $=id=>document.getElementById(id),view=$('cloudRenderStatus');
  if(!view)return;
  const mount={};mounts.set(view,mount);
  const mounted=()=>active()&&$('cloudRenderStatus')===view&&mounts.get(view)===mount;
  const start=$('cloudRenderStart'),refresh=$('cloudRenderRefresh'),cancel=$('cloudRenderCancel');
  for(const button of [start,refresh,cancel]){button.disabled=true;button.onclick=null;}
  if(!enabled){view.textContent='Cloud rendering is not connected yet. No local rendering starts from this button.';return;}
  const client=new CloudRenderClient(p,api);let timer;
  const paint=state=>{
    if(!mounted())return;
    start.disabled=client.busy||!terminal.has(state.status);cancel.disabled=!state.id||terminal.has(state.status)||state.cancelRequested;
    view.textContent=state.status==='NOT_STARTED'?'Ready. Save the current project and all assets to Google before rendering.':
      `${state.status} · ${state.stage??''}${state.elapsed?` · ${Math.floor(state.elapsed/60)} min ${Math.floor(state.elapsed%60)} sec`:''}${state.completedAssets?` · ${state.completedAssets} assets saved`:''}${state.cancelRequested?' · Cancellation requested':''}`;
    const outputs=state.status==='COMPLETE'?[state.result,...(state.result?.parts??[])].filter(x=>x?.path):[];
    $('cloudRenderResults').innerHTML=outputs.map(x=>`<p>${escape(x.downloadName??x.path)} <button data-render-open="${escape(x.path)}">Open saved video</button></p>`).join('');
    $('cloudRenderResults').querySelectorAll('[data-render-open]').forEach(b=>b.onclick=()=>action(()=>open(b.dataset.renderOpen)));
  };
  const schedule=()=>{
    clearTimeout(timer);
    if(mounted()&&!terminal.has(client.state.status))timer=setTimeout(()=>{if(mounted())void check();},4000);
  };
  const check=async()=>{
    try{paint(await client.refresh());schedule();}
    catch{if(mounted()){view.textContent='Cloud progress could not be checked. The saved job is retained; use Refresh rendering.';start.disabled=true;}}
  };
  refresh.disabled=false;refresh.onclick=()=>action(check);
  start.onclick=()=>action(async()=>{
    start.disabled=true;
    try{paint(await client.start($('cloudRenderDestination').value));schedule();}
    catch(error){if(mounted()){view.textContent='The render request was not confirmed. Refresh progress before starting another.';start.disabled=true;}throw error;}
  });
  cancel.onclick=()=>action(async()=>{paint(await client.cancel());schedule();});
  await check();
}
