import {test} from 'node:test';
import assert from 'node:assert/strict';
import {downloadLabel,setupAudioModels} from './audio-model-picker.mjs';

test('audition download labels describe the actual export rather than assuming AAC',()=>{
  assert.equal(downloadLabel({exportPath:'voice.opus',exportCodec:'Opus',exportBitrate:128000}),'Download Opus · 128 kbps');
  assert.equal(downloadLabel({exportPath:'voice.wav',exportCodec:'WAV',exportBitrate:null}),'Download WAV');
  assert.equal(downloadLabel({path:'voice.wav'}),'Download WAV');
  assert.equal(downloadLabel({exportPath:'old.m4a'}),'Download AAC');
});

for(const terminal of ['COMPLETE','INTERRUPTED'])test(`remote audition honors format and recovers a ${terminal} job`,async()=>{
  const original={};
  const globals=['Option','location','localStorage','sessionStorage','fetch'];
  for(const key of globals)original[key]=Object.getOwnPropertyDescriptor(globalThis,key);
  const fields=new Map(),statuses=[],requests=[];
  const element=()=>({value:'',disabled:false,hidden:false,children:[],
    replaceChildren(...children){this.children=children;this.value=children[0]?.value||'';},
    append(...children){this.children.push(...children);},prepend(child){this.children.unshift(child);},querySelectorAll(){return [];}});
  const doc={getElementById(id){if(!fields.has(id))fields.set(id,element());return fields.get(id);},createElement:element};
  const $=id=>doc.getElementById(id);
  $('studioVoice').value='am_michael';$('text').value='The gate opened.';
  $('format').value='opus';$('bitrate').value='128';$('rate').value='1';
  const storage=()=>{const saved=new Map();return {getItem:key=>saved.get(key)||null,setItem:(key,value)=>saved.set(key,value),removeItem:key=>saved.delete(key)};};
  try {
    globalThis.Option=class {constructor(text,value){this.text=text;this.value=value;}};
    globalThis.location={hash:'',pathname:'/'};
    globalThis.localStorage=storage();globalThis.sessionStorage=storage();
    localStorage.setItem('qt-local-helper-key','a'.repeat(64));
    let submitted=false;
    globalThis.fetch=async(url,options)=>{
      const path=new URL(url).pathname,body=options.body?JSON.parse(options.body):null;
      requests.push({path,body});
      const job={id:'audition',project:'project',kind:'voice-preview',status:terminal,message:terminal==='INTERRUPTED'?'Helper restarted.':''};
      const value=path==='/studio/projects'?[{id:'project',name:'Test'}]
        :path==='/studio/health'?{kaggleVoice:{configured:true}}
        :path==='/studio/jobs'?(submitted=true,{jobs:[job]})
        :path==='/studio/queue'?{jobs:submitted?[job]:[]}
        :path==='/studio/project'?{voicePreviews:[{text:'The gate opened.',model:'qwen3-tts-1.7b',voice:'Ryan',duration:2,path:'master.wav',exportPath:'audition.opus',exportCodec:'Opus',exportBitrate:128000}]}
        :path==='/studio/media-link'?{url:'https://example.invalid/audition.opus'}:null;
      assert.notEqual(value,null,`unexpected ${path}`);
      return {ok:true,json:async()=>value};
    };
    const picker=setupAudioModels({document:doc,status:text=>statuses.push(text),onChange:()=>{},isBusy:()=>false});
    $('audioVoiceModel').value='qwen3-tts-1.7b';$('audioVoiceModel').onchange();
    await picker.generate();
    const request=requests.find(r=>r.path==='/studio/jobs');
    assert.equal(request.body.options.audioFormat,'opus');
    assert.equal(request.body.options.audioBitrate,128);
    assert.equal(picker.busy(),false);
    assert.equal(sessionStorage.getItem('qt-audio-voice-job'),null);
    assert.equal($('format').disabled,false);
    if(terminal==='COMPLETE') {
      assert.match(statuses.at(-1),/Audition saved/);
      assert.equal($('audioModelResults').children[0].children.at(-1).textContent,'Download Opus · 128 kbps');
    } else assert.match(statuses.at(-1),/Helper restarted/);
  } finally {
    for(const key of globals)if(original[key])Object.defineProperty(globalThis,key,original[key]);else delete globalThis[key];
  }
});
