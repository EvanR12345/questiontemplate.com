export async function checkpointProject(project,{connected,cloudEnabled,save,remember,warn=()=>{}}){
  let browserBackup=false;
  if(!connected||!cloudEnabled||project._unsynced){
    try{await save(project);browserBackup=true;}
    catch(error){
      if(!connected||project._unsynced)throw new Error('Could not save offline edits. Keep this page open and use Export project. Browser storage is unavailable.',{cause:error});
      warn('Project saved to the helper; its optional browser backup is unavailable.');
    }
  }
  try{remember(project.id);}
  catch{warn('Project data is saved, but the browser could not remember the last selected project.');}
  return {browserBackup};
}

export function backupSnapshot(project,chapterId,tab,values={}){
  const backup=structuredClone(project);
  const chapter=backup.chapters.find(item=>item.id===chapterId);
  if(!chapter)return backup;
  if(tab==='write'){
    if(typeof values.sourceText==='string')chapter.sourceText=values.sourceText;
    if(typeof values.name==='string'&&values.name)chapter.name=values.name;
  }else if(tab==='narration'){
    for(const key of ['cleanNarrationText','narrationMode'])if(typeof values[key]==='string')chapter[key]=values[key];
    if(typeof values.includeChapterLabel==='boolean')chapter.includeChapterLabel=values.includeChapterLabel;
  }
  return backup;
}

export function canApplyBackgroundProject(current,latest,{requestedId,dirty=false,editing=false}={}){
  return Boolean(current&&latest&&current.id===requestedId&&latest.id===requestedId&&
    !dirty&&!editing&&!current._unsynced&&
    Number.isSafeInteger(latest.revision)&&Number.isSafeInteger(current.revision)&&latest.revision>=current.revision);
}


// Only chapter prose edits can be replayed on a newer helper edition. Legacy
// browser snapshots and edits to other structures use the revision-checked import.
const editorFields = new Set(['sourceText','name','cleanNarrationText','narrationMode','includeChapterLabel']);
export function rememberOfflineEdit(project, scope, target, values) {
  if (scope !== 'chapter' || !Object.keys(values).every(key => editorFields.has(key))) {
    delete project._offlineChapterEdits;
    return;
  }
  if (!project._unsynced) project._offlineChapterEdits = [];
  if (!Array.isArray(project._offlineChapterEdits)) return;
  const chapters = project._offlineChapterEdits;
  let edit = chapters.find(item => item.id === target.id);
  if (!edit) {
    edit = {id:target.id, before:{}};
    chapters.push(edit);
  }
  const {before} = edit;
  for (const [key, value] of Object.entries(values)) {
    if (target[key] !== value && !Object.hasOwn(before, key)) before[key] = target[key] ?? null;
  }
}

export function offlineSyncRequest(draft) {
  const { _unsynced, _offlineChapterEdits, ...snapshot } = draft;
  if (!Array.isArray(_offlineChapterEdits)) return {project:draft.id, expectedRevision:draft.revision, snapshot};
  const edits = [];
  for (const {id, before} of _offlineChapterEdits) {
    const chapter = draft.chapters.find(item => item.id === id);
    if (!chapter) throw Error('An edited chapter is missing from this draft. Export the project before synchronizing.');
    const fields = Object.keys(before).filter(key => chapter[key] !== before[key]);
    if (fields.length) edits.push({id, before:Object.fromEntries(fields.map(key => [key,before[key]])),
      values:Object.fromEntries(fields.map(key => [key,chapter[key]]))});
  }
  return {project:draft.id, edits};
}
