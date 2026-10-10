export function directorLimits(mode) {
  return ['staged-review','staged-lean'].includes(mode) ? 8 : 3;
}
export function directorSummary(settings) {
  const d = settings.director || {};
  if (d.provider === 'local-qwen') return 'Local Qwen directing. No Luna API processing tier applies. Existing assets and manual edits stay saved.';
  const staged = ['staged-review','staged-lean'].includes(d.executionMode);
  const tier = d.processingTier || 'default';
  return `${d.executionMode === 'staged-lean' ? 'Lean Luna (experimental): multiple shots per request, source and storyboard reviews' : staged ? 'Staged Luna: ordered source facts, independent reviewed visual groups' : 'Classic director passes'}. ${d.parallelism || 1} independent task${d.parallelism === 1 ? '' : 's'}. ${tier === 'fast' ? 'Fast API processing: 2× Standard token rates; service availability varies.' : tier === 'flex' ? 'Flex processing: variable latency; discounted token rates.' : 'Standard API processing.'} Existing shots and manual edits stay saved; changes apply when you request new analysis.`;
}
export function directorSettings(current, values, health) {
  const mode = values.executionMode;
  if (!['classic', 'staged-review','staged-lean'].includes(mode)) throw Error('Choose a supported director workflow.');
  const n = Number(values.parallelism);
  if (!Number.isInteger(n) || n < 1 || n > directorLimits(mode)) throw Error('Choose a supported independent-task limit.');
  if (!['default', 'fast', 'flex'].includes(values.processingTier)) throw Error('Choose Standard, Fast or Flex processing.');
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
    factGroupSentences: mode.startsWith('staged') ? 128 : current.factGroupSentences || 48,
    reasoningProfile: 'selected'};
}
