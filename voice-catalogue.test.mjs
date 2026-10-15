import test from 'node:test';
import assert from 'node:assert/strict';
import {NARRATOR,DEFAULT_VOICE_SCRIPT,voicesFor,voiceCards,mountVoicePicker} from './voice-catalogue.mjs';
import {voiceValues} from './studio-voice-settings.mjs';

test('audition keeps the complete requested description, cries, effects and emphasis in a short story',()=>{
  for(const text of [NARRATOR,'He had already lost everything. This time, he would fight for a second chance.','Ahhh!','*Slash*','Thud.',"No! I can't leave you here.",'already gone','only twelve days']) assert.ok(DEFAULT_VOICE_SCRIPT.includes(text),text);
  assert.ok(DEFAULT_VOICE_SCRIPT.split(/\s+/).length<160);
  assert.ok(DEFAULT_VOICE_SCRIPT.length<1800);
});
test('model catalogues expose actual named speakers or a clearly labeled project anchor',()=>{
  assert.equal(voicesFor('kokoro-82m').length,8);
  assert.equal(voicesFor('qwen3-tts-1.7b').length,9);
  assert.ok(voicesFor('qwen3-tts-1.7b').some(([id])=>id==='Uncle_Fu'));
  for(const model of ['dia-1.6b','fish-s2-pro']) {
    assert.equal(voicesFor(model).length,1);
    assert.match(voiceCards(model,'anchor'),/no named preset|commercial license/);
  }
});
test('switching model restores that model’s selection and card clicks choose its real id',()=>{
  globalThis.Option=class {constructor(text,value){this.text=text;this.value=value;}};
  const select={replaceChildren(...options){this.options=options;}};
  const buttons=[];
  const cards={querySelectorAll:()=>buttons};
  let chosen;
  mountVoicePicker({model:'qwen3-tts-1.7b',select,cards,initial:'Aiden',onSelect:id=>chosen=id});
  assert.equal(select.value,'Aiden');assert.equal(chosen,'Aiden');
  cards.onclick({target:{closest:()=>({dataset:{voice:'Sohee'}})}});
  assert.equal(chosen,'Sohee');
  mountVoicePicker({model:'kokoro-82m',select,cards,initial:'Sohee'});
  assert.equal(select.value,'am_michael');
  delete globalThis.Option;
});
test('selected Qwen speaker is saved independently of the Kokoro voice',()=>{
  const values={'#settingVoiceEngine':{value:'qwen3-tts-1.7b'},'#settingModelVoice':{value:'Aiden'}};
  assert.equal(voiceValues({querySelector:key=>values[key]}).qwenSpeaker,'Aiden');
});
