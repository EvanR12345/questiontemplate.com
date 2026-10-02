# Shared QuestionTemplate NVIDIA helper

Audio and Studio use one localhost service and the existing `.venv`. `start-windows.bat` launches installed code without overwriting fixes or reinstalling Torch. This launcher requires an existing shared environment; it never creates or replaces one.

## Use the website

1. Run `C:\Users\rezke\QuestionTemplateHelper\start-windows.bat`. Keep its window open.
2. It opens `https://questiontemplate.com/manga.html` with its pairing fragment. Allow Local network access if your browser asks. Once connected, Audio and Studio reconnect after refresh. The pairing token is saved in the helper's private `.pairing-key` file and browser storage; it is excluded from image metadata and exports.
3. Studio: paste your story, Analyze, review Cast and Layout, then Draw.
4. Select Volume for large batches, then Generate missing or Generate all.

The optional bundled `web/` directory remains available as a local fallback at `http://127.0.0.1:8765/app/`. The launcher opens the public website. Localhost API endpoints require pairing and restrict cross-origin access to questiontemplate.com and localhost. The service binds to 127.0.0.1 only.

## Image queue

The helper stores up to 1000 outstanding images in SQLite and saves each completed PNG plus seed/settings metadata in `outputs/`. Reference files are deduplicated. Browser project metadata and cached images use IndexedDB. Drawing and layout views show 50 panels at a time.

Pause holds the current image at its next denoising step, including its latents and scheduler. Resume continues that image. Cancel current discards that image and continues queued work unless paused. Cancel all discards the current image and cancels waiting jobs. Controls remain available while inference owns the GPU. Cancellation is checked before/after model loading, at every diffusion step, and before saving; an active download or CUDA kernel must return before it can stop.

After a helper restart or Audio switch, unfinished images restart with the saved seed when resumed. Completed images remain on disk. Three consecutive generation failures pause the queue for review. Retry failures preserves seeds.

Audio requests yield image work at its next step, pause the queue, unload image pipelines, and restore Kokoro to CUDA. Return to Studio and Resume to continue images. Audio remains float32 and retains its existing phonemizer/encoder.

## Prompt following and scene continuity

Studio now offers DreamShaper 8 (a SD 1.5 finetune for illustrated scenes) and the original SD 1.5 model. DreamShaper's fp16 weights download once, about 2.1 GB, into the existing Hugging Face cache. No environment, Torch or CUDA installation is needed. Only one image model is loaded at a time; the queue reuses it until the chosen model changes or Audio needs the GPU.

The planner groups nearby paragraphs until a location or explicit scene transition changes, keeps sentences as panels within that scene, and resolves simple pronouns from the last named cast. Review this heuristic plan: it is not a language model interpreting the story. Existing edited prompts, images and N/A selections remain intact when old projects gain scene groups. Panel scene assignments are editable.

Layout has a shared story-world field, shared setting/time/lighting for each scene, an action-first prompt and a complete prompt preview. CLIP embeddings are encoded in up to four 75-token windows, with equal-length negative embeddings. Longer prompts fail with a clear message instead of dropping the ending silently. Appearance descriptions and locked portraits are still necessary; a character's name alone does not define its appearance.

For subsequent panels with the same selected cast, the worker can add the first completed panel in that scene as a visual reference. It resolves the image when the job runs, including during a batch submitted before the first image exists. It never looks into another project. Inpaint and explicitly different/N/A casts skip incompatible scene references. Up to three references total are supported; three character references leave no slot for a scene image. If the anchor has not completed, the job uses text and available cast references. Metadata records whether the anchor was used. Disable the per-panel scene reference checkbox when changing composition requires it.

DreamShaper remains a small local model, with limited spatial reasoning and multi-character identity binding. More steps or references do not guarantee correct complex actions or exact faces. Test Balanced at 512×512 on several representative panels before a large batch. Volume is for previews; final panels usually benefit from Balanced or Quality.

## GPU fix and presets

On this GTX 1650, the original all-fp16 pipeline produced nonfinite denoising latents and NaN VAE output that silently became all-zero PNGs. Reproduced with seed 123 and a simple daylight apple prompt, without references or inpainting. Disabling cuDNN made the same prompt visible but was slow. The installed fix retains fp16 model weights/attention while computing GTX 16xx convolutions and VAE encoding/decoding in float32. PyTorch SDPA replaces maximum attention slicing; model CPU offload fits the 4 GB GPU. Every step checks finite latents; decoding checks finite pixels and rejects completely black outputs.

Volume: 8 steps, 512×512. Fast: 12 steps. Balanced: 20 steps. Quality: 30 steps. A warm Volume image measured 23.3 seconds on this GTX 1650; cold model loading adds time. This is a short measured sample, not a 1000-image stress run or throughput guarantee. At that rate, 1000 images take about 6.5 hours before pauses, references, retries, or loading.

Inpainting shares the base SD 1.5 model using its four-channel latent blending path; no second multi-GB inpaint model is downloaded. Masks must match source dimensions; white repaints, black preserves. Unmasked source pixels are composited unchanged. Reference preprocessing preserves all images in a square canvas; embeddings cache by content with a bounded 16-entry cache. Models prefer existing cached files before checking the network.

## Validation

`python -m unittest discover -s nvidia-helper -p '*_test.py'` covers the HTTP bridge and durable queue, including 1000-job cancellation, pause/resume, retry failure handling, restart recovery, and Audio yielding. GPU tests additionally verified visible pixels, disk PNG persistence, pause/resume/cancellation, preserved inpaint pixels, and Audio after image unload. JavaScript checks include the shared protocol 2 connection and existing audio regressions.
