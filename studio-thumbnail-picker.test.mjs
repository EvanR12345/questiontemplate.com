import test from 'node:test';
import assert from 'node:assert/strict';
import {thumbnailIndex,thumbnailPage,thumbnailPicker,wireThumbnailPicker} from './studio-thumbnail-picker.mjs';
const project={chapters:Array.from({length:80},(_,c)=>({id:'chapter'+c,name:'Chapter '+c,scenes:[{shots:Array.from({length:110},(_,s)=>({id:`shot-${c}-${s}`,action:s===109?'Sarah at the door':'A < B',imagePath:`${c}/${s}.png`}))}]}))};
test('80 chapters retain every image while mounting only 50 choices',()=>{
  const index=thumbnailIndex(project);assert.equal(index.length,8800);
  const html=thumbnailPicker(project);assert.equal((html.match(/<option/g)||[]).length,81+50);
  assert.ok(html.includes('A &lt; B'));assert.ok(!html.includes('A < B'));
  assert.equal(thumbnailPage(index,{page:175}).entries.length,50);
});
test('chapter and action filtering reach the last image without changing a chosen source',()=>{
  const index=thumbnailIndex(project),result=thumbnailPage(index,{chapter:'chapter79',term:'Sarah',selected:'0/0.png'});
  assert.equal(result.total,1);assert.equal(result.selected,'0/0.png');assert.equal(result.entries.length,2);
  assert.equal(result.entries[1].path,'79/109.png');
  assert.equal(thumbnailPage(index,{page:1000}).page,175);
});
test('empty projects and missing source choices do not select an invented image',()=>{
  const result=thumbnailPage([],{selected:'lost.png'});assert.equal(result.selected,'');assert.equal(result.pages,1);assert.equal(result.total,0);
});
test('picker binds pages and search without starting any generation',()=>{
  const nodes=new Map(['thumbSource','thumbChapter','thumbSearch','thumbPageStatus','thumbPrevious','thumbNext'].map(id=>[id,{value:id==='thumbSource'?'0/0.png':'',innerHTML:'',textContent:''}]));
  const document={getElementById:id=>nodes.get(id)};wireThumbnailPicker(project,document);
  nodes.get('thumbNext').onclick();assert.match(nodes.get('thumbPageStatus').textContent,/page 2/);assert.equal(nodes.get('thumbSource').value,'0/0.png');
  nodes.get('thumbSearch').value='Sarah';nodes.get('thumbSearch').oninput();assert.match(nodes.get('thumbPageStatus').textContent,/80 matching/);
  nodes.get('thumbChapter').value='chapter79';nodes.get('thumbChapter').onchange();assert.match(nodes.get('thumbPageStatus').textContent,/1 matching/);
  assert.ok(nodes.get('thumbSource').innerHTML.includes('79/109.png'));
});

test('selecting a filtered image removes the previously pinned selection on repaint',()=>{
  const nodes=new Map(['thumbSource','thumbChapter','thumbSearch','thumbPageStatus','thumbPrevious','thumbNext'].map(id=>[id,{value:id==='thumbSource'?'0/0.png':'',innerHTML:'',textContent:''}]));
  wireThumbnailPicker(project,{getElementById:id=>nodes.get(id)});
  nodes.get('thumbChapter').value='chapter79';nodes.get('thumbChapter').onchange();
  assert.ok(nodes.get('thumbSource').innerHTML.includes('Selected ·'));
  nodes.get('thumbSource').value='79/2.png';nodes.get('thumbSource').onchange();
  assert.ok(!nodes.get('thumbSource').innerHTML.includes('0/0.png'));assert.equal(nodes.get('thumbSource').value,'79/2.png');
});
