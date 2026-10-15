export const NARRATOR = 'Calm, controlled, confident fantasy audiobook narrator. Medium-deep pitch, clear enunciation, smooth connected delivery, restrained cinematic tension, natural sentence pauses, subtle emphasis on revelations. No shouting or promotional delivery. Maintain the same narrator throughout.';
export const DEFAULT_VOICE_SCRIPT = `His mentor's advice echoed in his memory: "${NARRATOR}"
He had already lost everything. This time, he would fight for a second chance.
"Ahhh!" A blade flashed.
*Slash*
Thud. His companion fell.
"No! I can't leave you here." But the man he knew was already gone.
He had trained for only twelve days. Tonight, everything depended on what he had learned.`;
export const VOICE_OPTIONS = {
  'kokoro-82m': [
    ['am_michael','Michael','American male','Balanced narration'],
    ['am_fenrir','Fenrir','American male','Bolder delivery'],
    ['am_puck','Puck','American male','Upbeat delivery'],
    ['af_heart','Heart','American female','Clear, even delivery'],
    ['af_bella','Bella','American female','Expressive delivery'],
    ['af_nicole','Nicole','American female','Another female voice to compare'],
    ['bm_george','George','British male','British accent'],
    ['bf_emma','Emma','British female','British accent'],
  ],
  'qwen3-tts-1.7b': [
    ['Ryan','Ryan','English · male','Rhythmic, dynamic delivery'],
    ['Aiden','Aiden','English · male','Clear midrange'],
    ['Vivian','Vivian','Native Chinese · female','Bright, slightly edgy'],
    ['Serena','Serena','Native Chinese · female','Warm, gentle'],
    ['Uncle_Fu','Uncle Fu','Native Chinese · male','Low, mellow timbre'],
    ['Dylan','Dylan','Native Chinese · Beijing','Youthful, clear delivery'],
    ['Eric','Eric','Native Chinese · Sichuan','Lively, slightly husky'],
    ['Ono_Anna','Ono Anna','Native Japanese · female','Light, playful delivery'],
    ['Sohee','Sohee','Native Korean · female','Warm, expressive delivery'],
  ],
  'dia-1.6b': [['anchor','Project narrator','Generated voice anchor','Reuses this project’s saved synthetic voice; no named preset bank']],
  'fish-s2-pro': [['anchor','Project narrator','Generated voice anchor','Reuses this project’s saved synthetic voice; commercial license required for monetized use']],
};
export function voicesFor(model) { return VOICE_OPTIONS[model] || VOICE_OPTIONS['kokoro-82m']; }
export function voiceCards(model, selected) {
  return voicesFor(model).map(([id,name,description,reason]) => `<button type="button" class="preset${id===selected?' active':''}" data-voice="${id}" aria-pressed="${id===selected}"><strong>${name}</strong><span>${description}</span><span class="reason">${reason}</span></button>`).join('');
}
export function mountVoicePicker({model,select,cards,onSelect,initial}) {
  const choices=voicesFor(model);
  const value=choices.some(([id])=>id===initial)?initial:choices[0][0];
  select.replaceChildren(...choices.map(([id,name,description])=>new Option(`${name} · ${description}`,id)));
  select.value=value;
  function choose(id) {
    select.value=id;
    for(const button of cards.querySelectorAll('[data-voice]')) {
      const active=button.dataset.voice===id;
      button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));
    }
    onSelect?.(id);
  }
  cards.innerHTML=voiceCards(model,value);
  cards.onclick=event=>{const button=event.target.closest('[data-voice]');if(button && !button.disabled)choose(button.dataset.voice);};
  select.onchange=()=>choose(select.value);
  choose(value);
  return value;
}
