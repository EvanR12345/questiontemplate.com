// Reads and missing-project imports are separate from UI adoption. A slow reply
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
  const ids=new Set(picker.projects.map(row=>row.id));
  return [...picker.projects,...previous.filter(row=>!ids.has(row.id) && (row.unsynced || !picker.complete))];
}

export async function resolveConnectedProject({project,projects,providers,api,preferredId,catalogueComplete=true,catalogueError=''}) {
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
  const copy=structuredClone(local);delete copy._unsynced;delete copy._offlineChapterEdits;
  const native=providers?.['native-flux'];
  if(!copy._imageSelectedByUser&&!copy.chapters.some(c=>c.scenes.length)&&native?.validated)
    copy.settings.image={...copy.settings.image,...native.recommended,provider:'native-flux',model:'flux2-klein-4b-q4',workflow:'reference-edit'};
  return {project:await api('import',{project:copy,onlyIfMissing:true}),preserveOffline:false};
}

export function canAdoptConnection(current,candidate,{requestedProject,editorRevision,startEditorRevision,preserveOffline}){
  return Boolean(candidate?.chapters?.length&&current===requestedProject&&
    editorRevision===startEditorRevision&&!preserveOffline&&
    (candidate.id!==current.id||candidate.revision>=current.revision));
}
