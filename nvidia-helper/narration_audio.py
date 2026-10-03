"""Speech-safe story delivery and inexpensive local onomatopoeia effects.

Source narration is retained unchanged for the director. Standard mode records
sentence durations; connected mode estimates boundaries from model durations.
Only the text sent to the voice model is normalized. Recognized marked sound
effects become PCM in the same chapter WAV instead of spoken letters.
"""
import hashlib
import re

VERSION = 3
EFFECTS = {'bang', 'bam', 'boom', 'thud', 'click', 'beep', 'slash', 'whoosh', 'crash'}
FLOW_VERSION = 1

def connected_units(sections, pipeline, vocab, effects, speed, emphasis=()):
    """Bounded contextual speech, never truncating or changing source sentences.

    Several short sentences share one inference. Effects and emphasized sentences
    end a group; model duration estimates map their boundaries back to the source.
    Local Kokoro has no acting-instructions input or guaranteed pitch control.
    """
    pending, length, rate = [], 0, speed
    phrases = [p.strip().casefold() for p in emphasis if p.strip()]
    for index, sentence in enumerate(sections):
        weighted = any(p in sentence.casefold() for p in phrases)
        desired_rate = max(.5, speed * .96) if weighted else speed
        for segment in audio_segments(sentence, effects):
            if segment['kind'] != 'speech':
                if pending:
                    yield {'kind':'speech', 'pieces':pending, 'speed':rate}
                    pending, length = [], 0
                yield {**segment, 'index':index}
                continue
            if pending and desired_rate != rate:
                yield {'kind':'speech', 'pieces':pending, 'speed':rate}
                pending, length = [], 0
            rate = desired_rate
            for result in pipeline(segment['ttsText']):
                remaining = ''.join(c for c in result.phonemes if c in vocab)
                if not remaining.strip():
                    raise ValueError('Narration contains an unpronounceable section. Edit narration preview.')
                while remaining:
                    if len(remaining) > 250:
                        punctuation = [m.end() for m in re.finditer(r'[,;:.!?](?: |$)', remaining[:251])]
                        boundary = punctuation[-1] if punctuation else remaining.rfind(' ', 0, 251)
                        if boundary <= 0:
                            boundary = 250
                    else:
                        boundary = len(remaining)
                    part, remaining = remaining[:boundary].strip(), remaining[boundary:].lstrip()
                    if not part:
                        continue
                    if pending and length + 1 + len(part) > 250:
                        yield {'kind':'speech', 'pieces':pending, 'speed':rate}
                        pending, length = [], 0
                    pending.append({'index':index, 'phonemes':part})
                    length += len(part) + (1 if len(pending) > 1 else 0)
        if weighted and pending:
            yield {'kind':'speech', 'pieces':pending, 'speed':rate}
            pending, length = [], 0
        if not audio_segments(sentence, effects):
            if pending:
                yield {'kind':'speech', 'pieces':pending, 'speed':rate}
                pending, length = [], 0
            yield {'kind':'pause', 'duration':.1, 'index':index}
    if pending:
        yield {'kind':'speech', 'pieces':pending, 'speed':rate}


def create_connected_audio(text, sections, pipeline, engine, gate, target, voice, speed, effects, emphasis):
    import numpy as np
    import time, wave
    timings = [{'index':i,'text':s,'start':None,'end':None,
                'timingSource':'model-phoneme-duration alignment', 'delivery':audio_segments(s,effects)}
               for i,s in enumerate(sections)]
    offset, narration_rms, calls = 0., .035, 0
    def mark(index, begin, end):
        item = timings[index]
        if item['start'] is None: item['start'] = begin
        item['end'] = end
    tmp = target.with_suffix('.partial.wav')
    with wave.open(str(tmp), 'wb') as wav:
        wav.setparams((1,2,24000,0,'NONE','not compressed'))
        for unit in connected_units(sections,pipeline,engine.model.vocab,effects,speed,emphasis):
            gate('Connected narration')
            if unit['kind'] == 'speech':
                parts = unit['pieces']
                ps = ' '.join(p['phonemes'] for p in parts)
                raw, weights = engine.synthesize_timed(ps,voice,unit['speed'])
                pcm = np.frombuffer(raw,dtype='<f4')
                weights = np.asarray(weights,dtype=float)
                if not len(pcm) or not np.isfinite(pcm).all() or weights.shape != (len(ps),) or not np.isfinite(weights).all() or (weights <= 0).any():
                    raise RuntimeError('Connected voice returned invalid audio/timing; previous WAV preserved.')
                elapsed = len(pcm)/24000
                weights = weights/weights.sum()*elapsed
                cursor, at = 0, offset
                for n, part in enumerate(parts):
                    size = len(part['phonemes']) + (1 if n < len(parts)-1 else 0)
                    end = at + float(weights[cursor:cursor+size].sum())
                    mark(part['index'],at,end)
                    cursor, at = cursor+size, end
                narration_rms = float(np.sqrt(np.mean(pcm**2)))
                calls += 1
            else:
                pcm = effect_pcm(unit['effect'],narration_rms=narration_rms) if unit['kind']=='effect' else np.zeros(round(unit['duration']*24000),dtype='<f4')
                elapsed = len(pcm)/24000
                mark(unit['index'],offset,offset+elapsed)
            wav.writeframes((np.clip(pcm,-1,1)*32767).astype('<i2').tobytes())
            offset += elapsed
    if not offset or any(t['start'] is None or t['end'] is None for t in timings):
        raise RuntimeError('Narration timing is incomplete; previous WAV preserved.')
    tmp.replace(target)
    return {'path':str(target),'duration':offset,'sampleRate':24000,'sentences':timings,'paragraphs':[],
            'wordTimingAvailable':False,'timingSource':'model-phoneme-duration alignment; exact total PCM duration',
            'created':time.time(),'deliveryVersion':VERSION,'flowVersion':FLOW_VERSION,
            'soundEffects':effects,'narrationDelivery':'cinematic','inferenceCalls':calls,
            'emphasisPhrases':list(emphasis)}

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

def audio_segments(text, effects='subtle'):
    """Keep unsupported cries out of TTS, retaining their source and timing.

    A plain cue is an effect only on its own line. Ordinary prose ("a thud",
    "slash the rope") remains speech. Stars still mark recognized effects.
    """
    if effects not in ('subtle', 'off'):
        raise ValueError('Sound effects must be subtle or off.')
    # Normalize stretched cries before finding whole interjection tokens.
    cry = re.compile(r'\b(?:a+h+|a+g+h*|a+c+k+|u+g+h+|h+a+h*)\b', re.I)
    def append_speech(source):
        prepared = speech_text(source)
        pos = 0
        for match in cry.finditer(prepared):
            before = prepared[pos:match.start()].strip()
            if re.search(r'\w', before):
                result.append({'kind': 'speech', 'text': before, 'ttsText': before})
            result.append({'kind': 'pause', 'duration': .18, 'source': match.group(),
                           'reason': 'Unsupported vocal cry; safe pause instead of spelling or synthesized scream'})
            pos = match.end()
        after = prepared[pos:].strip()
        if re.search(r'\w', after):
            result.append({'kind': 'speech', 'text': after, 'ttsText': after})
    def append_effect(name, source):
        result.append({'kind': 'effect', 'effect': name, 'source': source} if effects == 'subtle'
                      else {'kind': 'pause', 'duration': .18, 'source': source, 'reason': 'Sound effects disabled'})
    cursor = 0
    result = []
    cue = r'\*{1,2}([^*\n]+)\*{1,2}|^[ \t]*[\"\']?([A-Za-z]+)[!?. \t]*[\"\']?[!?. \t]*$'
    for match in re.finditer(cue, text, re.M):
        name = effect_name(match[1] or match[2])
        if not name:
            continue
        append_speech(text[cursor:match.start()])
        append_effect(name, match.group())
        cursor = match.end()
    append_speech(text[cursor:])
    return result

def effect_pcm(name, sample_rate=24000, narration_rms=None):
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
    # Effects must not suddenly dominate the narrator. They used to peak at
    # .5 regardless of speech level. Keep a conservative cap and adapt down.
    peak = .08 if name in ('slash', 'whoosh') else .12
    sound = sound / max(1.0, float(np.max(np.abs(sound)))) * peak
    if narration_rms is not None:
        desired = max(.003, min(.04, float(narration_rms) * .6))
        rms = float(np.sqrt(np.mean(sound**2)))
        sound *= min(1.0, desired / max(rms, 1e-9))
    # A short tail separates consecutive impacts and avoids edit clicks.
    sound[-min(240, len(sound)):] *= np.linspace(1, 0, min(240, len(sound)))
    return np.concatenate([sound, np.zeros(round(.08 * sample_rate))]).astype('<f4')
