import test from 'node:test';
import assert from 'node:assert/strict';
import {engagementForm,engagementValues,filesPanel} from './studio-cloud-ui.mjs';
const project={id:'pr-0123456789abcdef',name:'Story <script>',settings:{engagement:{}},chapters:[{scenes:[{shots:[{id:'shot-1',imagePath:'art.png',action:'A < b'}]}]}],render:{parts:[{number:1,path:'part1.mp4',start:0,end:7200}]}};
test('finishing form exposes every value that is persisted',()=>{
  const html=engagementForm(project),inputs={};
  const values={engPopup:true,engMin:'10',engMax:'15',engDuration:'5',engPosition:'bottom-left',engText:'Subscribe',engDing:true,engVolume:'12',engOutro:true,engOutroText:'Thanks for watching',engTeaser:'Next episode',engOutroDuration:'15',engSplit:true,engPartMinutes:'120'};
  for(const [id,value] of Object.entries(values)){assert.ok(html.includes(`id="${id}"`));inputs[id]={value,checked:value};}
  const original=globalThis.document;globalThis.document={getElementById:id=>inputs[id]};
  try{const result=engagementValues({outroAudioPath:'outro.wav',outroAudioSignature:'signature'});assert.equal(result.dingVolume,.12);assert.equal(result.partMinutes,120);assert.equal(result.outroAudioPath,'outro.wav');assert.equal(result.position,'bottom-left');}finally{globalThis.document=original;}
});
test('file dashboard escapes story metadata and shows parts without modifying it',()=>{
  const original=JSON.stringify(project);const html=filesPanel(project);
  assert.ok(html.includes('Story &lt;script&gt;'));assert.ok(html.includes('A &lt; b'));assert.ok(html.includes('Part 1'));assert.ok(html.includes('120.00 min'));assert.ok(!html.includes('Story <script>'));assert.equal(JSON.stringify(project),original);
});
