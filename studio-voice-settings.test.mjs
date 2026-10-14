import test from 'node:test';
import assert from 'node:assert/strict';
import {VOICE_MODELS,voiceValues,voiceNotice,NARRATOR} from './studio-voice-settings.mjs';
test('all four voice models remain selectable without claiming availability',()=>{
  assert.equal(VOICE_MODELS.length,4);
  assert.match(voiceNotice('qwen3-tts-1.7b',{}),/setup is required/);
  assert.match(voiceNotice('fish-s2-pro',{kaggleVoice:{configured:true}}),/commercial license/);
});
test('voice settings persist without including credentials',()=>{
  const fields={'#settingVoiceEngine':{value:'qwen3-tts-1.7b'},'#settingVoiceInstruction':{value:NARRATOR},'#settingFishLicense':{checked:false}};
  assert.deepEqual(voiceValues({querySelector:id=>fields[id]}),{voiceEngine:'qwen3-tts-1.7b',voiceInstruction:NARRATOR,voiceCompute:'kaggle',fishCommercialLicense:false});
});
test('unknown model and oversized instruction are rejected',()=>{
  assert.throws(()=>voiceValues({querySelector:id=>id==='#settingVoiceEngine'?{value:'bad'}:null}));
  assert.throws(()=>voiceValues({querySelector:id=>id==='#settingVoiceInstruction'?{value:'a'.repeat(1201)}:null}));
});
