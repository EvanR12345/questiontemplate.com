const imageExtensions = new Map([
  ['image/png', 'png'],
  ['image/x-png', 'png'],
  ['image/jpeg', 'jpg'],
  ['image/jpg', 'jpg'],
  ['image/webp', 'webp'],
  ['image/avif', 'avif'],
  ['image/gif', 'gif'],
  ['image/bmp', 'bmp'],
  ['image/x-ms-bmp', 'bmp'],
  ['image/tiff', 'tiff'],
  ['image/svg+xml', 'svg'],
  ['image/vnd.microsoft.icon', 'ico'],
]);

function imageExtension(blob) {
  return imageExtensions.get(blob.type) || 'bin';
}

function referenceFilename(name, image, usedNames) {
  const stem = name.replace(/[^\w-]+/g, '_') || 'reference';
  const extension = imageExtension(image);
  let filename = `${stem}.${extension}`;
  let suffix = 2;
  while (usedNames.has(filename.toLowerCase())) {
    filename = `${stem}_${suffix++}.${extension}`;
  }
  usedNames.add(filename.toLowerCase());
  return filename;
}

export async function buildComicZip(zip, project, { renderPanel, readPanel, readReference }) {
  const snapshot = structuredClone(project);
  const images = {};
  for (const [index, panel] of snapshot.panels.entries()) {
    const image = snapshot.images[panel.id];
    if (!image) continue;
    const number = String(index + 1).padStart(3, '0');
    const source = await readPanel(image);
    const sourcePath = `panel-sources/${number}.${imageExtension(source)}`;
    const rendered = await renderPanel(panel, source);
    if (!rendered) throw new Error('A panel image could not be rendered.');
    zip.file(sourcePath, source);
    zip.file(`panels/${number}.png`, rendered);
    images[panel.id] = sourcePath;
  }

  const usedNames = new Set();
  for (const character of snapshot.characters) {
    if (!character.reference) continue;
    const image = await readReference(character.reference);
    const filename = referenceFilename(character.name, image, usedNames);
    zip.file(`characters/${filename}`, image);
  }

  zip.file('project.json', JSON.stringify({ ...snapshot, images }, null, 2));
  return zip.generateAsync({ type: 'blob' });
}
