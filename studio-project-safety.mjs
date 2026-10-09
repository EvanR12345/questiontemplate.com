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
