import test from 'node:test';
import assert from 'node:assert/strict';
import {imagePresetChanges} from './studio-image-presets.mjs';
test('cloud Klein presets do not inherit SD CFG, steps or small local resolutions',()=>{
 const input={provider:'comfyui',model:'flux2-klein-4b',width:1280,height:720,steps:4,guidance:1,referenceStrength:.65};
 for(const preset of ['Anime/Manga','Maximum Quality','Fast Local','Cinematic']){
  const result=imagePresetChanges(input,preset,'Korean fantasy manhwa with crisp ink');
  assert.equal(result.image.steps,4);assert.equal(result.image.guidance,1);
  assert.equal(result.image.width,1280);assert.equal(result.image.height,720);assert.equal(result.image.referenceStrength,.65);
  assert.notEqual(result.style,'photorealistic cinema');
 }
 assert.equal(imagePresetChanges(input,'Anime/Manga','Korean fantasy manhwa with crisp ink').style,'Korean fantasy manhwa with crisp ink');
});
test('9B and local distilled variants use model rules; unknown workflows remain unchanged',()=>{
 for(const model of ['flux2-klein-9b','flux2-klein-4b-q4']) assert.equal(imagePresetChanges({model,steps:30,guidance:7},'Maximum Quality','anime').image.steps,4);
 const unknown={model:'future-edit-model',steps:9,guidance:3,width:1600};
 assert.deepEqual(imagePresetChanges(unknown,'Maximum Quality','anime',{recommended:{steps:20,guidance:7}}).image,{...unknown,preset:'Maximum Quality'});
});
test('existing SD presets preserve their established model settings',()=>{
 assert.equal(imagePresetChanges({model:'sd15'},'Maximum Quality','manga').image.steps,30);
 assert.equal(imagePresetChanges({model:'dreamshaper8'},'Fast Local','manga').image.steps,8);
});
