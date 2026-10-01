// Reviewed whole-word exceptions, expressed in Kokoro's supported IPA symbols.
// Stress on TAG; voiced IPA ɡ is required (ASCII g is not in Kokoro's vocab).
// Reference: https://dictionary.cambridge.org/pronunciation/english/protagonist
export const BUILTIN_WORD_PATTERN = String.raw`(?<![\p{L}\p{N}])protagonist(?:s['’]?|['’]s)?(?![\p{L}\p{N}])`;
export function builtinPhonemes(word) {
  const normalized = word.toLowerCase().replaceAll('’', "'");
  if (normalized === 'protagonist') return 'pɹəˈtæɡənɪst';
  if (['protagonists', "protagonist's", "protagonists'"].includes(normalized)) return 'pɹəˈtæɡənɪsts';
  return null;
}
