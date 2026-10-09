// One durable writer. Edits arriving during a save require a newer checkpoint.
export function createEditorSave(write) {
  let revision=0,savedRevision=0,inFlight;
  return {
    markDirty(){revision++;},
    get dirty(){return savedRevision<revision;},
    flush(){
      if(inFlight)return inFlight;
      if(savedRevision===revision)return Promise.resolve();
      inFlight=(async()=>{
        while(savedRevision<revision){
          const checkpoint=revision;
          await write();
          savedRevision=checkpoint;
        }
      })().finally(()=>{inFlight=undefined;});
      return inFlight;
    },
  };
}
