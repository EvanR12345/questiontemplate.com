import test from 'node:test';
import assert from 'node:assert/strict';
import { planScenes, panelPrompt, migrateScenes, continuityTarget, soundOnly } from './story-planner.mjs';
const cast = [{id:'m',name:'Mira',description:'short black hair',outfit:'red coat'}];
test('sentences share their paragraph scene and pronouns retain the named cast', () => {
  const scenes = planScenes('Mira waits at the broken station. She raises a sword.\n\nRain falls in the city.', cast);
  assert.equal(scenes.length, 2);
  assert.equal(scenes[0].frames.length, 2);
  assert.deepEqual(scenes[0].frames[1].characters, ['m']);
  assert.match(scenes[0].setting, /station/);
});
test('sound effects stay attached to a visible event', () => {
  const [scene] = planScenes('*Bang*! A man drops his cup.', []);
  assert.equal(scene.frames.length, 1);
  assert.equal(scene.frames[0].prompt, 'A man drops his cup.');
  assert.match(scene.frames[0].sfx, /BANG/);
});
test('action comes first, appearance and shared location are supplied, pronoun resolved', () => {
  const project = {characters:cast,scenes:[{setting:'broken station at night'}],world:'blue moonlight'};
  const p = {prompt:'She raises her sword.',characters:['m'],scene:1,shot:'Medium',angle:'Eye level',mood:'Tense'};
  const prompt = panelPrompt(p, project, 'comic illustration');
  assert.ok(prompt.startsWith("Mira raises Mira's sword."));
  for (const detail of ['black hair','red coat','station','blue moonlight']) assert.ok(prompt.includes(detail));
});
test('old projects retain edited prompts and N/A selections when scenes are regrouped', () => {
  const p = {source:'She raises a sword.',prompt:'My edited action',characters:[],scene:2};
  const project = {story:'Mira waits. She raises a sword.',panels:[p],scenes:[]};
  migrateScenes(project);
  assert.equal(p.scene, 1); assert.equal(p.prompt,'My edited action'); assert.deepEqual(p.characters,[]);
});
test('scene anchors stay within scene and matching cast; opting out works', () => {
  const a={id:'a',scene:1,characters:['m']}, b={id:'b',scene:1,characters:['m']};
  const project={panels:[a,b]};
  assert.equal(continuityTarget(b,project),'a');
  b.characters=[]; assert.equal(continuityTarget(b,project),null);
  b.characters=['m']; b.continuity=false; assert.equal(continuityTarget(b,project),null);
});

test('sound-only panels never become the scene anchor', () => {
  const sound={id:'s',scene:1,characters:[],prompt:'Bang!'};
  const man={id:'m',scene:1,characters:[],prompt:'A man falls onto the floor.'};
  const next={id:'n',scene:1,characters:[],prompt:'The man reaches toward the door.'};
  assert.equal(continuityTarget(man,{panels:[sound,man,next]}),null);
  assert.equal(continuityTarget(next,{panels:[sound,man,next]}),'m');
  assert.ok(soundOnly('"Aaaaacckkkkkkk!!"'));
  assert.equal(soundOnly('A woman screams at a falling man.'),false);
});

test('empty cast selections distinguish a room from unnamed people', () => {
  const room={id:'r',scene:1,characters:[],prompt:'A room with a wooden floor.'};
  const people={id:'p',scene:1,characters:[],prompt:'A woman sees a man falling.'};
  assert.equal(continuityTarget(people,{panels:[room,people]}),null);
  room.prompt='A man looks out the window.';
  assert.equal(continuityTarget(people,{panels:[room,people]}),null);
});

test('unnamed foreground people are reinforced without adding people to empty scenes', () => {
  const project={characters:[],scenes:[{setting:'inside a room'}]};
  const panel={prompt:'A woman screams as a man falls.',characters:[],scene:1,shot:'Wide',angle:'Eye level'};
  assert.match(panelPrompt(panel,project,'comic illustration'),/people in the foreground: man and woman/);
  panel.prompt='An empty room.';
  assert.doesNotMatch(panelPrompt(panel,project,'comic illustration'),/foreground/);
});
