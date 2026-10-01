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
  text = text.trim();
  // Only close the actual script ending. Internal batches retain the author's
  // punctuation rather than inventing a sentence ending after every chunk.
  if (final && text && !/[.!?,;:]["'”’)]?$/.test(text)) text += '.';
  return text;
}
