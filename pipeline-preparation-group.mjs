export const PREPARATION_GROUP='chapter-preparation';
export function preparationGroup(tasks){
  const members=tasks.filter(t=>t.chapter>0&&['bible','clean'].includes(t.kind));
  if(!members.length)return null;
  const ids=new Set(members.map(t=>t.id)),chapters=[...new Set(members.map(t=>t.chapter))].sort((a,b)=>a-b);
  const start=Math.min(...members.map(t=>t.start)),end=Math.max(...members.map(t=>t.end));
  return {id:PREPARATION_GROUP,kind:'preparation-group',name:'Load story state + clean narration',lane:'prepare',chapter:-1,
    start,end,duration:end-start,work:members.reduce((n,t)=>n+t.duration,0),members,chapters,
    deps:[...new Set(members.flatMap(t=>t.deps).filter(id=>!ids.has(id)))],manual:members.some(t=>t.manual)};
}
export function compactPreparation(tasks,compact=true){
  const group=compact?preparationGroup(tasks):null;
  if(!group)return tasks;
  const members=new Set(group.members);return [...tasks.filter(t=>!members.has(t)),group];
}
export function preparationMoves(group,target,preferences){
  if(!Number.isFinite(target)||target<0)throw Error('Choose a finite nonnegative start.');
  const delta=target-group.start,next={...preferences};
  for(const member of group.members)next[member.id]={...preferences[member.id],notBefore:Math.max(0,member.start+delta)};
  return next;
}
