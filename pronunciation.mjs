export function pronunciationRules(value = '') {
  return value.split(/\r?\n/).flatMap(line => {
    const separator = line.indexOf('=');
    if (separator < 1) return [];
    const word = line.slice(0, separator).trim(), spoken = line.slice(separator + 1).trim();
    if (!word || !spoken) return [];
    const escaped = word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return [{ pattern: new RegExp('(^|[^\\p{L}\\p{N}])' + escaped + '(?=$|[^\\p{L}\\p{N}])', 'giu'), spoken }];
  });
}
export function speechText(source, rules = [], final = true) {
  let text = source;
  for (const rule of rules) text = text.replace(rule.pattern, (_, prefix) => prefix + rule.spoken);
  // Stars mark emphasis / onomatopoeia in stories, not spoken instructions.
  // Remove markers even when a long script splits a pair across batches.
  text = text.replace(/\*/g, '').replace(/[‘’]/g, "'")
    .replace(/[\u200B-\u200D\uFEFF]/g, '').trim();
  // Elongated shouts are words, never acronyms spelled letter by letter.
  text = text.replace(/[A-Za-z]+/g, word => {
    if (!/([a-z])\1{2,}/i.test(word)) return word;
    const compact = word.toLowerCase().replace(/(.)\1+/g, '$1');
    const known = {no:'No', yes:'Yes', stop:'Stop', help:'Help', ah:'Ah', a:'Ah',
      ack:'Ah', ak:'Ah', ag:'Ah', agh:'Ah', ugh:'Ugh', ug:'Ugh', ha:'Ah', oh:'Oh', bom:'Boom'};
    return known[compact] || word.replace(/([a-z])\1{2,}/gi, '$1').toLowerCase();
  }).replace(/!{2,}/g, '!');
  if (!/[\p{L}\p{N}]/u.test(text)) return '';
  // Only close the actual script ending. Internal batches retain the author's
  // punctuation rather than inventing a sentence ending after every chunk.
  if (final && text && !/[.!?,;:]["'”’)]?$/.test(text)) text += '.';
  return text;
}
