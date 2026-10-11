import test from 'node:test';
import assert from 'node:assert/strict';
import {gpuSettingsForm,gpuSettingsValues} from './studio-gpu-settings.mjs';
const documentFor=patch=>{const values={managedPodId:'worker123',managedPodPool:'worker456\nworker456',managedGPUType:'NVIDIA GeForce RTX 5090',managedWaitMinutes:'30',managedPrice:'1.2',managedSSHFile:'C:/private/key',managedStock:true,...patch};return {getElementById:id=>({value:String(values[id]),checked:values[id]===true})};};
test('GPU controls preserve exact model, deduplicate workers and convert waiting minutes',()=>{
 const result=gpuSettingsValues(documentFor({}));
 assert.deepEqual(result.runpodPreparedPodIds,['worker456']);assert.equal(result.runpodAvailabilityWaitSeconds,1800);
 assert.equal(result.runpodRequiredGPUType,'NVIDIA GeForce RTX 5090');assert.equal(result.runpodCheckRegionalStock,true);
});
test('clearing optional hourly cap preserves project-budget enforcement',()=>assert.equal(gpuSettingsValues(documentFor({managedPrice:''})).runpodMaxHourlyUSD,null));
test('rejects malformed pool and invalid limits',()=>{
 for (const patch of [{managedPodPool:'../escape'},{managedWaitMinutes:'Infinity'},{managedWaitMinutes:''},{managedPrice:'0'},{managedPrice:'NaN'},{managedPodId:'',managedPodPool:''}]) assert.throws(()=>gpuSettingsValues(documentFor(patch)));
});
test('escapes stored paths and preserves unknown existing GPU requirements',()=>{
 const html=gpuSettingsForm({runpodSSHKeyFile:'"><script>',runpodRequiredGPUType:'future<gpu',runpodPreparedPodIds:['worker123']});
 assert.ok(!html.includes('<script>'));assert.ok(html.includes('future&lt;gpu'));assert.ok(html.includes('worker123'));
});
