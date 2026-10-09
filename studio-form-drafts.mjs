// In-memory drafts survive a redraw or project switch. Secrets and file inputs
// are deliberately excluded; this is not a durable project checkpoint.
export function createFormDrafts() {
  const projects=new Map();
  const read=control=>control.type==='checkbox'||control.type==='radio'
    ?{type:control.type,checked:control.checked}:{type:control.type,value:control.value};
  const equal=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
  return {
    remember(projectId,control){
      if(!control?.id||!['INPUT','SELECT','TEXTAREA'].includes(control.tagName)||
        ['password','file','hidden'].includes(control.type))return false;
      if(!projects.has(projectId))projects.set(projectId,new Map());
      projects.get(projectId).set(control.id,read(control));return true;
    },
    restore(projectId,root){
      for(const [id,state] of projects.get(projectId)||[]){
        // Avoid selectors built from untrusted IDs and avoid a different form.
        const control=root.ownerDocument.getElementById(id);
        if(!control||!root.contains(control)||control.type!==state.type)continue;
        if('checked'in state)control.checked=state.checked;
        else if(control.tagName!=='SELECT'||[...control.options].some(o=>o.value===state.value))control.value=state.value;
      }
    },
    snapshot(projectId){return new Map(projects.get(projectId)||[]);},
    acknowledge(projectId,saved){
      const drafts=projects.get(projectId);if(!drafts)return;
      for(const [id,state] of saved)if(equal(drafts.get(id),state))drafts.delete(id);
      if(!drafts.size)projects.delete(projectId);
    },
    has(projectId){return Boolean(projects.get(projectId)?.size);},
    get dirty(){return projects.size>0;},
  };
}

// Last selection wins only while the same editor remains unchanged. Rejected
// requests have no authority to restore the old selector or show a stale error.
export function createProjectSelection(){
  let version=0;
  return {
    begin(project,editorRevision){return {version:++version,project,editorRevision};},
    latest(request){return request.version===version;},
    current(request,project,editorRevision){return request.version===version&&
      request.project===project&&request.editorRevision===editorRevision;},
  };
}
