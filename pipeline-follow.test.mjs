import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {plannerSettings,profileSummary} from './studio-planner-profile.mjs';
import {plannerSourceState} from './pipeline-config.mjs';

function fixture(){
  const elements=new Map();
  const element=id=>{
    if(!elements.has(id))elements.set(id,{value:'',textContent:'',checked:true,
      replaceChildren(...options){this.value=options.find(o=>o.selected)?.value||options[0]?.value||'';}});
    return elements.get(id);
  };
  const profile={version:1,project:'a',name:'Fixture',revision:1,
    settings:{voice:'am_michael',qcCheckLevel:'practical',video:{width:1280,height:720,fps:30},
      image:{width:1280,height:720,model:'flux2-klein-4b',steps:4,guidance:1},director:{}},estimates:{}};
  const context=vm.createContext({
    DEFAULTS:{minutes:120},PRICING_DATE:'fixture',PRICING_URL:'fixture',VERSION:1,
    plannerSettings,profileSummary,plannerSourceState,
    document:{getElementById:element},location:{search:''},localStorage:{getItem:()=>null},
    URLSearchParams,structuredClone,setTimeout:()=>0,clearTimeout:()=>{},
    Option:function(name,value,defaultSelected,selected){Object.assign(this,{name,value,selected});},
    pairingKey:()=> 'fixture-only',createWorkerRequest:()=>({}),buildPlan:()=>({}),
    selectGPUConfig:config=>config,
    helperJson:async route=>route==='/studio/projects'?[{id:profile.project,name:'Fixture'}]:structuredClone(profile),
  });
  // Exercise the production loader and snapshot with an isolated DOM and transport.
  // No browser storage, live helper, production queue or paid API is reachable.
  const source=fs.readFileSync(new URL('./pipeline-ui.mjs',import.meta.url),'utf8')
    .replace(/^import .*;\r?$/gm,'').replaceAll('import.meta.url',JSON.stringify(import.meta.url))
    .replace(/\nstart\(\);\s*$/,'');
  vm.runInContext(source+`
    evidence={};
    rebuild=async()=>{renderedGeneration=computeGeneration;};
    setControls=()=>{};
    globalThis.fixtureAPI={load:()=>loadStudioSettings({refresh:true}),snapshot,
      selected:id=>{studioProject=id;document.getElementById('studioProject').value=id;},
      manual:studioManual};`,context);
  return {api:context.fixtureAPI,profile,element};
}

test('rejected Kaggle setup retains the scenario without exporting a false following claim',async()=>{
  const {api,profile,element}=fixture();
  await api.load();
  const before=api.snapshot();
  assert.equal(before.studioSource.following,true);
  profile.project='b';profile.settings.voiceCompute='kaggle';api.selected('b');
  await api.load();
  const rejected=api.snapshot();
  assert.equal(rejected.studioSource.following,false);
  assert.equal(rejected.studioSource.requestedFollowing,true);
  assert.equal(rejected.studioSource.revision,null);
  assert.deepEqual(rejected.config,before.config);
  assert.match(element('studioProfileStatus').textContent,/Saved setup not applied.*Kaggle/);
  // Recovery must reapply even when the saved revision equals the last valid one.
  profile.settings.voiceCompute='local';
  await api.load();
  assert.equal(api.snapshot().studioSource.following,true);
  assert.equal(api.snapshot().studioSource.project,'b');
  api.manual();
  assert.equal(api.snapshot().studioSource.following,false);
  assert.equal(api.snapshot().studioSource.requestedFollowing,false);
});
