"""Core-node Qwen workflows; no third-party node pack or duplicate Torch required."""
import json
import sys
from pathlib import Path

EDIT = "qwen_image_edit_2511_fp8mixed.safetensors"
TEXT = "qwen_image_2512_fp8_e4m3fn.safetensors"
ENCODER = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
VAE = "qwen_image_vae.safetensors"

def node(kind, **inputs):
    return {"class_type": kind, "inputs": inputs}

def variant(name, reference, steps, lightning, default=False):
    model = "qwen-image-edit-2511" if reference else "qwen-image-2512"
    graph = {
        "1": node("UNETLoader", unet_name=EDIT if reference else TEXT, weight_dtype="default"),
        "2": node("CLIPLoader", clip_name=ENCODER, type="qwen_image", device="default"),
        "3": node("VAELoader", vae_name=VAE),
        "4": node("ModelSamplingAuraFlow", model=["1", 0], shift=3.1),
        "5": node("CFGNorm", model=["4", 0], strength=1.0, pre_cfg=False),
        "8": node("EmptySD3LatentImage", width=1344, height=768, batch_size=1),
        "9": node("KSampler", model=["5", 0], positive=["6", 0], negative=["7", 0], latent_image=["8", 0], seed=42,
                  steps=steps, cfg=1.0 if lightning else (3.0 if reference else 4.0), sampler_name="euler", scheduler="simple", denoise=1.0),
        "10": node("VAEDecode", samples=["9", 0], vae=["3", 0]),
        "11": node("SaveImage", images=["10", 0], filename_prefix="QuestionTemplate/" + name),
    }
    bindings = {"prompt": [["6", "prompt" if reference else "text"]], "negativePrompt": [["7", "prompt" if reference else "text"]],
                "width": [["8", "width"]], "height": [["8", "height"]], "seed": [["9", "seed"]], "steps": [["9", "steps"]],
                "guidance": [["9", "cfg"]], "sampler": [["9", "sampler_name"]], "scheduler": [["9", "scheduler"]], "denoisingStrength": [["9", "denoise"]]}
    if reference:
        for idx in range(3):
            graph[str(20 + idx)] = node("LoadImage", image="reference-not-bound.png")
            bindings["reference" + str(idx)] = [[str(20 + idx), "image"]]
        images = {"image" + str(i + 1): [str(20 + i), 0] for i in range(3)}
        graph["6"] = node("TextEncodeQwenImageEditPlus", clip=["2", 0], vae=["3", 0], prompt="", **images)
        graph["7"] = node("TextEncodeQwenImageEditPlus", clip=["2", 0], vae=["3", 0], prompt="", **images)
        graph["12"] = node("FluxKontextMultiReferenceLatentMethod", conditioning=["6", 0], reference_latents_method="index_timestep_zero")
        graph["13"] = node("FluxKontextMultiReferenceLatentMethod", conditioning=["7", 0], reference_latents_method="index_timestep_zero")
        graph["9"]["inputs"].update(positive=["12", 0], negative=["13", 0])
    else:
        graph["6"] = node("CLIPTextEncode", clip=["2", 0], text="")
        graph["7"] = node("CLIPTextEncode", clip=["2", 0], text="")
    presets = {"steps": steps, "guidance": graph["9"]["inputs"]["cfg"], "sampler": "euler", "scheduler": "simple", "denoisingStrength": 1.0, "loras": []}
    if lightning:
        filename = f"Qwen-Image-Edit-2511-Lightning-{steps}steps-V1.0-bf16.safetensors" if reference else "Qwen-Image-Lightning-4steps-V1.0.safetensors"
        graph["14"] = node("LoraLoaderModelOnly", model=["1", 0], lora_name=filename, strength_model=1.0)
        graph["4"]["inputs"]["model"] = ["14", 0]
        presets["loras"] = [{"name": filename, "strength": 1.0}]
    return {"name": name, "model": model, "kind": "reference" if reference else "text", "default": default,
            "prompt": graph, "bindings": bindings, "presetSettings": presets,
            "referenceNodes": {str(i): str(20 + i) for i in range(3)} if reference else {}}

def bundle():
    return {"model": "qwen-studio-auto", "name": "qwen-studio-auto", "version": 1,
        "repairWorkflow": "qwen-reference-quality-40",
        "referenceCreationWorkflow": "qwen-text-quality-40",
        "referenceResolution": [1024, 1024],
        "selection": "Configured text-to-image for shots without references; configured 8-step image-edit conditioning for shots with references. Explicit workflows remain selectable.",
        "capabilities": {"supportsMultipleReferences": True, "supportsImageEditing": True, "supportsNegativePrompt": True,
            "supportsSeed": True, "supportsLoRA": True, "supportsStyleReference": True, "maxReferenceImages": 3, "maxResolution": 2048,
            "recommendedResolution": [1344, 768], "recommendedSteps": 8, "recommendedSampler": "euler", "recommendedGuidance": 1.0,
            "recommendedDenoisingStrength": 1.0, "promptFormat": "natural-language", "negativePromptFormat": "natural-language",
            "hardwareRequirements": {"vramGB": 40, "ramGB": 64},
            "referenceLimitations": "Up to 3 total references including repair source. Natural-language identity and appearance instructions. Lightning uses CFG 1; negative guidance is inactive there. Automatic workflow selection is explicit in saved metadata."},
        "recommendedSettings": {"width": 1344, "height": 768, "steps": 8, "guidance": 1.0, "sampler": "euler", "scheduler": "simple", "denoisingStrength": 1.0},
        "variants": [variant("qwen-text-fast-4", False, 4, True, True), variant("qwen-text-quality-40", False, 40, False),
                     variant("qwen-reference-fast-4", True, 4, True), variant("qwen-reference-balanced-8", True, 8, True, True),
                     variant("qwen-reference-quality-40", True, 40, False)]}

if __name__ == "__main__":
    Path(sys.argv[1]).write_text(json.dumps(bundle(), indent=2), encoding="utf-8")
