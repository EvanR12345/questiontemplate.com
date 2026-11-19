export function automaticRepairLabel(decision) {
  const state=decision?.automaticRepair;
  if (!decision?.blocking || !state) return '';
  if (state.reason==='manual') return 'You locked this image';
  if (state.reason==='policy') return state.limit===0 ? 'Automatic repair limit is zero' : 'Saved repair settings require review';
  if (state.reason==='exhausted') return state.used>state.limit
    ? `${state.used} automatic repairs used · current limit ${state.limit}`
    : `${state.used} of ${state.limit} automatic repairs used for this shot`;
  if (state.reason===null && state.remaining>0)
    return `${state.remaining} automatic repair${state.remaining===1?'':'s'} available for this shot`;
  return '';
}

// Renderer placement is independent of permanent storage and image generation.
export function chapterDisplayStatus(project, chapter) {
  const saved = String(chapter?.status || 'NOT_ANALYZED').replaceAll('_', ' ');
  if (chapter?.status !== 'FAILED') return saved;
  const shots = (chapter.scenes || []).flatMap(scene => scene.shots || []);
  if (!shots.length || shots.some(shot => !shot.imagePath || shot.generationStale)) return saved;
  // A prior attempt's failure remains in job history. A verified later export
  // can be ready without silently certifying its unchecked visual contents.
  if (project.settings?.qcCheckLevel === 'off' && chapter.render?.path && !chapter.renderStale)
    return 'VIDEO READY · visual checks off';
  if (shots.some(shot => shot.qcDecision?.blocking)) return 'REVIEW REQUIRED';
  return saved;
}

export function rendererPlacement(connected, health = {}) {
  if (!connected) return {
    title: 'Renderer offline',
    detail: 'Connect the shared helper to check rendering. Saved cloud files do not provide a running renderer.',
  };
  const backend = health?.renderer?.backend;
  const label = backend === 'native' ? 'NVIDIA GPU' : backend === 'cpu' ? 'CPU' : 'backend not reported';
  const cloud = health?.cloudRender?.enabled;
  return {
    title: `Full workflow renders on this computer · ${label}`,
    detail: (backend === 'native'
      ? 'Photo motion uses the local GPU. Intro, crossfades and overlays still use CPU processing. '
      : 'Video export uses the local helper. ') +
      'Cloud image generation is separate. Rendering uses temporary local disk space; cloud storage keeps uploaded files. ' +
      (cloud ? 'The separate cloud-render action is available; it does not change the full workflow automatically.'
        : 'Cloud rendering is not active for the full workflow.'),
  };
}
