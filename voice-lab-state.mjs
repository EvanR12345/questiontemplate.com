export const SAMPLE_SCRIPT = "The room fell silent as Mara placed the old compass on the table. For years, its needle had pointed north. Tonight, it pointed straight at her. She tried to laugh, but the sound caught in her throat. Beyond the window, a single light appeared in the abandoned tower. Then a voice, quiet and unmistakably familiar, whispered her name. Mara closed her hand around the compass. This time, she was going to find out who was waiting.";
export const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function usd(value) {return Number.isFinite(value) && value >= 0 ? '$' + value.toFixed(value < .01 ? 5 : 3) : 'Not measured';}
export function previewCosts(metrics = {}) {
  const {hourlyUSD,generationSeconds,audioSeconds,startupSeconds,loadSeconds} = metrics;
  if (![hourlyUSD,generationSeconds,audioSeconds].every(v=>Number.isFinite(v)&&v>=0) || !audioSeconds) return null;
  const warm30USD = generationSeconds / audioSeconds * 30 * hourlyUSD / 3600;
  const modelLoad30USD=Number.isFinite(loadSeconds)&&loadSeconds>=0?warm30USD+loadSeconds*hourlyUSD/3600:null;
  return {warm30USD,modelLoad30USD,cold30USD:modelLoad30USD!==null&&Number.isFinite(startupSeconds)&&startupSeconds>=0 ? modelLoad30USD+startupSeconds*hourlyUSD/3600 : null};
}
export function auditionRequest(model, values) {
  if (!model) throw Error('Choose a model.');
  const mode = model.modes.find(item=>(typeof item==='string'?item:item.id)===values.mode);
  if (!mode) throw Error('Choose a supported mode.');
  const requirements=typeof mode==='string'?{}:mode;
  const text=String(values.text||'').trim();
  if (!text || text.length>2500) throw Error('Enter a script of up to 2,500 characters.');
  if (!model.languages.includes(values.language)) throw Error('Choose a supported language.');
  if (requirements.requiresReference && !values.referenceId) throw Error('Choose a saved voice reference.');
  if (requirements.requiresDescription && !String(values.description||'').trim()) throw Error('Describe the voice you want to create.');
  const parameters={};
  for (const control of model.parameters||[]) {
    const value=parameterValue(control,values.parameters?.[control.id]??control.default);
    parameters[control.id]=value;
  }
  let character;
  if(model.id==='drama3'&&values.mode==='design'){
    const name=String(values.character?.name||'Narrator').trim(),scene=String(values.character?.scene||'').trim();
    if(!name||name.length>80||/[\r\n:<>]/.test(name)||scene.length>500)throw Error('Check the Drama character name and scene.');
    character={name,scene};
  }
  return {model:model.id,mode:values.mode,text,language:values.language,description:String(values.description||'').trim(),...(values.referenceId?{referenceId:values.referenceId}:{}),...(character?{character}:{}),parameters};
}
export function parameterValue(control,raw) {
  if(control.type==='boolean'){
    if(typeof raw!=='boolean')throw Error('Check '+control.label+'.');
    return raw;
  }
  if(control.type==='select'){
    if(!(control.options||[]).some(option=>(typeof option==='string'?option:option.value)===raw))throw Error('Check '+control.label+'.');
    return raw;
  }
  const value=Number(raw);
  if(raw===''||!Number.isFinite(value)||value<control.min||value>control.max||(control.type==='integer'&&!Number.isInteger(value)))throw Error('Check '+control.label+'.');
  return value;
}
