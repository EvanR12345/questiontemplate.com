import test from 'node:test';
import assert from 'node:assert/strict';
import { phonemize } from './phonemize.mjs';
import { pronunciationRules, speechText } from './pronunciation.mjs';
import { builtinPhonemes } from './pronunciation-lexicon.mjs';

test('protagonist forms bypass the failing grapheme converter in both accents', async () => {
  for (const language of ['a', 'b']) {
    for (const word of ['protagonist', 'PROTAGONIST', 'Protagonists', "protagonist's", 'protagonist’s', "protagonists'", 'protagonists’']) {
      let called = false;
      const actual = await phonemize(word + '.', language, true, async () => { called = true; throw new Error('Must not guess this word.'); });
      assert.equal(actual, builtinPhonemes(word) + '.');
      assert.equal(called, false);
    }
  }
});
test('neighbor words, punctuation, Unicode boundaries and repeated exceptions survive', async () => {
  const seen = [];
  const convert = async text => { seen.push(text); return [text.trim()]; };
  const result = await phonemize('The protagonist meets protagonists, then the protagonist’s friends.', 'a', false, convert);
  assert.equal(result, 'The pɹəˈtæɡənɪst meets pɹəˈtæɡənɪsts, then the pɹəˈtæɡənɪsts fɹiends.');
  assert.equal(seen.join('').replace(/\s+/g, ' ').trim(), 'The meets then the friends');
  for (const source of ['antiprotagonist', 'protagonistic', 'éprotagonist', 'protagonisté', 'protagonist2']) {
    const result = await phonemize(source, 'a', false, async text => [text]);
    assert.ok(!result.includes('ˈtæɡ'));
  }
});
test('custom spoken spellings override the built-in entry without changing source', async () => {
  const source = 'The protagonist wins.';
  const spoken = speechText(source, pronunciationRules('protagonist = hero'));
  let seen;
  const result = await phonemize(spoken, 'a', false, async text => { seen = text; return ['ðə hɪɹoʊ wɪnz']; });
  assert.equal(source, 'The protagonist wins.');
  assert.ok(seen.includes('hero'));
  assert.equal(result, 'ðə hɪɹoʊ wɪnz.');
});
