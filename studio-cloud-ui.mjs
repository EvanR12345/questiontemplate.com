// Cloud storage and finishing controls share the existing Studio connection.
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const bytes=n=>n>=2**30?(n/2**30).toFixed(2)+' GiB':n>=2**20?(n/2**20).toFixed(1)+' MiB':(n/1024).toFixed(1)+' KiB';
const transferring=new Set();
const starting=new Set();
const publishingDrafts=new Map();
export async function cachedMediaLink(cache,projectId,path,api,now=Date.now){
  if(!path)return '';
  const key=projectId+'/'+path,cached=cache.get(key);
  if(cached?.expires>now())return cached.url;
  cache.delete(key);
  const result=await api('media-link',{project:projectId,path});
  // The helper may reuse a ticket nearing expiry. Never grant it a fresh
  // client-side lifetime longer than the actual signed/local capability.
  const expires=Math.min(Number(result.expires)*1000-1000,now()+3000000);
  if(expires>now())cache.set(key,{url:result.url,expires});
  return result.url;
}
function destinationControls(s){
  return `<label>Export version<select id="engDestination"><option value="youtube" ${s.exportDestination!=='patreon'?'selected':''}>YouTube</option><option value="patreon" ${s.exportDestination==='patreon'?'selected':''}>Patreon</option></select></label><p class="muted">Both versions reuse your narration and story footage. Patreon leaves out promotional reminders and the outro by default. Earlier exports remain saved.</p><label class="inline"><input id="engPatreonOutro" type="checkbox" ${s.patreonOutroEnabled?'checked':''}>Also include the spoken outro in Patreon exports</label><label class="inline"><input id="engPatreonPopup" type="checkbox" ${s.patreonPopupEnabled?'checked':''}>Also include subscribe reminders in Patreon exports</label>`;
}
function splitControls(s,prefix){
  return `<label>Split at<select id="${prefix}Mode"><option value="duration" ${s.splitMode!=='chapters'?'selected':''}>Time intervals</option><option value="chapters" ${s.splitMode==='chapters'?'selected':''}>Chapter boundaries</option></select></label><label>Chapters per part<input id="${prefix}Chapters" type="number" min="1" step="1" value="${esc(s.chaptersPerPart??1)}"></label><p class="muted">One chapter per part, or group consecutive chapters. Chapter splits preserve complete narration and reuse cached footage. The project intro appears in the first part unless configured for every chapter. Each part uses the selected export version's ending.</p>`;
}
export function engagementForm(p){
  const s={popupEnabled:false,minMinutes:10,maxMinutes:15,popupDuration:5,popupText:'Enjoying the story? Subscribe for the next part.',dingEnabled:true,dingVolume:.12,position:'bottom-right',outroEnabled:false,outroDuration:15,outroText:'Subscribe for more fantasy stories, and tell us your favorite moment in the comments.',nextPartTeaser:'',splitEnabled:false,partMinutes:120,...p.settings.engagement};
  return `<div class="section-box" id="settings-engagement"><h3>Export versions, reminders & ending</h3>${destinationControls(s)}<label class="inline"><input id="engPopup" type="checkbox" ${s.popupEnabled?'checked':''}>Show subscribe reminder</label><div class="two-col"><label>Minimum interval (minutes)<input id="engMin" type="number" min="1" max="180" value="${s.minMinutes}"></label><label>Maximum interval (minutes)<input id="engMax" type="number" min="1" max="180" value="${s.maxMinutes}"></label><label>Show for (seconds)<input id="engDuration" type="number" min="2" max="15" value="${s.popupDuration}"></label><label>Corner<select id="engPosition"><option value="bottom-right" ${s.position==='bottom-right'?'selected':''}>Bottom right</option><option value="bottom-left" ${s.position==='bottom-left'?'selected':''}>Bottom left</option></select></label></div><label>Reminder message<input id="engText" maxlength="160" value="${esc(s.popupText)}"></label><label class="inline"><input id="engDing" type="checkbox" ${s.dingEnabled?'checked':''}>Play a quiet ding</label><label>Ding volume (%)<input id="engVolume" type="number" min="0" max="30" value="${Math.round(s.dingVolume*100)}"></label><p class="muted">Intervals vary within your range. Retrying keeps the same timings. Chapter narration masters stay unchanged.</p><label class="inline"><input id="engOutro" type="checkbox" ${s.outroEnabled?'checked':''}>Add spoken outro to the finished video</label><label>Outro narration<textarea id="engOutroText" maxlength="800" rows="3">${esc(s.outroText)}</textarea></label><label>Next-part teaser · only story facts you approve<textarea id="engTeaser" maxlength="800" rows="2">${esc(s.nextPartTeaser)}</textarea></label><label>Minimum outro duration (seconds)<input id="engOutroDuration" type="number" min="5" max="60" value="${s.outroDuration}"></label><p class="muted">Uses the selected narrator. Longer speech extends the outro instead of cutting it off. The full story receives one outro; a separately exported chapter can receive its own.</p><button id="prepareOutro">Prepare outro audio</button><label class="inline"><input id="engSplit" type="checkbox" ${s.splitEnabled?'checked':''}>Split the full video into parts automatically</label>${splitControls(s,'engSplit')}<label>Minutes per part<input id="engPartMinutes" type="number" min="1" max="1440" value="${s.partMinutes}"></label><p class="muted">120 minutes makes six parts from a 12-hour story. Cuts use nearby keyframes and reuse the original quality. The Files tab shows actual boundaries.</p></div>`;
}
export function engagementValues(previous={}){
  const v=id=>document.getElementById(id);
  return {...previous,exportDestination:v('engDestination')?.value??previous.exportDestination??'youtube',patreonOutroEnabled:v('engPatreonOutro')?.checked??previous.patreonOutroEnabled??false,patreonPopupEnabled:v('engPatreonPopup')?.checked??previous.patreonPopupEnabled??false,splitMode:v('engSplitMode')?.value??previous.splitMode??'duration',chaptersPerPart:Number(v('engSplitChapters')?.value??previous.chaptersPerPart??1),popupEnabled:v('engPopup').checked,minMinutes:Number(v('engMin').value),maxMinutes:Number(v('engMax').value),popupDuration:Number(v('engDuration').value),position:v('engPosition').value,popupText:v('engText').value,dingEnabled:v('engDing').checked,dingVolume:Number(v('engVolume').value)/100,outroEnabled:v('engOutro').checked,outroText:v('engOutroText').value,nextPartTeaser:v('engTeaser').value,outroDuration:Number(v('engOutroDuration').value),splitEnabled:v('engSplit').checked,partMinutes:Number(v('engPartMinutes').value)};
}
export function filesPanel(p){
  return `<h2>Files & publishing</h2><p class="muted">Your saved project, artwork, audio, videos and thumbnails in one place. Cloud saves finish only after all assets upload successfully.</p><div id="cloudStatus" role="status">Checking storage…</div><div class="toolbar"><button id="cloudRefresh">Refresh files</button><button id="cloudSync">Save this project to cloud</button><button id="cloudSyncAll">Save all helper projects</button></div><div class="two-col"><label>Find a file<input id="cloudSearch" type="search" placeholder="Name or folder"></label><label>Category<select id="cloudCategory">${['All','Video','Audio','Images','Project data'].map(x=>`<option>${x}</option>`).join('')}</select></label></div><div id="cloudFiles"></div><div class="section-box"><h3>Video parts</h3>${splitControls(p.settings.engagement??{},'fileSplit')}<label>Minutes per part<input id="splitMinutes" type="number" min="1" max="1440" value="${p.settings.engagement?.partMinutes??120}"></label><button id="cloudSplit" ${p.render?.path?'':'disabled'}>Split saved full video</button><p class="muted">No images, directing or narration are regenerated. Existing full video stays saved.</p><div>${(p.render?.parts??[]).map(x=>`<p>Part ${x.number} · ${(x.start/60).toFixed(2)} → ${(x.end/60).toFixed(2)} min <button data-cloud-open="${esc(x.path)}">Open part</button></p>`).join('')}</div></div><div class="section-box"><h3>Thumbnail maker</h3><p class="muted">Uses your existing story art, with a clear part badge near the top left. Creating a thumbnail does not alter the video.</p><label>Story image<select id="thumbSource">${p.chapters.flatMap(c=>c.scenes.flatMap(s=>s.shots)).filter(s=>s.imagePath).map(s=>`<option value="${esc(s.imagePath)}">${esc(s.id)} · ${esc(s.action||'Story image')}</option>`).join('')}</select></label><label>Title<input id="thumbTitle" maxlength="180" value="${esc(p.name)}"></label><label>Part label · 1, 1–6, or 5<input id="thumbPart" maxlength="24" value="1"></label><button id="makeThumbnail">Create thumbnail</button><div id="thumbnailResult">${(p.thumbnails??[]).slice(-4).map(x=>`<img data-asset="${esc(x.path)}" alt="${esc(x.title)} · Part ${esc(x.part)}" style="width:240px;max-width:100%"><button data-cloud-open="${esc(x.path)}">Open thumbnail</button>`).join('')}</div></div><div class="section-box"><h3>Upload to YouTube from cloud</h3><p id="youtubeStatus">Connect a cloud publisher and authorize your YouTube channel before uploading. Files remain in private cloud storage; uploads default to private.</p><p class="muted">Cloud storage holds files. It does not itself run narration, video rendering or YouTube uploads. The current helper still uses a local working cache. Cloud production and the publisher must be connected before the laptop can be removed from the workflow.</p></div>`;
}
export async function wireFiles({p,api,action,note,media,submit,reload}){
  const $=id=>document.getElementById(id);
  let rows=[],page=0,filter='All',term='',refreshVersion=0;
  const view=$('cloudStatus');
  const active=()=>$('cloudStatus')===view;
  const open=async name=>{const url=await media(name); const a=document.createElement('a');a.href=url;a.target='_blank';a.rel='noopener noreferrer';a.click();};
  function bindOpen(){document.querySelectorAll('[data-cloud-open]').forEach(b=>b.onclick=()=>action(()=>open(b.dataset.cloudOpen)));}
  function paint(){
    const selected=rows.filter(r=>(filter==='All'||r.category===filter)&&r.path.toLowerCase().includes(term));
    page=Math.min(page,Math.max(0,Math.ceil(selected.length/50)-1));
    $('cloudFiles').innerHTML=`<p class="muted">${selected.length} files · ${bytes(selected.reduce((sum,r)=>sum+r.bytes,0))}</p><div style="overflow-x:auto"><table style="width:100%;text-align:left"><thead><tr><th>File</th><th>Type</th><th>Size</th><th>Storage</th><th></th></tr></thead><tbody>${selected.slice(page*50,page*50+50).map(r=>`<tr><td style="overflow-wrap:anywhere">${esc(r.path)}</td><td>${esc(r.category)}</td><td>${bytes(r.bytes)}</td><td>${r.cloud?'Cloud saved':'Helper cache'}</td><td><button data-cloud-open="${esc(r.path)}">Open</button></td></tr>`).join('')}</tbody></table></div><div class="toolbar"><button id="filePrev" ${page?'':'disabled'}>Previous</button><span>Page ${page+1} / ${Math.max(1,Math.ceil(selected.length/50))}</span><button id="fileNext" ${(page+1)*50<selected.length?'':'disabled'}>Next</button></div>`;
    $('filePrev').onclick=()=>{page--;paint();};$('fileNext').onclick=()=>{page++;paint();};bindOpen();
  }
  async function refresh(){
    const version=++refreshVersion;
    let status,files,publisher;
    try{
      [status,files,publisher]=await Promise.all([api('storage?project='+p.id),api('files?project='+p.id),api('publisher').catch(()=>({configured:false,connected:false}))]);
    }catch{
      if(!active()||version!==refreshVersion)return;
      $('cloudStatus').textContent='Files could not be loaded. Connect the shared helper using Project actions, then Refresh files. Existing cloud saves are unchanged.';
      if(!rows.length)$('cloudFiles').innerHTML='<p class="muted">The file list will appear after the helper connects.</p>';
      for(const id of ['cloudSync','cloudSyncAll','cloudSplit','makeThumbnail','publishStart'])if($(id))$(id).disabled=true;
      return;
    }
    const state=status.projects[p.id]??{};
    const publisherMatches=(publisher.storageProvider??'r2')===(status.provider==='Google Cloud Storage'?'gcs':'r2');
    if(!active()||version!==refreshVersion)return;
    $('cloudSync').disabled=!status.enabled;$('cloudSyncAll').disabled=!status.enabled;
    $('cloudSplit').disabled=!p.render?.path;
    $('makeThumbnail').disabled=!p.chapters.some(c=>c.scenes.some(s=>s.shots.some(shot=>shot.imagePath)));
    $('cloudStatus').textContent=status.enabled?`${status.provider||'Cloud storage'} · ${state.status??'Not yet synced'} · ${state.files??0} cloud files · ${bytes(state.bytes??0)}${state.error?' · '+state.error:''}${status.error?' · '+status.error:''}`:status.error||'Cloud storage is not connected yet. Existing helper files remain safe.';
    rows=files.files;paint();
    const publishedJobs=await Promise.all((p.publishing??[]).map(async job=>{
      if(!publisher.configured||['COMPLETE','CANCELLED'].includes(job.status))return job;
      try{return await api('publish',{project:p.id,operation:'status',options:{id:job.id}});}catch{return job;}
    }));
    if(!active()||version!==refreshVersion)return;
    const yt=$('youtubeStatus');
    if(yt){
      yt.textContent=publisher.configured&&!publisherMatches?'Publisher storage does not match this archive. Connect the matching private publisher before uploading.':publisher.connected?'YouTube connected. Uploads travel from cloud storage to YouTube; this laptop sends only control messages.':publisher.configured?'Cloud publisher ready. Connect your YouTube channel.':'Cloud publisher deployment and Google OAuth setup are still pending.';
      if(publisher.configured){
        const remember=()=>{
          if(!active()||!$('publisherControls'))return;
          publishingDrafts.set(p.id,{source:$('publishSource').value,title:$('publishTitle').value,
            description:$('publishDescription').value,privacy:$('publishPrivacy').value,kids:$('publishKids').checked});
        };
        remember();
        const controls=document.createElement('div');controls.id='publisherControls';
        controls.innerHTML=`<button id="youtubeConnect">Connect YouTube</button><label>Saved cloud video<select id="publishSource">${rows.filter(r=>r.category==='Video'&&r.cloud).map(r=>`<option value="${esc(r.path)}">${esc(r.path)}</option>`).join('')}</select></label><label>YouTube title<input id="publishTitle" maxlength="100" value="${esc(p.name.slice(0,100))}"></label><label>Description<textarea id="publishDescription" maxlength="5000" rows="3"></textarea></label><label>Visibility<select id="publishPrivacy"><option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select></label><label class="inline"><input type="checkbox" id="publishKids">This video is made for kids</label><button id="publishStart" ${publisher.connected&&publisherMatches&&rows.some(r=>r.category==='Video'&&r.cloud)?'':'disabled'}>Upload selected cloud video</button><div id="publishProgress" role="status"></div>${publishedJobs.map(j=>`<p>${esc(j.title)} · ${esc(j.status)} · ${Math.round(100*j.uploaded/j.total)}% ${j.status==='COMPLETE'?`<a href="https://www.youtube.com/watch?v=${encodeURIComponent(j.videoId)}" target="_blank" rel="noopener">Open video</a>`:j.status==='CANCELLED'?'':`<button data-upload-resume="${esc(j.id)}">Resume upload</button><button data-upload-cancel="${esc(j.id)}">Cancel upload</button>`}</p>`).join('')}`;
        $('publisherControls')?.remove();yt.after(controls);
        const draft=publishingDrafts.get(p.id);
        if(draft){
          if(rows.some(r=>r.category==='Video'&&r.cloud&&r.path===draft.source))$('publishSource').value=draft.source;
          $('publishTitle').value=draft.title;$('publishDescription').value=draft.description;
          $('publishPrivacy').value=draft.privacy;$('publishKids').checked=draft.kids;
        }
        controls.oninput=remember;controls.onchange=remember;
        controls.insertAdjacentHTML('beforeend','<p class="muted">Choose a video part for uploads over 12 hours. YouTube may keep a new unaudited app\'s uploads private; final visibility is confirmed after upload. <a href="https://developers.google.com/youtube/v3/docs/videos/insert" target="_blank" rel="noopener">YouTube requirements</a></p>');
        controls.insertAdjacentHTML('beforeend','<p class="muted">During Google OAuth Testing mode, reconnect the channel after seven days. Saved videos and upload progress remain in cloud storage. <a href="https://developers.google.com/identity/protocols/oauth2#expiration" target="_blank" rel="noopener">Google authorization lifetime</a></p>');
        $('youtubeConnect').onclick=()=>action(async()=>{const result=await api('publish',{operation:'connect'});const a=document.createElement('a');a.href=result.url;a.target='_blank';a.rel='noopener';a.click();});
        async function runUpload(job){
          if(transferring.has(job.id))return;transferring.add(job.id);
          try{
            while(active()&&!['COMPLETE','CANCELLED'].includes(job.status)){
              job=await api('publish',{project:p.id,operation:'next',options:{id:job.id}});
              if(!active())return;
              if($('publishProgress'))$('publishProgress').textContent=`${job.title} · ${Math.round(100*job.uploaded/job.total)}% · ${job.status}`;
            }
            if(!active())return;
            await reload();note(job.status==='COMPLETE'?'YouTube upload completed. '+(job.actualPrivacy?'Visibility: '+job.actualPrivacy:'Check final visibility in YouTube Studio.'):'Upload cancelled; source video remains in cloud storage.');
          }finally{transferring.delete(job.id);}
        }
        $('publishStart').onclick=()=>action(async()=>{
          if(starting.has(p.id))return;
          const privacy=$('publishPrivacy').value;
          if(!confirm(`Upload this video to YouTube as ${privacy}? ${privacy==='public'?'Anyone will be able to watch it.':'You can review it in YouTube Studio.'}`))return;
          starting.add(p.id);const button=$('publishStart');button.disabled=true;
          try{
            const job=await api('publish',{project:p.id,operation:'start',options:{path:$('publishSource').value,title:$('publishTitle').value,description:$('publishDescription').value,privacy,madeForKids:$('publishKids').checked}});await runUpload(job);
          }finally{starting.delete(p.id);if(button.isConnected)button.disabled=!publisher.connected||!publisherMatches||!rows.some(r=>r.category==='Video'&&r.cloud);}
        });
        controls.querySelectorAll('[data-upload-resume]').forEach(b=>b.onclick=()=>action(async()=>{const job=await api('publish',{project:p.id,operation:'status',options:{id:b.dataset.uploadResume}});await runUpload(job);}));
        controls.querySelectorAll('[data-upload-cancel]').forEach(b=>b.onclick=()=>action(async()=>{await api('publish',{project:p.id,operation:'cancel',options:{id:b.dataset.uploadCancel}});await reload();}));
      }
    }
  }
  $('cloudSearch').oninput=e=>{term=e.target.value.toLowerCase();page=0;paint();};$('cloudCategory').onchange=e=>{filter=e.target.value;page=0;paint();};
  $('cloudRefresh').onclick=()=>action(refresh);
  $('cloudSync').onclick=()=>action(async()=>{await api('storage-sync',{project:p.id});await refresh();note('Cloud save queued. Completed uploads will be verified before the project snapshot is published.');});
  $('cloudSyncAll').onclick=()=>action(async()=>{await api('storage-sync',{});await refresh();});
  $('cloudSplit').onclick=()=>action(()=>submit('split-video',{minutes:Number($('splitMinutes').value),mode:$('fileSplitMode')?.value??'duration',chaptersPerPart:Number($('fileSplitChapters')?.value??1)}));
  $('makeThumbnail').onclick=()=>action(async()=>{const t=await api('thumbnail',{project:p.id,options:{source:$('thumbSource').value,title:$('thumbTitle').value,part:$('thumbPart').value}});await reload();note('Thumbnail saved at 1280 × 720.');});
  bindOpen();await refresh();
}
