const drafts=new Map();
const pending=new Set();
const mounts=new WeakMap();
export function vimeoForm(){
  return `<div class="section-box" id="vimeoPanel"><h3>Vimeo → Patreon · direct cloud delivery</h3><p>Vimeo can fetch a saved Google video directly. Your laptop sends control messages only; the upload can continue after you close Studio.</p><p id="vimeoStatus" role="status">Checking Vimeo connection…</p><details><summary>Private Vimeo connection</summary><p>Requires an upload-capable Vimeo account/API application. No paid plan is purchased automatically. <a href="https://help.vimeo.com/hc/en-us/articles/12427803706001-How-to-request-API-upload-access" target="_blank" rel="noopener">Vimeo API upload access</a></p><label>Vimeo API token<input id="vimeoToken" type="password" autocomplete="off" spellcheck="false"></label><button id="vimeoSaveToken">Save token privately</button><button id="vimeoVerify">Check connection</button></details><label>Saved cloud video<select id="vimeoSource"></select></label><label>Video title<input id="vimeoTitle" maxlength="128"></label><label>Vimeo visibility<select id="vimeoPrivacy"><option value="private">Private · review first</option><option value="unlisted">Unlisted · anyone with the link</option></select></label><div class="toolbar"><button id="vimeoStart" disabled>Upload selected cloud video to Vimeo</button><button id="vimeoRefresh">Refresh Vimeo uploads</button></div><div id="vimeoJobs"></div><p class="muted">Uploads start only when you press Upload and confirm. For Patreon, choose an appropriate Vimeo visibility and use its completed link in your video post. Patreon’s documented public API does not provide general post creation. <a href="https://www.patreon.com/cw/Manhwa_Stories1" target="_blank" rel="noopener">Open your Patreon</a></p></div>`;
}
export function vimeoJobLabel(job){
  if(job.status==='SUBMISSION_UNKNOWN'||job.status==='SUBMITTING')return 'Submission not confirmed. Check Vimeo before creating another upload; Studio will not resubmit automatically.';
  return `${job.title} · ${job.status}${job.uploadStatus?' · upload '+job.uploadStatus:''}${job.transcodeStatus?' · transcode '+job.transcodeStatus:''}${job.actualPrivacy?' · visibility '+job.actualPrivacy:''}`;
}
export async function wireVimeo({p,files,api,action,active,note,document:doc=globalThis.document}){
  const $=id=>doc.getElementById(id),panel=$('vimeoPanel');
  if(!panel)return;
  const mount={};mounts.set(panel,mount);
  const mounted=()=>active()&&$('vimeoPanel')===panel&&mounts.get(panel)===mount;
  const videos=files.filter(file=>file.cloud&&file.category==='Video');
  const prior=drafts.get(p.id)||{};
  $('vimeoSource').replaceChildren(...videos.map(file=>new Option(file.path,file.path)));
  if(videos.some(file=>file.path===prior.path))$('vimeoSource').value=prior.path;
  $('vimeoTitle').value=prior.title??p.name.slice(0,128);$('vimeoPrivacy').value=prior.privacy??'private';
  let configured=false,jobs=[],timer;
  function remember(){drafts.set(p.id,{path:$('vimeoSource').value,title:$('vimeoTitle').value,privacy:$('vimeoPrivacy').value});}
  for(const id of ['vimeoSource','vimeoTitle','vimeoPrivacy'])$(id).onchange=remember;
  function paint(){
    if(!mounted())return;
    $('vimeoStatus').textContent=configured?'Private Vimeo token saved. Verify API upload access before your first upload.':'Vimeo is not connected. Configure its private token and account upload access.';
    $('vimeoStart').disabled=!configured||!videos.length||pending.has(p.id)||jobs.some(j=>['SUBMITTING','SUBMISSION_UNKNOWN'].includes(j.status));
    $('vimeoJobs').replaceChildren();
    for(const job of jobs){
      const row=doc.createElement('p');row.textContent=vimeoJobLabel(job);
      if(job.videoURL){const a=doc.createElement('a');a.href=job.videoURL;a.target='_blank';a.rel='noopener noreferrer';a.textContent=' Open on Vimeo';row.append(a);}
      $('vimeoJobs').append(row);
    }
  }
  async function refresh(){
    clearTimeout(timer);
    try{
      const result=await api('vimeo?project='+encodeURIComponent(p.id));
      if(!mounted())return;configured=result.configured===true;jobs=result.jobs||[];
      for(const job of jobs.filter(j=>j.status==='PROCESSING')){
        const latest=await api('vimeo',{project:p.id,operation:'status',options:{id:job.id}});
        if(!mounted())return;jobs=jobs.map(j=>j.id===latest.id?latest:j);
      }
      paint();
      if(jobs.some(j=>j.status==='PROCESSING')&&mounted())timer=setTimeout(()=>{if(mounted())void refresh();},10000);
    }catch(error){if(mounted()){$('vimeoStatus').textContent=error.message||'Vimeo status is unavailable; saved files remain intact.';$('vimeoStart').disabled=true;}}
  }
  $('vimeoSaveToken').onclick=()=>action(async()=>{
    const field=$('vimeoToken'),token=field.value.trim();if(!token)throw Error('Enter the Vimeo API token.');
    await api('config',{vimeoApiToken:token});field.value='';await refresh();note('Vimeo token saved in the private helper. No video was uploaded.');
  });
  $('vimeoVerify').onclick=()=>action(async()=>{
    const result=await api('vimeo?verify=true');if(mounted())$('vimeoStatus').textContent=result.connected?'Vimeo account connection verified. API upload approval and quota still apply.':'Vimeo is not connected.';
  });
  $('vimeoRefresh').onclick=()=>action(refresh);
  $('vimeoStart').onclick=()=>action(async()=>{
    if(pending.has(p.id))return;remember();const options=drafts.get(p.id);
    if(!options.path||!options.title.trim())throw Error('Choose a cloud video and title.');
    if(!confirm(`Upload this saved video to Vimeo as ${options.privacy}? ${options.privacy==='unlisted'?'Anyone with the completed link can watch.':'It will be private for review.'}`))return;
    pending.add(p.id);paint();
    try{await api('vimeo',{project:p.id,operation:'start',options:{...options,id:crypto.randomUUID().replaceAll('-','')}});}
    finally{pending.delete(p.id);await refresh();}
  });
  await refresh();
}
