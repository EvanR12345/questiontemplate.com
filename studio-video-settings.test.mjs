import test from 'node:test';import assert from 'node:assert/strict';
import {videoRecipePatch,videoSettingsHTML}from './studio-video-settings.mjs';
const receipt={version:1,source:'export-input-snapshot',settings:{video:{width:1280,height:720,fps:30},image:{model:'flux2-klein-4b',steps:4,guidance:1},voice:'am_michael',budget:{totalUSD:500},automaticRepair:true,qcCheckLevel:'off',director:{apiKey:'never-copy'}}};
test('copying an export setup preserves spending grants, QC, manual assets and unrelated settings',()=>{
 const target={settings:{budget:{totalUSD:.3},automaticRepair:false,qcCheckLevel:'practical',watermark:{enabled:true},image:{referenceStrength:.65}},intro:{enabled:true},chapters:[{manual:true}]};
 const result=videoRecipePatch(target,receipt);
 assert.equal(result.settings.image.steps,4);assert.equal(result.settings.image.referenceStrength,.65);assert.equal(result.settings.video.fps,30);
 assert.deepEqual(result.settings.budget,{totalUSD:.3});assert.equal(result.settings.automaticRepair,false);assert.equal(result.settings.qcCheckLevel,'practical');assert.equal(result.settings.watermark.enabled,true);
 assert.equal(target.settings.image.steps,undefined);assert.ok(!JSON.stringify(result).includes('never-copy'));assert.equal(result.intro,undefined);
});
test('legacy and mixed exports cannot pretend to be one exact recipe',()=>{
 for(const r of [{},{...receipt,mixedImageSettings:true},{...receipt,mixedVoices:true}])assert.throws(()=>videoRecipePatch({settings:{}},r));
 assert.match(videoSettingsHTML({render:{path:'legacy.mp4'},chapters:[]}),/Older exports/);
});
test('saved setup is visible and labels are escaped',()=>{
 const html=videoSettingsHTML({render:{settingsSnapshot:{...receipt,settings:{...receipt.settings,voice:'<script>'}}}});
 assert.match(html,/1280 × 720/);assert.match(html,/30 fps/);assert.match(html,/4 steps/);assert.ok(!html.includes('<script>'));
});
