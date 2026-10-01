// General dictionary lookup and inflections adapted from hexgrad/misaki/en.py;
// eSpeak conversion adapted from misaki/espeak.py (Apache-2.0).
// Changes: lightweight browser implementation, conservative context fallback,
// lazy compressed dictionaries, and conversion of untied browser IPA.
const dictionaries = new Map();
const WEAK = new Set('a an the am are as at be been being but by can could do does for from had has have he her him his i in is it its may me must my of on or our shall she should some than that their them there these they this those to us used was we were will with would you your'.split(' '));
const TAUS = 'AIOWYiuæɑəɛɪɹʊʌ';

export async function loadLexicon(language = 'a', fetcher = fetch) {
  const locale = language === 'b' ? 'gb' : 'us';
  if (!dictionaries.has(locale)) {
    const pending = (async () => {
      const response = await fetcher(new URL(`./vendor/english-${locale}.json.gz?v=english-2`, import.meta.url));
      if (!response.ok) throw new Error('Pronunciation dictionary could not load. Refresh and try again.');
      const stream = response.body.pipeThrough(new DecompressionStream('gzip'));
      const data = await new Response(stream).json();
      if (!data || typeof data !== 'object' || Object.keys(data).length < 50000) throw new Error('Pronunciation dictionary is incomplete. Refresh and try again.');
      return data;
    })();
    dictionaries.set(locale, pending);
    pending.catch(() => dictionaries.delete(locale));
  }
  return dictionaries.get(locale);
}

export function dictionaryPhonemes(word, dictionary, language = 'a') {
  const lower = word.toLowerCase().replaceAll('’', "'");
  // Avoid turning initialisms into words, or freezing weak/contextual words.
  if (WEAK.has(lower) || /^[A-Z]{2,4}$/.test(word) || !/^[a-z]+(?:'[a-z]*)?$/.test(lower)) return null;
  const get = key => Object.hasOwn(dictionary, key) ? dictionary[key] : null;
  const exact = get(lower);
  if (exact) return exact;
  const british = language === 'b';
  const plural = ps => !ps ? null : ps + ('ptkfθ'.includes(ps.at(-1)) ? 's' : 'szʃʒʧʤ'.includes(ps.at(-1)) ? (british ? 'ɪz' : 'ᵻz') : 'z');
  if (lower.endsWith("s'")) return dictionaryPhonemes(lower.slice(0, -1), dictionary, language);
  if (lower.endsWith("'s")) return plural(get(lower.slice(0, -2)));
  if (lower.endsWith('s') && !lower.endsWith('ss')) {
    const stem = get(lower.slice(0, -1)) || (lower.endsWith('ies') ? get(lower.slice(0, -3) + 'y') : lower.endsWith('es') ? get(lower.slice(0, -2)) : null);
    if (stem) return plural(stem);
  }
  if (lower.endsWith('ed')) {
    const stem = get(lower.slice(0, -1)) || (!lower.endsWith('eed') && get(lower.slice(0, -2)));
    if (stem) {
      const last = stem.at(-1);
      if (last === 't') return !british && TAUS.includes(stem.at(-2)) ? stem.slice(0, -1) + 'Tᵻd' : stem + (british ? 'ɪd' : 'ᵻd');
      return stem + (last === 'd' ? (british ? 'ɪd' : 'ᵻd') : 'pkfθʃsʧ'.includes(last) ? 't' : 'd');
    }
  }
  if (lower.endsWith('ing') && lower.length > 4) {
    const root = lower.slice(0, -3);
    const stem = get(root) || get(root + 'e') || (/([bcdgklmnprstvxz])\1$|ck$/.test(root) && get(root.slice(0, -1)));
    if (stem && !(british && 'əː'.includes(stem.at(-1)))) {
      return !british && stem.endsWith('t') && TAUS.includes(stem.at(-2)) ? stem.slice(0, -1) + 'Tɪŋ' : stem + 'ɪŋ';
    }
  }
  return null;
}

export function espeakToKokoro(ipa, language = 'a') {
  // The browser eSpeak API emits untied IPA; normalize to Kokoro v1's
  // training alphabet instead of letting the tokenizer delete IPA marks.
  let ps = ipa.replace(/\([a-z]{2}(?:-[a-z]+)?\)/g, '')
    .replace(/a\^?ɪ/g, 'I').replace(/a\^?ʊ/g, 'W')
    .replace(/e\^?ɪ/g, 'A').replace(/ɔ\^?ɪ/g, 'Y')
    .replace(/d\^?ʒ/g, 'ʤ').replace(/t\^?ʃ/g, 'ʧ')
    .replace(/ə\^l/g, 'ᵊl').replace(/ʲ/g, 'j')
    .replace(/ɚ/g, 'əɹ').replace(/r/g, 'ɹ').replace(/[xç]/g, 'k')
    .replace(/ɐ/g, 'ə').replace(/ɬ/g, 'l').replace(/̃/g, '')
    .replace(/(\S)\u0329/g, 'ᵊ$1').replace(/\u0329/g, '');
  if (language === 'b') ps = ps.replace(/e\^?ə/g, 'ɛː').replace(/iə/g, 'ɪə').replace(/ə\^?ʊ/g, 'Q');
  else ps = ps.replace(/o\^?ʊ/g, 'O').replace(/ɜːɹ/g, 'ɜɹ').replace(/ɜː/g, 'ɜɹ').replace(/ɪə/g, 'iə').replace(/ː/g, '');
  return ps.replace(/o/g, 'ɔ').replace(/ɾ/g, 'T').replace(/ʔ/g, 't').replace(/\^/g, '');
}
