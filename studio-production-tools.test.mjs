import test from 'node:test';
import assert from 'node:assert/strict';
import {searchProject,indexProjectSearch,searchProjectIndex,reportHTML,createProductionTools} from './studio-production-tools.mjs';
import {readFileSync} from 'node:fs';
const p={id:'p',characters:[{name:'Jonathan',description:'A magician'}],chapters:[{id:'c',name:'Return',sourceText:'He said, I cannot leave you.',cleanNarrationText:'I cannot leave you.',people:[{name:'Guard',role:'Watchman'}],scenes:[{id:'s',location:'Castle',purpose:'Return home',shots:[{id:'sh',action:'Raises a wand',prompt:'blue magic',narrationSegment:'He returned.'}]}]}]};
test('project search finds characters, dialogue, temporary people, scene composition, prompts and files',()=>{
  assert.equal(searchProject(p,'Jonathan')[0].kind,'Character');
  assert.deepEqual(searchProject(p,'cannot').map(r=>r.kind),['Chapter','Narration']);
  assert.equal(searchProject(p,'watchman')[0].kind,'Person');
  assert.equal(searchProject(p,'Castle')[0].tab,'review');
  assert.equal(searchProject(p,'blue magic')[0].shot,'sh');
  assert.equal(searchProject(p,'video',[{path:'full-video.mp4'}])[0].path,'full-video.mp4');
  assert.deepEqual(searchProject(p,'   '),[]);
});
test('search never indexes settings or credentials and report escapes arbitrary story text',()=>{
  assert.equal(searchProject({...p,settings:{secret:'private-key'}},'private-key').length,0);
  const html=reportHTML({status:'<img>',scope:'<b>',issues:[{label:'<script>'}],time:{},cost:{},counts:{}});
  assert.ok(!html.includes('<script>'));assert.match(html,/Unknown/);
  assert.match(reportHTML({}),/not certify an unchecked/);
});
test('toolbar exposes all four project-wide tools without starting requests',()=>{
  const tools=createProductionTools({api:()=>{throw Error('unexpected request');}});
  for(const name of ['health','search','history','report'])assert.match(tools.toolbar(),new RegExp('data-project-tool="'+name+'"'));
});
test('one-modal index preserves matches and snippets without rereading project data',()=>{
  const fixture=structuredClone(p),files=[{path:'private-magic.png',storageStatus:'MISSING'}];
  const index=indexProjectSearch(fixture,files);
  for(const query of [' Jonathan ','CASTLE','cannot','blue magic','private-magic','absent',''])
    assert.deepEqual(searchProjectIndex(index,query),searchProject(fixture,query,files));
  fixture.chapters[0].sourceText='Changed after opening';
  assert.equal(searchProjectIndex(index,'cannot').filter(r=>r.kind==='Chapter').length,1);
  assert.equal(searchProject(fixture,'cannot').filter(r=>r.kind==='Chapter').length,0);
  for(const result of searchProjectIndex(index,'magic')){
    assert.ok(!('source' in result));assert.ok(!('folded' in result));
  }
});
test('search pagination reuses results and inventory errors remain visible while typing',async()=>{
  const fixture=structuredClone(p);let reads=0;
  Object.defineProperty(fixture.chapters[0],'sourceText',{get(){reads++;return 'A quiet opening.';}});
  fixture.chapters[0].scenes[0].shots=Array.from({length:65},(_,i)=>({id:'shot-'+i,prompt:'blue magic'}));
  const nodes=new Map(),node=id=>nodes.get(id)||nodes.set(id,{innerHTML:'',textContent:'',value:'',focus(){}}).get(id);
  const modal={isConnected:true,querySelector:node,querySelectorAll:()=>[]};
  const button={dataset:{projectTool:'search'}};
  const tools=createProductionTools({get:()=>({project:fixture,connected:true,root:{querySelectorAll:()=>[button]}}),
    formDialog:()=>modal,action:fn=>fn(),api:async()=>{throw Error('inventory unavailable');}});
  tools.wire();button.onclick();
  await Promise.resolve();await Promise.resolve();
  node('#wsSearchAll').value='blue magic';node('#wsSearchAll').oninput();
  assert.match(node('#wsSearchStatus').textContent,/65 matches/);
  assert.match(node('#wsSearchStatus').textContent,/file inventory failed/);
  assert.match(node('#wsSearchResults').innerHTML,/shot-0</);
  node('#wsSearchNext').onclick();
  assert.match(node('#wsSearchResults').innerHTML,/shot-30</);
  assert.doesNotMatch(node('#wsSearchResults').innerHTML,/shot-0</);
  assert.equal(reads,1,'querying and changing pages must not re-read chapter text');
  modal.isConnected=false;node('#wsSearchAll').oninput();assert.equal(reads,1);
});
test('shared options are initialized in module scope for project tools and production controls',()=>{
  const source=readFileSync(new URL('./story-studio.mjs',import.meta.url),'utf8');
  assert.match(source,/^function productionOptions\(validate=true\)/m);
  assert.match(source,/^async function saveStrategy\(/m);
  assert.match(source,/validate\?strategyOptions/);
});
