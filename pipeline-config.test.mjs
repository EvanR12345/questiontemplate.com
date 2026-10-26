import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DEFAULTS,buildPlan,schedule} from './pipeline-engine.mjs';
import {gpuChoices,selectGPUConfig,executionLabel,generationSpeed,productionModeNote,plannerSourceState} from './pipeline-config.mjs';
const evidence=JSON.parse(fs.readFileSync(new URL('./pipeline-evidence.json',import.meta.url)));
evidence.concurrency=JSON.parse(fs.readFileSync(new URL('./pipeline-concurrency.json',import.meta.url)));
const config={...DEFAULTS,resolution:'720p',encodingProfile:'fresh',executionMode:'hybrid',measurementAttempt:'5090-attempt-8'};

test('exported source requires a successfully applied selected profile and preserves retry intent',()=>{
  assert.deepEqual(plannerSourceState('kaggle-project',true,null),
    {project:'kaggle-project',following:false,requestedFollowing:true,revision:null});
  const applied={project:'local-project',revision:12};
  assert.equal(plannerSourceState('kaggle-project',true,applied).following,false);
  assert.deepEqual(plannerSourceState('local-project',true,applied),
    {project:'local-project',following:true,requestedFollowing:true,revision:12});
  assert.equal(plannerSourceState('local-project',false,applied).following,false);
});

test('installed scheduling explanation follows model copies and actual chapter-render dependencies',()=>{
  for(const enabled of [true,false]){
    const p=buildPlan({...config,policy:'current',imageWorkers:'4',productionModelCopies:2,
      productionRequestSlots:4,productionRenderOverlap:enabled,productionAudioLookahead:enabled},evidence);
    const note=productionModeNote(p.config);
    assert.match(note,/Two independent model copies.*one GPU.*four bounded request slots/);
    const chapter=p.tasks.find(t=>t.id==='c1-render');
    const laterImages=p.tasks.filter(t=>t.chapter>1&&['save','delivered'].includes(t.kind)).map(t=>t.id);
    assert.ok(laterImages.length>0);
    if(enabled){
      assert.match(note,/while later chapters continue/);
      assert.ok(laterImages.every(id=>!chapter.deps.includes(id)));
    }else{
      assert.match(note,/rendering waits for all image work/);
      assert.ok(laterImages.some(id=>chapter.deps.includes(id)));
    }
  }
  assert.match(productionModeNote({...config,policy:'current',productionModelCopies:1}),/One model copy/);
  assert.match(productionModeNote({...config,policy:'proposed'}),/Planning experiment/);
});

test('switching from 5090 hybrid to A6000 selects measured resident workers',()=>{
  const chosen=selectGPUConfig(config,evidence,'a6000'),p=buildPlan(chosen,evidence);
  assert.equal(chosen.executionMode,'resident');
  assert.equal(chosen.measurementAttempt,'latest');
  assert.equal(p.measurement.workers,3);
  assert.deepEqual(schedule(p).diagnostics,[]);
});
test('4090 switches to its measured pipeline without changing resolution or checks',()=>{
  const chosen=selectGPUConfig({...config,qc:'sampled',retries:7},evidence,'4090'),p=buildPlan(chosen,evidence);
  assert.equal(chosen.executionMode,'pipeline');assert.equal(p.measurement.clientSlots,2);
  for(const key of ['resolution','encodingProfile','minutes','cadence','intro','directorCost'])assert.equal(chosen[key],config[key]);
  assert.equal(chosen.qc,'sampled');assert.equal(chosen.retries,7);
});
test('every completed fresh 720p GPU can be selected from the hybrid default',()=>{
  let checked=0;
  for(const g of gpuChoices(evidence)) {
    const m=evidence.concurrency.gpus.find(x=>x.id===g.id);
    if(!['productionProfiles','pipelineProfiles','hybridProfiles'].some(k=>m?.[k]?.some(p=>p.resolution==='720p'&&p.encodingProfile==='fresh'&&p.rounds>=2&&!p.postControl)))continue;
    const p=buildPlan(selectGPUConfig(config,evidence,g.id),evidence);
    assert.ok(p.measurement.rounds>=2);assert.equal(p.measurement.resolution,'720p');
    checked++;
  }
  assert.ok(checked>=18);
});
test('A5000 cannot silently substitute legacy images for missing 720p evidence',()=>{
  assert.throws(()=>selectGPUConfig(config,evidence,'a5000'),/No completed 720p/);
  assert.equal(config.gpu,'5090');assert.equal(config.executionMode,'hybrid');
});
test('explicit legacy profiles remain available',()=>{
  const chosen=selectGPUConfig({...DEFAULTS},evidence,'a5000');
  assert.equal(buildPlan(chosen,evidence).gpu.id,'a5000');
  assert.equal(chosen.resolution,'legacy');
});
test('1080p selection uses completed 1080p measurements instead of pixel scaling',()=>{
  const chosen=selectGPUConfig({...config,resolution:'1080p'},evidence,'4090');
  const p=buildPlan(chosen,evidence);
  assert.equal(p.measurement.resolution,'1080p');assert.match(executionLabel(p),/images\/min/);
});
test('incomplete and control rounds cannot win selection',()=>{
  const modified=structuredClone(evidence),g=modified.concurrency.gpus.find(g=>g.id==='a6000');
  const source=g.productionProfiles.find(p=>p.resolution==='720p'&&p.encodingProfile==='fresh'&&p.rounds===2);
  g.hybridProfiles=[{...source,executionMode:'hybrid',workers:6,clientSlots:6,imagesPerSecond:999,rounds:1},
    {...source,executionMode:'hybrid',workers:6,clientSlots:6,imagesPerSecond:999,postControl:true}];
  assert.equal(selectGPUConfig(config,modified,'a6000').executionMode,'resident');
});

test('automatic speed shows the best measured delivery rate without changing video cadence',()=>{
  const p=buildPlan({...config,executionMode:'resident',measurementAttempt:'latest',imageWorkers:'1'},evidence);
  const speed=generationSpeed(p,evidence);
  assert.equal(speed.highest.toFixed(1),'38.0');
  assert.ok(speed.current<speed.highest);assert.equal(speed.isFastest,false);
  assert.equal(p.config.cadence,config.cadence);assert.equal(p.imageCount,buildPlan(config,evidence).imageCount);
});
test('automatic speed changes with GPU and resolution',()=>{
  const a6000=generationSpeed(buildPlan(selectGPUConfig(config,evidence,'a6000'),evidence),evidence);
  assert.equal(a6000.highest.toFixed(1),'11.8');assert.equal(a6000.isFastest,true);
  const p720=buildPlan(selectGPUConfig(config,evidence,'4090'),evidence);
  const p1080=buildPlan(selectGPUConfig({...config,resolution:'1080p'},evidence,'4090'),evidence);
  assert.ok(generationSpeed(p720,evidence).highest>generationSpeed(p1080,evidence).highest);
  assert.equal(p720.config.cadence,p1080.config.cadence);
});
test('legacy profiles do not invent a highest possible speed',()=>{
  const chosen=selectGPUConfig({...config,resolution:'legacy'},evidence,'5090');
  assert.equal(chosen.executionMode,'resident');
  const speed=generationSpeed(buildPlan(chosen,evidence),evidence);
  assert.equal(speed.highest,null);assert.equal(speed.current,null);
});

 test('continuous warm speed excludes startup, idle, directing, duration and rendering settings',()=>{
  const base=generationSpeed(buildPlan(config,evidence),evidence);
  const changes={minutes:1000,chapters:80,warmCache:true,gpuStartSeconds:100000,readyChapters:80,directorCost:8,exhaustive:true,qc:'strict'};
  const changed=generationSpeed(buildPlan({...config,...changes},evidence),evidence);
  assert.equal(changed.highest,base.highest);assert.equal(changed.current,base.current);
 });
