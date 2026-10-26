export const REFERENCE_SCRIPT = 'At dawn, the rain stopped and the town began to wake. A baker opened her shop, a bicycle rattled over the bridge, and somewhere a dog announced the morning. Clara paused beside the river and listened. For the first time in weeks, she felt no need to hurry. She had a letter to deliver, a promise to keep, and the whole day ahead of her.';

export const VOICE_DESIGNS = [
  {id:'warm-narrator',name:'Warm storyteller',description:'An adult male narrator with a warm, medium-low baritone, clear neutral American English, relaxed confidence and subtle emotional range. Speak at a natural, steady conversational pace, with precise consonants, smooth phrasing and light sentence pauses. Clean close-microphone speech, no music, effects, whispering or exaggerated acting.'},
  {id:'clear-narrator',name:'Clear, expressive narrator',description:'An adult female narrator with a clear, warm middle register and neutral American English. Thoughtful, engaging and expressive without theatrical exaggeration. Use a natural conversational pace, crisp articulation, balanced volume and gentle sentence pauses. Clean close-microphone speech without music, effects, breathy whispering or vocal fry.'},
  {id:'bright-storyteller',name:'Bright, lively storyteller',description:'A young adult male storyteller with a bright, clear medium register and neutral British English. Friendly and alert, with restrained curiosity and a little warmth. Speak at a steady natural pace with clear diction and smooth connected phrasing. Clean close-microphone speech, consistent volume, no music, sound effects, shouting or whispering.'},
];

export const QWEN_PRESETS = [
  {id:'vivian',name:'Vivian'},{id:'serena',name:'Serena'},{id:'uncle_fu',name:'Uncle Fu'},
  {id:'dylan',name:'Dylan'},{id:'eric',name:'Eric'},{id:'ryan',name:'Ryan'},
  {id:'aiden',name:'Aiden'},{id:'ono_anna',name:'Ono Anna'},{id:'sohee',name:'Sohee'},
];

export const INCLUDED_VOICES = [
  {model:'drama3',name:'Drama 3',summary:'The model has no named speaker bank. Its official demo supplies three example clone references; these are reference recordings, rather than built-in speaker IDs.',url:'https://huggingface.co/spaces/fishaudio/Drama-3/tree/main/voices'},
  {model:'voxcpm2',name:'VoxCPM2',summary:'The model uses reference audio or voice descriptions. The official repository includes example reference recordings; it does not expose a named speaker bank.',url:'https://github.com/OpenBMB/VoxCPM/tree/main/examples'},
  {model:'qwen3',name:'Qwen Base + VoiceDesign + CustomVoice',summary:'CustomVoice provides the nine named preset voices listed here. Base clones reference audio, including a preset voice after its audition is saved as a reference. VoiceDesign creates voices from descriptions.',url:'https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice'},
];

export function preferredReference(duration) {return Number.isFinite(duration)&&duration>=25&&duration<=30;}
export function referenceSource(reference,models=[]) {
  if(reference.source?.kind!=='audition')return 'Saved reference recording';
  const model=models.find(item=>item.id===reference.source.model)?.name||reference.source.model||'Voice Lab';
  return `${model} · ${reference.source.mode||'audition'}`;
}
export function jobVoiceName(job,references=[]) {
  if(job.mode==='preset')return QWEN_PRESETS.find(voice=>voice.id===job.parameters?.speaker)?.name||job.parameters?.speaker||'Qwen preset';
  return references.find(ref=>ref.id===job.referenceId)?.name||VOICE_DESIGNS.find(voice=>voice.description===job.description)?.name||(job.mode==='design'?'Designed voice':'Unconditioned voice');
}
