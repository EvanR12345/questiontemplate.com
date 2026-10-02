// Shared scene facts and literal visual actions, independent of the UI.
const clean = value => String(value || '').replace(/\\([*"'])/g, '$1').replace(/\*+/g, '').trim();
const named = (text, cast) => cast.filter(c => new RegExp(`\\b${c.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i').test(text)).map(c => c.id);
const sentences = text => clean(text).match(/[^.!?]+[.!?]+(?:[”"']|$)?|[^.!?]+$/g)?.map(s => s.trim()).filter(Boolean) || [];
export function storyBlocks(text) { return String(text).split(/\n\s*\n/).map(clean).filter(Boolean); }
export function inferSetting(text) {
  const match = clean(text).match(/\b(?:at|inside|beneath|under|within|in)\s+(?:the\s+|a\s+|an\s+)?(?:[\w'-]+\s+){0,5}(?:station|city|forest|castle|palace|ballroom|hall|room|street|market|school|tavern|temple|ruins|platform|village|house|courtyard|church|arena|garden|office|hospital|beach|ship)\b/i);
  return match ? match[0] : '';
}
export function planScenes(text, cast) {
  const groups = [];
  for (const block of storyBlocks(text)) {
    const setting = inferSetting(block);
    const previous = groups.at(-1);
    if (!previous || (setting && previous.setting && setting.toLowerCase() !== previous.setting.toLowerCase()) || /^(?:chapter\b|scene\b|later\b|meanwhile\b|the next (?:day|morning)\b)/i.test(block)) {
      groups.push({text:block,setting});
    } else { previous.text += ' ' + block; previous.setting ||= setting; }
  }
  return groups.map(({text:block,setting}, index) => {
    let recent = [];
    const frames = [];
    for (const sentence of sentences(block)) {
      const ids = named(sentence, cast);
      if (ids.length) recent = ids;
      const characters = ids.length ? ids : /\b(he|she|his|her|they|their)\b/i.test(sentence) ? [...recent] : [];
      // A sound alone is not a visible scene. Keep it beside the visible event.
      if (/^(?:bang|boom|crash|pow|thud)[.!?]*$/i.test(sentence)) {
        if (frames.length) frames.at(-1).sfx = clean(sentence).replace(/[.!?]+$/, '').toUpperCase();
        else frames.push({ source: sentence, prompt: '', characters: [], sfx: clean(sentence).toUpperCase() });
        continue;
      }
      if (frames.at(-1)?.prompt === '') {
        Object.assign(frames.at(-1), { source: frames.at(-1).source + ' ' + sentence, prompt: sentence, characters });
      } else frames.push({ source: sentence, prompt: sentence, characters, sfx: '' });
    }
    return { title: 'Scene ' + (index + 1), text: block, setting, frames };
  });
}
export function migrateScenes(project) {
  if (project.continuityVersion === 1) return;
  const blocks = planScenes(project.story, project.characters || []).map(scene => scene.text);
  if (!blocks.length) { project.continuityVersion = 1; return; }
  const groups = blocks.map((text, i) => ({ id: 'continuity-' + i, title: 'Scene ' + (i + 1), text, setting: inferSetting(text) }));
  for (const panel of project.panels) {
    const source = clean(panel.source);
    const index = blocks.findIndex(block => source && block.includes(source));
    if (index >= 0) panel.scene = index + 1;
  }
  project.scenes = groups;
  project.continuityVersion = 1;
}
export function sceneFor(panel, project) { return project.scenes[Number(panel.scene) - 1]; }
export function panelPrompt(panel, project, style) {
  const cast = panel.characters.map(id => project.characters.find(c => c.id === id)).filter(Boolean);
  let action = clean(panel.prompt);
  const single = cast.length === 1 ? cast[0].name : null;
  if (single) action = action.replace(/\b(?:he|she)\b/gi, single).replace(/\b(?:his|her)\b/gi, single + "'s");
  const people = cast.map(c => [c.name, clean(c.description), c.outfit ? 'wearing ' + clean(c.outfit) : ''].filter(Boolean).join(', '));
  const scene = sceneFor(panel, project);
  // Put the event before style so even older helpers retain the subject/action.
  return [action, ...people, clean(scene?.setting), clean(project.world), style,
    `${panel.shot}, ${panel.angle}, ${String(panel.mood || 'neutral').toLowerCase()} mood`,
    'single comic panel, no lettering'].filter(Boolean).join('. ');
}
export function continuityTarget(panel, project) {
  if (panel.continuity === false) return null;
  const first = project.panels.find(p => p.scene === panel.scene);
  if (!first || first.id === panel.id) return null;
  // Do not inject another character into an explicitly empty or different cast.
  const sameCast = first.characters.length === panel.characters.length && first.characters.every(id => panel.characters.includes(id));
  return sameCast ? first.id : null;
}
