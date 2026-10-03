"""Speech-safe story delivery and inexpensive local onomatopoeia effects.

Source narration is retained unchanged for the director and exact sentence timing.
Only the text sent to the voice model is normalized. Recognized marked sound
effects become PCM in the same chapter WAV instead of spoken letters.
"""
import hashlib
import re

VERSION = 2
EFFECTS = {'bang', 'bam', 'boom', 'thud', 'click', 'beep', 'slash', 'whoosh', 'crash'}

def effect_name(text):
    word = re.sub(r'[^a-z]', '', text.lower())
    short = re.sub(r'(.)\1+', r'\1', word)
    return next((name for name in EFFECTS if re.sub(r'(.)\1+', r'\1', name) == short), None)

def speech_text(text):
    text = str(text).replace('‘', "'").replace('’', "'").replace('“', '"').replace('”', '"')
    text = re.sub(r'[\u200b-\u200d\ufeff]', '', text)
    # Quotes around a word are punctuation; internal apostrophes are meaningful.
    text = re.sub(r"(?<!\w)['\"]|['\"](?!\w)", '', text)
    def stretched(match):
        word = match.group()
        compact = re.sub(r'(.)\1+', r'\1', word.lower())
        known = {'no': 'No', 'yes': 'Yes', 'stop': 'Stop', 'help': 'Help',
                 'ah': 'Ah', 'a': 'Ah', 'ack': 'Ah', 'ak': 'Ah', 'ag': 'Ah',
                 'agh': 'Ah', 'ugh': 'Ugh', 'ug': 'Ugh', 'ha': 'Ah', 'hah': 'Ah', 'oh': 'Oh'}
        if compact in ('ah', 'ha', 'hah', 'agh', 'ag', 'ak', 'ack', 'ugh', 'ug', 'oh') and re.search(r'([a-z])\1+', word, re.I):
            return known[compact]
        if re.search(r'([a-z])\1{2,}', word, re.I):
            return known.get(compact, re.sub(r'([a-z])\1{2,}', r'\1', word, flags=re.I).capitalize())
        return word
    text = re.sub(r'[A-Za-z]+', stretched, text)
    text = re.sub(r'!{2,}', '!', text).replace('*', '')
    return text.strip()


def pronunciation_text(text):
    """Known vocal interjections use Kokoro/Misaki's explicit IPA override.

    Keep the visible script clean. Never spell a scream as an acronym, and do
    not apply this to names, contractions, or words merely containing 'ah'.
    """
    phonemes = {'ah': 'ɑ', 'ugh': 'ʌh', 'oh': 'O', 'huh': 'hʌ', 'hah': 'hɑ'}
    return re.sub(r'\b(Ah|Ugh|Oh|Huh|Hah)\b',
                  lambda m: '[' + m.group() + '](/' + phonemes[m.group().lower()] + '/)',
                  text, flags=re.I)

def audio_segments(text):
    """Only known onomatopoeia inside stars is an effect, never arbitrary emphasis."""
    cursor = 0
    result = []
    for match in re.finditer(r'\*{1,2}([^*\n]+)\*{1,2}', text):
        name = effect_name(match[1])
        if not name:
            continue
        before = speech_text(text[cursor:match.start()])
        if re.search(r'\w', before):
            result.append({'kind': 'speech', 'text': before, 'ttsText': pronunciation_text(before)})
        result.append({'kind': 'effect', 'effect': name, 'source': match.group()})
        cursor = match.end()
    after = speech_text(text[cursor:])
    if re.search(r'\w', after):
        result.append({'kind': 'speech', 'text': after, 'ttsText': pronunciation_text(after)})
    return result

def effect_pcm(name, sample_rate=24000):
    """Deterministic synthesized effects; no downloads or paid sound API."""
    import numpy as np
    durations = {'bang': .27, 'bam': .35, 'boom': 1.1, 'thud': .25,
                 'click': .12, 'beep': .28, 'slash': .32, 'whoosh': .45, 'crash': .7}
    duration = durations[name]
    t = np.arange(round(duration * sample_rate), dtype=np.float64) / sample_rate
    rng = np.random.default_rng(int(hashlib.sha256(name.encode()).hexdigest()[:8], 16))
    noise = rng.standard_normal(len(t))
    if name == 'beep':
        sound = np.sin(2 * np.pi * 1100 * t) * np.minimum(t / .01, 1) * np.minimum((duration - t) / .015, 1)
    elif name in ('slash', 'whoosh'):
        sound = np.convolve(noise, np.ones(6) / 6, mode='same') * np.sin(np.pi * t / duration)**2
    else:
        bass = np.sin(2 * np.pi * (65 * t - 18 * t**2))
        low_noise = np.convolve(noise, np.ones(20) / 20, mode='same')
        envelope = np.exp(-t * (4 if name == 'boom' else 17))
        sound = (.55 * bass + .7 * low_noise + .25 * noise * np.exp(-t * 90)) * envelope
        sound[:min(24, len(sound))] *= np.linspace(0, 1, min(24, len(sound)))
    sound = sound / max(1.0, float(np.max(np.abs(sound)))) * .5
    # A short tail separates consecutive impacts and avoids edit clicks.
    sound[-min(240, len(sound)):] *= np.linspace(1, 0, min(240, len(sound)))
    return np.concatenate([sound, np.zeros(round(.08 * sample_rate))]).astype('<f4')
