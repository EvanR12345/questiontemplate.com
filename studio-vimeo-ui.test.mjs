import test from 'node:test';
import assert from 'node:assert/strict';
import {vimeoForm,vimeoJobLabel,wireVimeo} from './studio-vimeo-ui.mjs';

function fixture(){
  const nodes=new Map();
  const element=()=>({value:'',textContent:'',children:[],disabled:false,
    replaceChildren(...children){this.children=children;if(children.length&&'value' in children[0])this.value=children[0].value;},
    append(child){this.children.push(child);}});
  for(const [,id] of vimeoForm().matchAll(/id="([^"]+)"/g))nodes.set(id,element());
  nodes.get('vimeoStart').disabled=true;
  return {nodes,document:{getElementById:id=>nodes.get(id),createElement:element}};
}
const video={path:'cloud/story.mp4',cloud:true,category:'Video'};
const params=(dom,api,id='pr-test')=>({p:{id,name:'A cloud story'},files:[video],api,
  action:fn=>fn(),active:()=>true,note:()=>{},document:dom.document});

test('cloud publishing begins disabled and never implies automatic Patreon posting',()=>{
  assert.match(vimeoForm(),/id="vimeoStart" disabled/);
  assert.match(vimeoForm(),/type="password"/);
  assert.match(vimeoForm(),/public API does not provide general post creation/);
  assert.match(vimeoJobLabel({status:'SUBMISSION_UNKNOWN'}),/will not resubmit automatically/);
});

test('uncertain receipt prevents another upload; losing connection disables it too',async()=>{
  const original=globalThis.Option;globalThis.Option=class{constructor(text,value){this.text=text;this.value=value;}};
  try{
    const dom=fixture();let configured=true,jobs=[{status:'SUBMISSION_UNKNOWN'}];
    await wireVimeo(params(dom,async()=>({configured,jobs})));
    assert.equal(dom.nodes.get('vimeoStart').disabled,true);
    jobs=[];await dom.nodes.get('vimeoRefresh').onclick();
    assert.equal(dom.nodes.get('vimeoStart').disabled,false);
    configured=false;await dom.nodes.get('vimeoRefresh').onclick();
    assert.equal(dom.nodes.get('vimeoStart').disabled,true);
  }finally{globalThis.Option=original;}
});

test('late replies cannot update a replacement project panel',async()=>{
  const original=globalThis.Option;globalThis.Option=class{constructor(text,value){this.value=value;}};
  try{
    const dom=fixture();let resolve;
    const work=wireVimeo(params(dom,()=>new Promise(done=>{resolve=done;})));
    const replacement=fixture();for(const [id,node] of replacement.nodes)dom.nodes.set(id,node);
    dom.nodes.get('vimeoStatus').textContent='New project';
    resolve({configured:true,jobs:[]});await work;
    assert.equal(dom.nodes.get('vimeoStatus').textContent,'New project');
    assert.equal(dom.nodes.get('vimeoStart').disabled,true);
  }finally{globalThis.Option=original;}
});

test('saving a token sends only private configuration and clears its field',async()=>{
  const original=globalThis.Option;globalThis.Option=class{constructor(text,value){this.value=value;}};
  try{
    const dom=fixture(),calls=[];
    await wireVimeo(params(dom,async(path,body)=>{calls.push({path,body});return {configured:!!body};},'pr-token-test'));
    dom.nodes.get('vimeoToken').value='test-private-token';
    await dom.nodes.get('vimeoSaveToken').onclick();
    assert.deepEqual(calls.find(call=>call.path==='config').body,{vimeoApiToken:'test-private-token'});
    assert.equal(dom.nodes.get('vimeoToken').value,'');
    assert.equal(calls.filter(call=>call.body?.operation==='start').length,0);
  }finally{globalThis.Option=original;}
});
