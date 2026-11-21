// Project reads are separate from UI adoption. A slow reply
// cannot choose which editor wins; the caller checks its editor revision.
export function connectionLoading(project, hasPairingKey, pending) {
  // An empty bootstrap placeholder is not the user's saved story or an
  // offline edit. Real local drafts keep their mounted editable interface.
  return Boolean(project?._connectionPlaceholder && hasPairingKey && pending);
}

export async function readProjectPicker(api,capabilities) {
  if (capabilities?.projectPickerAvailable) return api('projects?picker=true');
  return {projects:await api('projects'),complete:true,refreshing:false};
}

export function mergePickerProjects(picker, previous) {
  const complete=picker.complete&&!picker.refreshing&&!picker.error;
  const previousById=new Map(previous.map(row=>[row.id,row]));
  const helperRows=picker.projects.map(row=>{
    const local=previousById.get(row.id);
    previousById.delete(row.id);
    return local?.browserSaved?{...row,browserSaved:true}:row;
  });
  const retained=[...previousById.values()].filter(row=>row.browserSaved||row.unsynced||!complete);
  return [...helperRows,...retained.map(row=>complete&&row.browserSaved?{...row,browserOnly:true}:row)];
}

export async function saveBrowserOnlyDraft(api,draft,capabilities){
  // The catalogue may have advanced since the browser-only row was drawn.
  // Leave any existing helper/cloud edition for explicit conflict-aware sync.
  if(!capabilities?.projectCheckpointAvailable)throw Error('Update the helper to check the private library before saving this browser copy.');
  try{await api('project-checkpoint?id='+encodeURIComponent(draft.id));}
  catch(error){
    if(error.status!==404||error.code!=='PROJECT_NOT_FOUND')throw error;
    const {_unsynced,_offlineChapterEdits,_connectionPlaceholder,...snapshot}=draft;
    return api('import',{project:snapshot,onlyIfMissing:true});
  }
  throw Error('A helper copy now exists. Refresh the project list and use Sync offline edits to compare revisions. Your browser copy is retained.');
}

export async function resolveConnectedProject({project,projects,api,preferredId,catalogueComplete=true,catalogueError=''}) {
  const local=project?._connectionPlaceholder?null:project;
  if(!local){
    if(preferredId && (projects.some(p=>p.id===preferredId) || !catalogueComplete)){
      try{return {project:await api('project?id='+encodeURIComponent(preferredId)),preserveOffline:false};}
      catch(error){if(error.status!==404||error.code!=='PROJECT_NOT_FOUND')throw error;}
    }
    const first=projects.find(p=>p.id!==preferredId);
    if(first)return {project:await api('project?id='+encodeURIComponent(first.id)),preserveOffline:false};
    if(!catalogueComplete)throw new Error(catalogueError || 'Loading saved cloud projects. Studio will reconnect automatically.');
    return {project:await api('create',{name:'My story'}),preserveOffline:false};
  }
  let remote;
  try{remote=await api('project?id='+encodeURIComponent(local.id));}
  catch(error){if(error.status!==404||error.code!=='PROJECT_NOT_FOUND')throw error;}
  if(remote)return {project:remote,preserveOffline:Boolean(local._unsynced)};
  // An offline revision-zero draft can also be an edited copy of a removed
  // private project. Reconnection alone must never publish either record.
  return {project:{...local,_unsynced:true},preserveOffline:false,browserOnly:true};
}

export function canAdoptConnection(current,candidate,{requestedProject,editorRevision,startEditorRevision,preserveOffline}){
  return Boolean(Array.isArray(candidate?.chapters)&&current===requestedProject&&
    editorRevision===startEditorRevision&&!preserveOffline&&
    (candidate.id!==current.id||candidate.revision>=current.revision));
}
