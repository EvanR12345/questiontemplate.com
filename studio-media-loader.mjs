// Resolve only visible image previews. Running requests retain their shared limit
// when a tab/project changes; late replies can never populate a newer screen.
export function createMediaLoader({resolve,limit=4,Observer=globalThis.IntersectionObserver,now=Date.now,renewAfter=45*60*1000}) {
  let generation=0,active=0,queue=[],observer;
  let seen=new WeakSet();
  let cleanups=[];
  function bindPlayback(item){
    const node=item.node;
    if(!['VIDEO','AUDIO'].includes(node.tagName)||!node.addEventListener)return;
    let issued=now(),recovering=false,applying=false,playing=false,restore;
    const valid=()=>item.generation===generation&&node.isConnected;
    const onPlaying=()=>{playing=true;},onPause=()=>{if(!recovering)playing=false;};
    const onMetadata=()=>{
      if(!restore||!applying||!valid())return;
      const saved=restore;restore=undefined;recovering=false;applying=false;
      try{node.currentTime=Math.min(saved.time,Number.isFinite(node.duration)?node.duration:saved.time);}catch{}
      node.playbackRate=saved.rate;
      if(saved.playing){try{Promise.resolve(node.play()).catch(()=>{node.title='Viewing link renewed. Press Play to continue.';});}catch{node.title='Viewing link renewed. Press Play to continue.';}}
    };
    const onError=()=>{
      if(!valid()||recovering||now()-issued<renewAfter)return;
      recovering=true;
      restore={time:Number(node.currentTime)||0,rate:node.playbackRate||1,playing};
      node.title='Renewing your private viewing link…';
      queue.push({...item,refresh:true,renewed:url=>{
        if(!valid())return;
        issued=now();applying=true;node.title='';node.src=url;node.load?.();
      },failed:error=>{recovering=false;restore=undefined;node.title='Could not renew viewing link: '+error.message;}});
      pump();
    };
    for(const [name,handler] of [['playing',onPlaying],['pause',onPause],['loadedmetadata',onMetadata],['error',onError]]){
      node.addEventListener(name,handler);cleanups.push(()=>node.removeEventListener(name,handler));
    }
  }
  function pump(){
    while(active<limit&&queue.length){
      const item=queue.shift();
      if(item.generation!==generation||!item.node.isConnected)continue;
      active++;
      Promise.resolve().then(()=>resolve(item.path,item.projectId,{refresh:Boolean(item.refresh)})).then(url=>{
        if(item.generation===generation&&item.node.isConnected){
          if(item.renewed)item.renewed(url);
          else {bindPlayback(item);item.node.src=url;}
        }
      }).catch(error=>{
        if(item.generation===generation&&item.node.isConnected){
          if(item.failed){item.failed(error);return;}
          if(item.node.tagName==='IMG')item.node.alt='Asset preview unavailable: '+error.message;
          else item.node.title='Asset preview unavailable: '+error.message;
        }
      }).finally(()=>{active--;pump();});
    }
  }
  function clear(){generation++;queue=[];seen=new WeakSet();observer?.disconnect();observer=undefined;for(const cleanup of cleanups)cleanup();cleanups=[];}
  return {
    clear,
    load(root,projectId){
      clear();
      const current=generation;
      const enqueue=node=>{
        if(current!==generation||seen.has(node))return;
        seen.add(node);queue.push({node,path:node.dataset.asset,projectId,generation:current});pump();
      };
      if(Observer)observer=new Observer(entries=>{
        if(current!==generation)return;
        for(const entry of entries)if(entry.isIntersecting){observer?.unobserve(entry.target);enqueue(entry.target);}
      },{rootMargin:'240px'});
      for(const node of root.querySelectorAll('[data-asset]')){
        if(!node.dataset.asset)continue;
        if(node.tagName==='IMG'){
          node.loading='lazy';node.decoding='async';
          if(observer)observer.observe(node);else enqueue(node);
        }else enqueue(node);
      }
    },
  };
}
