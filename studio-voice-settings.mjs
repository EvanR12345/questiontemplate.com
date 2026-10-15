import {NARRATOR, DEFAULT_VOICE_SCRIPT, voicesFor, voiceCards, mountVoicePicker} from './voice-catalogue.mjs?v=voice-picker-20261015';
export {NARRATOR, DEFAULT_VOICE_SCRIPT, voicesFor, voiceCards, mountVoicePicker};
export const VOICE_MODELS = [
  ['kokoro-82m','Kokoro 82M · existing local voice'],
  ['qwen3-tts-1.7b','Qwen3-TTS 1.7B · Kaggle'],
  ['dia-1.6b','Dia 1.6B · Kaggle'],
  ['fish-s2-pro','Fish Audio S2 Pro · Kaggle research / licensed use'],
];
export function voiceValues(root) {
  const voiceEngine=root.querySelector('#settingVoiceEngine')?.value||'kokoro-82m';
  if(!VOICE_MODELS.some(([id])=>id===voiceEngine))throw Error('Choose a supported narration model.');
  const voiceInstruction=root.querySelector('#settingVoiceInstruction')?.value??NARRATOR;
  if(voiceInstruction.length>1200)throw Error('Narrator instructions must be at most 1200 characters.');
  return {voiceEngine,voiceInstruction,...(voiceEngine==='qwen3-tts-1.7b'?{qwenSpeaker:root.querySelector('#settingModelVoice')?.value||'Ryan'}:{}),voiceCompute:voiceEngine!=='kokoro-82m'||root.querySelector('#settingKokoroKaggle')?.checked?'kaggle':'local',fishCommercialLicense:root.querySelector('#settingFishLicense')?.checked||false};
}
export function voiceNotice(engine,health,compute='local') {
  if(engine==='kokoro-82m' && compute==='local')return 'Existing local Kokoro voice. Chapter narration and earlier auditions remain saved.';
  const setup=health?.kaggleVoice?.configured?'Private Kaggle notebook connection configured.':'Kaggle setup is required before generating this voice.';
  return `${setup} GPU stock and weekly quota apply; remote startup and model downloading add time. No long-form speed or quality guarantee has been measured. ${engine==='kokoro-82m'?'Uses your selected Kokoro speaker remotely.':engine==='qwen3-tts-1.7b'?'Uses your selected preset and narration instructions. Native English presets are Ryan and Aiden.':engine==='dia-1.6b'?'Uses short English passages with a generated voice anchor; identity drift is still possible.':'Free research auditions only unless you hold a Fish commercial license.'} Sound-effect cues become brief pauses on Kaggle. Cancellation stops local publication; an admitted notebook can continue until its four-hour timeout.`;
}
