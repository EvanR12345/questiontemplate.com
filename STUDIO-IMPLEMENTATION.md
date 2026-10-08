# QuestionTemplate story-to-video studio

Implemented in the existing `studio.html` application. Audio and the original comic/panel tools remain available. The new **Story → video** workspace uses the same paired helper on port 8765.

## Using the website

1. Start the existing `QuestionTemplateHelper/start-windows.bat` and leave it open. Use its pairing link once; refresh reconnects with the saved connection.
2. Open Studio, select **Story → video**, then **Project actions → New project**.
3. Add recurring main characters and their reference images. Supporting/temporary people can be detected during analysis.
4. Paste Chapter 1. Review the cleaned narration, then select **Analyze chapter**.
5. Review characters, scene cards, prompts and planned appearance changes. Select **Generate missing images**.
6. Review images. Use **Edit shot**, **Visual QC**, **Repair**, or **Accept image** where appropriate.
7. Open **Video** and select **Render chapter**. Add another chapter when ready.
8. Select **Render full story**. Intro is off by default; project settings allow 10–30 seconds, once before the full story or before every chapter.

### Intro timing and editing — October 8

**Settings → Optional intro → End with narration** is on by default, including older projects. With a saved WAV, the selected duration becomes a maximum: rendering ends one quarter-second after the audio, rounded to a video frame, with a ten-second minimum. Turn it off to keep a fixed duration. Narration longer than the selected duration is rejected rather than cut off. This removes padding after the audio file; it does not detect long pauses inside that file.

Editing intro voice text requires regenerating its separate audio before rendering. Regenerating an image at the same filename invalidates the intro cache through its byte fingerprint. Changing intro placement invalidates affected exports while retaining their previous files. Preparation reuses matching intro audio and keeps existing storyboard images and manual edits. Individual chapter narration and story text stay separate from the intro.

New AI intro plans use the supplied duration/style and verified source. They begin with a concrete conflict, progress through four to six purposeful shots, and avoid invented events, unrelated characters, text, borders and spoilers outside the supplied source. This improves future planning; it does not silently rewrite an existing authored hook or regenerate saved pictures.

Verification: targeted regression tests plus an offline real FFmpeg fixture with 10.5 seconds of intro audio and a two-second chapter produced a decoded 12.75-second video. No cloud image generation was used for this check.

Cloud archives and cloud compute remain separate. Google migration must verify all assets and manifests before changing the active provider. Audio and rendering remain on the existing helper until an authorized cloud compute job is deployed and tested. The file dashboard and edit controls do not establish all-cloud production by themselves.

Existing comic projects remain in their original browser database. The import action creates a story project while retaining the old comic snapshot; the original panel images/export tools remain accessible through **Comic & panel tools**.

The familiar workspace now groups the editors under **Story, Narration, Characters, Layout, Video and Settings**. Chapters sit beside the editor on larger screens and above it on smaller screens. **Project actions** retains reconnect/import/export, **Chapter actions** retains duplicate/reorder/delete, and **Story tools** retains test-story loading and comic imports. Costs and measured stage times are expandable above the editor. Queue progress counts actual saved story images; previous failed attempts stay in Job history. Paused queues display a paused ETA, and images flagged for visual review remain visible even when all assets have been generated. Arrow keys and Home/End navigate the workflow tabs.

**Generate full video** is available above the chapter editor after entering chapters. Its `produce-story` queue job processes narration, analysis, missing main-character references, shot images, cached chapter renders and final assembly sequentially. Project `production` metadata records the current chapter/image/stage across reloads and restarts. Retry restarts orchestration but reuses completed assets and saved shot seeds. Separate jobs for the same project are blocked while the full run is active. The explicit automatic action accepts detected main characters; supporting/temporary people remain in chapters. Strict appearance review, proposed manual-plan conflicts and unrepaired generation failures stop the run with a clear error. Audio use can yield the GPU and pause the run; Resume continues it.

The canonical website route is now `studio.html`; `manga.html` redirects while preserving URL queries and pairing fragments. Audio and Studio use the same vector logo, SVG/ICO favicon and touch icon. Five focused orchestration tests cover two-chapter completion, reference creation, completed-stage reuse, seed-preserving recovery, cancellation and competing-job protection, bringing the backend suite to 44 tests.

## Architecture and persistence

The website remains vanilla HTML/CSS/ES modules on GitHub Pages. No framework replacement or external paid image service was introduced.

| Component | Responsibility |
|---|---|
| `story-studio.mjs`, `story-studio.css` | Integrated chapter rail, narration review, cast library, scene/shot editing, settings, timeline, queue and media previews |
| `studio_data.py` | Versioned project facts, permanent identity versus appearance, narration cleaning, validation, atomic JSON saves, history and handoffs |
| `director_provider.py` | Replaceable DirectorProvider, focused structured Qwen passes, bounded context, JSON-schema constrained responses and persistent pass cache |
| `image_provider.py` | ImageProvider capabilities/settings, model-specific prompts, existing SD fallback, quantized native FLUX and configurable ComfyUI API workflows |
| `studio_service.py` | Shared GPU scheduling, continuous narration, analysis, reference selection, durable production jobs, visual review and bounded repair |
| `studio_render.py` | FFmpeg still-image motion/transitions, narration synchronization, cached chapters, separate intro and ordered full-story assembly |

Projects live in `QuestionTemplateHelper/outputs/studio/pr-…/project.json`. WAVs, PNGs, reference images, generation metadata sidecars and MP4s live beside the project. The production queue is SQLite/WAL. Every completed image is saved before visual QC. Reanalysis archives the prior plan; manual edits produce a proposed plan until explicitly applied. Deleted chapters are archived in project JSON and their files are retained.

Browser IndexedDB keeps offline text/project edits. A reconnect offers explicit synchronization when the helper already has that project. Export JSON saves project data; **copy the entire helper project folder for a complete backup including generated assets**. Asset files are never embedded into every JSON edit. Media uses scoped, expiring localhost tickets and HTTP range streaming.

## Director, narration and continuity

The initial director is **Qwen3.5-4B Q4_K_M**, served by pinned llama.cpp b11334 with Vulkan. On this laptop Vulkan device 0 is Intel; **Vulkan1 is NVIDIA**. Context is 4096 tokens with one request at a time. Analysis is split at sentence boundaries, at most 12 sentences/2500 characters per group. Completed passes are cached by input/schema/model stamp.

The director analyzes people/beats/locations, plans scenes/shots/cameras, validates an installed workflow, checks continuity and refines prompts in focused JSON passes. The application validates narration coverage, quote evidence, known character IDs and available workflows. If a visual state change is buried inside an earlier shot, the application splits at the evidence boundary and asks Qwen to direct those moments. This preserves important story beats without fixing the scene count.

Permanent identity is stored separately from outfit, hairstyle, injuries and objects. Object fields such as `key: right hand` do not replace clothing accessories. Each accepted change has source narration evidence, chapter and scene. Later chapters receive prior handoff state plus bounded summaries, objects, locations and unresolved items. Main character identity comes from the library; temporary people stay in chapter data until promoted/attached by the user.

Narration uses the existing Kokoro CUDA model/environment. The application strips unquoted chapter headings and production labels by default, preserves quoted dialogue, and exposes the exact script for manual editing. Short synthesis sections stream into **one continuous WAV per chapter**. Actual synthesized sentence durations and paragraph boundaries are stored. Word-level forced alignment is not installed; sentence timing is used rather than claiming guessed word timestamps. Changing narration creates a new asset, preserving previous audio.

Fast/Balanced/High director settings vary response budget. QUICK skips optional cinematography/prompt-refinement work; BALANCED adds normal creative/continuity passes; MAX QUALITY requests visual QC and bounded repairs. The structured project format stays the same.

## Existing SD workflow: diagnosis

The original black-frame failure was reproduced with a daylight apple prompt and fixed seed, without references or inpainting. Denoising latents and VAE output became nonfinite on this GTX 1650 with the original all-fp16 path. Disabling cuDNN made the output visible but slow. The retained fix computes GTX 16xx convolutions and VAE encode/decode in float32 while keeping other eligible weights/attention in fp16; it checks finite latents/pixels and rejects all-black results. SDPA and CPU model offload fit 4 GB.

Other poor results had separate causes: sound-effect-only action prompts, wide establishing framing, long CLIP prompts being truncated, and strong empty-room reference conditioning. The existing safeguards keep the visible action first, encode all CLIP windows within a checked bound, reject sound-only prompts, require relevant cast, and reduce incompatible scene-anchor conditioning. The SD fallback uses DPM++ 2M/Karras and 512px presets. Inpainting shares its existing base model, respects the user’s denoising strength and composites unmasked pixels back. Seed, references, actual settings and errors are saved.

SD 1.5 and DreamShaper still have limited spatial reasoning and multi-character identity binding. IP-Adapter’s combined reference conditioning is approximate. Improving those prompts alone does not make the underlying models equivalent to a newer reference/editing model.

## Model evaluation and recommendation

Computer tested: GTX 1650 4 GB, approximately 8 GB system RAM, i5-10500H, NVIDIA driver 596.08. ComfyUI and Ollama were not already installed. The table distinguishes actual measurements from estimates; untested larger models were not downloaded merely to fill this comparison.

| Candidate | Practical memory / speed on this computer | Quality and conditioning | Integration, license, recommendation |
|---|---|---|---|
| Existing SD 1.5 | Tested with 4 GB offload. Warm 512px/8-step image: 23.3 s. | Limited complex prompts; familiar illustration/cinema styles; IP-Adapter, negative prompts and masked inpaint. Multiple identities approximate. | Already installed, CreativeML OpenRAIL-M terms. Keep as fastest preview/fallback. |
| DreamShaper 8, SD 1.5 family | Tested 512px/20 steps: approximately 79 s. Same low-memory backend. | Better stylized appearance on some simple scenes, weak multi-person binding; same adapter/inpaint support. | Existing cached checkpoint; inspect its own model-card/license terms. Optional illustration fallback, not the default quality upgrade. [Model card](https://huggingface.co/Lykon/dreamshaper-8). |
| SDXL base / SDXL-Lightning / anime SDXL checkpoints | Rough planning range 6–8+ GB without heavy offload, workflow dependent; 4 GB can require aggressive offload. Laptop speed unmeasured. | Better native detail at higher resolution; style/checkpoint matters. IP-Adapter/ControlNet/LoRA can add reference/pose control, with more memory. | ComfyUI workflows supported by adapter; no checkpoint downloaded. SDXL license and each derivative’s terms apply. Useful after a RAM/GPU upgrade. [SDXL](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0), [Lightning](https://huggingface.co/ByteDance/SDXL-Lightning). |
| FLUX.1 Schnell / Dev, 12B | Quantized transformer alone roughly 6–7 GB plus text encoders/working memory; full precision much larger. Slow offload expected here, not benchmarked. | Stronger natural-language compositions; identity/editing depends on extra workflows/adapters. | ComfyUI integration possible. Schnell Apache-2.0; Dev weights have a separate noncommercial license. Not selected for this 4 GB/8 GB machine. [Schnell](https://huggingface.co/black-forest-labs/FLUX.1-schnell), [Dev](https://huggingface.co/black-forest-labs/FLUX.1-dev). |
| **FLUX.2 Klein 4B Q4_0, native Vulkan** | **Actually tested at 4 GB** with disk-backed quantized weights and sequential model use. 448px text-only: 42.6 s including load; 384px reference shots average around 49–54 s in the two-chapter test; source edit 55.6 s. | Visible, coherent anime two-person scenes and current clothing changes. Natural-language prompts, multiple image references and source-image editing. Exact handedness, small injuries and identity still need review. | Native stable-diffusion.cpp avoids replacing Torch/Diffusers. Apache-2.0 parent model. **Recommended story default after local validation**; 384px with references, 448px without. [Official model](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B), [native workflow](https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/flux2.md). |
| Qwen-Image / Qwen-Image Edit, 20B | BF16 transformer weights alone about 40 GB; Q4 roughly 10–12 GB before encoder/working memory. These are parameter-size estimates, not measured VRAM guarantees. Heavy paging on this laptop would be impractical for 1000 images. | Natural-language generation, varied styles; Edit offers semantic/appearance repair. Extra workflow nodes determine pose/control features. | Apache-2.0, ComfyUI workflow route available if independently installed on suitable hardware. Not downloaded or enabled here. [Image](https://huggingface.co/Qwen/Qwen-Image), [Edit](https://huggingface.co/Qwen/Qwen-Image-Edit). |

The official Klein 4B full-precision recipe targets approximately 13 GB VRAM. The **measured 4 GB result uses Q4 quantization, native disk-backed execution, reduced resolution and sequential model switching**. It is not evidence that the ordinary BF16 Diffusers recipe fits this laptop. Standard Klein uses four steps and guidance 1; this adapter uses Euler/flux2, with no SD-style negative prompt. The director Qwen3.5 and the image text encoder Qwen3 are separate models.

An initial native configuration selected Intel instead of NVIDIA and was much slower. A GPU configuration with a CPU VAE also took approximately 232 s at 512px, including 146 s in VAE decoding. Full-GPU 512px exceeded available workspace; 448px text-only passed. Two 224px identity references fit at a 384px output; editing uses a 320px source plus at most one identity reference. Native runtime logs reported BF16 VAE weights; native Vulkan decoding produced visible images. The SD float32 VAE fix remains specific to its tested CUDA path.

These are short production measurements, **not a 1000-image throughput benchmark**. At 50 s/image, 1000 images take roughly 14 hours before directing, QC, retries or pauses. Fast Local with SD previews is faster; native Klein has better scene/reference behavior in the tested story. Maximum Quality increases checking within available hardware, not model size or output resolution beyond validated limits.

## Providers and references

ImageProvider exposes generation/edit/inpaint/upscale entry points, health, capabilities, recommendation and settings validation. Unsupported controls fail before queuing. A provider advertises only its real features; the native Klein path does not claim ControlNet, IP-Adapter, LoRA, masked inpaint or a neural upscaler. Existing SD supports IP-Adapter and masked inpaint. ComfyUI capabilities come from the configured API workflow.

References are selected by shot angle and participating main characters. Multiple main identities take priority over location references. Current appearance overrides reference clothing. Structured canonical constraints are retained when Qwen refines the visual direction. Per-image metadata records provider/model/workflow, sampler/scheduler/seed/resolution, prompt/negative, conditioning settings, references/preprocessing, edit source, runtime, attempts, QC and intentional changes. An individual shot can change provider/model independently of its chapter.

For ComfyUI, provide an exported API workflow JSON wrapped with `prompt`, `bindings`, `model`, `name`, and truthful `capabilities`; configure the localhost endpoint/workflow path in Advanced runtime settings. Bindings map model-specific prompt/seed/dimension/settings fields to node inputs. Reference/source/mask bindings upload the selected images. The adapter checks node classes and installed weight selections before submission, tracks its own prompt ID, and cancels only its own job. Installation of ComfyUI/custom nodes/models is not automatic. Larger model memory requirements must be declared; oversized GPU workflows are rejected unless explicitly configured for CPU/offload.

DirectorProvider isolates creative decisions from project storage. The supplied implementation uses local llama.cpp; replacing the provider class/runtime does not change project, image, timeline or render formats. No future API integration is presented as currently installed.

## Queue and review behavior

The production queue accepts up to 1000 unfinished jobs, stores immutable per-shot settings and seed, supports priority, retry, health checks, errors and ETA. Pause lets the current native image finish and saves it; subsequent jobs wait. Director/audio work pauses at safe checkpoints. Cancel requests the current backend to stop and cancels queued work while retaining completed assets. A helper restart recovers interrupted jobs as queued/paused; Resume restarts the interrupted item with its saved seed and assets. It does not resume a half-computed diffusion latent. Models stay loaded between compatible images and are released before audio/director use.

Visual QC uses the optional local Qwen vision projector, loaded on CPU to conserve GPU memory. Its observations are **advisory**: the actual test found inaccurate/contradictory observations, including a pass paired with repair issues. Validation rejects such a pass; unavailable vision is UNREVIEWED, and retry limits prevent endless regeneration. The user can inspect, repair or manually accept the image. Source editing was exercised and retained the source/identity, but it did not reliably correct fine left/right details in the test. No exact character/handedness guarantee is claimed.

## Rendering and verification

One chapter timeline begins at narration 00:00. Actual sentence durations anchor scene/shot boundaries. Still-image pan/zoom is optional; per-boundary cuts/crossfades preserve narration duration. Only a bounded number of inputs are decoded at once. Render signatures reuse unchanged chapters. Full story joins chapter order and adds the optional intro only where configured. Chapter exports include an intro only when Every chapter is explicitly selected; the narration WAV remains separate.

The automated fictional fixture has two chapters, Michael/Sarah, a supporting guard/teacher, an unnamed temporary person, a key handover, injury, clothing/hairstyle changes, multiple locations, dialogue, action, quiet moments and chapter headings. Real local testing produced chapter narration durations 45.725 s and 44.275 s, two chapter MP4s, a full story starting directly at Chapter 1, and a second version with a separate 10 s intro. Character reference generation, multi-reference shots, source editing and vision input were exercised. Unit tests cover persistence/CAS, evidence/schema validation, clean narration, object/appearance handoffs, dependent-chapter warnings, provider settings and 1000-job cancel/recovery/retry.

The final revised storyboard produced **7 shots in Chapter 1 and 8 in Chapter 2** using the real local models. Generation took 360.4 s and 336.3 s respectively; chapter rendering took 18.2 s and 19.2 s. FFprobe verified chapter videos at 45.725 s and 44.292 s, full story at 90.017 s, and the separate-intro version at 100.017 s. All 15 PNGs passed visible-pixel checks and retained actual provider/seed metadata. Video frame rounding is bounded by the selected frame rate. Full-story assembly copies cached chapter video and encodes joined original narration WAVs once, avoiding accumulated AAC padding.

Live native cancellation stopped the current generation in 0.91 s; the remaining queued images did not run. A cancelled image was retried with its original seed. One shot switched to the SD fallback without removing its previous native PNG; CUDA audio worked after releasing the image models. A separate render fixture verified selected-boundary crossfades, full-story-only versus every-chapter intros, exact expected durations, and cache reuse. Failed/cancelled FFmpeg work stays at a staging filename and cannot become a completed cached asset. Queue snapshots retain old waiting jobs even after newer history fills the display window, with complete project counters.

Automated regression results: **39 Python backend tests and 40 existing JavaScript tests passed**. The 1000-job test exercises durable storage and controls without generating 1000 GPU images. Visual inspection found coherent environments, characters and story actions, but also a reversed key handover and imperfect injury/handedness detail. These are remaining model/QC limitations, not evidence of perfect semantic adherence.

Browser visual QA remains pending because a saved browser permission blocks the localhost preview even after the user’s approval. This limitation does not validate the rendered interface; syntax, backend and real generation/render tests are separate evidence. Test results and implementation notes should distinguish runtime success from semantic image correctness.

## Disk space and maintenance

The existing `.venv`, Torch/CUDA and Kokoro are preserved. Quantized director/image weights require about 7.7 GB combined, plus approximately 675 MB for optional vision and small native runtimes. The helper download contains source/web files only, not those models or a duplicate Python/Torch install. The optional model installer verifies hashes and reuses configured files that already exist. Native model provenance is recorded beside their files.

Never delete the base Python directory referenced by `.venv/pyvenv.cfg`; the venv depends on it. No automatic deletion of old model caches or generated assets occurs. SD remains available as fallback. Freeing checkpoint caches is a separate explicit action because selecting a deleted checkpoint downloads it again.

## October 2: full-frame motion and Luna

Video settings now offer project-wide gentle motion, per-shot director motion, or static playback. Fill-frame cropping and gentle movement are the new-project defaults; existing project defaults are preserved. Automatic movement follows shot size/duration and respects manual shot motion. Versioned chapter and clip render caches prevent old static/letterboxed clips being reused after the renderer changes. Images, WAV narration, shot timing and previous rendered videos remain saved.

`OpenAIDirector` implements the existing director contract with GPT-6 Luna using the Responses API, streamed structured JSON, local schema validation, source-preserving context, separate provider/model/reasoning cache identity and token-usage timing metadata. The local Qwen provider remains available; no silent fallback occurs. API credentials are stored outside project data and omitted from configuration reads, Git and the source-only helper package. Cloud setup verifies model access before selecting Luna. Live Luna generation remains unverified until API billing/key setup is complete.

The Runpod account, 50 GB network volume and RTX 5090 have not been provisioned yet. See [Cloud setup](./CLOUD-SETUP.md). Remote generation uses the existing ComfyUI adapter through an SSH tunnel, with an exported API workflow and explicit reference bindings. Image-model selection remains pending an actual ten-shot benchmark; Qwen-Image-Edit-2511 and full-precision FLUX are candidates, not validated cloud defaults. No remote models were downloaded onto the laptop and the working `.venv` was preserved.
