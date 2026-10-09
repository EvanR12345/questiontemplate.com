import test from 'node:test';
import assert from 'node:assert/strict';
import {rendererPlacement} from './studio-render-status.mjs';
test('cloud storage cannot imply cloud rendering', () => {
  const status = rendererPlacement(true, {renderer:{backend:'native'},storage:{enabled:true,provider:'gcs'}});
  assert.match(status.title,/this computer.*NVIDIA GPU/);
  assert.match(status.detail,/crossfades.*CPU/);
  assert.match(status.detail,/temporary local disk/);
  assert.match(status.detail,/not active/);
});
test('optional cloud action cannot change full workflow placement', () => {
  const status = rendererPlacement(true,{renderer:{backend:'cpu'},cloudRender:{enabled:true}});
  assert.match(status.title,/this computer.*CPU/);
  assert.match(status.detail,/separate cloud-render action/);
});
test('offline or missing health reports no invented backend', () => {
  assert.match(rendererPlacement(false,{renderer:{backend:'native'}}).title,/offline/);
  assert.match(rendererPlacement(true,null).title,/not reported/);
});
test('private configuration is never included in placement text', () => {
  const status = JSON.stringify(rendererPlacement(true,{renderer:{backend:'native',note:'SECRET'},apiKey:'SECRET'}));
  assert.ok(!status.includes('SECRET'));
});
