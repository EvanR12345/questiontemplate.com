const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function thumbnailIndex(project){
  const entries=[];
  for(const chapter of project.chapters)for(const scene of chapter.scenes)for(const shot of scene.shots)
    if(shot.imagePath){const label=`${chapter.name||'Chapter'} · ${shot.id} · ${shot.action||'Story image'}`;
      entries.push({path:shot.imagePath,chapterId:chapter.id,label,search:label.toLowerCase()});}
  return entries;
}
export function thumbnailPage(index,{chapter='',term='',page=0,selected=''}={}){
  const search=term.trim().toLowerCase();
  const filtered=index.filter(e=>(!chapter||e.chapterId===chapter)&&(!search||e.search.includes(search)));
  const pages=Math.max(1,Math.ceil(filtered.length/50)),current=Math.min(Math.max(0,page),pages-1);
  const entries=filtered.slice(current*50,current*50+50);
  const retained=index.find(e=>e.path===selected);
  if(retained&&!entries.some(e=>e.path===selected))entries.unshift({...retained,label:'Selected · '+retained.label});
  return {entries,total:filtered.length,page:current,pages,selected:retained?.path||entries[0]?.path||''};
}
export function thumbnailOptions(result){
  return result.entries.map(e=>`<option value="${esc(e.path)}" ${e.path===result.selected?'selected':''}>${esc(e.label)}</option>`).join('');
}
export function thumbnailPicker(project){
  const result=thumbnailPage(thumbnailIndex(project));
  return `<div class="two-col"><label>Image chapter<select id="thumbChapter"><option value="">All chapters</option>${project.chapters.map(c=>`<option value="${esc(c.id)}">${esc(c.name||'Chapter')}</option>`).join('')}</select></label><label>Find story image<input id="thumbSearch" type="search" maxlength="200" placeholder="Character, action or shot ID"></label></div><label>Story image<select id="thumbSource">${thumbnailOptions(result)}</select></label><div class="toolbar"><button id="thumbPrevious" disabled>Previous images</button><span id="thumbPageStatus" class="muted" role="status">${result.total} images · page 1 / ${result.pages}</span><button id="thumbNext" ${result.pages===1?'disabled':''}>Next images</button></div>`;
}
export function wireThumbnailPicker(project,document){
  const source=document.getElementById('thumbSource');if(!source)return;
  const index=thumbnailIndex(project);let page=0;
  const control=id=>document.getElementById(id);
  const paint=()=>{
    if(control('thumbSource')!==source)return;
    const result=thumbnailPage(index,{chapter:control('thumbChapter').value,term:control('thumbSearch').value,page,selected:source.value});
    page=result.page;source.innerHTML=thumbnailOptions(result);source.value=result.selected;
    control('thumbPageStatus').textContent=`${result.total} matching images · page ${page+1} / ${result.pages}`;
    control('thumbPrevious').disabled=page===0;control('thumbNext').disabled=page===result.pages-1;
  };
  source.onchange=paint;
  control('thumbChapter').onchange=()=>{page=0;paint();};
  control('thumbSearch').oninput=()=>{page=0;paint();};
  control('thumbPrevious').onclick=()=>{page--;paint();};
  control('thumbNext').onclick=()=>{page++;paint();};
}
