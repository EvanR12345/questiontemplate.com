// One active calculation. Replacing it settles the previous caller as cancelled
// as well as terminating its worker, so rapid control changes do not retain
// permanently pending rebuild promises and their project snapshots.
export function createWorkerRequest(makeWorker){
  let active;
  const cancel=()=>active?.finish(null);
  const run=payload=>{
    cancel();
    return new Promise((resolve,reject)=>{
      let worker,finished=false;
      const finish=(result,error)=>{
        if(finished)return;finished=true;
        if(active?.finish===finish)active=null;
        if(worker){worker.onmessage=null;worker.onerror=null;worker.terminate();}
        if(error)reject(error);else resolve(result);
      };
      try{
        worker=makeWorker();active={finish};
        worker.onmessage=({data})=>data.type==='error'?finish(null,Error(data.message)):finish(data);
        worker.onerror=()=>finish(null,Error('The background planner could not load. Refresh the page to load its updated files.'));
        worker.postMessage(payload);
      }catch(error){finish(null,error);}
    });
  };
  return {run,cancel};
}
