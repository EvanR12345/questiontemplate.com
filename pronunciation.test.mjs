import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { phonemize } from './phonemize.mjs';
import { dictionaryPhonemes, espeakToKokoro, loadLexicon } from './english-phonemes.mjs';
import { pronunciationRules, speechText } from './pronunciation.mjs';
import { prepareBatches, splitText } from './audio-core.mjs';
const dictionaries = Object.fromEntries(['a', 'b'].map(language => [language,
  JSON.parse(gunzipSync(readFileSync(new URL(`./vendor/english-${language === 'a' ? 'us' : 'gb'}.json.gz`, import.meta.url))))]));
test('broad dictionaries cover ordinary words, both accents and general inflections', async () => {
  for (const language of ['a', 'b']) {
    const dictionary = dictionaries[language];
    assert.ok(Object.keys(dictionary).length > 50000);
    for (const word of ['party', 'protagonist', 'water', 'city', 'battle', 'hello', 'knight', 'dragon', 'castle', 'adventure', 'magic']) {
      assert.equal(dictionaryPhonemes(word, dictionary, language), dictionary[word]);
      assert.ok(dictionary[word]);
      assert.equal(await phonemize(word + '.', language, true, () => { throw new Error('Known words should use the dictionary'); }, dictionary), dictionary[word] + '.');
    }
    assert.equal(dictionaryPhonemes('parties', dictionary, language), dictionary.party + 'z');
    assert.equal(dictionaryPhonemes('protagonists', dictionary, language), dictionary.protagonist + 's');
    assert.equal(dictionaryPhonemes('Party’s', dictionary, language), dictionary.party + 'z');
    assert.equal(dictionaryPhonemes("parties'", dictionary, language), dictionary.party + 'z');
    assert.equal(dictionaryPhonemes('returned', dictionary, language), dictionary.return + 'd');
    assert.equal(dictionaryPhonemes('walking', {walk: 'wˈɔk'}, language), 'wˈɔkɪŋ');
    assert.equal(dictionaryPhonemes('walked', {walk: 'wˈɔk'}, language), 'wˈɔkt');
    assert.equal(dictionaryPhonemes('wanted', {want: 'wˈɑnt'}, language), language === 'a' ? 'wˈɑntᵻd' : 'wˈɑntɪd');
    assert.equal(dictionaryPhonemes('waiting', {wait: 'wˈAt'}, language), language === 'a' ? 'wˈATɪŋ' : 'wˈAtɪŋ');
  }
});
test('unknown, contextual, acronym and Unicode words remain in fallback phrases', async () => {
  const dictionary = dictionaries.a, seen = [];
  const convert = async text => { seen.push(text.trim()); return ['həlˈoʊ']; };
  const output = await phonemize('The party, the protagonist’s adventure. Zyrava éparty API record read.', 'a', false, convert, dictionary);
  assert.ok(output.includes(dictionary.party + ', '));
  assert.ok(output.includes(dictionary.protagonist + 's ' + dictionary.adventure + '. '));
  assert.equal(seen.at(-1), 'Zyrava éparty API record read');
  for (const word of ['record', 'the', 'API', 'éparty', 'party2']) assert.equal(dictionaryPhonemes(word, dictionary), null);
  assert.equal(await phonemize('party-party!', 'a', false, convert, dictionary), dictionary.party + ' ' + dictionary.party + '!');
});
test('fallback IPA uses Kokoro v1 diphthongs, rhotics, affricates and flaps', () => {
  assert.equal(espeakToKokoro('pˈɑːɹɾi həlˈoʊ wˈɔːɾɚ tʃˈeɪndʒ aɪ aʊ ɔɪ ɜː l̩', 'a'), 'pˈɑɹTi həlˈO wˈɔTəɹ ʧˈAnʤ I W Y ɜɹ ᵊl');
  assert.equal(espeakToKokoro('pˈɑːti həlˈəʊ tʃˈeɪndʒ', 'b'), 'pˈɑːti həlˈQ ʧˈAnʤ');
});
test('weak words keep neighboring context instead of sounding like isolated words', async () => {
  const seen = [];
  const result = await phonemize('The party went to a castle', 'a', false, async text => {
    seen.push(text);
    if (text === 'The party') return ['ðə pˈɑːɹɾi'];
    if (text === 'party went to a castle') return ['pˈɑːɹɾi wɛnt tʊ ɐ kˈæsəl'];
    throw new Error('Unexpected missing word context: ' + text);
  }, {party: 'pˈɑɹTi', castle: 'kˈæsᵊl'});
  assert.deepEqual(seen, ['The party', 'party went to a castle']);
  assert.equal(result, 'ðə pˈɑɹTi wɛnt tʊ ə kˈæsᵊl');
});
test('custom spellings run first and the original script is preserved', async () => {
  const source = 'The protagonist wins.';
  const spoken = speechText(source, pronunciationRules('protagonist = hero'));
  const result = await phonemize(spoken, 'a', false, async () => ['ðə'], dictionaries.a);
  assert.equal(result, 'ðə ' + dictionaries.a.hero + ' ' + dictionaryPhonemes('wins', dictionaries.a) + '.');
  assert.equal(source, 'The protagonist wins.');
});
test('only the last recursive batch gets synthetic final punctuation', async () => {
  const source = 'A party started and everyone walked toward the castle without stopping';
  const chunks = [...splitText(source, 20)];
  assert.equal(chunks.join(''), source);
  assert.ok(chunks.every((chunk, i) => i === chunks.length - 1 || /\s$/.test(chunk)));
  const spoken = [], items = [];
  for await (const item of prepareBatches(source, text => ({input_ids: {dims: [1, text.length + 2]}}), async (text, language, final) => {
    const result = speechText(text, [], final); spoken.push({text, final, result}); return result;
  }, 'a', 25)) items.push(item);
  assert.equal(items.map(item => item.text).join(''), source);
  for (const item of items.slice(0, -1)) assert.ok(spoken.some(call => call.text === item.text && !call.final && !call.result.endsWith('.')));
  assert.ok(spoken.some(call => call.text === items.at(-1).text && call.final && call.result.endsWith('.')));
  assert.deepEqual([...splitText('protagonist', 3)], ['protagonist']);
  await assert.rejects(async () => { for await (const _ of prepareBatches('protagonist', text => ({input_ids: {dims: [1, 999]}}), async text => text, 'a', 4)) {} }, /word is too long/);
});
test('compressed dictionaries load once per accent and retry after failure', async () => {
  let calls = 0;
  const fetcher = async url => { calls++; const file = new URL(url); file.search = ''; return new Response(readFileSync(file), {status: 200}); };
  const a = await loadLexicon('a', fetcher);
  assert.equal(a.party, dictionaries.a.party);
  assert.equal(await loadLexicon('a', fetcher), a);
  assert.equal(calls, 1);
  await assert.rejects(loadLexicon('b', async () => new Response('', {status: 404})), /could not load/);
  const b = await loadLexicon('b', fetcher);
  assert.equal(b.party, dictionaries.b.party);
  assert.equal(calls, 2);
});
