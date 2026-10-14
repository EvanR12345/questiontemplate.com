# Studio narration models

Choose Settings → Voice → Narration model, save, and use Preview selected voice.
Changing the model does not delete any previous audio. Regenerate a chapter to
apply a new voice; its scene timing must be regenerated against the new duration.

| Model | Runs on | Voice control | Production license |
| --- | --- | --- | --- |
| Kokoro 82M | Existing laptop helper, or optional Kaggle notebook | Built-in voices; speed and connected delivery | Apache 2.0 |
| Qwen3-TTS 1.7B CustomVoice | Private Kaggle GPU notebook | Ryan English preset and narrator instructions | Apache 2.0 |
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

- Qwen uses bounded batches of up to four passages with the Ryan preset.
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
