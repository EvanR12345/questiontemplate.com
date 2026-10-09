// Rebuilt only when a new schedule is adopted, never once per pointer movement.
export function indexTasks(tasks){return new Map(tasks.map(task=>[task.id,task]));}
export function dependsOnTask(index,startId,targetId){
  const waiting=[startId],seen=new Set();
  while(waiting.length){
    const id=waiting.pop();if(seen.has(id))continue;seen.add(id);
    const task=index.get(id);if(!task)throw Error('Missing dependency: '+id);
    for(const dependency of task.deps){if(dependency===targetId)return true;waiting.push(dependency);}
  }
  return false;
}
