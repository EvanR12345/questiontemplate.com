import test from 'node:test';
import assert from 'node:assert/strict';
import {rendererPlacement,chapterDisplayStatus} from './studio-render-status.mjs';
test('completed unchecked exports do not inherit a misleading historical failure label', () => {
  const project={settings:{qcCheckLevel:'off'}};
  const chapter={status:'FAILED',render:{path:'chapter.mp4'},renderStale:false,
    scenes:[{shots:[{imagePath:'image.png',qcDecision:{blocking:false}}]}]};
  assert.match(chapterDisplayStatus(project,chapter),/VIDEO READY.*checks off/);
  assert.equal(chapter.status,'FAILED');
  chapter.renderStale=true;assert.equal(chapterDisplayStatus(project,chapter),'FAILED');
  chapter.renderStale=false;chapter.scenes[0].shots[0].generationStale=true;
  assert.equal(chapterDisplayStatus(project,chapter),'FAILED');
});
test('saved images needing actual review remain visibly blocked, and missing images stay failed', () => {
  const project={settings:{qcCheckLevel:'practical'}};
  const chapter={status:'FAILED',scenes:[{shots:[{imagePath:'image.png',qcDecision:{blocking:true}}]}]};
  assert.equal(chapterDisplayStatus(project,chapter),'REVIEW REQUIRED');
  chapter.scenes[0].shots[0].imagePath='';assert.equal(chapterDisplayStatus(project,chapter),'FAILED');
  chapter.status='GENERATING';assert.equal(chapterDisplayStatus(project,chapter),'GENERATING');
});
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
