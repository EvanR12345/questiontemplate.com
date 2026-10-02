# Studio cloud setup

The video can keep the existing 6–7 images per minute. Luna plans the story; a rented RTX 5090 generates images; the existing local voice environment continues to generate narration. Existing projects, references, image history and videos remain on the laptop.

## 1. Connect Luna

1. Open [OpenAI Platform](https://platform.openai.com/) and create or sign into your account.
2. Enable API billing in the Platform dashboard. Start with a small credit balance and review usage in the dashboard.
3. Create a project API key on [API keys](https://platform.openai.com/api-keys).
4. In QuestionTemplate Studio, open **Settings → Advanced AI settings → Cloud setup · Luna + Runpod**.
5. Paste the key into the password field and press **Save**. The helper checks access to `gpt-6-luna`, then selects Luna for this project. Connection verification does not generate a story or benchmark inference.

The key is stored in `.studio-secrets.json` beside the local helper. It is excluded from project exports, browser storage, the helper source archive and Git. Do not paste API keys into chat. Requests use the Responses API with structured output, streaming, `store: false`, bounded retries and per-pass caching. Actual input/output token usage is recorded alongside director timings. API use sends the relevant story context and, for visual QC, selected images to OpenAI.

Reference: [OpenAI quickstart](https://developers.openai.com/api/docs/quickstart), [Luna](https://developers.openai.com/api/docs/models/gpt-6-luna).

## 2. Create Runpod storage and rent the GPU

### Requested setup: two separate accounts

With separate accounts, reserve one for **71 GB standard network storage** and the other for compute. Do not merge their credits, create a team or put GPU charges on the storage account without changing that decision explicitly.

An archive can be named `questiontemplate-studio-archive` in an **S3-compatible region**, such as US-IL-1. Standard 71 GB storage costs **$4.97/month** at the published rate. If Create returns **“Insufficient funds, please add funds to continue”**, resolve account funding before using the archive. A monthly price is not necessarily the service's creation threshold; do not guess the minimum required balance.

For this split, use the storage account's S3-compatible API for explicit downloads/uploads. A direct cross-account filesystem mount has not been verified. A compute pod still needs its own working disk. Download/cache selected models there, run generation, and copy each completed result back to the archive. Persist job metadata before advancing the queue; verify the archived object before declaring an upload complete. Repeated cold starts can require downloading model weights again and incur paid setup time. Retaining a compute-side disk also costs money on the compute account. This cross-account transfer path is planned, **not connected or tested yet**.

Do not pass storage credentials to the website, director model, project JSON or logs. Use the storage account's S3 credentials only in the helper/worker configuration. Any remote credential provisioning remains a separate setup step. Keep a local copy of original project data and completed assets. The existing audio `.venv` must remain intact.

At the published standard rate, $5 funds approximately one month of a 71 GB volume. Storage must remain funded after GPU work stops. See [S3 access](https://docs.runpod.io/storage/s3-api) and [network volumes](https://docs.runpod.io/storage/network-volumes).

### Simpler alternative: one account (not the user's current choice)

1. Create an account at [Runpod Console](https://console.runpod.io/) and add billing.
2. Check **Pods** for an available **RTX 5090, 32 GB VRAM**. Note its data center before creating storage.
3. In **Storage**, create a **standard network volume**, name it `questiontemplate-studio`, choose **50 GB**, and choose that same data center. Network storage persists independently of the GPU pod.
4. Deploy **one RTX 5090** in that data center using a ComfyUI template and attach the network volume at `/workspace`. Review the actual GPU rate and container-storage charges shown before deployment.
5. Store models, the image workflow and generated assets under `/workspace`. Install only the selected model's required files; 50 GB should not become a collection of duplicate checkpoints.
6. Enable SSH for the pod. The helper's existing ComfyUI provider can connect through a localhost SSH port forward to the pod's ComfyUI port. This keeps the local authenticated helper connection shared with Audio and avoids exposing a public, unauthenticated generator.
7. After the pod is running, use its **Connect** details to create the SSH tunnel. The exact host, port and user depend on the deployed pod. Export the tested ComfyUI **API workflow** and supply its node bindings, capabilities and model name to the helper configuration.

Reference: [Network volumes](https://docs.runpod.io/storage/network-volumes), [Pod connections](https://docs.runpod.io/pods/connect-to-a-pod), [Manage pods](https://docs.runpod.io/pods/manage-pods).

## 3. Select the image workflow through an actual benchmark

The 5090 has not been provisioned or benchmarked yet. There is no honest single “best model” conclusion before testing the actual character references and scenes.

First compare ten representative shots: a close-up, two characters, an action, a wide location, selected illustration/anime style, and targeted appearance repair. Keep prompts, saved seeds and required references in the benchmark record. Compare total time including retries, prompt adherence, identity, correct appearance, editing, memory and output resolution.

- **FLUX.2 Klein 4B, full precision on the remote GPU:** established reference/edit workflow, Apache 2.0; compare at larger native resolution against the existing low-memory Q4 workflow. This is the baseline, not a claim of maximum quality.
- **Qwen-Image-Edit-2511, memory-compatible quantized/offloaded ComfyUI workflow:** candidate for stronger identity-preserving editing and multiple characters. Apache 2.0. Validate runtime and the exact quantization before selecting it.
- **FLUX.2 Klein 9B:** candidate for stronger generation/reference quality and speed, but released under a non-commercial model license. Review the license and intended use before choosing this workflow for a monetized project.
- **Qwen-Image-2.1:** newer unified generation/editing model with up to ten references. Released September 20, 2026, under a **research-only/non-commercial license**, unlike Qwen-Image-Edit-2511's Apache 2.0 license. Keep it as an evaluation candidate; do not enable it for commercial Studio production without the required license.

The current first quality candidate is **Qwen-Image-Edit-2511 FP8 mixed**, using ComfyUI's maintained repack and model-specific natural-language instructions. The diffusion weights are approximately 20.5 GB on disk, the FP8 text encoder 9.38 GB, and VAE 254 MB. Disk size is not peak VRAM: profile the entire graph on the 32 GB 5090, including references, activations and decoding, and offload the encoder when necessary. Do not claim BF16 inference fits entirely in 32 GB: the diffusion weights alone are approximately 40.9 GB. Start at roughly one megapixel with two character references and 40 steps; compare a 4-step Lightning workflow separately for speed versus fidelity. Do not apply a Lightning LoRA blindly to an incompatible quantized model.

Qwen-Image-Edit requires image input. Existing accepted character references can supply that input. For initial reference sheets and shots with no relevant image input, use a separate validated text-to-image workflow, such as Qwen-Image-2512, rather than pretending the edit graph works without references. Share compatible encoders and VAE files, and retain only required model variants on the 71 GB archive. The current laptop generator remains available until both cloud generation and editing pass actual tests.

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
