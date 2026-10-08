import test from 'node:test';
import assert from 'node:assert/strict';
import {engagementForm,engagementValues,filesPanel,wireFiles,cachedMediaLink,directCloudDownload} from './studio-cloud-ui.mjs';
test('cloud download navigates directly to signed attachment without fetching video bytes',async()=>{
  const calls=[],link={click(){calls.push('download');}},document={createElement(tag){assert.equal(tag,'a');return link;}};
  const api=async(operation,body)=>{calls.push({operation,body});return {provider:'Google Cloud Storage',url:'https://storage.googleapis.com/private/signed'};};
  assert.equal(await directCloudDownload('project','cloud-only.mp4','Part 1.mp4',api,document),true);
  assert.deepEqual(calls,[{operation:'media-link',body:{project:'project',path:'cloud-only.mp4',download:'Part 1.mp4'}},'download']);
  assert.equal(link.referrerPolicy,'no-referrer');assert.equal(link.download,'Part 1.mp4');
  assert.equal(await directCloudDownload('project','local.wav','Audio.wav',async()=>({url:'http://127.0.0.1:8765/studio/media?ticket=local'}),document),false);
  await assert.rejects(()=>directCloudDownload('project','video','Video',async()=>({provider:'Google Cloud Storage',url:'http://unsafe.example/video'}),document),/HTTPS/);
});
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
test('destination and chapter grouping controls retain independent export settings',()=>{
  const original=globalThis.document;
  const inputs={engDestination:{value:'patreon'},engPatreonOutro:{checked:false},
    engPatreonPopup:{checked:false},engSplitMode:{value:'chapters'},engSplitChapters:{value:'3'}};
  // Existing controls still serialize through the same form.
  const values={engMin:'10',engMax:'15',engDuration:'5',engPosition:'bottom-right',
    engText:'Subscribe',engVolume:'12',engOutroText:'Visit Patreon',engTeaser:'',
    engOutroDuration:'15',engPartMinutes:'120'};
  for(const [id,value] of Object.entries(values))inputs[id]={value};
  for(const id of ['engPopup','engDing','engOutro','engSplit'])inputs[id]={checked:true};
  globalThis.document={getElementById:id=>inputs[id]};
  try{
    const s=engagementValues({outroAudioPath:'master.wav'});
    assert.equal(s.exportDestination,'patreon');assert.equal(s.splitMode,'chapters');
    assert.equal(s.chaptersPerPart,3);assert.equal(s.outroEnabled,true);
    assert.equal(s.patreonOutroEnabled,false);assert.equal(s.patreonPopupEnabled,false);
    const html=engagementForm({...project,settings:{engagement:s}});
    assert.ok(html.includes('value="patreon" selected'));assert.ok(html.includes('Chapter boundaries'));
    assert.ok(filesPanel(project).includes('id="fileSplitChapters"'));
  }finally{globalThis.document=original;}
});

// Minimal mounted-element harness for asynchronous dashboard integration.
// Detached controls do not replace live IDs until inserted, like browser DOM.
function filesDOM(){
  const nodes=new Map();
  class Element{
    constructor(id='',value=''){this.id=id;this.value=value;this.children=[];this.textContent='';this.checked=false;this.disabled=false;this.html='';}
    get isConnected(){return nodes.get(this.id)===this;}
    mount(){if(this.id)nodes.set(this.id,this);this.children.forEach(e=>e.mount());}
    remove(){if(this.isConnected)nodes.delete(this.id);this.children.forEach(e=>e.remove());}
    set innerHTML(html){
      const live=this.isConnected;this.children.forEach(e=>e.remove());this.html=html;this.children=[];
      for(const match of html.matchAll(/<(\w+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){
        const [,tag,attrs,id]=match;let value=/\bvalue="([^"]*)"/.exec(attrs)?.[1]??'';
        if(tag==='select')value=/<option[^>]*value="([^"]+)"/.exec(html.slice(match.index))?.[1]??'';
        const child=new Element(id,value);child.disabled=/\bdisabled\b/.test(attrs);this.children.push(child);if(live)child.mount();
      }
    }
    get innerHTML(){return this.html;}
    after(child){child.mount();}
    insertAdjacentHTML(_,html){this.html+=html;}
    querySelectorAll(){return [];}
  }
  const reset=()=>{
    nodes.clear();
    for(const id of ['cloudStatus','cloudFiles','cloudSearch','cloudCategory','cloudRefresh','cloudSync','cloudSyncAll','cloudSplit','splitMinutes','makeThumbnail','youtubeStatus'])new Element(id).mount();
  };
  reset();
  return {nodes,reset,document:{getElementById:id=>nodes.get(id)??null,querySelectorAll:()=>[],createElement:()=>new Element()}};
}
function dashboardAPI({files=[],publisher={configured:false,connected:false}}={}){
  return async path=>path.startsWith('storage?')?{enabled:true,projects:{}}:path.startsWith('files?')?{files}:publisher;
}
const options=(p,api)=>({p,api,action:fn=>fn(),note:()=>{},media:async()=>'',submit:async()=>{},reload:async()=>{}});
const video=path=>({path,category:'Video',cloud:true,bytes:100});

test('preview cache respects original reused-ticket expiry and isolates project paths',async()=>{
  const cache=new Map();let now=3000000,calls=0;
  const api=async (_,body)=>{calls++;return {url:body.project+'/ticket'+calls,expires:3600};};
  assert.equal(await cachedMediaLink(cache,'one','video.mp4',api,()=>now),'one/ticket1');
  now=3500000;assert.equal(await cachedMediaLink(cache,'one','video.mp4',api,()=>now),'one/ticket1');assert.equal(calls,1);
  assert.equal(await cachedMediaLink(cache,'two','video.mp4',api,()=>now),'two/ticket2');
  now=3599500;assert.equal(await cachedMediaLink(cache,'one','video.mp4',api,()=>now),'one/ticket3');
  assert.equal(cache.has('one/video.mp4'),false);
});

test('preview without a verified expiry is not cached beyond an unknown lifetime',async()=>{
  const cache=new Map();let calls=0;const api=async()=>({url:'ticket'+(++calls)});
  assert.equal(await cachedMediaLink(cache,'one','art.png',api),'ticket1');
  assert.equal(await cachedMediaLink(cache,'one','art.png',api),'ticket2');assert.equal(cache.size,0);
});

test('late responses from a previous project cannot overwrite the new dashboard',async()=>{
  const dom=filesDOM(),original=globalThis.document;globalThis.document=dom.document;
  try{
    let release;const pending=new Promise(resolve=>{release=resolve;});
    const run=wireFiles(options(project,async path=>path.startsWith('storage?')?pending:path.startsWith('files?')?{files:[video('old.mp4')]}:{configured:false}));
    dom.reset();dom.nodes.get('cloudStatus').textContent='New project';dom.nodes.get('cloudFiles').innerHTML='New files';
    release({enabled:true,projects:{}});await run;
    assert.equal(dom.nodes.get('cloudStatus').textContent,'New project');assert.equal(dom.nodes.get('cloudFiles').innerHTML,'New files');
  }finally{globalThis.document=original;}
});

test('refresh preserves entered publishing metadata and cancelled jobs have no resume controls',async()=>{
  const dom=filesDOM(),original=globalThis.document;globalThis.document=dom.document;
  try{
    const p={...project,id:'pr-1111111111111111',publishing:[{id:'cancelled',title:'Old upload',status:'CANCELLED',uploaded:25,total:100}]};
    await wireFiles(options(p,dashboardAPI({files:[video('part1.mp4'),video('part2.mp4')],publisher:{configured:true,connected:true}})));
    for(const [id,value] of Object.entries({publishSource:'part2.mp4',publishTitle:'My edited title',publishDescription:'My description',publishPrivacy:'unlisted'}))dom.nodes.get(id).value=value;
    dom.nodes.get('publishKids').checked=true;dom.nodes.get('publisherControls').oninput();
    await dom.nodes.get('cloudRefresh').onclick();
    assert.equal(dom.nodes.get('publishSource').value,'part2.mp4');assert.equal(dom.nodes.get('publishTitle').value,'My edited title');
    assert.equal(dom.nodes.get('publishDescription').value,'My description');assert.equal(dom.nodes.get('publishPrivacy').value,'unlisted');assert.equal(dom.nodes.get('publishKids').checked,true);
    assert.ok(!dom.nodes.get('publisherControls').innerHTML.includes('data-upload-resume="cancelled"'));
  }finally{globalThis.document=original;}
});

test('file search belongs to its dashboard and upload requires a cloud video',async()=>{
  const dom=filesDOM(),original=globalThis.document;globalThis.document=dom.document;
  try{
    await wireFiles(options(project,dashboardAPI({files:[video('first.mp4')]})));
    dom.nodes.get('cloudSearch').oninput({target:{value:'no-match'}});assert.ok(!dom.nodes.get('cloudFiles').innerHTML.includes('first.mp4'));
    dom.reset();await wireFiles(options({...project,id:'pr-2222222222222222'},dashboardAPI({files:[video('next.mp4')],publisher:{configured:true,connected:true}})));
    assert.ok(dom.nodes.get('cloudFiles').innerHTML.includes('next.mp4'));
    dom.reset();await wireFiles(options({...project,id:'pr-3333333333333333'},dashboardAPI({publisher:{configured:true,connected:true}})));
    assert.equal(dom.nodes.get('publishStart').disabled,true);
  }finally{globalThis.document=original;}
});

test('loss of publisher configuration removes stale upload controls and retains the draft on reconnect',async()=>{
  const dom=filesDOM(),original=globalThis.document;globalThis.document=dom.document;
  let configured=true;
  const api=async path=>path.startsWith('storage?')?{enabled:true,projects:{}}:
    path.startsWith('files?')?{files:[video('part1.mp4')]}:{configured,connected:configured};
  try{
    await wireFiles(options({...project,id:'pr-4444444444444444'},api));
    dom.nodes.get('publishTitle').value='Keep my title';
    dom.nodes.get('publishDescription').value='Keep my description';
    configured=false;await dom.nodes.get('cloudRefresh').onclick();
    assert.equal(dom.nodes.has('publisherControls'),false);
    assert.equal(dom.nodes.has('publishStart'),false);
    configured=true;await dom.nodes.get('cloudRefresh').onclick();
    assert.equal(dom.nodes.get('publishTitle').value,'Keep my title');
    assert.equal(dom.nodes.get('publishDescription').value,'Keep my description');
    assert.equal(dom.nodes.get('publishStart').disabled,false);
  }finally{globalThis.document=original;}
});

test('a slower earlier refresh cannot replace a newer file list in the same project',async()=>{
  const dom=filesDOM(),original=globalThis.document;globalThis.document=dom.document;
  try{
    let cycle=0,release;const slow=new Promise(resolve=>{release=resolve;});
    const api=async path=>{
      if(path.startsWith('storage?')){cycle++;return cycle===2?slow:{enabled:true,projects:{}};}
      if(path.startsWith('files?'))return {files:[video(cycle===2?'old.mp4':'new.mp4')]};
      return {configured:false};
    };
    await wireFiles(options(project,api));const older=dom.nodes.get('cloudRefresh').onclick();await dom.nodes.get('cloudRefresh').onclick();
    release({enabled:true,projects:{}});await older;
    assert.ok(dom.nodes.get('cloudFiles').innerHTML.includes('new.mp4'));assert.ok(!dom.nodes.get('cloudFiles').innerHTML.includes('old.mp4'));
  }finally{globalThis.document=original;}
});

test('leaving the dashboard stops chunk dispatch after the in-flight request and preserves resumable progress',async()=>{
  const dom=filesDOM(),original=globalThis.document,previousConfirm=globalThis.confirm;
  globalThis.document=dom.document;globalThis.confirm=()=>true;
  try{
    let release,started,nextCalls=0,reloads=0;
    const chunk=new Promise(resolve=>{release=resolve;}),dispatch=new Promise(resolve=>{started=resolve;});
    const base=dashboardAPI({files:[video('story.mp4')],publisher:{configured:true,connected:true}});
    const api=async(path,body)=>{
      if(path!=='publish')return base(path);
      if(body.operation==='start')return {id:'nav-test',title:'Story',uploaded:0,total:100,status:'UPLOADING'};
      if(body.operation==='next'){nextCalls++;started();return chunk;}
      throw Error('Unexpected operation');
    };
    await wireFiles({...options({...project,id:'pr-4444444444444444'},api),reload:async()=>{reloads++;}});
    const upload=dom.nodes.get('publishStart').onclick();await dispatch;
    dom.reset();dom.nodes.get('cloudStatus').textContent='Another tab';
    release({id:'nav-test',title:'Story',uploaded:25,total:100,status:'UPLOADING'});await upload;
    assert.equal(nextCalls,1);assert.equal(reloads,0);assert.equal(dom.nodes.get('cloudStatus').textContent,'Another tab');
  }finally{globalThis.document=original;globalThis.confirm=previousConfirm;}
});

test('Google archive requires a matching Google publisher before enabling upload',async()=>{
  const dom=filesDOM(),original=globalThis.document;globalThis.document=dom.document;
  try{
    const base=dashboardAPI({files:[video('story.mp4')],publisher:{configured:true,connected:true,storageProvider:'r2'}});
    const api=async path=>path.startsWith('storage?')?{enabled:true,provider:'Google Cloud Storage',projects:{}}:base(path);
    await wireFiles(options({...project,id:'pr-5555555555555555'},api));
    assert.equal(dom.nodes.get('publishStart').disabled,true);assert.match(dom.nodes.get('youtubeStatus').textContent,/does not match/);
  }finally{globalThis.document=original;}
});

test('offline file load ends the loading state and disables mutations; refresh recovers',async()=>{
  const dom=filesDOM(),original=globalThis.document;globalThis.document=dom.document;
  try{
    let offline=true;
    const api=async path=>{if(offline)throw Error('offline');return dashboardAPI({files:[video('saved.mp4')]})(path);};
    await wireFiles(options(project,api));
    assert.match(dom.nodes.get('cloudStatus').textContent,/could not be loaded/);
    assert.ok(!dom.nodes.get('cloudStatus').textContent.includes('Checking'));
    for(const id of ['cloudSync','cloudSyncAll','cloudSplit','makeThumbnail'])assert.equal(dom.nodes.get(id).disabled,true);
    offline=false;await dom.nodes.get('cloudRefresh').onclick();
    assert.ok(dom.nodes.get('cloudFiles').innerHTML.includes('saved.mp4'));
    assert.equal(dom.nodes.get('cloudSync').disabled,false);
    assert.equal(dom.nodes.get('makeThumbnail').disabled,false);
  }finally{globalThis.document=original;}
});

test('a failed earlier refresh cannot replace a newer successful file list',async()=>{
  const dom=filesDOM(),original=globalThis.document;globalThis.document=dom.document;
  try{
    let cycle=0,reject;const slow=new Promise((_,fail)=>{reject=fail;});
    const base=dashboardAPI({files:[video('current.mp4')]});
    const api=async path=>{if(path.startsWith('storage?')){cycle++;if(cycle===2)return slow;}return base(path);};
    await wireFiles(options(project,api));const older=dom.nodes.get('cloudRefresh').onclick();
    await dom.nodes.get('cloudRefresh').onclick();reject(Error('old failure'));await older;
    assert.ok(dom.nodes.get('cloudFiles').innerHTML.includes('current.mp4'));
    assert.ok(!dom.nodes.get('cloudStatus').textContent.includes('could not be loaded'));
    assert.equal(dom.nodes.get('cloudSync').disabled,false);
  }finally{globalThis.document=original;}
});
