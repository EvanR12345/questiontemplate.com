// Same earliest-feasible greedy policy as the frozen vendor scheduler.
// Optimization: resource-vector buckets and active end calendars. Not optimal.
const EPS=1e-7;
class Heap{
 constructor(compare){this.items=[];this.compare=compare;}
 get size(){return this.items.length;}
 peek(){return this.items[0];}
 push(value){const a=this.items;a.push(value);let i=a.length-1;while(i){const p=(i-1)>>1;if(this.compare(a[p],value)<=0)break;a[i]=a[p];i=p;}a[i]=value;}
 pop(){const a=this.items;if(!a.length)return;const first=a[0],last=a.pop();if(a.length){let i=0;for(;;){let k=i*2+1;if(k>=a.length)break;if(k+1<a.length&&this.compare(a[k+1],a[k])<0)k++;if(this.compare(last,a[k])<=0)break;a[i]=a[k];i=k;}a[i]=last;}return first;}
}
const comparePriority=(a,b)=>a.priority-b.priority||a.task.priority-b.task.priority||a.queued-b.queued;
export function validateFastInputs(plan,preferences={}){
 if(!plan||!Array.isArray(plan.tasks)||!plan.tasks.length||!plan.capacities||typeof plan.capacities!=='object'||Array.isArray(plan.capacities))throw Error('Fast scheduling requires a nonempty task graph and resource capacities.');
 for(const [pool,capacity] of Object.entries(plan.capacities))if(!pool||!Number.isFinite(capacity)||capacity<=0)throw Error('Fast resource capacities must be finite and positive.');
 const ids=new Set();for(const t of plan.tasks){if(!t||typeof t.id!=='string'||!t.id||ids.has(t.id))throw Error('Fast task IDs must be unique nonempty strings.');ids.add(t.id);}
 for(const t of plan.tasks){
  if(!Number.isFinite(t.duration)||t.duration<=EPS||!Number.isFinite(t.priority))throw Error('Fast duration/priority must be finite; duration must exceed the scheduling tolerance.');
  if(t.notBefore!==undefined&&(!Number.isFinite(t.notBefore)||t.notBefore<0))throw Error('Fast notBefore must be finite and nonnegative.');
  if(!Array.isArray(t.deps)||new Set(t.deps).size!==t.deps.length||t.deps.some(id=>typeof id!=='string'||id===t.id||!ids.has(id)))throw Error('Fast dependencies must be unique known non-self IDs.');
  if(!t.resources||typeof t.resources!=='object'||Array.isArray(t.resources))throw Error('Fast task resources must be an object.');
  for(const [pool,units] of Object.entries(t.resources))if(!Object.hasOwn(plan.capacities,pool)||!Number.isFinite(units)||units<=0||units>plan.capacities[pool]+1e-8)throw Error('Fast resource demands must be finite positive known units within capacity.');
 }
 if(!preferences||typeof preferences!=='object'||Array.isArray(preferences))throw Error('Fast preferences must be an object.');
 for(const [id,p] of Object.entries(preferences)){
  // Old chart snapshots can contain preferences for removed groups. Preserve
  // the existing scheduler's behavior: those entries have no scheduling effect.
  if(!ids.has(id))continue;
  if(!p||typeof p!=='object'||Array.isArray(p))throw Error('Fast preferences require release/priority objects.');
  if(p.notBefore!==undefined&&(!Number.isFinite(p.notBefore)||p.notBefore<0)||p.priority!==undefined&&(!Number.isFinite(p.priority)||Math.abs(p.priority)>1000000))throw Error('Fast preference time/priority is invalid.');
 }
 return true;
}
export function scheduleTasksFast(plan,preferences={}){
 validateFastInputs(plan,preferences);
 const completed=new Map(),followers=new Map(),remaining=new Map(),buckets=new Map(),active={},out=[];
 let clock=0,queued=0;
 const ready=(task)=>{
  const key=JSON.stringify(Object.entries(task.resources).sort(([a],[b])=>a.localeCompare(b)));
  let bucket=buckets.get(key);if(!bucket){bucket={resources:task.resources,future:new Heap((a,b)=>a.lower-b.lower||comparePriority(a,b)),eligible:new Heap(comparePriority)};buckets.set(key,bucket);}
  const lower=task.deps.reduce((at,id)=>Math.max(at,completed.get(id).end),Math.max(task.notBefore||0,preferences[task.id]?.notBefore||0));
  bucket.future.push({task,lower,priority:preferences[task.id]?.priority??task.priority,queued:queued++});
 };
 for(const task of plan.tasks){remaining.set(task.id,task.deps.length);for(const id of task.deps)(followers.get(id)||followers.set(id,[]).get(id)).push(task);}
 for(const task of plan.tasks)if(!task.deps.length)ready(task);
 function available(resources,lower){
  let at=Math.max(clock,lower);
  for(let guard=0;;guard++){
   if(guard>plan.tasks.length*3+20)throw Error('Fast resource allocation failed.');
   let next=at;
   for(const [pool,units] of Object.entries(resources)){
    const occupied=(active[pool]||[]).filter(a=>a.end>at+EPS);
    if(occupied.reduce((sum,a)=>sum+a.units,0)+units>plan.capacities[pool]+EPS)next=Math.max(next,Math.min(...occupied.map(a=>a.end)));
   }
   if(!Number.isFinite(next))throw Error('Fast resource time became nonfinite.');if(next<=at+EPS)return at;at=next;
  }
 }
 while(out.length<plan.tasks.length){
  let choice=null,chosenBucket=null;
  for(const bucket of buckets.values()){
   if(!bucket.future.size&&!bucket.eligible.size)continue;
   const resourceAt=available(bucket.resources,clock);
   while(bucket.future.size&&bucket.future.peek().lower<=resourceAt)bucket.eligible.push(bucket.future.pop());
   const fromEligible=Boolean(bucket.eligible.size),candidate=fromEligible?bucket.eligible.peek():bucket.future.peek();
   const at=fromEligible?resourceAt:Math.max(resourceAt,candidate.lower);
   if(!choice||at<choice.start||at===choice.start&&comparePriority(candidate,choice.item)<0){choice={item:candidate,start:at,fromEligible};chosenBucket=bucket;}
  }
  if(!choice)throw Error('Cycle in task dependencies.');
  (choice.fromEligible?chosenBucket.eligible:chosenBucket.future).pop();const {task}=choice.item,start=choice.start;
  if(start<clock-EPS)throw Error('Fast scheduler violated monotonic start invariant.');clock=start;
  for(const pool of Object.keys(active))active[pool]=active[pool].filter(a=>a.end>clock+EPS);
  const item={...task,start,end:start+task.duration,manual:Boolean(preferences[task.id])};if(!Number.isFinite(item.end))throw Error('Fast task time overflow.');out.push(item);completed.set(item.id,item);
  for(const [pool,units] of Object.entries(task.resources))(active[pool]||=[]).push({end:item.end,units});
  for(const next of followers.get(item.id)||[]){const left=remaining.get(next.id)-1;remaining.set(next.id,left);if(!left)ready(next);}
 }
 out.sort((a,b)=>a.start-b.start||a.priority-b.priority);
 return out;
}
