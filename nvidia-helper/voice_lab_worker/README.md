# QuestionTemplate private Voice Lab worker

This worker implements auditions of **Fish Audio Drama 3**, **VoxCPM2**, and
**Qwen3-TTS 1.7B Base + VoiceDesign + CustomVoice**. CustomVoice and its nine
named speakers were added after the user's explicit opt-in. It does not provision, start, stop, or change
RunPod resources. The owner's controller must enforce the authorized total spend
and stop the exact voice pod at its deadline. Releasing model memory does not stop
GPU billing. No anonymous inference endpoint is exposed.

## Preparation and storage

Use an official RunPod PyTorch/Ubuntu base selected from the current catalog.
Preparation can run on a CPU pod mounting the same network volume, in the same
data center, before any GPU is rented. The service requires Linux, Python 3.11,
`uv`, `git`, `ffmpeg`, and `ffprobe`; install these from the base image's official
repositories. The model environments are independent because current Drama3
SGLang-Omni pins a different Torch/Transformers stack than Qwen.
For Drama3, the adapter explicitly selects the complete pip-installed CUDA13
toolkit inside its own environment for JIT compilation. A CUDA-capable driver
alone does not make an older base-image compiler suitable: DeepGemm rejected
the CUDA12.8 image compiler during the live probe. The override applies only to
the Drama subprocess and does not replace the image's shared CUDA installation.
The final live Drama probe reached CUDA compilation, then failed at the host
linker because the pip toolkit supplied `libcudart.so.13` without the development
name `libcudart.so` required by `-lcudart`. The packaged layout repair now creates
that relative link inside the owned toolkit and runs at every Drama launch,
including launches that reuse earlier preparation receipts. This source fix has
offline regression coverage; it does not establish successful Drama inference.

Copy this directory to the persistent volume. For example:

```sh
export QT_LAB_ROOT=/workspace/questiontemplate-voice-lab
python3 /workspace/voice_lab_worker/prepare.py --root "$QT_LAB_ROOT" --model all --volume-gb 150
```

`prepare.py` explicitly downloads pinned snapshots and installs pinned upstream
source commits. It writes preparation receipts and exact `uv pip freeze` outputs
under `prepared/`. These receipts state `inferenceVerified: false`; a successful
installation is not a passed inference test. `--model drama3`, `voxcpm2`, or
`qwen3` can prepare one model at a time. Downloads resume at the same paths.
Managed Python interpreters also live on the volume, so the environments do not
depend on the CPU setup pod's ephemeral interpreter. The wheel cache is on that
same filesystem and uses hard links to avoid a second full copy of package data.
No model is downloaded by the HTTP inference service, which starts with Hub and
Transformers offline modes.

Before another authorized Drama GPU test, run the build preflight on the cheap
preparation machine:

```sh
python3 /workspace/voice_lab_worker/preflight.py --root /workspace/questiontemplate-voice-lab
```

It checks NVCC, Ninja and C++20, then compiles a tiny `cudaGetDeviceCount` program
with NVCC and links it with the same `-lcudart` development-library search used
by SGLang. It never executes that program or initializes a GPU. Only a completed
compile/link writes `cudaRuntimeCompileLink: true` in the preflight receipt;
the receipt explicitly keeps `inferenceVerified: false`. This strengthened probe
was added after the final budget-limited GPU shutdown and was not executed on
that installation. All test pods and the temporary volume were subsequently
deleted. A future authorized setup must recreate the pinned installation and
pass this preflight plus a live Drama inference test.

The five full checkpoint directories total approximately **60.05 decimal GB**,
from the primary repositories' current file metadata. Drama3 also requires the
official pinned S2-Pro **codec file only**, not the S2-Pro language model. Runtime
environments, package caches, CUDA caches and outputs require additional disk.
The live preparation audit reached approximately **100.6 GB** including runtime
files, and the initial 100 GB quota failed. The all-model example therefore uses
a **150 GB** volume. Pass its actual approved quota explicitly; do not rely on
the script's 100 GB default for this full installation.
The setup script measures deduplicated allocated usage with GNU `du` against the
explicit `--volume-gb` quota. A network mount's `df` free-space value can describe
the shared storage system and does not establish this volume's remaining quota.
Preparation reserves ten decimal GB and checks known remaining checkpoint bytes.
It polls child installs/downloads between usage scans. A large NFS usage scan
may take up to 120 seconds; at most three attempts fit within an absolute
300-second scan deadline. Exact disappearing-file races and timeouts are retried,
but preparation stops if a complete usable numeric total never arrives. It stops setup
when the watermark is reached. This reduces quota-related partial writes;
transient peak writes can still cause an individual command to fail. Stop and
review rebuildable caches or approve a resize explicitly if installation does
not fit. Model and environment files are never silently deleted to free space.

Preparation needs network access and may need private Hub access if a license
gate is added upstream. Credentials belong only in the owner's private runtime
environment. Never put an API credential into this directory, a website bundle,
a command-line argument, a Git commit, or an output report.

## Start the private service

Create a random bearer token in a private file, mode 0600, and copy that token
privately to the paired helper's `voiceLabWorkerTokenFile`. Start the worker:

```sh
umask 077
python3 /workspace/voice_lab_worker/service.py \
  --root /workspace/questiontemplate-voice-lab \
  --token-file /workspace/private/voice-lab-token \
  --hourly-usd ACTUAL_QUOTED_GPU_RATE
```

Replace `ACTUAL_QUOTED_GPU_RATE` with the rate from the exact pod creation
receipt. Omit the entire rate option when unknown. Do not treat a catalog's
minimum or a different GPU's rate as this pod's measured rate.

The service listens on **127.0.0.1:18765 only**. Use an SSH local tunnel from the
owner's computer, for example `-L 18765:127.0.0.1:18765`, using the existing private
SSH identity. Set the helper's `voiceLabWorkerURL` to
`http://127.0.0.1:18765`. Do not expose port 18765 or the internal Drama3 port
18767 through a public pod proxy. The website sends requests through its paired
local helper, which holds the worker token server-side.

Keep the service and tunnel under the owner's process supervisor. The worker
does not stop its paid pod when the website closes. A bounded, independent
provider watchdog must stop compute even if this service, browser or tunnel
fails. Retained disk continues billing after compute stops.

## API contract

Every endpoint requires `Authorization: Bearer <private token>`.

- `GET /health`: service availability, per-model preparation status, resident
  model, immutable checkpoint revisions, actual configured hourly rate.
- `POST /jobs`: durable, asynchronous acceptance. Body keys: `id`, `model`,
  `mode`, `text`, `language`, `description`, `parameters`, and optional
  `reference:{audioBase64,mime,transcript}`. IDs must have 8–80 ASCII letters,
  digits, underscores or hyphens. Repeating an identical ID and input returns
  the existing job. Reusing an ID for changed input returns 409.
- `GET /jobs/<id>`: `queued`, `running`, `completed`, `failed`, or `cancelled`.
  Completed jobs include timing and audio-duration metrics.
- `GET /jobs/<id>/audio`: canonical PCM16 WAV for a completed job. The helper
  converts this transport artifact to the existing stored Opus128 preview.
- `POST /jobs/<id>/cancel`: cancels a queued job or kills the active model
  process group. A late completion cannot undo an acknowledged cancellation.

The queue admits at most eight unfinished auditions. Accepted input is stored
in a private SQLite WAL database. A worker restart resumes queued jobs; it marks
an interrupted running job failed and asks the owner to review partial output.
It never automatically resubmits that job or blindly retries a request.

## Actual model behavior

| ID | Modes | Behavior |
| --- | --- | --- |
| `drama3` | `default`, `clone` | Official SGLang-Omni `POST /v1/audio/speech`, full `fishaudio/drama-3` checkpoint. FP8 MoE experts/BF16 remainder are upstream's actual checkpoint precision. No int4 substitution. Clone needs a reference plus accurate transcript. Sampling controls are temperature, top-p, and top-k. Emotion/event tags belong in the input text. |
| `voxcpm2` | `default`, `design`, `clone` | Official `VoxCPM.from_pretrained` and `generate`. Voice description is prepended in parentheses as upstream documents. Explicit cloning strategies select prompt continuation, reference conditioning, or both. Controls: guidance scale, diffusion steps, seed. |
| `qwen3` | `design`, `clone`, `preset` | Official `Qwen3TTSModel.generate_voice_design` with VoiceDesign, `create_voice_clone_prompt` + `generate_voice_clone` with Base, or `generate_custom_voice` with the explicitly added CustomVoice checkpoint. BF16/SDPA baseline. Documented sampler settings and seed are forwarded, retaining pinned checkpoint defaults unless changed. Each mode loads its own pinned checkpoint. |

Drama3 and VoxCPM2 do not contain a named stock-speaker catalog. Both pinned
Qwen Base and VoiceDesign configurations have empty speaker-ID maps. The
explicitly added CustomVoice checkpoint supplies nine real speaker IDs:
`vivian`, `serena`, `uncle_fu`, `dylan`, `eric`, `ryan`, `aiden`, `ono_anna`, and
`sohee`. Preset mode forwards the selected ID and optional instruction to
`generate_custom_voice`; it never silently uses Base or VoiceDesign instead.
Public demonstration reference recordings remain separate reference assets.

Qwen exposes its documented main and sub-talker sampling controls. Both pinned
checkpoints default to `do_sample=true`, `temperature=0.9`, `top_p=1.0`,
`top_k=50`, `repetition_penalty=1.05`, `subtalker_dosample=true`,
`subtalker_temperature=0.9`, `subtalker_top_p=1.0`, and `subtalker_top_k=50`.
The service forwards explicit false switches as false. Top-p/top-k/temperature
affect sampling only when the corresponding sampling switch is enabled. The
750-token audition cap stays separate from these controls. There is no invented
numeric pacing control: Qwen VoiceDesign and Vox descriptions can request a
speaking style or pace, but do not guarantee a duration.

Vox cloning now has an explicit `clone_strategy`. `auto` preserves prior jobs:
a transcript selects prompt continuation; no transcript selects reference-only
cloning. `reference` always uses reference-only cloning and allows an optional
style description. `prompt_reference` sends the same clip through both prompt
and reference paths, as the official maximum-similarity example does, and
requires an exact transcript. It rejects a nonempty style description instead
of implying that reference-preserving delivery also applied an independent
style instruction. Parentheses inside Vox descriptions are stripped like the
official demo to preserve its `(control)text` conditioning format. No automatic
retry, denoiser download, checkpoint change or new paid run is enabled here.

Qwen language selection supports English, Chinese, Japanese, Korean, German,
French, Russian, Portuguese, Spanish, Italian and Auto. Drama3 and VoxCPM2 infer
language from input text in this initial adapter. A drop-down's presence is not
a native-speaker quality verdict. Audition ranges are deliberate service limits,
not claims about the models' complete native parameter ranges.

The current baseline disables VoxCPM `torch.compile` (`optimize=False`) to avoid
an initial compile delay, and disables automatic bad-case retries so costs are
bounded and failures stay visible. These choices must be retained in benchmark
records. Qwen uses the official SDPA implementation to avoid an additional
FlashAttention source build. Drama3 limits its scheduler/CUDA-graph batch size
to one for sequential auditions. These are audition settings, not a throughput
optimization claim.

Reference uploads are limited to 10 MiB and decoded with FFmpeg. The worker
requires 0.25–60 seconds, canonicalizes references to mono 16 kHz PCM, and caches
them by content hash. Qwen caches up to 16 clone-conditioning prompts while its
Base model stays resident. Reference changes produce different cache keys.
References, payloads, outputs, and diagnostic logs stay private on the volume.
Only use recordings you are authorized to clone.

One model process stays resident for repeated auditions. Switching families
terminates the old process to release VRAM. Switching Qwen modes replaces its
loaded checkpoint. Persistent model/env/CUDA caches avoid redownloading and
reinstalling after a pod restart; they do not eliminate GPU allocation, image
pull, model loading, or graph warmup. An idle model is unloaded after 15 minutes,
but that is not a billing shutdown.

## Honest 30-second cost measurement

Use the same approximately 30-second script for each model and record the actual
output duration. The models stop on their own end marker; no audio is cut to
force a 30-second score. Record startup cold and a second warm run separately.
The worker rejects cap-bound output instead of marking it complete. Drama3 must
return a normal `X-Finish-Reason` and stay below its frame cap. The pinned Qwen
and Vox adapters inspect generated codec/feature counts before decoding, including
VoxCPM's internal text-ratio cap. An unknown or cap-bound result fails visibly
and is never automatically retried. This detects generation limits, not omitted
words or semantic errors; listening and later ASR quality checks remain separate.

The worker records `loadSeconds`, `referenceDecodeSeconds`, `conditioningSeconds`,
`generationSeconds`, `audioSeconds`, and `workerWallSeconds`. `startupSeconds`
stays **null** until the owner's provider lifecycle measurement supplies it;
`loadSeconds` records model loading within this worker. `generationSeconds` includes
conditioning; the conditioning field is a subset, not an extra amount to add.
There is no first-audio latency metric because this audition API returns the
completed artifact.

When the exact quoted rate is supplied, the worker calculates
`workerEstimatedUSD = workerWallSeconds × hourlyUSD / 3600`. That figure excludes
provider startup, image pull, package/model downloads, idle time and disk. To
answer the user's all-in cold-start question, add those measured lifecycle
intervals from the owner's provider receipts exactly once and include storage
separately. A measured rate-normalized 30-second audio estimate is
`warm cost × 30 / actual audio seconds`; label it calculated, not measured
30-second output. Do not present provider marketing benchmarks as local results.

## Verification

### Drama audition startup

The audition launcher sets
`--tts_engine.engine.cuda_graph_backend_prefill disabled` and
`--tts_engine.factory.first_frame_cuda_graphs false`. These documented options
are supported by the pinned source revision
`65868af0592a852459aa31a6be7ae742c7756841`: the engine explicitly accepts eager
prefill, and its factory exposes the first-frame graph boolean. Decode graphs
retain their native default. The full FP8-expert/BF16 checkpoint, codec, sampling
settings and one-request limit are unchanged.

In the 2026-10-26 H200 audition, weight loading alone took **116.66 seconds**.
The old launcher then began capturing 42 prefill shapes; the worker had not
become ready before the authorized rental was stopped. This change avoids that
optional prefill/first-frame capture work, trading its warm first-audio
optimization for less startup work. Model loading, decode-graph capture and
first-use kernel compilation still take time. **The new total startup time and
generation behavior are GPU-unverified**; CPU tests verify the launch arguments
and readiness gate, not a measured startup improvement or a completed Drama
audition. No paid retest was run.

Sources: [official defaults](https://sgl-project.github.io/sglang-omni/cookbook/fishaudio_drama3.html#defaults-parameters),
[pinned factory fields](https://github.com/sgl-project/sglang-omni/blob/65868af0592a852459aa31a6be7ae742c7756841/sglang_omni/models/fishaudio_drama3/config.py),
[pinned engine validation and capture branch](https://github.com/sgl-project/sglang-omni/blob/65868af0592a852459aa31a6be7ae742c7756841/sglang_omni/models/fishaudio_drama3/engine_builder.py).

Drama character design uses the official Fish Audio Space's pinned
`drama3_space_ext.py`, vendored unchanged as `drama_space_ext.py` from revision
`c368729ab9347dda6635c5272ad31e6521114b79` (upstream SHA256
`df7e7c1e497b4488b69eef6b35c62370d3281277a2b8cd1c7423de8341947aa7`).
The reviewed extension builds the model's Figure 13 scene/character prompt and
performs no external I/O or secret reads. Before launching SGLang, the adapter
uses the author's Dockerfile technique to rebind the installed preprocessing
factory. The module is replaced atomically to preserve uv wheel-cache hardlinks;
the worker directory is placed on `PYTHONPATH` for spawned pipeline stages.
The hook is idempotent and rejects an unrecognized factory interface.
It supports both installed `site-packages` and the preparation script's owned
editable checkout at `sources/drama3`; ambiguous or out-of-root paths fail closed.

Design requests send the author's `@@drama3-space@@` JSON envelope with the
original script and `{name, description, scene}` character. Optional top-level
`character: {name, scene}` preserves the author's eight named templates;
generic designs default to `Narrator` with no scene. Clone and plain TTS take
the extension's unchanged stock path. Source/CPU fixture validation does not
establish GPU generation quality or a stable identity across unseeded designs.
Drama's author-default UI top-k 0 means all main-token candidates: the adapter
sends 4097 to the native API while codebook heads keep their internal cap 30.
An explicitly saved top-k 30 still sends 30. The official cookbook warns that
capping the main token at 30 can make a performance fall silent after its first
phrase; this default follows the author's current Space behavior.

Primary design sources:
[author extension](https://huggingface.co/spaces/fishaudio/Drama-3/blob/c368729ab9347dda6635c5272ad31e6521114b79/drama3_space_ext.py),
[factory hook](https://huggingface.co/spaces/fishaudio/Drama-3/blob/c368729ab9347dda6635c5272ad31e6521114b79/Dockerfile.engine),
[eight templates](https://huggingface.co/spaces/fishaudio/Drama-3/blob/c368729ab9347dda6635c5272ad31e6521114b79/studio_content.json).

CPU-only tests use fake inference while exercising real HTTP authentication,
persistent job recovery, duplicate protection, cancellation races and accounting:

```sh
cd /workspace/voice_lab_worker
python3 -m unittest -v test_worker.py
```

Passing these tests does not verify GPU inference. Before marking any model
working, run a real authenticated request, fetch and decode its output, listen
to it, inspect actual duration, and save a redacted timing/cost receipt. Check
the exact model's current license before monetized or self-service release.
This private audition worker does not imply a commercial-license approval.

Primary implementation references:

- [Drama3 official SGLang-Omni cookbook](https://github.com/sgl-project/sglang-omni/blob/65868af0592a852459aa31a6be7ae742c7756841/docs/cookbook/fishaudio_drama3.md)
- [VoxCPM official API](https://github.com/OpenBMB/VoxCPM/blob/cce58a5b59303c9bd63d12f23afe6a49a4a80c59/src/voxcpm/core.py)
- [Qwen3-TTS official API](https://github.com/QwenLM/Qwen3-TTS/blob/022e286b98fbec7e1e916cb940cdf532cd9f488e/qwen_tts/inference/qwen3_tts_model.py)
