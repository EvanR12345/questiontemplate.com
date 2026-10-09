import test from 'node:test';
import assert from 'node:assert/strict';
import {createFormDrafts,createProjectSelection} from './studio-form-drafts.mjs';
const input=(id,value,type='text')=>({id,value,type,tagName:'INPUT'});
function root(...controls){return {contains:c=>controls.includes(c),ownerDocument:{getElementById:id=>controls.find(c=>c.id===id)}};}
test('settings drafts restore only the matching project and mounted form',()=>{
  const drafts=createFormDrafts(),old=input('title','Manual intro');drafts.remember('a',old);
  const fresh=input('title','Server value');drafts.restore('b',root(fresh));assert.equal(fresh.value,'Server value');
  drafts.restore('a',root(fresh));assert.equal(fresh.value,'Manual intro');assert.equal(drafts.has('a'),true);
  const outside=input('title','Other dialog');drafts.restore('a',{contains:()=>false,ownerDocument:{getElementById:()=>outside}});assert.equal(outside.value,'Other dialog');
});
test('drafts never retain keys, files or hidden values',()=>{
  const drafts=createFormDrafts();for(const type of ['password','file','hidden'])assert.equal(drafts.remember('a',input('secret','private',type)),false);
  assert.equal(drafts.dirty,false);
});
test('checkboxes and available select choices survive reconnect',()=>{
  const drafts=createFormDrafts(),box={...input('enabled','', 'checkbox'),checked:true};drafts.remember('a',box);
  const fresh={...box,checked:false},select={id:'model',tagName:'SELECT',type:'select-one',value:'selected',options:[{value:'selected'}]};drafts.remember('a',select);
  select.value='other';drafts.restore('a',root(fresh,select));assert.equal(fresh.checked,true);assert.equal(select.value,'selected');
  select.options=[{value:'other'}];select.value='other';drafts.restore('a',root(select));assert.equal(select.value,'other');
});
test('successful save clears its snapshot but retains edits typed while saving',()=>{
  const drafts=createFormDrafts(),field=input('title','Saved title');drafts.remember('a',field);
  const saved=drafts.snapshot('a');field.value='Newer title';drafts.remember('a',field);drafts.acknowledge('a',saved);
  assert.equal(drafts.has('a'),true);drafts.acknowledge('a',drafts.snapshot('a'));assert.equal(drafts.dirty,false);
});
test('late project selection loses authority after a newer choice or manual edit',()=>{
  const select=createProjectSelection(),project={id:'a'},first=select.begin(project,2),second=select.begin(project,2);
  assert.equal(select.current(first,project,2),false);assert.equal(select.current(second,project,2),true);
  assert.equal(select.current(second,project,3),false);assert.equal(select.current(second,{id:'a'},2),false);
});
