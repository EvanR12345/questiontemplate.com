# Studio narration models

Choose Settings → Voice → Narration model, save, and use Preview selected voice.
Changing the model does not delete any previous audio. Regenerate a chapter to
apply a new voice; its scene timing must be regenerated against the new duration.

| Model | Runs on | Voice control | Production license |
| --- | --- | --- | --- |
| Kokoro 82M | Existing laptop helper, or optional Kaggle notebook | Built-in voices; speed and connected delivery | Apache 2.0 |
| Qwen3-TTS 1.7B CustomVoice | Private Kaggle GPU notebook | Nine selectable presets and narrator instructions | Apache 2.0 |
| Dia 1.6B | Private Kaggle GPU notebook | Generated anchor, English narration; drift remains possible | Apache 2.0 |
| Fish Audio S2 Pro | Private Kaggle GPU notebook | Generated anchor, research auditions | Fish Audio Research License; commercial license required for monetized use |

The screenshot's first/second/third rankings and storage figures are not a
controlled quality benchmark. Kaggle T4 compatibility, long-form pronunciation,
speed and consistency must be established by actual auditions. New models are
marked unverified until checked; configuring a token is not a GPU compatibility
test. Qwen CustomVoice is selected deliberately: VoiceDesign and Base are different
checkpoints and are not downloaded together.

## Tested Kaggle compatibility fixes

Kaggle's current Python 3.13/Torch 2.11 image differs from the laptop's working
Python 3.11 environment. Remote packages use an isolated environment sharing the
provided Torch/CUDA installation. No new Torch/CUDA stack is installed locally.

- Qwen uses bounded batches of up to four passages with the selected preset (Ryan by default).
- Dia uses the official `Dia-1.6B-0626` checkpoint, float32, and grouped short
  passages. Sentence times inside a group are explicitly estimated; the complete
  audio duration is measured. Generated voice anchors persist per project.
- Fish uses both Kaggle T4 GPUs: semantic model on GPU 0 and codec on GPU 1.
  Two narrowly asserted patches to the pinned upstream loader/router keep token
  and decoder devices correct. Generated voice anchors persist per project.
- Optional cloud Kokoro reuses the existing helper's CPU phonemizer and runs only
  the neural model on Kaggle. Its isolated package import excludes the unused
  English G2P stack that fails to build on Python 3.13. The local model is unchanged.

Short generated WAV tests establish technical compatibility, not a quality ranking
or a guarantee of long-story consistency. Listen to the auditions before choosing.

## Private Kaggle connection

1. Complete free Kaggle registration and any required phone verification.
2. Generate a dedicated Kaggle API token and save it in Studio's password field.
   The helper stores it privately, outside project JSON and browser storage.
3. Install the official `kaggle==1.8.4` CLI in the existing helper environment.
   This does not install another Torch/CUDA stack or any voice weights locally.
4. Preview a short voice sample. Each request creates a private notebook, with
   Internet and a T4 accelerator requested through Kaggle's official CLI. The
   notebook downloads only its selected model into ephemeral Kaggle scratch.
5. The notebook saves one mono PCM WAV and actual passage timing metadata.
   Studio downloads and validates these before selecting the result. Its existing
   cloud archive then stores normal audio assets with other project files.

Kaggle has GPU quotas, availability limits and notebook session limits. It is not
an always-on HTTP endpoint. A submission timeout is journaled and never blindly
resubmitted. Cancel stops local publication; an admitted notebook can continue
until its four-hour timeout. Open the private notebook to stop it sooner.
Sound-effect cues use brief pauses on Kaggle; the existing Kokoro SFX remain local.
Narrator instructions apply to Qwen; Dia and Fish do not pretend to accept the
same instruction format. Generated anchors do not clone a real person's voice.

The planner refuses to substitute Kokoro timing for uncalibrated Kaggle narration.
No unlimited free production, quality ranking, storage size, or full 12-hour
completion guarantee is claimed.

## Official sources

- [Kaggle notebook limits](https://www.kaggle.com/docs/notebooks)
- [Kaggle CLI notebook jobs](https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md)
- [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)
- [Dia](https://github.com/nari-labs/dia)
- [Fish S2 Pro license](https://huggingface.co/fishaudio/s2-pro)
- [Fish inference protocol](https://speech.fish.audio/inference/)

## Model-specific voice picker

Audio and Studio both show each model’s supported voices as selectable cards.
Kokoro retains its eight enabled English speakers. Qwen CustomVoice exposes
Ryan, Aiden, Vivian, Serena, Uncle_Fu, Dylan, Eric, Ono_Anna and Sohee. Ryan
and Aiden are native English voices; the other presets are labeled by native
language, and English synthesis still uses English text. Dia and Fish have
one project narrator option backed by their saved synthetic anchor; they do
not expose a fabricated named-speaker bank. New presets use the same tested
model runtime, but have not each had a new perceptual audition.

Audio’s Kaggle previews are saved to the selected existing Studio project and
use its durable queue, private credential vault, and cloud archiving. The
page monitors the same saved job after refresh without resubmission. Cancel
applies only to that audition, never the whole production queue. Full remote
chapter narration stays in Studio; the Audio audition limit is 1800 characters.
The common default script retains the user’s narrator description, cries,
sound cues, dialogue, and emphasis examples. Instructions remain a separate
Qwen control; the text in the script field is the actual audition source.

## Download formats and actual model storage

New narration, auditions, intro/outro narration, sound cues and narrator references
are saved as Opus at 128 kbps. PCM stays in memory or pipes; no new WAV master
is created. Editing reads Opus directly. Optional 64/256 kbps Opus downloads reuse
the existing recording without another synthesis request. Previously saved audio
remains readable and is not deleted. MP4 video exports retain compatible AAC
tracks; this does not create a WAV intermediary.

Grouped Kaggle jobs encode each chapter directly from generated PCM into its own
Opus file, avoiding chapter re-encoding from a combined compressed recording.
Qwen, Dia and remote Kokoro weights use `/kaggle/tmp/studio-voice/models/hub`;
Fish uses `/kaggle/tmp/studio-voice/s2-pro`. These are temporary notebook caches.
Outputs retain `narration.opus` or separate `chapter-NNN.opus` assets, optional
`voice-anchor.opus`, and `result.json`.
Local Kokoro uses the laptop's Hugging Face cache. No new local voice weights
are installed by this update.

## DramaBox evaluation (October 26, 2026)

Official sources: https://github.com/resemble-ai/DramaBox and
https://huggingface.co/ResembleAI/Dramabox . This is **DramaBox**, an expressive
audio model with optional voice references, distinct from the four enabled models.

The documented warm workflow peaks around 24 GB VRAM. Its audio transformer
is 3.3B and its separate Gemma 3 text encoder is 12B (4-bit); downloads total
roughly 16.5 GB. Upstream warm-server code places the encoder, transformer,
conditioner and decoder on the same selected device. Two T4s do not automatically
pool their 16 GB memories. Its default dtype is BF16, another setting requiring
care on a T4. The upstream requirements pin Torch/torchaudio 2.8; installing them
blindly would replace Kaggle's current stack. Do not download these weights on
the laptop or mark DramaBox T4-compatible without a separately measured adapter.

DramaBox supports delivery directions and non-verbal vocalizations. This may
help the requested cries, but it does not prove reliable pronunciation or
generate arbitrary sound effects from words like Slash. Long-form generation
uses bounded chunks and an optional reusable reference. The reported H100 warm
timing is upstream evidence, not a Studio/Kaggle benchmark.

The model uses LTX-2 Community License, with its commercial threshold and
use restrictions; it is not Apache-2.0. Optional RE-USE denoising is separately
non-commercial and should remain off for monetized work unless licensed.
Retain the default watermark. DramaBox is evaluated but **not enabled or
downloaded** by this update; a successful memory/dependency/audio test is still
required before it becomes a working selection.

## Kaggle primary, local quota backup

New Studio projects use Kaggle GPU narration, with quota backup enabled. Existing
projects and explicit saved profiles retain their choices. In Settings → Voice,
select Kaggle for Kokoro and enable local fallback to apply this routing to an
existing project. Choose the local Kokoro / backup speaker separately.

Fallback is allowed only when Kaggle explicitly rejects admission for exhausted
GPU quota. Stock, authentication, privacy errors, timeouts and uncertain
submissions do not substitute another voice. Auditions never fall back: a
successful model comparison must actually use the selected model.

A fallback produces the entire narration with local Kokoro; it does not splice
two engines into a chapter. Qwen/Dia/Fish will sound different. Saved chapter
metadata and the narration panel identify the actual fallback. Intro/outro
records retain actual voice metadata too. A 30-minute quota circuit avoids
repeated rejected notebooks; it resets on helper restart and never bypasses
account/model validation. Fallback serializes against local narration/rendering
and its timing is excluded from Kaggle ETA learning. Completed audio is reused
normally, without an automatic re-generation when quota later returns.
