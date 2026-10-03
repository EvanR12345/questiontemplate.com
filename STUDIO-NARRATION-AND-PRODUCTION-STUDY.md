# Narration and two-hour production study — October 3, 2026

## Decision

Do **not** start a two-hour production yet. Keeping the existing image model,
resolution, references and image frequency within $2–4 is plausible after
removing idle GPU rental, but has not been demonstrated at this scale. The
previous images still contain unresolved visual flags, so a small assumed repair
allowance cannot be presented as an established quality result.

No paid API generations or GPU runs were made for this study. All existing
chapter audio, images and the finished video are preserved. Retained Runpod
storage continues billing even while workers are stopped.

## What was inspected

Ten YouTube videos, two per channel, using their visible transcripts. The review
covered the openings, a passage around five minutes and a passage halfway through
the captured transcript. It was **not** a complete viewing or listening of all
ten videos. Automatically generated captions can misspell names. Two captures
hit the browser's 2,000-segment limit; those are explicitly partial. The other
captures also are not proof that every second or chapter was inspected.

The reference audio itself was not supplied as a file. The voice implementation
uses the user's written description; this study does not establish an exact
audible match with any channel's narrator.

| Channel | Video | Observed script structure |
| --- | --- | --- |
| Manhwa For You | [Every Genius Trained Magic for Years While He Mastered ANY SPELL After Seeing It Once!](https://www.youtube.com/watch?v=6nYQgJxSDeE) | Opens with future magical achievements, establishes the replacement identity, then rewinds around 1:10. Dialogue, action and descriptions are condensed into third-person narration. |
| Manhwa For You | [While Geniuses Needed Years to Master Skills, He Maxed Any Skill Instantly?!](https://www.youtube.com/watch?v=kQjjmvgbSc4) | Record-breaking achievement hook, question, then rewind around 0:43. Later passages retain important purchases and combat statistics while paraphrasing surrounding explanation. |
| Junkie's Manhwa | [He Was Reborn as a Genius Who Could Master Every Element](https://www.youtube.com/watch?v=ZxAmjvUiA94) | Starts chronologically with death and a conversation with a god. Speech and reactions become indirect narration. It concerns Michael, a different story from the requested Vaughn reference. |
| Junkie's Manhwa | [Called Weak at Home, He Froze a Giant Magma Beast With One Slash of His Sword](https://www.youtube.com/watch?v=uppXQTUGedA) | Begins with training failure and family conflict. It compresses dialogue and past training into narrated exchanges rather than starting with a future victory. |
| Manhwa Fresh | [He Eats Monster Cores and Receives Their Power!](https://www.youtube.com/watch?v=AxnmJZXPQL0) | Starts with the power premise and unusual outcome, then rewinds. Years of training are summarized quickly; later battles retain cause and effect but use lively commentary. |
| Manhwa Fresh | [Kicked Out of the Academy, He Unlocks the Crazy Cheat System and Dominates Everyone!](https://www.youtube.com/watch?v=7NwVJmuRAtc) | Opens on failure, motivation and recruitment. Earlier years and conversations are summarized; later game-system choices still receive substantial explanation. Capture stops around 4:13 despite the longer video. |
| Tobs Manhwa | [His Party Tried to Kill Him But He Came Back 100x STRONGER for Revenge!](https://www.youtube.com/watch?v=cgQuVO6OeSQ) | Preview of a powerful battle, explanation of betrayal and abilities, then chronological restart around 1:14. Narrator commentary is more casual than the requested restrained voice. |
| Tobs Manhwa | [When a Modern Man Reborn as a Medieval Mage Genius And Shows NO MERCY!](https://www.youtube.com/watch?v=uMCO00dP9Hs) | Future battlefield reversal leads into reincarnation and six years of training, before returning to the journey. Condensed third-person recap mixed with jokes. |
| KAI Manhwa Recap | [SSS Talent Odds: 1 in a Billion. Only 30 Exist Worldwide. I Reborn and Pulled 10 in a Row!](https://www.youtube.com/watch?v=nqZgKu8IMGU) | First-person chronological narration, lengthy explanation of ranks and probabilities, and retained system announcements. More detailed and closer to story reading than the strongest recap examples. |
| KAI Manhwa Recap | [They Mocked My Weak Summoner Class—Then I Pulled an SSS Class, SSS Divine Pet, and SSS Talents!](https://www.youtube.com/watch?v=37xvsC562XQ) | Immediate conflict hook followed by first-person chronology, dialogue and rules. Detailed rather than consistently compressed. Capture stops around 4:27 at 2,000 segments. |

### Duration findings

The requested Manhwa For You video lasts **2:06:41**, not six minutes. Its early
ship/revenge/rebirth passage corresponding approximately to the first two saved
chapters runs from **1:10 to 5:13**, about **4:03**. Our saved narration runs
approximately **17:30**, plus a separate 30-second intro. These are approximate
story-boundary comparisons, not verified identical chapter edits.

That difference comes mainly from **rewriting a condensed recap**, with some
contribution from speaking pace. It is not a fourfold voice-speed problem.
The first-minute caption word counts were about 181 and 185 for the two Manhwa
For You samples. Counts are approximate: caption segments can cross the minute
boundary, and are not audio-derived speech-rate measurements.

Do not conclude that every channel shortens the same source by the same ratio.
Original chapter coverage is unknown for the other nine samples. KAI's samples
show that a channel called a recap can retain substantial novel-like detail.

### Recommended script format

Keep **Original story** and **Recap rewrite** separate. Never replace the author's
text silently. A future recap pass should produce an editable script plus a
source-beat coverage map; retain causal events, identity changes, important
dialogue, objects and major progression. Condense repeated reactions, filler,
redundant explanations and most exchanges into indirect speech.

A 30-second intro should use an approximately 65–80-word factual hook: contrast
between expectation and unusual ability, the central identity/rebirth reveal,
then the unresolved question. Generate the real audio before aligning its
visuals. Avoid a title card, generic welcome, invented accomplishments or
reading chapter labels. Use only verified story facts from the selected range.

Do not copy the channels' scripts. Their intros also are not all restrained;
Tobs' jokier style is not the voice target requested here.

## Implemented voice improvement

**Settings → Voice → Narration delivery → Restrained cinematic** groups short
sentences into connected synthesis, using the existing Kokoro model and selected
voice. A maximum of 250 phonemes per request preserves the existing conservative
memory limit. Long passages split at natural punctuation or word boundaries;
no content is truncated.

Optional emphasis phrases slow matching sentences by 4%. They do not provide
word-level acting, pitch control or guaranteed emphasis on only the phrase.
The model does not accept the user's descriptive acting instructions. This
mode improves the context supplied to synthesis; perceived voice quality still
requires listening to the audition.

Pure unsupported cries remain short pauses; recognized isolated/marked sound
effects remain quiet effects rather than spoken letter sequences. The source
script stays available to the director. Every chapter still creates one WAV.

Sentence boundaries in connected mode use **model phoneme-duration estimates**
normalized to the actual PCM duration. Total duration is exact; these are not
forced-aligned words. Standard sentence mode remains available. Saved audio and
video are not automatically regenerated when auditioning.

Four real, free auditions were generated: Michael and Fenrir, each in standard
and connected modes. Connected versions used four inference calls for seven
sentences. Duration was 21.875 seconds for Michael and 19.700 for Fenrir at 1×.
Neither subjective similarity nor superior acting has been certified.

For stronger instruction-driven delivery, [Qwen3-TTS's official documentation](https://github.com/QwenLM/Qwen3-TTS/blob/main/README.md)
lists instruction control for **1.7B CustomVoice** and **1.7B VoiceDesign**.
The 0.6B CustomVoice variant does not list that control. A designed original
voice could follow the tone brief without copying a specific narrator. It needs
a bounded benchmark of long-form stability, pronunciation, throughput and GPU
cost before becoming the default. No new model was downloaded or run here.

[OpenAI's speech guide](https://developers.openai.com/api/docs/guides/text-to-speech)
also documents instruction-controlled speech, but a paid speech service would
need its own verified price and budget. It is not included in the $2–4 estimates
below. The existing API credential's grants were not expanded.

## Measured production baseline

These values come from the saved two-chapter production receipts, not a new GPU
benchmark. Old failed attempts and idle time must not be confused with successful
model inference.

| Stage | Measured two-chapter work | Meaning |
| --- | ---: | --- |
| Successful image backend time | 3:55 | 103 final story images, approximately 2.28 seconds/image; excludes queue overhead. |
| Successful story-image queue jobs | 10:28 | Approximately 6.10 seconds/image including transfer, setup and saving. |
| Completed chapter QC jobs | 24:13 | Sequential checks and overhead; important current bottleneck. |
| Latest local narration repair | 1:24 | Both chapter WAVs, using the existing voice model. |
| Latest local chapter rendering and assembly | 14:49 | 18-minute output with the repaired smooth motion. |
| Fresh GPU rental windows | 32:52 | Includes startup, idle and failed work; approximately $2.111/hour including pod disk. |

The existing visual status is **42 passed, 21 review-required, 40 failed**.
These are automated flags, not proof that 61 images are unusable. They nevertheless
prevent claiming a proven small repair rate or an approved consistent output.

At the same visual cadence, two hours requires roughly **702 story images** plus
intro assets. Linear scaling gives approximately **71 minutes for the image
queue**, **165 minutes for existing sequential QC jobs**, and **101 minutes for
local smooth rendering**. These are projections, not guaranteed completion times.
The fastest backend-only figure is not an end-to-end production estimate.

## Conditional cost scenarios

All amounts are USD. Formula: measured image queue time × assumed image repair
factor + startup allowance, multiplied by rental rate, plus director/QC API and
daily storage allowance. The existing API spending scales to about $0.65 for
120 minutes; future recap work, extra repairs and expressive voice inference may
add cost. Free OpenAI quota is not assumed.

| Scenario | Assumptions | Approximate two-hour cost |
| --- | --- | ---: |
| Existing worker, GPU active only for images | 71.4-minute queue, 15% extra image work, five-minute startup, $2.111/hour | **$3.88** including ~$0.65 API and ~$0.17 storage |
| Lower-rate RTX 5090 | Same throughput and repair assumptions, approximately $1/hour with pod disk | **$2.27** including API/storage |
| RTX 4090 sensitivity case | Queue twice as slow, same repair assumptions, approximately $0.76/hour with pod disk | **$2.96** including API/storage |
| Existing process scaled without removing rental idle | Existing fresh rental and API totals scaled to two hours | **About $8.53 before additional repairs/storage** |

The 15% repair figure is a scenario assumption, **not measured acceptance**.
Current [Runpod advertised GPU prices](https://www.runpod.io/pricing) list RTX
5090 at $0.99/hour and RTX 4090 at $0.74/hour. Specific capacity, region, pod disk,
template and total checkout price must be checked before provisioning. Lower
prices do not prove identical speed. The 4090 case is a sensitivity calculation,
not a 4090 benchmark.

Our existing 71-GB volume is in US-NE-1. The catalog snapshot did not show these
cheaper GPUs available there; migrating/rebuilding the cache elsewhere adds
time, transfer and storage costs. A GPU advertised elsewhere cannot simply
attach this regional volume. No infrastructure was moved for this study.

## Quality-preserving optimization order

1. Prepare and validate every prompt and reference before starting GPU rental.
   Generate only images during rental; stop it before remote director/QC work
   and local rendering. Group bounded repair requests into a second short run
   only if the remaining budget covers it.
2. Preserve FLUX.2 Klein 4B FP8, four steps, 1344×768, illustration style,
   reference conditioning and the current ~5.9 images/minute. Do not reduce
   resolution, steps or image frequency merely to meet a price claim.
3. Reuse the existing exact-input director cache and reference upload cache.
   Record cache hits and measured transfer time. A cache is not a reason to reuse
   a wrong appearance state; changes to identity, outfit, source, prompts or
   workflow must invalidate the relevant results.
4. Check failures specifically. Separate permanent identity from intentional
   clothing changes; use only relevant reference crops, one simultaneous action
   per image, explicit character roles/positions and grounded story evidence.
   Shorter, clearer prompts can reduce contradictions; better results must be
   measured rather than assumed.
5. Benchmark bounded API QC concurrency and batches of independent shots.
   The current spend ledger allows one pending reservation, so safe parallel
   requests first need per-request reservations, cancellation and rate-limit
   handling. Do not blindly launch three model copies. GPU saturation alone does
   not demonstrate improved images per dollar.
6. Keep validated QC coverage. Cache checks of identical image/expected-state
   pairs and avoid repeated full-prompt rewrites. Sampling fewer checks is a
   quality tradeoff and requires an explicit mode; it is not silently assumed in
   the same-quality cost scenario.
7. Preserve smooth fractional camera motion and render caching. Unchanged shots
   and chapters should not rerender. The previous small NVENC benchmark was
   slower for this filter chain, so switching encoders is not a proven speedup.
8. Compare a bounded representative pilot on a cheaper worker only with a new
   paid allowance. Include multi-character, action, quiet scene, reference and
   outfit-change shots; measure actual queue time, total rental, first-pass visual
   acceptance, repair cost and full render time. Reserve budget for failures and
   stop before the cap, not after settlement.

Confidence requires a pilot that preserves user-approved image quality, the
visual cadence and narration quality, and fits a pessimistic full-project budget.
Until then, obtaining and processing the remaining chapters is intentionally
withheld under the user's conditional instruction.
