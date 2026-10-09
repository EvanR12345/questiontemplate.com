# Local photo-motion rendering

Studio supports the existing CPU renderer and an optional NVIDIA GPU path. The
GPU path fits each photo once, keeps it on the GPU, and applies its fractional
zoom/pan coordinates with bicubic sampling directly into the hardware encoder.
It prepares only the next photo and caches the small compiled movement program
across chapters. It still exports every video frame; the timeline stores photos
and movement instructions rather than thousands of intermediate bitmap files.

In Studio Settings → Video output, choose **NVIDIA GPU · photo motion** and Apply
renderer. This is a local helper setting. Cloud image generation, voice models,
story content and previous videos stay saved.

If needed, run `install-gpu-renderer.ps1` from the existing QuestionTemplateHelper
directory. It adds one pinned NVIDIA PyNvVideoCodec 2.2.3 wheel to `render-libs`,
separate from `.venv`, with dependencies disabled and SHA256 verification. The
current installer supports the existing Windows x64 Python 3.11 helper. It
does not install Torch, CUDA, another Python environment or a new voice model.
GPU libraries load only when missing clips actually need rendering.

Missing optional libraries select CPU with an explicit status. Hardware errors
preserve completed assets; CPU compatibility remains selectable. A failed GPU
clip never becomes a successful cached result. GPU and CPU cache identities
remain separate. Cancel removes owned unfinished staging, retains saved clips,
and retry uses those saved results. Manual holds, all seven movement choices,
linear/smooth easing, framing, absolute-frame timing and original narration are
preserved. Full-story narration is encoded from the original WAVs once so AAC
padding does not accumulate between chapters. Exports request AAC at 128 kbps;
actual AAC bitrate varies with the input and encoder.

## Measured result, October 9, 2026

Same laptop/GTX 1650, 720p30, two chapters, twelve distinct saved story photos,
120 seconds of saved narration; three fresh exports each, without cached output.

| Renderer | Median complete export | Output per test |
| --- | ---: | ---: |
| CPU cubic perspective / x264 veryfast CRF21 | 150.18 seconds | 30.38 MB |
| GPU bicubic / NVENC P4 QP24 / AAC fast | 14.28 seconds | 28.03 MB |

The observed median speedup was **10.5× for this test**. Includes photo fitting,
upload, per-clip encoder initialization, chapter assembly and final narration
mux. Excludes cold helper startup, intro, watermark, reminder/outro processing,
cloud transfers and platform processing. It is not a two-hour/12-hour production
guarantee. CQP24 is not mathematically equivalent to CRF21.

264 unencoded geometry comparisons against the previously approved GPU version
passed, with at most one colour level difference out of 255. Three full 300-frame
clips had SSIM 0.977–0.984 against that version. SSIM is diagnostic similarity,
not a quality percentage. The final audio had exactly 2,880,000 mono samples at
24 kHz for the 120-second fixture. Pause, cancel/retry, unchanged-video reuse and
single-photo rebuilding were also tested with the actual GPU renderer.

One longer six-minute/six-chapter/36-clip export passed full decode and 10,800
frames in 51.93 seconds. A later repeat exhausted the laptop's available disk;
the longer matrix was stopped and its owned scratch removed. No larger benchmark
or unconditional tenfold long-video speedup is claimed.

## Storage and remaining work

Speed does not eliminate space requirements: original assets, reusable clips,
chapter exports and final assembly may coexist. Final assembly checks estimated
space first, and disk exhaustion reports a recoverable error. Original assets
and previous videos remain saved. The longer test peaked near 420 MB of owned
scratch for six minutes. Optional watermarks, fades and subscribe overlays still
use the compatible renderer and can reduce the overall speedup. Cloud rendering
requires compute; object storage alone does not execute the renderer.

The public production planner offers Auto/GPU/CPU forecasts and uses this matched
GPU calibration only for 720p30. Other formats retain their measured CPU data.
Native rendering and local voice reserve the same laptop GPU; cloud image
generation can remain independent. Long-video rates are explicitly projections.
