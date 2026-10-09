// Resolve only visible image previews. Running requests retain their shared limit
// when a tab/project changes; late replies can never populate a newer screen.
export function createMediaLoader({resolve,limit=4,Observer=globalThis.IntersectionObserver}) {
  let generation=0,active=0,queue=[],observer;
  let seen=new WeakSet();
  function pump(){
    while(active<limit&&queue.length){
      const item=queue.shift();
      if(item.generation!==generation||!item.node.isConnected)continue;
      active++;
      Promise.resolve().then(()=>resolve(item.path,item.projectId)).then(url=>{
        if(item.generation===generation&&item.node.isConnected)item.node.src=url;
      }).catch(error=>{
        if(item.generation===generation&&item.node.isConnected){
          if(item.node.tagName==='IMG')item.node.alt='Asset preview unavailable: '+error.message;
          else item.node.title='Asset preview unavailable: '+error.message;
        }
      }).finally(()=>{active--;pump();});
    }
  }
  function clear(){generation++;queue=[];seen=new WeakSet();observer?.disconnect();observer=undefined;}
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
