export function directorLimits(mode) {
  return ['staged-review','staged-lean'].includes(mode) ? 8 : 3;
}
export function directorSummary(settings) {
  const d = settings.director || {};
  if (d.provider === 'local-qwen') return 'Local Qwen directing. No Luna API processing tier applies. Existing assets and manual edits stay saved.';
  const staged = ['staged-review','staged-lean'].includes(d.executionMode);
  const tier = d.processingTier || 'default';
  const leanDetails=d.executionMode==='staged-lean'?` Source requests: up to ${d.factGroupSentences??48} sentences; visual groups: ${d.visualPacking??'small'}. ${d.sourceVisualOverlap?'Accepted-source/visual overlap enabled.':'Source/visual overlap disabled.'} ${d.factBeatsMode==='storyboard'?'Storyboard owns emotional beats once.':'Source analyst and storyboard both produce emotional metadata.'}`:'';
  return `${d.executionMode === 'staged-lean' ? 'Lean Luna (experimental): multiple shots per request, source and storyboard reviews' : staged ? 'Staged Luna: ordered source facts, independent reviewed visual groups' : 'Classic director passes'}. ${d.parallelism || 1} independent task${d.parallelism === 1 ? '' : 's'}. ${tier === 'fast' ? 'Fast API processing: 2× Standard token rates; service availability varies.' : tier === 'flex' ? 'Flex processing: variable latency; discounted token rates.' : 'Standard API processing.'}${leanDetails} Existing shots and manual edits stay saved; changes apply when you request new analysis.`;
}
export function directorRequestControls(d,health) {
  const lean=d.executionMode==='staged-lean',ready=health?.leanPackingAvailable===true;
  const facts=[48,96,128,256].map(n=>`<option value="${n}" ${n===(d.factGroupSentences??48)?'selected':''} ${n===256&&(!lean||!ready)?'disabled':''}>Up to ${n} sentences${n===256?' · experimental':''}</option>`).join('');
  const packing=[['small','Small · existing default'],['balanced','Medium · experimental'],['large','Large · experimental']].map(([key,label])=>`<option value="${key}" ${key===(d.visualPacking??'small')?'selected':''}>${label}</option>`).join('');
  return `<details style="grid-column:1/-1"><summary>Advanced director request sizes</summary><div class="settings-grid"><label>Source facts per request<select id="settingFactGroupSentences">${facts}</select></label><label>Visual request size<select id="settingVisualPacking" ${lean&&ready?'':'disabled'}>${packing}</select></label><label class="inline"><input id="settingSourceVisualOverlap" type="checkbox" ${d.sourceVisualOverlap===true?'checked':''} ${lean&&health?.sourceVisualOverlapAvailable?'':'disabled'}>Overlap accepted source groups with visual planning · experimental</label><label>Emotional beat ownership<select id="settingFactBeatsMode" ${lean&&health?.leanFactBeatsAvailable?'':'disabled'}><option value="analyst" ${d.factBeatsMode!=='storyboard'?'selected':''}>Source analyst + storyboard · existing</option><option value="storyboard" ${d.factBeatsMode==='storyboard'?'selected':''}>Storyboard once · experimental</option></select></label></div><p class="muted">Source facts, injuries, held objects and dialogue cues remain mandatory. Storyboard ownership derives emotional-beat metadata from accepted AI scene moods instead of asking the source analyst to repeat it. This does not remove scene planning or reviews.</p><p class="muted">These group API work, not the number of pictures. Required story cuts, references, selected image cadence and complete reviews remain. Larger requests are experimental: current tests have failed speaker/fidelity checks. Keep Small unless testing deliberately. Overlap uses the same API slots; later facts stay ordered, and no chapter commits before all reviews pass. A changed size needs its own timing history; no speed or quality guarantee.</p></details>`;
}
export function directorSettings(current, values, health) {
  const mode = values.executionMode;
  if (!['classic', 'staged-review','staged-lean'].includes(mode)) throw Error('Choose a supported director workflow.');
  const n = Number(values.parallelism);
  if (!Number.isInteger(n) || n < 1 || n > directorLimits(mode)) throw Error('Choose a supported independent-task limit.');
  if (!['default', 'fast', 'flex'].includes(values.processingTier)) throw Error('Choose Standard, Fast or Flex processing.');
  if(values.compactCuts!==undefined&&typeof values.compactCuts!=='boolean')throw Error('Compact storyboard cuts must be enabled or disabled.');
  if(values.compactCuts&&mode!=='staged-lean')throw Error('Compact storyboard cuts require Lean Luna.');
  const facts=Number(values.factGroupSentences??current.factGroupSentences??48);
  const packing=values.visualPacking??current.visualPacking??'small';
  const overlap=values.sourceVisualOverlap??current.sourceVisualOverlap??false;
  const beats=values.factBeatsMode??current.factBeatsMode??'analyst';
  if(!['analyst','storyboard'].includes(beats))throw Error('Choose source analyst or storyboard beat ownership.');
  if(beats==='storyboard'&&mode!=='staged-lean')throw Error('Storyboard-owned beats require Lean Luna.');
  if(beats==='storyboard'&&!health?.leanFactBeatsAvailable)throw Error('Update the shared helper before using storyboard-owned beats.');
  if(typeof overlap!=='boolean')throw Error('Accepted source/visual overlap must be enabled or disabled.');
  if(overlap&&mode!=='staged-lean')throw Error('Accepted source/visual overlap requires Lean Luna.');
  if(overlap&&!health?.sourceVisualOverlapAvailable)throw Error('Update the shared helper before overlapping accepted source groups.');
  if(![48,96,128,256].includes(facts))throw Error('Choose a bounded source-fact request size.');
  if(!['small','balanced','large'].includes(packing))throw Error('Choose a bounded visual request size.');
  if(mode!=='staged-lean'&&(facts===256||packing!=='small'))throw Error('Wider request groups require Lean Luna.');
  if((facts===256||packing!=='small')&&!health?.leanPackingAvailable)throw Error('Update the shared helper before using wider request groups.');
  if (['staged-review','staged-lean'].includes(mode)) {
    if (!health?.stagedDirectorAvailable) throw Error('Update and connect the shared helper before using staged Luna.');
    if (mode === 'staged-lean' && !health?.leanDirectorAvailable) throw Error('Update the shared helper before using Lean Luna.');
    if (mode === 'staged-lean' && values.processingTier === 'fast') throw Error('Lean Luna uses Standard or Flex processing; premium Fast is not enabled.');
    if (values.provider !== 'openai-luna' || values.imageProvider !== 'comfyui' || values.economyPanels)
      throw Error('Staged Luna requires Luna, ComfyUI cloud images and individual shots.');
    if (!(Number(values.apiBudget) > 0) || !Number.isFinite(Number(values.apiBudget)))
      throw Error('Set an API spending cap before using staged Luna.');
  }
  return {...current, executionMode: mode, processingTier: values.processingTier, parallelism: n,
    compactCuts:values.compactCuts??current.compactCuts??false,
    factGroupSentences: facts,visualPacking:packing,sourceVisualOverlap:overlap,factBeatsMode:beats,
    reasoningProfile: 'selected'};
}
