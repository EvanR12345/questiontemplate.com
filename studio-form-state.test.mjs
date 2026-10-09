import test from 'node:test';
import assert from 'node:assert/strict';
import {captureFormFocus,restoreFormFocus,openFormDialog} from './studio-form-state.mjs';
test('same-view redraw preserves textarea caret and scroll but never steals focus after navigation',()=>{
  const original={id:'story',type:'textarea',tagName:'TEXTAREA',selectionStart:25,selectionEnd:30,selectionDirection:'backward',scrollTop:100,scrollLeft:0};
  const root={dataset:{focusContext:'a/chapter/write'},contains:c=>c===original,ownerDocument:{activeElement:original}};
  assert.equal(captureFormFocus(root,'b/chapter/write'),null);
  const saved=captureFormFocus(root,'a/chapter/write'),calls=[];
  const next={id:'story',type:'textarea',tagName:'TEXTAREA',focus:v=>calls.push(v),setSelectionRange:(...v)=>calls.push(v)};
  root.ownerDocument.getElementById=()=>next;root.contains=c=>c===next;
  restoreFormFocus(root,saved);assert.deepEqual(calls,[{preventScroll:true},[25,30,'backward']]);assert.equal(next.scrollTop,100);
  next.type='password';restoreFormFocus(root,saved);assert.equal(calls.length,2);
});
test('focus restoration ignores an unmounted control or focus outside the workspace',()=>{
  const root={dataset:{focusContext:'same'},contains:()=>false,ownerDocument:{activeElement:{id:'outside'},getElementById:()=>undefined}};
  assert.equal(captureFormFocus(root,'same'),null);assert.doesNotThrow(()=>restoreFormFocus(root,{id:'missing'}));
});
const deferred=()=>{let resolve;return {promise:new Promise(y=>resolve=y),get resolve(){return resolve;}};};
function dialogFixture(onSave,stillCurrent){
  const previous={isConnected:true,focus:()=>previous.restored=true},controls=new Map();
  for(const key of ['.dialog-save','.dialog-close','.dialog-error','input:not([type="hidden"]),textarea,select,.dialog-save'])controls.set(key,{disabled:false,hidden:true,focus(){this.focused=true;}});
  const listeners={};const modal={dataset:{},setAttribute(){},querySelector:s=>controls.get(s),addEventListener:(name,fn)=>listeners[name]=fn,showModal(){this.open=true;},close(){this.open=false;listeners.close();},remove(){this.removed=true;}};
  const document={activeElement:previous,createElement:tag=>{assert.equal(tag,'dialog');return modal;},body:{append:el=>assert.equal(el,modal)}};
  openFormDialog({document,title:'Edit <story>',body:'<textarea>Saved</textarea>',escape:v=>v.replaceAll('<','&lt;'),onSave,stillCurrent});
  return {modal,controls,listeners,previous,save:()=>controls.get('.dialog-save').onclick()};
}
test('a modal survives outside workspace redraws and saves only once while pending',async()=>{
  const pending=deferred();let calls=0;const f=dialogFixture(()=>{calls++;return pending.promise;});
  const first=f.save(),second=f.save();assert.equal(calls,1);assert.equal(f.controls.get('.dialog-close').disabled,true);
  let cancelled=false;f.listeners.cancel({preventDefault:()=>cancelled=true});assert.equal(cancelled,true);
  pending.resolve();await Promise.all([first,second]);assert.equal(f.modal.removed,true);assert.equal(f.previous.restored,true);
});
test('failed modal save retains entered fields and displays an accessible error',async()=>{
  const f=dialogFixture(async()=>{throw Error('Connection failed');});await f.save();
  assert.equal(f.modal.open,true);assert.equal(f.controls.get('.dialog-error').textContent,'Connection failed');
  assert.equal(f.controls.get('.dialog-error').hidden,false);assert.equal(f.controls.get('.dialog-save').disabled,false);
  assert.match(f.modal.innerHTML,/role="alert"/);assert.match(f.modal.innerHTML,/&lt;story>/);
});
test('a dialog cannot apply its fields to a different selected project',async()=>{
  let calls=0;const f=dialogFixture(async()=>calls++,()=>false);await f.save();assert.equal(calls,0);assert.equal(f.modal.open,true);
  assert.match(f.controls.get('.dialog-error').textContent,/selected project changed/);
});
