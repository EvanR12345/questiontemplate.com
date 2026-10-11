const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fields=['style','layoutMode','generationMode','voice','speed','narrationDelivery','soundEffects','continuityStrictness','appearanceHandling','cloudImagesOnly'];
const groups={video:['width','height','fps','crf','imageFit','motionMode','zoomAmount','motionEasing'],
 image:['provider','model','workflow','preset','width','height','steps','sampler','scheduler','guidance','referenceStrength','denoisingStrength'],
 director:['provider','model','reasoning','executionMode','parallelism','processingTier','factGroupSentences','visualPacking','sourceVisualOverlap','factBeatsMode','compactCuts']};
export function videoRecipePatch(target,receipt){
 if(receipt?.version!==1||receipt.source!=='export-input-snapshot')throw Error('This older export has no exact saved setup. Use a new short test before copying settings.');
 if(receipt.mixedImageSettings||receipt.mixedVoices)throw Error('This export uses several image or voice setups. Choose one shot or chapter before copying a single setup.');
 const settings=structuredClone(target.settings),source=receipt.settings||{};
 for(const key of fields)if(key in source)settings[key]=structuredClone(source[key]);
 for(const [group,keys]of Object.entries(groups)){
  settings[group]={...settings[group]};
  for(const key of keys)if(key in(source[group]||{}))settings[group][key]=structuredClone(source[group][key]);
 }
 // Budgets, automatic repair permission, checks, intro content, engagement,
 // character state, assets and existing per-shot settings belong to the target.
 return {settings};
}
export function videoSettingsHTML(project){
 const r=project.render?.settingsSnapshot;
 const choices=(project.chapters||[]).filter(c=>c.render?.settingsSnapshot);
 const receipt=r||choices[0]?.render.settingsSnapshot;
 if(!receipt)return `<div class="section-box"><h3>Saved video setup</h3><p class="muted">Older exports did not record an exact settings snapshot. New exports save their image, voice and video setup here. Current project choices can differ from already generated shots.</p><label>Copy from a saved test project<select id="videoRecipeSource"></select></label><button id="copyVideoRecipe">Use saved video setup</button></div>`;
 const s=receipt.settings,v=s.video||{},i=s.image||{};
 return `<div class="section-box"><h3>Saved video setup</h3><p>${esc(v.width)} × ${esc(v.height)} · ${esc(v.fps)} fps · ${esc(i.model)} · ${esc(i.steps)} steps · guidance ${esc(i.guidance)}</p><p>${esc(s.voice)} · ${esc(s.narrationDelivery)} narration · ${esc(s.speed)}× · ${esc(v.motionMode)} motion</p><p class="muted">Recorded when this export was assembled. ${receipt.mixedImageSettings||receipt.mixedVoices?'Several setups were used; individual image metadata remains authoritative.':'Copying applies to future generation. Existing images, narration and exports stay saved.'} Spending caps, checks, repairs, intro and overlays keep this project’s settings.</p><label>Copy from a saved test project<select id="videoRecipeSource"></select></label><button id="copyVideoRecipe">Use saved video setup</button></div>`;
}
