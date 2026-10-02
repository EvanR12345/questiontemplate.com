# Studio cloud setup

The video can keep the existing 6–7 images per minute. Luna plans the story; a rented cloud GPU generates images; the existing local voice environment continues to generate narration. Existing projects, references, image history and videos remain on the laptop.

## 1. Connect Luna

1. Open [OpenAI Platform](https://platform.openai.com/) and create or sign into your account.
2. Enable API billing in the Platform dashboard. Start with a small credit balance and review usage in the dashboard.
3. Create a project API key on [API keys](https://platform.openai.com/api-keys).
4. In QuestionTemplate Studio, open **Settings → Advanced AI settings → Cloud setup · Luna + Runpod**.
5. Paste the key into the password field and press **Save**. The helper checks access to `gpt-6-luna`, then selects Luna for this project. Connection verification does not generate a story or benchmark inference.

The key is stored in `.studio-secrets.json` beside the local helper. It is excluded from project exports, browser storage, the helper source archive and Git. Do not paste API keys into chat. Requests use the Responses API with structured output, streaming, `store: false`, bounded retries and per-pass caching. Actual input/output token usage is recorded alongside director timings. API use sends the relevant story context and, for visual QC, selected images to OpenAI.

Reference: [OpenAI quickstart](https://developers.openai.com/api/docs/quickstart), [Luna](https://developers.openai.com/api/docs/models/gpt-6-luna).

## 2. Create Runpod storage and rent the GPU

### Current setup: one Runpod account

Use the same funded account for the GPU and **71 GB of standard network storage**. Do not create resources in the previous storage-only account or attempt cross-account mounting.

1. Check current GPU availability before choosing a storage region. Prefer an available RTX PRO 6000 with 96 GB VRAM for the first Qwen quality/reference benchmark; an RTX 5090 with 32 GB remains a lower-cost alternative if available and the tested graph fits.
2. Create a standard 71 GB network volume named `questiontemplate-studio` in the **same data center** as the available GPU. The sampled monthly storage quote is **$4.97**. Review the console's final quote and account funding.
3. Select the **official ComfyUI CUDA 13.0 template** for Blackwell hardware. The current official image observed on October 2 is `runpod/comfyui:1.3.3-comfyuiv0.30.0-cuda13.0`. Its template requests a 150 GB container disk; that working disk is separate from the 71 GB persistent volume and has an hourly charge.
4. Enable SSH and register the helper's public key before the worker's first boot. The private SSH key stays on the laptop. Use a localhost port forward to ComfyUI; do not expose an unauthenticated image-generation endpoint publicly.
5. Download only the selected model files directly into the mounted cloud volume. Do not download multi-GB weights or another Torch install onto the laptop.
6. Export and validate the ComfyUI API workflow, attach real character references, and test generation through the website before selecting it for normal production.
7. Bound the paid test time and stop the GPU when idle. Persistent storage continues billing while the GPU is stopped; preserve it because it holds the models.

The Studio Cloud setup form accepts OpenAI and Runpod keys independently. Keys are saved in the local helper's `.studio-secrets.json`; updating one retains the other. Saving a Runpod key does not deploy a GPU or claim that the image worker is ready. Create credentials in their provider dashboards, enter them privately in Studio, and never paste them into chat.

The observed RTX PRO 6000 quote was $2.09/hour plus approximately $0.021/hour for the template's 150 GB container disk. GPU availability changes during setup. Keep the account identity, region, exact deployment quote and timed benchmark result in the private setup record. OpenAI API credit is separate from Runpod credit.

References: [Network volumes](https://docs.runpod.io/storage/network-volumes), [Pod connections](https://docs.runpod.io/pods/connect-to-a-pod), [Manage pods](https://docs.runpod.io/pods/manage-pods).

## 3. Select the image workflow through an actual benchmark

The cloud GPU has not been provisioned or benchmarked yet. There is no honest single “best model” conclusion before testing the actual character references and scenes.

First compare ten representative shots: a close-up, two characters, an action, a wide location, selected illustration/anime style, and targeted appearance repair. Keep prompts, saved seeds and required references in the benchmark record. Compare total time including retries, prompt adherence, identity, correct appearance, editing, memory and output resolution.

- **FLUX.2 Klein 4B, full precision on the remote GPU:** established reference/edit workflow, Apache 2.0; compare at larger native resolution against the existing low-memory Q4 workflow. This is the baseline, not a claim of maximum quality.
- **Qwen-Image-Edit-2511, memory-compatible quantized/offloaded ComfyUI workflow:** candidate for stronger identity-preserving editing and multiple characters. Apache 2.0. Validate runtime and the exact quantization before selecting it.
- **FLUX.2 Klein 9B:** candidate for stronger generation/reference quality and speed, but released under a non-commercial model license. Review the license and intended use before choosing this workflow for a monetized project.
- **Qwen-Image-2.1:** newer unified generation/editing model with up to ten references. Released September 20, 2026, under a **research-only/non-commercial license**, unlike Qwen-Image-Edit-2511's Apache 2.0 license. Keep it as an evaluation candidate; do not enable it for commercial Studio production without the required license.

The current first quality candidate is **Qwen-Image-Edit-2511 FP8 mixed**, using ComfyUI's maintained repack and model-specific natural-language instructions. The diffusion weights are approximately 20.5 GB on disk, the FP8 text encoder 9.38 GB, and VAE 254 MB. Disk size is not peak VRAM: profile the entire graph on the 32 GB 5090, including references, activations and decoding, and offload the encoder when necessary. Do not claim BF16 inference fits entirely in 32 GB: the diffusion weights alone are approximately 40.9 GB. Start at roughly one megapixel with two character references and 40 steps; compare a 4-step Lightning workflow separately for speed versus fidelity. Do not apply a Lightning LoRA blindly to an incompatible quantized model.

Qwen-Image-Edit requires image input. Existing accepted character references can supply that input. For initial reference sheets and shots with no relevant image input, use a separate validated text-to-image workflow, such as Qwen-Image-2512, rather than pretending the edit graph works without references. Share compatible encoders and VAE files, and retain only required model variants on the 71 GB volume. The current laptop generator remains available until both cloud generation and editing pass actual tests.

Every identity reference must be named by its image index and associated with the correct character. A repair uses image 1 as its source; identity references follow it. Scene appearance state overrides clothing in reference images. Reference capacity includes the repair source. The adapter now checks ComfyUI's reported GPU memory, not the laptop's, and rejects unbound references instead of uploading and ignoring them. These adapter checks passed automated tests; they are not a cloud quality benchmark.

Sources: [Qwen editing workflow](https://docs.comfy.org/tutorials/image/qwen/qwen-image-edit-2511), [FP8 mixed weights](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/blob/main/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors), [Qwen-Image-2.1 model](https://huggingface.co/Qwen/Qwen-Image-2.1), [2.1 license](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE).

Model references: [FLUX 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B), [Qwen Image Edit](https://huggingface.co/Qwen/Qwen-Image-Edit-2511), [FLUX 9B](https://huggingface.co/black-forest-labs/FLUX.2-klein-9B).

## Costs and shutdown

Rates checked October 2, 2026: Runpod lists a 5090 at **US$0.99/hour**, and standard network storage at **US$0.07/GB/month**: **US$3.50/month for 50 GB**. Verify live rates in the console; availability, taxes and extra storage can change the total. These rates do not predict generation duration.

Start with a small benchmark rather than enqueueing 800 images immediately. Stop the GPU after the outputs have downloaded and the worker is idle. GPU rental stops when the pod is stopped, but retained storage continues billing. Do not delete the network volume while it holds needed assets.

References: [GPU pricing](https://www.runpod.io/pricing), [storage pricing](https://docs.runpod.io/storage/network-volumes).

## Full-frame video and motion

New projects default to **Fill frame** and **Gentle zooms and pans**. Fill frame crops the image edges to match the video aspect ratio. Generate landscape shots for full-width compositions. Existing projects retain their settings until changed.

In **Settings → Video**, select **Fill frame (crop edges)** and **Gentle zooms and pans**, then save and render. AI static holds gain slow, composition-aware movement. Brief shots and inserts remain static; manually edited shot motion is preserved. Choose **Use each shot’s motion** or **All images static** to override. Changes affect rendering only: they do not regenerate narration or images, and old videos remain in history.
