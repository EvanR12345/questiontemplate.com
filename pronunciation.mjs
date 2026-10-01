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
export function speechText(source, rules = []) {
  let text = source;
  for (const rule of rules) text = text.replace(rule.pattern, (_, prefix) => prefix + rule.spoken);
  text = text.trim();
  // A mid-sentence chunk still needs a clear acoustic end. Never truncate PCM
  // or cut off word tails; the model receives an explicit terminal punctuation.
  if (text && !/[.!?,;:]["'”’)]?$/.test(text)) text += '.';
  return text;
}
