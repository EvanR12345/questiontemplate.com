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

## GPU fix and presets

On this GTX 1650, the original all-fp16 pipeline produced nonfinite denoising latents and NaN VAE output that silently became all-zero PNGs. Reproduced with seed 123 and a simple daylight apple prompt, without references or inpainting. Disabling cuDNN made the same prompt visible but was slow. The installed fix retains fp16 model weights/attention while computing GTX 16xx convolutions and VAE encoding/decoding in float32. PyTorch SDPA replaces maximum attention slicing; model CPU offload fits the 4 GB GPU. Every step checks finite latents; decoding checks finite pixels and rejects completely black outputs.

Volume: 8 steps, 512×512. Fast: 12 steps. Balanced: 20 steps. Quality: 30 steps. A warm Volume image measured 23.3 seconds on this GTX 1650; cold model loading adds time. This is a short measured sample, not a 1000-image stress run or throughput guarantee. At that rate, 1000 images take about 6.5 hours before pauses, references, retries, or loading.

Inpainting shares the base SD 1.5 model using its four-channel latent blending path; no second multi-GB inpaint model is downloaded. Masks must match source dimensions; white repaints, black preserves. Unmasked source pixels are composited unchanged. Reference preprocessing preserves all images in a square canvas; embeddings cache by content with a bounded 16-entry cache. Models prefer existing cached files before checking the network.

## Validation

`python -m unittest discover -s nvidia-helper -p '*_test.py'` covers the HTTP bridge and durable queue, including 1000-job cancellation, pause/resume, retry failure handling, restart recovery, and Audio yielding. GPU tests additionally verified visible pixels, disk PNG persistence, pause/resume/cancellation, preserved inpaint pixels, and Audio after image unload. JavaScript checks include the shared protocol 2 connection and existing audio regressions.
