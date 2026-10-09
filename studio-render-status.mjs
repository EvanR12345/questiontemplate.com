// Renderer placement is independent of permanent storage and image generation.
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
