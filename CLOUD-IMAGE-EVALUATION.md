# Cloud image selection — October 2, 2026

Target: one funded Runpod account for both 71 GB persistent storage and an available high-end GPU. Prefer an RTX PRO 6000 (96 GB) for the first reference/quality benchmark; retain the RTX 5090 (32 GB) as a lower-cost option when available. Preserve the existing 6–7 story images per minute, original project data, references, assets, and working local voice environment.

## Recommendation pending a real benchmark

Test **Qwen-Image-Edit-2511 FP8 mixed** first for shots containing recurring characters and for continuity repair. Use a dedicated text-to-image workflow to create initial reference sheets or images that have no source/reference. Qwen-Image-2512 is a quality candidate for that role. Compare the result against FLUX.2 Klein 4B at higher resolution on the cloud GPU. This is an evidence-based shortlist, not a claim that a cloud model has already been installed or that it wins on the user's characters.

| Candidate | References and edits | Practical 5090 approach | Main tradeoff / decision |
| --- | --- | --- | --- |
| Current FLUX.2 Klein 4B Q4 laptop workflow | Existing reference edit support | Retain as local fallback | Small, heavily quantized, constrained-resolution baseline; use saved outputs for comparison. |
| FLUX.2 Klein 4B full precision / Base | Multi-reference generation and editing | Fits comfortably according to BFL's approximately 13 GB reference estimate; actual multi-reference peak must be measured | Apache 2.0. Distilled variant offers speed; Base trades more steps for diversity/control. Does not guarantee desired manga style or identity. |
| Qwen-Image-Edit-2511 FP8 mixed | Character-preserving editing, multi-person fusion, appearance repair | Approximately 20.5 GB diffusion file plus encoder/VAE; use offload if measured peak requires it | Apache 2.0. First character/repair candidate. Forty-step quality mode may be slower; compare Lightning separately. Requires image input. |
| Qwen-Image-2512 FP8 workflow | Text-to-image; not a substitute for the edit model's multi-reference conditioning | Profile independently; share compatible Qwen encoder/VAE files | Apache 2.0. Candidate for environments and original reference-sheet creation. Separate diffusion checkpoint consumes archive space. |
| FLUX.2 Klein 9B / Base | Multiple references and editing | Profile resident components and references on the 32 GB card; benchmark optimized precision if useful | BFL non-commercial weights license; commercial use needs the applicable license. Evaluation candidate, not automatic commercial default. |
| Qwen-Image-2.1 | Unified text-to-image and edits, up to ten references, local editing, transparency | New 7B visual component plus text encoder; assess compatible ComfyUI version, memory and actual reference workload | Released September 20, 2026. Qwen Research License restricts weights use to non-commercial research/evaluation without a separate license. Newness alone is not sufficient reason to replace a working provider. |

There is no measured seconds-per-image result for this user's 5090 yet. Vendor speed claims (including sub-second BFL claims measured on GB200) are not RTX 5090 benchmarks. Record actual end-to-end durations and repair counts rather than quoting sampling-only time.

## Character and appearance contract

Persistent project data owns identity, clothing, injuries, accessories, objects, location, relationships and chapter handoffs. Image models receive only the state applicable to the current shot.

Name the people and roles of references explicitly. Canonical face references establish identity, not a permanent outfit. For example: keep Michael's face, black hair and scar; render his current school uniform and watch on the left wrist even if the reference shows a black jacket. Do not condition unrelated faces from a previous scene onto a new cast. Keep intentional changes distinct from visual errors in QC.

Use the generated image as the first reference for a targeted repair, followed by relevant identity references. Tell the model exactly what to change and what to preserve. Limit repair attempts and retain the original image. Never claim pixel-exact preservation or perfect identity simply because editing is supported.

## Ten-shot acceptance set

Use accepted project reference images and real story facts. Preserve every prompt, seed, full workflow, model revision, dtype/quantization, dimensions, steps, sampler, guidance, image index mapping, source image, output, timings and error/retry history.

1. Close-up with a distinctive face/mark; match the canonical reference.
2. Two main characters with separate references, positions and distinct clothing.
3. The same character in a story-authorized new outfit; identity must remain stable.
4. Hair tied back plus a new facial injury; the old hairstyle must not be restored by QC.
5. An object acquired earlier and still held in the intended hand in the next chapter.
6. Quiet dialogue with a temporary supporting person; no permanent profile required.
7. Action composition in the selected anime/manga illustration style.
8. Wide establishing shot in the correct location with no unwanted people.
9. Targeted repair of a watch on the wrong wrist; preserve unaffected composition.
10. A later-chapter shot using its handoff state and planned appearance.

Begin around one megapixel with landscape dimensions suitable for full-frame video. Try higher resolution only after identity and adherence pass. Compare human identity/style review alongside bounded Luna/Qwen QC. Record analysis, audio, transfers, model loading, sampling, decoding, QC, retries and rendering separately. Maintain shot density across model comparisons.

Accept the default only after successful generation, one successful multi-character edit, a cancelled job that preserves previous outputs, downloaded outputs verified through the local helper, same-seed retry metadata preservation, and provider selection surviving refresh. Keep current images and the local provider until acceptance.

## Single-account storage design

The user now requires one Runpod account for both storage and compute. Create a 71 GB standard network volume in the same data center as the available GPU and mount it at `/workspace`. The sampled rate is $4.97/month, independent of GPU rental. Previous cross-account transfer planning is superseded.

Prepare SSH and cloud management credentials before starting paid compute. Preserve the local audio environment and project assets. Run downloads on the cloud worker, reuse compatible encoders and VAE files, and avoid duplicate diffusion variants until the benchmark justifies their space. Actual model loading, generation, reference editing and Luna QC remain mandatory acceptance checks.

## Storage performance and deployment selection

Choose compute availability before committing to a storage region. For a directly mounted network volume, the GPU and volume must be in the same data center and the account must have access to both. Geographic proximity alone does not establish compatibility or availability. For separate accounts using S3 transfers, optimize the archive region for S3 support and the compute region for available hardware, transfer cost/time, and the measured workload. A global volume removes a region restriction; it does not establish cross-account ownership/access.

Observed October 2, 2026 console quotes for 71 GB:

| Storage | Monthly storage quote | Deployment consideration |
| --- | --- | --- |
| Standard network volume | $4.97 | Region-bound when mounted. Lowest quoted archive cost; require S3-compatible region for the proposed two-account transfers. |
| High-performance network volume | $9.94 in the sampled premium regions | Up to 3x throughput / 4x IOPS are Runpod's parallel-storage benchmarks, not image-generation speedups. The tier may reduce loading/transfer waits; profile disk bottlenecks before paying the premium. |
| Global volume, beta | $6.39 for 71 GB stored, plus requests | Elastic and region-independent, optimized for read-heavy models. Limited file-locking/rename semantics make it unsuitable as an untested replacement for a transactional project store. |

Check the current console quote before creating any resource. Include GPU, container/working disk, retained disk while stopped, archive storage, model transfers, loading, retries, and output transfers in the cost comparison. A single 96 GB RTX PRO 6000 was deployed for actual validation at the observed $2.11/hour checkout, using the same account's 71 GB US-NE-1 volume. Benchmark results must distinguish real inference from connection checks.

The live catalog during this inspection showed RTX 5090 out of capacity under the checked filters. Available alternatives included 48 GB RTX 6000 Ada/L40S and 96 GB RTX PRO 6000. Availability and quoted host allocations change; these are alternatives to evaluate, capacity candidates, not a speed benchmark. Do not treat a two-GPU 24 GB offer as a single 48 GB GPU.

Before deployment, verify one GPU's VRAM, assigned system RAM, CPU allocation, local NVMe/SSD, supported CUDA and template, actual region, cloud type, and the complete hourly quote. For Qwen FP8, a 48 GB card provides more memory headroom than a 32 GB card, but actual peak memory with multiple references must still be measured. A 96 GB card can test higher-precision configurations at higher hourly cost; it does not guarantee a visually superior result. Obtain the user's choice if the requested RTX 5090 is unavailable.

- [Runpod high-performance storage, benchmarks and regional pricing](https://docs.runpod.io/storage/high-performance-storage)
- [Runpod global volumes, limitations and request charges](https://docs.runpod.io/storage/globalvolume/overview)
- [Runpod disk types and lifecycle](https://docs.runpod.io/pods/storage/types)

## Model and storage sources

## Fresh Luna production after the rejected fantasy draft

The rejected draft reused its original scene structure and some existing images. Its canonical protagonist profile also contained "black skin" despite the supplied chapters describing black hair and a beard rather than skin color. These are verified input and orchestration defects; they do not establish that the image model itself is adequate. Four-shot canvases introduce another composition and identity risk and are disabled for the fresh remake.

The fresh project preserves only source narration scripts and user-downloaded reference inputs. It discards old generated references, images, audio, plans and rendered videos. Christopher and Jonathan have distinct identities; Christopher remains clothed until the chapter's explicit stripping event. The user's appearance override is recorded separately from extracted story facts. Unsupported skin, eye, hair, build and height traits are filtered when extracting new identities. Every visual prompt includes the project illustration style and current appearance.

The candidate first trial is one landscape image per shot using Qwen-Image-Edit-2511 FP8 with the eight-step reference workflow. Qwen-Image-2512 remains available for unreferenced images. Neither this new configuration nor a replacement model is accepted until fresh single-character, multiple-character and intentional clothing-change samples pass visual inspection. No new weights are downloaded merely to change a model name. A poor sample stops bulk production; model or workflow changes require another sample.

The prepare-story queue performs fresh narration and the full selected director pipeline with the GPU worker stopped. Declared workflow configuration permits planning; live installation, capabilities and VRAM are validated before generation. Luna chooses new scene boundaries, shots, camera, motion, prompts and workflows. Strict schemas use strings for quoted source evidence; the application verifies the evidence rather than embedding arbitrary dialogue as enum literals. The separately authored first-15-chapter intro script is supplied with verified facts and narration timing for a new Luna-directed montage. Intro and chapter images receive the configured vision review. Title overlays are off by default.

The speech delivery version invalidates old narration signatures and normalizes extended interjections before supplying explicit vowel pronunciations to the existing Kokoro engine. Real synthesis and a real full-frame FFmpeg motion render were exercised without local image generation. Motion choices use valid enum values, and the fresh project uses 16% eased zooms; this is not a claim of exact motion matching to the reference channels.

The existing 50-cent combined spending cap still applies. Approximately 42.24 cents were estimated for the rejected production. Fresh preparation has its own bounded API ledger. A new cloud run needs an explicitly revised cap if its projected cumulative spend exceeds the remaining allowance. Cloud queue guards preserve outputs but do not stop infrastructure billing; stop the Runpod pod separately before local rendering. Storage continues accruing while the pod is stopped.

- [Runpod network volumes and standard pricing](https://docs.runpod.io/storage/network-volumes)
- [Runpod S3-compatible access](https://docs.runpod.io/storage/s3-api)
- [Qwen-Image-Edit-2511 model and Apache 2.0 license](https://huggingface.co/Qwen/Qwen-Image-Edit-2511)
- [ComfyUI Qwen-Image-Edit-2511 workflow](https://docs.comfy.org/tutorials/image/qwen/qwen-image-edit-2511)
- [ComfyUI-maintained FP8 mixed checkpoint](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/blob/main/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors)
- [Qwen-Image-2512](https://huggingface.co/Qwen/Qwen-Image-2512)
- [BFL Klein family capabilities and benchmark hardware](https://bfl.ai/blog/flux2-klein-towards-interactive-visual-intelligence)
- [BFL local weights licensing](https://help.bfl.ai/articles/7108141705-can-i-run-or-fine-tune-flux-2-klein-locally)
- [Qwen-Image-2.1 model](https://huggingface.co/Qwen/Qwen-Image-2.1)
- [Qwen-Image-2.1 actual license](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE)
- [ComfyUI Qwen-Image-2.1 workflows](https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1)
