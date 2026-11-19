import test from 'node:test';
import assert from 'node:assert/strict';
import { buildComicZip } from './manga-export.mjs';

function captureZip() {
  const files = new Map();
  return {
    file(path, data) {
      assert(!files.has(path), `Archive path was overwritten: ${path}`);
      files.set(path, data);
    },
    async generateAsync(options) {
      assert.deepEqual(options, { type: 'blob' });
      return files;
    },
  };
}

test('comic ZIP preserves sparse panel positions, exact source artwork and rendering', async () => {
  const panels = [
    { id: 'bridge', dialogue: 'Find the bridge.' },
    { id: 'before-dawn' },
    { id: 'dawn', sfx: 'WHOOSH' },
  ];
  const project = { name: 'Comic', panels, images: { bridge: 'local:first', dawn: 'local:last',
    'deleted-panel': 'local:orphan' }, characters: [] };
  const sources = {
    'local:first': new Blob(['original bridge'], { type: 'image/png' }),
    'local:last': new Blob(['original dawn'], { type: 'image/webp' }),
  };
  const rendered = {
    bridge: new Blob(['rendered bridge with dialogue'], { type: 'image/png' }),
    dawn: new Blob(['rendered dawn with SFX'], { type: 'image/png' }),
  };
  const files = await buildComicZip(captureZip(), project, {
    renderPanel: panel => rendered[panel.id],
    readPanel: image => sources[image],
    readReference: () => assert.fail('No references expected'),
  });

  assert.equal(files.size, 5);
  assert.equal(files.get('panels/001.png'), rendered.bridge);
  assert.equal(files.get('panels/003.png'), rendered.dawn);
  assert.equal(files.get('panel-sources/001.png'), sources['local:first']);
  assert.equal(files.get('panel-sources/003.webp'), sources['local:last']);
  assert.deepEqual(JSON.parse(files.get('project.json')), {
    ...project, images: { bridge: 'panel-sources/001.png', dawn: 'panel-sources/003.webp' },
  });
  assert.deepEqual(project.images, { bridge: 'local:first', dawn: 'local:last', 'deleted-panel': 'local:orphan' });
});

test('comic ZIP keeps reference bytes, file types and distinct names on Windows', async () => {
  const characters = [
    { id: '1', name: 'Iris North', reference: 'one' },
    { id: '2', name: 'IRIS_NORTH', reference: 'two' },
    { id: '3', name: 'Iris_North_2', reference: 'three' },
    { id: '4', name: 'Iris North', reference: 'four' },
    { id: '5', name: 'Unnamed' },
  ];
  const references = Object.fromEntries(
    ['one', 'two', 'three', 'four'].map((key, index) => [key, new Blob([key],
      { type: index === 3 ? 'image/jpeg' : 'image/png' })]),
  );
  const files = await buildComicZip(captureZip(), { panels: [], images: {}, characters }, {
    renderPanel: () => assert.fail('No panels expected'),
    readPanel: () => assert.fail('No sources expected'),
    readReference: reference => references[reference],
  });

  assert.equal(files.get('characters/Iris_North.png'), references.one);
  assert.equal(files.get('characters/IRIS_NORTH_2.png'), references.two);
  assert.equal(files.get('characters/Iris_North_2_2.png'), references.three);
  assert.equal(files.get('characters/Iris_North.jpg'), references.four);
  assert.deepEqual(JSON.parse(files.get('project.json')).characters, characters);
});

test('comic ZIP uses one source and metadata snapshot while a background result arrives', async () => {
  const project = {
    name: 'Initial name',
    panels: [{ id: 'changing', dialogue: 'Before' }, { id: 'missing', dialogue: '' }],
    characters: [{ id: 'guide', name: 'Original guide', reference: 'guide-before' }],
    images: { changing: 'local:initial' },
  };
  const source = new Blob(['original artwork'], { type: 'image/png' });
  const reference = new Blob(['original portrait'], { type: 'image/png' });
  let finishRead;
  const pendingSource = new Promise(resolve => { finishRead = resolve; });
  const result = buildComicZip(captureZip(), project, {
    readPanel(marker) {
      assert.equal(marker, 'local:initial');
      return pendingSource;
    },
    renderPanel(panel, artwork) {
      assert.equal(panel.dialogue, 'Before');
      assert.equal(artwork, source);
      return new Blob(['rendered original']);
    },
    readReference(marker) {
      assert.equal(marker, 'guide-before');
      return reference;
    },
  });

  project.name = 'Changed name';
  project.panels[0].dialogue = 'After';
  project.images.changing = 'local:replacement';
  project.images.missing = 'local:newly-completed';
  project.characters[0].reference = 'guide-after';
  finishRead(source);

  const files = await result;
  assert.equal(files.get('panel-sources/001.png'), source);
  assert.equal(files.get('characters/Original_guide.png'), reference);
  assert(!files.has('panels/002.png'));
  const manifest = JSON.parse(files.get('project.json'));
  assert.equal(manifest.name, 'Initial name');
  assert.equal(manifest.panels[0].dialogue, 'Before');
  assert.deepEqual(manifest.images, { changing: 'panel-sources/001.png' });
});
