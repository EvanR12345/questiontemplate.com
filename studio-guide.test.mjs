import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {FEATURES,FIELD_HELP,WORKSPACES,findFeatures,matchesSetting,workspaceGuideHTML} from './studio-guide.mjs';

test('every Studio workspace has a specific explanation and a reachable function',()=>{
  for(const tab of ['write','narration','cast','review','timeline','settings','files','audio','planner']) {
    assert.ok(WORKSPACES[tab]?.[1]);
    assert.ok(findFeatures('',tab).length,tab);
    assert.match(workspaceGuideHTML(tab),new RegExp(`data-open-guide="${tab}"`));
  }
  for(const item of FEATURES)assert.ok(WORKSPACES[item[3]],item[1]);
});
test('help finds concrete terms across categories without fuzzy unrelated results',()=>{
  assert.ok(findFeatures('Vimeo Patreon').some(x=>x[1]==='Vimeo → Patreon'));
  assert.ok(findFeatures('REPAIR').some(x=>x[1]==='Automatic repairs'));
  assert.ok(findFeatures('parallel').some(x=>x[3]==='settings'||x[3]==='planner'));
  assert.equal(findFeatures('a-made-up-function').length,0);
  assert.ok(findFeatures('voice','settings').every(x=>x[3]==='settings'));
  assert.equal(new Set(FEATURES.map(x=>x[1])).size,FEATURES.length);
});
test('settings search matches word beginnings without confusing motion with promotional or emotional',()=>{
  assert.ok(matchesSetting('Image motion and motion amount',['motion']));
  assert.ok(matchesSetting('Automatic repairs and retry limits',['repair']));
  assert.equal(matchesSetting('promotional overlays or emotional beats',['motion']),false);
  assert.equal(matchesSetting('voice',['voice','budget']),false);
});
test('all directly rendered settings controls have plain-language field help',async()=>{
  const source=await readFile(new URL('./story-studio.mjs',import.meta.url),'utf8');
  const settings=source.slice(source.indexOf('function settings()'),source.indexOf('function shotMotionLabel'));
  const ids=[...settings.matchAll(/<(?:input|select|textarea)\b[^>]*>/g)].filter(m=>!m[0].includes('type="hidden"')).map(m=>m[0].match(/\bid="([\w-]+)"/)?.[1]).filter(Boolean);
  assert.ok(ids.length>60);
  for(const id of ids)assert.ok(FIELD_HELP[id],`Missing explanation for ${id}`);
});
test('help states current production limits and separates rendering, storing and publishing',()=>{
  assert.match(findFeatures('render in cloud')[0][2],/120-second gate/);
  assert.match(findFeatures('Vimeo → Patreon')[0][2],/does not create the Patreon post/);
  assert.match(findFeatures('Production planner: import')[0][2],/not an executed production job/);
  assert.match(FIELD_HELP.settingVideoFps,/30 fps/);
  assert.match(findFeatures('Generate full video')[0][2],/Publishing is a separate action/);
});
test('organizing the UI retains the existing save, generation and project-safety wiring',async()=>{
  const source=await readFile(new URL('./story-studio.mjs',import.meta.url),'utf8');
  assert.match(source,/settingsDrafts\.restore\(project.id,root\);\s*settingsOrganizer=organizeSettings/);
  assert.match(source,/organizeFileTools\(root.querySelector/);
  assert.match(source,/settings-save-bar/);
  assert.match(source,/strategyChoices\.get\(project.id\) \|\| selectedStrategy\(project\)/);
  const guide=await readFile(new URL('./studio-guide.mjs',import.meta.url),'utf8');
  assert.doesNotMatch(guide,/\bfetch\s*\(|\blocalStorage\b|\bindexedDB\b|\bhelperJson\s*\(/);
});
