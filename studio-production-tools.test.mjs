import test from 'node:test';
import assert from 'node:assert/strict';
import {searchProject,reportHTML,createProductionTools} from './studio-production-tools.mjs';
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
test('shared options are initialized in module scope for project tools and production controls',()=>{
  const source=readFileSync(new URL('./story-studio.mjs',import.meta.url),'utf8');
  assert.match(source,/^function productionOptions\(validate=true\)/m);
  assert.match(source,/^async function saveStrategy\(/m);
  assert.match(source,/validate\?strategyOptions/);
});
