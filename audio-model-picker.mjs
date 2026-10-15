import {VOICE_MODELS, voiceNotice} from './studio-voice-settings.mjs?v=voice-picker-20261015b';
import {DEFAULT_VOICE_SCRIPT,NARRATOR,mountVoicePicker} from './voice-catalogue.mjs?v=voice-picker-20261015b';
import {helperJson,pairingKey} from './helper-connection.mjs?v=connection-20261009';

// Audio and Studio share the authenticated helper and the same durable job queue.
export function setupAudioModels({document:doc,status,onChange,isBusy}) {
  const $=id=>doc.getElementById(id), model=$('audioVoiceModel');
  const selections={'kokoro-82m':$('studioVoice').value};
  model.replaceChildren(...VOICE_MODELS.map(([id,name])=>new Option(name,id)));
  const savedText=$('text').value.trim();
  if(!savedText || savedText==='default script' || savedText.startsWith('The lights went out at exactly midnight.')) $('text').value=DEFAULT_VOICE_SCRIPT;
  $('audioVoiceInstruction').value=NARRATOR;
  let jobId=null, projectId=null, polling=false;
  const call=(path,body)=>helperJson('/studio/'+path,pairingKey(),body);
  const remote=()=>model.value!=='kokoro-82m';
  function load() {
    mountVoicePicker({model:model.value,select:$('studioVoice'),cards:$('presets'),initial:selections[model.value],onSelect:id=>{
      selections[model.value]=id;onChange(id);
    }});
    $('audioModelNotice').textContent=model.value==='qwen3-tts-1.7b'?'Nine preset voices. Ryan and Aiden are native English speakers.':remote()?'Uses this project’s saved synthetic narrator; no named voice bank.':'Eight English voices. Pick a card, then preview your script.';
    $('audioRuntimeNotice').textContent=voiceNotice(model.value,{});
    $('audioRemoteOptions').hidden=!remote();
    $('sizeEstimate').hidden=remote();
    $('audioInstructionLabel').hidden=model.value!=='qwen3-tts-1.7b';
    $('sample').textContent=remote()?'▶ Preview selected voice':'▶ Preview voice';
    $('generate').textContent=remote()?'↓ Generate audition':'↓ Generate audio';
    for(const id of ['engine','format','bitrate']) $(id).disabled=remote()||isBusy();
    if(remote()) void loadProjects().catch(error=>status(error.message));
  }
  model.onchange=load;
  async function loadProjects() {
    if(!pairingKey()) {status('Connect the shared helper to use Kaggle voices.');return;}
    const [projects,health]=await Promise.all([call('projects'),call('health')]);
    $('audioRuntimeNotice').textContent=voiceNotice(model.value,health);
    if(!Array.isArray(projects))throw Error('The helper returned an invalid project list.');
    let preferred;try{preferred=localStorage.getItem('qt-production-project');}catch{}
    const current=$('audioVoiceProject').value||preferred;
    $('audioVoiceProject').replaceChildren(...projects.map(p=>new Option(p.name,p.id)));
    if(projects.some(p=>p.id===current))$('audioVoiceProject').value=current;
  }
  async function showResult(project, preview) {
    const path=preview.exportPath||preview.path;
    const ticket=await call('media-link',{project,path});
    const section=doc.createElement('section');section.className='part';
    const title=doc.createElement('h3');title.textContent=`${VOICE_MODELS.find(([id])=>id===preview.model)?.[1]||preview.model||model.value} · ${preview.voice||selections[model.value]} · ${Number(preview.duration).toFixed(1)}s`;
    const player=doc.createElement('audio');player.controls=true;player.preload='metadata';player.src=ticket.url;
    player.onerror=async()=>{player.onerror=null;try{player.src=(await call('media-link',{project,path})).url;}catch(error){status(error.message);}};
    const link=doc.createElement('a');link.href=ticket.url;link.textContent=preview.exportPath?'Download AAC · 128 kbps':'Download WAV';link.download=path;
    section.append(title,player,link);$('audioModelResults').prepend(section);
  }
  async function generate() {
    if(!remote())return false;
    if(polling || isBusy())return true;
    let submitted=false;
    polling=true;setBusy(true);
    try {
      const text=$('text').value.trim();
      if(!text || text.length>1800)throw Error('Kaggle auditions need 1–1800 characters. For full narration, use Studio → Settings → Voice, then generate your chapters.');
      if(!pairingKey())throw Error('Connect the shared helper before using Kaggle voices.');
      if(!$('audioVoiceProject').value)await loadProjects();
      projectId=$('audioVoiceProject').value;
      if(!projectId)throw Error('Create a project in Studio first, then return here to save voice auditions in it.');
      const settings={text,voiceEngine:model.value,voiceCompute:'kaggle',voiceInstruction:$('audioVoiceInstruction').value,
        voice:model.value==='kokoro-82m'?$('studioVoice').value:'am_michael',audioRequestId:crypto.randomUUID(),qwenSpeaker:model.value==='qwen3-tts-1.7b'?$('studioVoice').value:'Ryan',
        speed:Number($('rate').value),soundEffects:'subtle',narrationDelivery:'cinematic',emphasisPhrases:['already gone','only twelve days']};
      status('Submitting a private Kaggle audition…');
      const previous=await call('queue'),known=new Set(previous.jobs.map(j=>j.id));
      const queue=await call('jobs',{project:projectId,kind:'voice-preview',options:settings});
      const job=queue.jobs.find(j=>!known.has(j.id) && j.project===projectId && j.kind==='voice-preview');
      if(!job)throw Error('Audition submission could not be identified. Inspect Studio’s queue before retrying.');
      jobId=job.id;submitted=true;
      try{sessionStorage.setItem('qt-audio-voice-job',JSON.stringify({jobId,projectId,text,model:model.value,speaker:selections[model.value]}));}catch{}
      await watch(text,model.value,selections[model.value]);
    } catch(error) {status(`${error.message}${submitted?' The job remains saved in Studio’s queue; refreshing this page will reconnect.':''}`);}
    finally{polling=false;setBusy(false);}
    return true;
  }
  async function watch(text,engine,speaker) {
    for(;;) {
      const queue=await call('queue'),job=queue.jobs.find(j=>j.id===jobId);
      if(!job)throw Error('Saved audition job is no longer visible. Check Studio’s saved auditions.');
      status(`${job.status} · ${job.message||'Kaggle voice audition'} · cold startup and quota can add time`);
      if(['COMPLETE','FAILED','CANCELLED'].includes(job.status)) {
        try{sessionStorage.removeItem('qt-audio-voice-job');}catch{}
        if(job.status!=='COMPLETE')throw Error(job.message||job.status);
        const project=await call('project?id='+encodeURIComponent(projectId));
        const preview=[...(project.voicePreviews||[])].reverse().find(p=>p.text===text && p.model===engine && (engine!=='qwen3-tts-1.7b'||p.voice===speaker));
        if(!preview)throw Error('Job completed but the saved audition is missing. Open Studio’s Voice section.');
        await showResult(projectId,preview);status('Audition saved. Listen below; existing chapter narration is preserved.');break;
      }
      await new Promise(resolve=>setTimeout(resolve,8000));
    }
  }
  function setBusy(value) {
    for(const id of ['audioVoiceModel','studioVoice','audioVoiceProject','audioVoiceInstruction','sample','generate','text','rate','clear','importScript'])$(id).disabled=value;
    for(const button of $('presets').querySelectorAll('button'))button.disabled=value;
    $('audioVoiceCancel').hidden=!value;
  }
  $('audioVoiceCancel').onclick=async()=>{try{await call('control',{action:'cancel-job',job:jobId,project:projectId});status('Cancellation requested. Admitted Kaggle work may continue remotely until timeout.');}catch(error){status(error.message);}};
  load();
  // Refresh restores monitoring, never resubmits a notebook.
  try {
    const saved=JSON.parse(sessionStorage.getItem('qt-audio-voice-job')||'null');
    if(saved?.jobId && saved.projectId && saved.text){jobId=saved.jobId;projectId=saved.projectId;polling=true;setBusy(true);void watch(saved.text,saved.model,saved.speaker).catch(e=>status(e.message)).finally(()=>{polling=false;setBusy(false);});}
  }catch{}
  return {generate,remote,defaultScript:DEFAULT_VOICE_SCRIPT,busy:()=>polling};
}
