"""Image backends are replaceable; no story facts or timeline logic lives here."""

import base64, copy, hashlib, io, json, math, secrets, subprocess, time, urllib.request
from pathlib import Path
from PIL import Image, ImageStat
from director_provider import local_url, request_json

BASE_CAPS = {
    k: False
    for k in (
        "supportsMultipleReferences",
        "supportsImageEditing",
        "supportsNegativePrompt",
        "supportsSeed",
        "supportsPoseReference",
        "supportsControlNet",
        "supportsIPAdapter",
        "supportsStyleReference",
        "supportsLoRA",
        "supportsInpainting",
        "supportsOutpainting",
        "supportsUpscaling",
        "supportsVisionInput",
        "supportsBatchGeneration",
    )
}
BASE_CAPS.update(
    maxReferenceImages=0,
    maxResolution=512,
    recommendedResolution=[512, 512],
    recommendedSteps=20,
    recommendedSampler="DPM++ 2M",
    recommendedGuidance=7,
    recommendedDenoisingStrength=0.65,
    promptFormat="sd-tags",
    negativePromptFormat="comma-tags",
    hardwareRequirements={"vramGB": 4, "ramGB": 8},
)
_GPU_MEMORY = (0, None)


def gpu_memory_gb():
    global _GPU_MEMORY
    if time.time() - _GPU_MEMORY[0] > 60:
        try:
            memory = (
                float(
                    subprocess.check_output(
                        [
                            "nvidia-smi",
                            "--query-gpu=memory.total",
                            "--format=csv,noheader,nounits",
                        ],
                        text=True,
                    ).splitlines()[0]
                )
                / 1024
            )
        except Exception:
            memory = None
        _GPU_MEMORY = (time.time(), memory)
    return _GPU_MEMORY[1]


def data_url(path):
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode()


def visible_image(raw):
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img.load()
    if max(max(v) for v in img.getextrema()) <= 2:
        raise RuntimeError(
            "Generator returned a black image. The output was rejected; inspect model precision, VAE and workflow."
        )
    return img


class ImageProvider:
    id = "abstract"

    def available_vram_gb(self):
        return gpu_memory_gb()

    def getCapabilities(self):
        return dict(BASE_CAPS)

    def getRecommendedSettings(self):
        return {
            "width": 512,
            "height": 512,
            "steps": 20,
            "sampler": "dpm++2m",
            "scheduler": "karras",
            "guidance": 7,
            "denoisingStrength": 0.65,
        }

    def validateSettings(self, s):
        caps = self.getCapabilities()
        result = copy.deepcopy(s)
        memory = self.available_vram_gb()
        requirements = caps.get("hardwareRequirements", {})
        minimum = requirements.get("vramGB", 0)
        if (
            memory is not None
            and minimum > memory + 0.05
            and requirements.get("mode") not in ("cpu", "cpu-offload")
        ):
            raise ValueError(
                f"This workflow requires approximately {minimum} GB VRAM; this GPU has {memory:.1f} GB. Select a smaller model or explicitly configure a tested CPU/offload workflow."
            )
        for field in ("width", "height", "steps"):
            n = result.get(field, self.getRecommendedSettings()[field])
            if (
                isinstance(n, bool)
                or not isinstance(n, (int, float))
                or not math.isfinite(n)
            ):
                raise ValueError("Invalid " + field)
            result[field] = int(n)
        if (
            not 256 <= result["width"] <= caps["maxResolution"]
            or not 256 <= result["height"] <= caps["maxResolution"]
        ):
            raise ValueError("Resolution exceeds this provider’s tested memory limits.")
        if (
            result["width"] % 8
            or result["height"] % 8
            or not 1 <= result["steps"] <= 60
        ):
            raise ValueError("Use dimensions divisible by eight and 1–60 steps.")
        if result.get("loras") and not caps["supportsLoRA"]:
            raise ValueError("This workflow does not support LoRA.")
        if result.get("controlnets") and not caps["supportsControlNet"]:
            raise ValueError("This workflow does not support ControlNet.")
        if result.get("ipAdapter") and not caps["supportsIPAdapter"]:
            raise ValueError("This workflow does not support IP-Adapter.")
        if result.get("seed") is None:
            result["seed"] = secrets.randbelow(2**31)
        if (
            isinstance(result["seed"], bool)
            or not isinstance(result["seed"], int)
            or not 0 <= result["seed"] < 2**32
        ):
            raise ValueError("Invalid seed.")
        return result

    def generateImage(self, request, checkpoint):
        raise NotImplementedError

    def editImage(self, request, checkpoint):
        if not self.getCapabilities()["supportsImageEditing"]:
            raise ValueError("Image editing unavailable for this workflow.")
        return self.generateImage(request | {"operation": "edit"}, checkpoint)

    def inpaintImage(self, request, checkpoint):
        if not self.getCapabilities()["supportsInpainting"]:
            raise ValueError("Inpainting unavailable for this workflow.")
        return self.generateImage(request | {"operation": "inpaint"}, checkpoint)

    def upscaleImage(self, request, checkpoint):
        raise ValueError(
            "No neural upscaler is installed. Rendering can resize images."
        )

    def unload(self):
        pass

    def healthCheck(self):
        return {"installed": False}


class ExistingImageProvider(ImageProvider):
    id = "existing"

    def __init__(self, engine):
        self.engine = engine

    def getCapabilities(self):
        return BASE_CAPS | {
            "supportsMultipleReferences": True,
            "supportsNegativePrompt": True,
            "supportsSeed": True,
            "supportsIPAdapter": True,
            "supportsStyleReference": True,
            "supportsInpainting": True,
            "supportsImageEditing": True,
            "supportsVisionInput": False,
            "maxReferenceImages": 3,
            "maxResolution": 768,
            "recommendedResolution": [512, 512],
            "promptFormat": "sd-tags",
            "negativePromptFormat": "comma-tags",
            "hardwareRequirements": {"vramGB": 4, "ramGB": 8},
            "referenceLimitations": "IP-Adapter combines references; separate multiple-character identity is approximate.",
        }

    def healthCheck(self):
        if time.time() - getattr(self, "checked", 0) > 15:
            self.checked = time.time()
            self.cached = []
            try:
                from huggingface_hub import snapshot_download

                for model, repo in (
                    ("sd15", "stable-diffusion-v1-5/stable-diffusion-v1-5"),
                    ("dreamshaper8", "Lykon/dreamshaper-8"),
                ):
                    try:
                        folder = Path(snapshot_download(repo, local_files_only=True))
                        if all(
                            (folder / name).is_file()
                            for name in (
                                "model_index.json",
                                "text_encoder/model.fp16.safetensors",
                                "unet/diffusion_pytorch_model.fp16.safetensors",
                                "vae/diffusion_pytorch_model.fp16.safetensors",
                            )
                        ):
                            self.cached.append(model)
                    except Exception:
                        pass
            except ImportError:
                pass
        return {
            "installed": bool(self.cached),
            "models": self.cached,
            "workflow": ["text-to-image", "ip-adapter", "inpaint"],
            "error": (
                ""
                if self.cached
                else "SD weights are not cached. Use an installed provider or explicitly install the checkpoint; no download was started."
            ),
        }

    def validateSettings(self, s):
        s = super().validateSettings(s)
        if s.get("model") not in ("sd15", "dreamshaper8"):
            raise ValueError("Unknown existing model.")
        if s["model"] not in self.healthCheck()["models"]:
            raise ValueError(
                "Selected SD checkpoint is not installed. Choose a cached model; no automatic multi-GB download was started."
            )
        if (
            not 384 <= s["width"] <= 768
            or not 384 <= s["height"] <= 768
            or not 8 <= s["steps"] <= 50
        ):
            raise ValueError("SD fallback needs dimensions 384–768 and 8–50 steps.")
        if (
            s.get("sampler", "DPM++ 2M") not in ("DPM++ 2M", "dpm++2m")
            or s.get("scheduler", "karras") != "karras"
        ):
            raise ValueError("Existing backend supports DPM++ 2M / karras only.")
        return s

    def generateImage(self, r, checkpoint):
        s = self.validateSettings(r["settings"])
        operation = r.get("operation", "generate")
        if operation == "edit" and not r.get("mask"):
            raise ValueError(
                "SD fallback repairs need an inpaint mask. Use native FLUX editing or provide a mask."
            )
        data = s | {
            "prompt": r["prompt"],
            "negative": r.get("negativePrompt", ""),
            "reference_images": r.get("referenceImages", [])[:3],
            "reference_strength": s.get("referenceStrength", 0.65),
            "preset": "balanced",
            "operation": "inpaint" if operation in ("edit", "inpaint") else "generate",
        }
        if data["operation"] == "inpaint":
            data.update(
                image=r["sourceImage"],
                mask=r["mask"],
                denoising_strength=s.get("denoisingStrength", 0.65),
            )
        result = self.engine().generate(data, checkpoint)
        result["provider"] = self.id
        return result

    def unload(self):
        self.engine().unload()


class NativeFluxProvider(ImageProvider):
    id = "native-flux"

    def __init__(self, config, log_root):
        self.config = config
        self.root = Path(log_root)
        self.process = None
        self.log = None
        self.url = "http://127.0.0.1:8767"

    def getCapabilities(self):
        return BASE_CAPS | {
            "supportsMultipleReferences": True,
            "supportsImageEditing": True,
            "supportsSeed": True,
            "supportsStyleReference": True,
            "supportsVisionInput": True,
            "maxReferenceImages": 2,
            "maxResolution": 448,
            "recommendedResolution": [448, 448],
            "recommendedSteps": 4,
            "recommendedSampler": "euler",
            "recommendedGuidance": 1,
            "promptFormat": "natural-language",
            "negativePromptFormat": "unsupported",
            "hardwareRequirements": {
                "vramGB": 4,
                "ramGB": 8,
                "mode": "quantized Q4, mmap disk weights, sequential GPU use; references/editing at 384px",
            },
        }

    def getRecommendedSettings(self):
        return {
            "width": 448,
            "height": 448,
            "steps": 4,
            "sampler": "euler",
            "scheduler": "flux2",
            "guidance": 1,
            "denoisingStrength": 1,
        }

    def healthCheck(self):
        installed = all(
            Path(self.config.get(k, "")).is_file()
            for k in ("imageExecutable", "fluxModel", "fluxEncoder", "fluxVae")
        )
        return {
            "installed": installed,
            "validated": bool(self.config.get("fluxValidated", False)),
            "models": ["flux2-klein-4b-q4"],
            "workflow": ["text-to-image", "reference-edit"],
            "running": bool(self.process and self.process.poll() is None),
        }

    def validateSettings(self, s):
        s = super().validateSettings(s)
        if s.get("model") != "flux2-klein-4b-q4":
            raise ValueError(
                "Only installed FLUX.2 Klein 4B Q4 is supported by this adapter."
            )
        if s.get("denoisingStrength", 1) != 1:
            raise ValueError(
                "Native Klein uses instruction-based editing. Its workflow has no SD denoising-strength control; use 1 or choose a masked SD/Comfy workflow."
            )
        if s.get("guidance", 1) != 1:
            raise ValueError(
                "Distilled Klein uses guidance 1. Choose its preset to reset SD settings."
            )
        if (
            s.get("sampler", "euler") != "euler"
            or s.get("scheduler", "flux2") != "flux2"
        ):
            raise ValueError("Use Euler / flux2 for this tested Klein workflow.")
        if not self.healthCheck()["installed"]:
            raise RuntimeError(
                "FLUX native files are unavailable. Choose an installed provider or configure the native model paths."
            )
        return s

    def start(self, checkpoint):
        if self.process and self.process.poll() is None:
            return
        if not self.healthCheck()["installed"]:
            raise RuntimeError("Native FLUX runtime/model files missing.")
        self.root.mkdir(parents=True, exist_ok=True)
        self.log = (self.root / "native-images.log").open("w", encoding="utf-8")
        backend = self.config.get("imageBackend")
        if not backend:
            raise ValueError(
                "Configure imageBackend using the detected NVIDIA Vulkan device. Integrated GPU selection is not allowed implicitly."
            )
        args = [
            self.config["imageExecutable"],
            "--listen-ip",
            "127.0.0.1",
            "--listen-port",
            "8767",
            "--diffusion-model",
            self.config["fluxModel"],
            "--llm",
            self.config["fluxEncoder"],
            "--vae",
            self.config["fluxVae"],
            "--mmap",
            "--params-backend",
            "disk",
            "--backend",
            backend,
            "--max-vram",
            "3.7",
            "--conditioning-cache-size",
            "8",
            "-t",
            "4",
        ]
        self.process = subprocess.Popen(
            args,
            stdout=self.log,
            stderr=self.log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for _ in range(180):
            checkpoint(0, 4, "Loading quantized FLUX")
            if self.process.poll() is not None:
                raise RuntimeError(
                    "Native FLUX failed to start. See outputs/studio/native-images.log."
                )
            try:
                request_json(self.url + "/sdcpp/v1/capabilities", timeout=1)
                return
            except Exception:
                time.sleep(0.5)
        self.unload()
        raise RuntimeError("Native FLUX startup timed out.")

    def generateImage(self, r, checkpoint):
        if r.get("operation") == "inpaint":
            raise ValueError(
                "Native Klein does not implement masked inpainting. Use its image-edit operation, or select a provider with masked inpaint support."
            )
        s = self.validateSettings(r["settings"])
        self.start(checkpoint)
        started = time.time()
        refs = r.get("referenceImages", [])[:2]

        def resize_reference(value, size):
            img = Image.open(
                io.BytesIO(base64.b64decode(value.split(",", 1)[-1]))
            ).convert("RGB")
            img.thumbnail((size, size))
            out = io.BytesIO()
            img.save(out, "PNG")
            return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()

        refs = [resize_reference(x, 224) for x in refs]
        if r.get("operation") == "edit":
            refs = [resize_reference(r["sourceImage"], 320)] + refs[:1]
        if refs and (s["width"] > 384 or s["height"] > 384):
            raise ValueError(
                "Reference/editing shots need at most 384×384 on this 4 GB GPU. Choose the Character Reference or Image Repair preset."
            )
        body = {
            "prompt": r["prompt"],
            "width": s["width"],
            "height": s["height"],
            "seed": s["seed"],
            "batch_count": 1,
            "ref_images": refs,
            "increase_ref_index": True,
            "sample_params": {
                "sample_steps": s["steps"],
                "sample_method": "euler",
                "scheduler": "flux2",
                "guidance": {"txt_cfg": 1, "img_cfg": 1, "distilled_guidance": 1},
            },
            "vae_tiling_params": {
                "enabled": True,
                "tile_size_w": 256,
                "tile_size_h": 256,
            },
        }
        job = request_json(self.url + "/sdcpp/v1/img_gen", body)
        jobid = job["id"]
        try:
            while True:
                info = request_json(self.url + "/sdcpp/v1/jobs/" + jobid)
                checkpoint(0, s["steps"], "FLUX " + info["status"])
                if info["status"] == "completed":
                    break
                if info["status"] in ("failed", "cancelled"):
                    tail = (self.root / "native-images.log").read_text(
                        errors="replace"
                    )[-1800:]
                    raise RuntimeError(
                        f'FLUX failed at {s["width"]}×{s["height"]} on this local backend: {info.get("error")}. Use 384×384 for references/repair and close other GPU apps. Runtime details: '
                        + tail
                    )
                time.sleep(0.3)
        except Exception:
            try:
                request_json(self.url + "/sdcpp/v1/jobs/" + jobid + "/cancel", {})
            except Exception:
                pass
            raise
        result = info["result"]
        img = visible_image(base64.b64decode(result["images"][0]["b64_json"]))
        return {
            "pil": img,
            "seed": s["seed"],
            "seconds": time.time() - started,
            "provider": self.id,
            "model": "FLUX.2-klein-4B Q4_0",
            "precision": "Q4_0 transformer/text encoder; native BF16 VAE weights with Vulkan decoding; black-frame validation",
            "backend": self.config["imageBackend"],
            "referenceCount": len(refs),
            "referencePreprocessing": {
                "identityMaxPixels": 224,
                "sourceEditMaxPixels": 320,
            },
            "settings": s,
            "runtime": "stable-diffusion.cpp 3f8527a",
        }

    def unload(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None
        if self.log:
            self.log.close()
            self.log = None


class ComfyImageProvider(ImageProvider):
    id = "comfyui"

    def __init__(self, config):
        self.config = config
        self.url = local_url(config.get("comfyEndpoint", "http://127.0.0.1:8188"))
        self._server_memory = (0, None)
        self._health = (0, None)
        self._uploads = {}

    def available_vram_gb(self):
        # A localhost SSH tunnel can lead to a different computer's GPU.
        # Always inspect ComfyUI's device rather than the helper's laptop.
        if time.time() - self._server_memory[0] > 60:
            try:
                stats = request_json(self.url + "/system_stats", timeout=3)
                devices = stats.get("devices", [])
                memory = max(
                    (float(d.get("vram_total", 0)) / 2**30 for d in devices),
                    default=0,
                )
                if not math.isfinite(memory) or memory < 0:
                    raise ValueError("Invalid GPU memory reported by ComfyUI")
                self._server_memory = (time.time(), memory)
            except Exception as e:
                raise RuntimeError(
                    "Cannot verify the image server's GPU memory. Check ComfyUI and the SSH tunnel: "
                    + str(e)
                ) from e
        return self._server_memory[1]

    def template(self):
        path = self.config.get("comfyWorkflow")
        if not path or not Path(path).is_file():
            raise ValueError(
                "Configure an exported ComfyUI API workflow and bindings in studio-config.json."
            )
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def getRecommendedSettings(self):
        try:
            template = self.template()
            caps = self.getCapabilities()
            size = caps["recommendedResolution"]
            return {
                "width": size[0],
                "height": size[1],
                "steps": caps["recommendedSteps"],
                "sampler": caps["recommendedSampler"],
                "scheduler": "normal",
                "guidance": caps["recommendedGuidance"],
                "denoisingStrength": caps["recommendedDenoisingStrength"],
            } | template.get("recommendedSettings", {})
        except Exception:
            return super().getRecommendedSettings()

    def getCapabilities(self):
        try:
            return BASE_CAPS | self.template()["capabilities"]
        except Exception:
            return BASE_CAPS | {
                "maxReferenceImages": 0,
                "maxResolution": 1024,
                "promptFormat": "natural-language",
                "hardwareRequirements": {"configuredWorkflowRequired": True},
            }

    def resolve_template(self, request, settings):
        root = self.template()
        variants = root.get("variants")
        if not variants:
            return root, settings
        model = settings.get("model", root["model"])
        if model not in {root["model"], *(v["model"] for v in variants)}:
            raise ValueError("Selected Comfy model is not installed in this workflow bundle.")
        workflow = request.get("workflow") or settings.get("workflow") or root["name"]
        references = bool(request.get("referenceImages")) or request.get("operation") == "edit"
        if request.get("purpose") == "character-reference" and not references and root.get("referenceCreationWorkflow"):
            workflow = root["referenceCreationWorkflow"]
            settings = settings | dict(zip(("width", "height"), root.get("referenceResolution", (1024, 1024))))
        elif workflow == root["name"] and request.get("operation") == "edit" and root.get("repairWorkflow"):
            workflow = root["repairWorkflow"]
        if workflow == root["name"]:
            kind = "reference" if references else "text"
            candidates = [v for v in variants if v.get("kind") == kind and v.get("default")]
            if model != root["model"]:
                candidates = [v for v in candidates if v["model"] == model]
        else:
            candidates = [v for v in variants if v["name"] == workflow and model in (root["model"], v["model"])]
        if len(candidates) != 1:
            raise ValueError("Selected model/workflow is incompatible with the supplied references. Select the configured automatic workflow or a compatible explicit workflow.")
        template = candidates[0]
        if settings.get("loras") and settings["loras"] != template.get("presetSettings", {}).get("loras"):
            raise ValueError("Custom LoRA settings do not match this exported workflow. Configure a workflow containing the requested adapters before generating.")
        if template.get("kind") == "reference" and not references:
            raise ValueError("Qwen Image Edit needs a source or character reference image. Use Qwen Image text-to-image for a new reference sheet.")
        if template.get("kind") == "text" and references:
            raise ValueError("This text-to-image workflow cannot condition on character references. Select a reference workflow; no references were discarded.")
        # Lightning adapters are trained for a fixed step count and CFG.
        # Their exact preset settings are stored with the resulting image.
        return template, settings | template.get("presetSettings", {})

    def healthCheck(self):
        try:
            template = self.template()
            stamp = (self.config.get("comfyWorkflow"), Path(self.config["comfyWorkflow"]).stat().st_mtime_ns)
            if self._health[1] and self._health[1][0] == stamp and time.time() - self._health[0] < 30:
                return copy.deepcopy(self._health[1][1])
            nodes = request_json(self.url + "/object_info", timeout=10)
            templates = template.get("variants", [template])
            missing = [
                v["class_type"]
                for t in templates for v in t["prompt"].values()
                if v["class_type"] not in nodes
            ]
            missing_weights = []
            for node in (n for t in templates for n in t["prompt"].values()):
                info = (
                    nodes.get(node["class_type"], {})
                    .get("input", {})
                    .get("required", {})
                )
                for field in (
                    "ckpt_name",
                    "unet_name",
                    "vae_name",
                    "clip_name",
                    "lora_name",
                    "control_net_name",
                ):
                    choices = info.get(field, [None])[0]
                    if (
                        field in node["inputs"]
                        and isinstance(choices, list)
                        and node["inputs"][field] not in choices
                    ):
                        missing_weights.append(node["inputs"][field])
            result = {
                "installed": not missing and not missing_weights,
                "models": list(dict.fromkeys([template["model"]] + [t["model"] for t in templates])),
                "workflow": list(dict.fromkeys([template["name"]] + [t["name"] for t in templates])),
                "missingNodes": missing,
                "missingWeights": missing_weights,
            }
            if result["installed"]:
                self._health = (time.time(), (stamp, result))
            return result
        except Exception as e:
            return {"installed": False, "error": str(e)}

    def generateImage(self, r, checkpoint):
        s = self.validateSettings(r["settings"])
        t, s = self.resolve_template(r, s)
        health = self.healthCheck()
        if not health["installed"]:
            raise RuntimeError("ComfyUI workflow unavailable: " + str(health))
        if not self.template().get("variants") and s.get("model") != t["model"]:
            raise ValueError(
                "Selected Comfy model does not match the configured workflow."
            )
        prompt = copy.deepcopy(t["prompt"])
        values = {
            "prompt": r["prompt"],
            "negativePrompt": r.get("negativePrompt", ""),
            **s,
        }
        for key, bindings in t["bindings"].items():
            if key in values:
                for node, field in bindings:
                    prompt[str(node)]["inputs"][field] = values[key]
        # Reference slots are explicit bindings, never assumed from model names.
        refs = r.get("referenceImages", [])
        if r.get("operation") == "edit":
            refs = [r["sourceImage"]] + refs
        if len(refs) > self.getCapabilities().get("maxReferenceImages", 0):
            raise ValueError("Too many references for this ComfyUI workflow, including the edit source. No character references were silently dropped.")
        for index in range(len(refs)):
            if not t["bindings"].get("reference" + str(index)):
                raise ValueError(f"ComfyUI workflow has no binding for reference {index + 1}. Configure character references before generating.")
        for index, node in t.get("referenceNodes", {}).items():
            if int(index) >= len(refs):
                prompt.pop(str(node), None)
                for value in prompt.values():
                    value["inputs"] = {key: field for key, field in value["inputs"].items()
                        if not (isinstance(field, list) and field and str(field[0]) == str(node))}
        for index, ref in enumerate(refs):
            checkpoint(0, s["steps"], "Uploading character references")
            raw = base64.b64decode(ref.split(",", 1)[-1])
            fingerprint = hashlib.sha256(raw).hexdigest()
            uploaded_name = self._uploads.get(fingerprint)
            boundary = "qt-" + secrets.token_hex(8)
            name = "qt-" + secrets.token_hex(8) + ".png"
            body = (
                f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\nContent-Type: image/png\r\n\r\n'.encode()
                + raw
                + f"\r\n--{boundary}--\r\n".encode()
            )
            req = urllib.request.Request(
                self.url + "/upload/image",
                data=body,
                headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
            )
            if not uploaded_name:
                with urllib.request.urlopen(req, timeout=30) as response:
                    uploaded = json.load(response)
                uploaded_name = uploaded["name"]
                self._uploads[fingerprint] = uploaded_name
            for node, field in t["bindings"].get("reference" + str(index), []):
                prompt[str(node)]["inputs"][field] = uploaded_name
        job = request_json(
            self.url + "/prompt", {"prompt": prompt, "client_id": secrets.token_hex(16)}
        )
        id = job["prompt_id"]
        started = time.time()
        try:
            while True:
                checkpoint(0, s["steps"], "ComfyUI generation")
                history = request_json(self.url + "/history/" + id)
                if id in history:
                    break
                time.sleep(0.5)
        except Exception:
            # Remove this queued job. Interrupt only if this job owns the running slot.
            try:
                request_json(self.url + "/queue", {"delete": [id]})
                q = request_json(self.url + "/queue")
                if any(v[1] == id for v in q.get("queue_running", [])):
                    request_json(self.url + "/interrupt", {})
            except Exception:
                pass
            raise
        entry = history[id]
        if entry.get("status", {}).get("status_str") == "error":
            raise RuntimeError("ComfyUI failed: " + str(entry.get("status"))[:1200])
        image = next(
            v["images"][0] for v in entry["outputs"].values() if v.get("images")
        )
        from urllib.parse import urlencode

        with urllib.request.urlopen(self.url + "/view?" + urlencode(image)) as response:
            img = visible_image(response.read())
        return {
            "pil": img,
            "seed": s["seed"],
            "seconds": time.time() - started,
            "provider": self.id,
            "model": t["model"],
            "workflow": t["name"],
            "settings": s,
            "resolvedWorkflow": prompt,
        }


def format_prompt(project, shot, provider):
    people = {c["id"]: c for c in project["characters"]}
    ch = next(c for c in project["chapters"] if c["id"] == shot["chapterId"])
    people.update({c["id"]: c for c in ch["people"]})
    descriptions = []
    for selected in shot["characters"]:
        p = people.get(selected["id"], {})
        identity = p.get("permanentIdentity", {})
        details = (
            [p.get("name", selected["id"])]
            + ([p.get("description", "")] if not any(identity.values()) else [])
            + [f"{k}: {v}" for k, v in identity.items() if v]
            + [f"{k}: {v}" for k, v in selected.get("appearanceState", {}).items() if v]
        )
        descriptions.append("; ".join(details))
    camera = shot["camera"]
    parts = [
        shot["action"],
        (
            "Characters: " + " / ".join(descriptions)
            if descriptions
            else "No people in this shot."
        ),
        "Camera: "
        + camera["shot"]
        + ", "
        + camera["angle"]
        + ". "
        + camera["composition"],
        "Location: " + shot.get("location", ""),
        "Expression and pose: "
        + shot.get("expression", "")
        + "; "
        + shot.get("pose", ""),
        "Lighting: " + shot.get("lighting", ""),
        "Style: " + project["settings"]["style"],
    ]
    if shot.get("continuity"):
        parts.append("Keep: " + json.dumps(shot["continuity"], ensure_ascii=False))
    caps = provider.getCapabilities()
    prompt = (
        ". ".join(p for p in parts if p)
        + ". No rendered text, captions, or additional important people."
    )
    if caps.get("promptFormat") == "sd-tags":
        prompt = (
            ", ".join(p for p in parts if p)
            + ", clear foreground action, consistent character identity, coherent composition"
        )
    negative = (
        "text, watermark, black frame, severe artifacts, extra limbs, malformed hands, unwanted characters"
        if caps.get("supportsNegativePrompt")
        else ""
    )
    return prompt, negative


def reference_prompt(project, shot, metadata, operation="generate"):
    """Name reference roles without locking a character to old reference clothing."""
    chapter = next(c for c in project["chapters"] if c["id"] == shot["chapterId"])
    people = {c["id"]: c for c in project["characters"] + chapter["people"]}
    appearances = {c["id"]: c.get("appearanceState", {}) for c in shot["characters"]}
    notes = []
    offset = 1 if operation == "edit" else 0
    if offset:
        notes.append("Image 1 is the shot to edit. Preserve its unaffected people, objects and composition.")
    for index, ref in enumerate(metadata, 1 + offset):
        if ref.get("characterId"):
            person = people.get(ref["characterId"], {})
            notes.append(
                f'Use image {index} as the identity reference for {person.get("name", ref["characterId"])}. '
                "Preserve face and permanent traits. Current appearance overrides reference clothing: "
                + json.dumps(appearances.get(ref["characterId"], {}), ensure_ascii=False) + "."
            )
        elif ref.get("locationId"):
            notes.append(f"Use image {index} for the location architecture and palette, not character identity.")
        elif ref.get("referenceType") == "style":
            notes.append(f"Use image {index} for visual style only, not its characters or events.")
        else:
            notes.append(f"Use image {index} only for its user-selected reference role.")
    return " ".join(notes)


def select_references(project, shot, store, provider, reserved_slots=0):
    caps = provider.getCapabilities()
    maximum = max(0, caps.get("maxReferenceImages", 0) - reserved_slots)
    refs = []
    metadata = []
    if shot.get("manual", {}).get("referenceImages"):
        selected = shot.get("referenceImages", [])[:maximum]
        return [
            data_url(store.asset(project["id"], r["path"])) for r in selected
        ], copy.deepcopy(selected)
    chapter = next(c for c in project["chapters"] if c["id"] == shot["chapterId"])
    people = project["characters"] + chapter["people"]
    for selected in sorted(shot["characters"], key=lambda c: c["type"] != "main"):
        p = next((c for c in people if c["id"] == selected["id"]), None)
        if not p:
            continue
        options = p.get("references", [])
        angle = shot["camera"].get("angle", "").lower()
        distance = shot["camera"].get("shot", "").lower()
        desired = (
            ["side", "full-body"]
            if "profile" in angle + distance
            else (
                ["back", "full-body"]
                if "behind" in angle
                else (
                    ["face", "front", "expression"]
                    if "close" in distance
                    else ["full-body", "three-quarter", "front"]
                )
            )
        )
        ordered = sorted(
            options,
            key=lambda r: (
                desired.index(r.get("kind")) if r.get("kind") in desired else 9
            ),
        )
        if ordered:
            r = ordered[0]
            refs.append(data_url(store.asset(project["id"], r["path"])))
            metadata.append(r | {"characterId": p["id"]})
    if len(refs) < maximum and caps.get("supportsStyleReference"):
        location = next(
            (
                x
                for x in project.get("locations", [])
                if x["id"] == shot.get("locationId")
            ),
            None,
        )
        if location and location.get("references"):
            r = location["references"][0]
            refs.append(data_url(store.asset(project["id"], r["path"])))
            metadata.append(r | {"locationId": location["id"]})
        if len(refs) < maximum and project.get("styleReferences"):
            r = project["styleReferences"][0]
            refs.append(data_url(store.asset(project["id"], r["path"])))
            metadata.append(r | {"referenceType": "style"})
    return refs[:maximum], metadata[:maximum]
