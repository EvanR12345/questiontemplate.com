// Distilled flow models must not inherit SD's CFG and step defaults.
export function imagePresetChanges(image,preset,style,provider={}) {
  const next={...image,preset},model=String(image.model||'').toLowerCase();
  const klein=/^flux2-klein-(4b|9b)(-q4)?$/.test(model);
  if(klein) Object.assign(next,{steps:4,guidance:1,sampler:'euler',scheduler:'flux2'});
  else if(['sd15','dreamshaper8'].includes(model)) Object.assign(next,{
    steps:preset==='Fast Local'?8:preset==='Maximum Quality'?30:20,guidance:7});
  // Unknown workflows retain their own validated parameters. A generic backend
  // recommendation may belong to another model in the same workflow bundle.
  let nextStyle=style;
  if(preset==='Anime/Manga'&&!/manhwa|manga|anime/i.test(style)) nextStyle='full-color manhwa';
  if(preset==='Cinematic'&&!/cinematic/i.test(style)) nextStyle='cinematic illustration';
  if(preset==='Storybook'&&!/storybook/i.test(style)) nextStyle='storybook';
  return {image:next,style:nextStyle,note:klein?
    'Distilled Klein keeps 4 steps and guidance 1. Style presets preserve landscape resolution and reference settings.':
    'The selected model keeps its own generation settings; inspect Advanced settings for manual changes.'};
}
