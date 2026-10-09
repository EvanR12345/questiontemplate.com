// Reads and missing-project imports are separate from UI adoption. A slow reply
// cannot choose which editor wins; the caller checks its editor revision.
export async function resolveConnectedProject({project,projects,providers,api}) {
  const local=project?._connectionPlaceholder?null:project;
  if(!local)return {project:projects.length
    ?await api('project?id='+encodeURIComponent(projects[0].id))
    :await api('create',{name:'My story'}),preserveOffline:false};
  let remote;
  try{remote=await api('project?id='+encodeURIComponent(local.id));}
  catch(error){if(error.status!==404||error.code!=='PROJECT_NOT_FOUND')throw error;}
  if(remote)return {project:remote,preserveOffline:Boolean(local._unsynced)};
  const copy=structuredClone(local);delete copy._unsynced;
  const native=providers?.['native-flux'];
  if(!copy._imageSelectedByUser&&!copy.chapters.some(c=>c.scenes.length)&&native?.validated)
    copy.settings.image={...copy.settings.image,...native.recommended,provider:'native-flux',model:'flux2-klein-4b-q4',workflow:'reference-edit'};
  return {project:await api('import',{project:copy}),preserveOffline:false};
}

export function canAdoptConnection(current,candidate,{requestedProject,editorRevision,startEditorRevision,preserveOffline}){
  return Boolean(candidate?.chapters?.length&&current===requestedProject&&
    editorRevision===startEditorRevision&&!preserveOffline&&
    (candidate.id!==current.id||candidate.revision>=current.revision));
}
