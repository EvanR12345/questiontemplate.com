"""Optional verified native model setup. Uses stdlib, never installs Python/Torch."""

import argparse, hashlib, json, re, shutil, subprocess, urllib.request, zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vision", action="store_true")
    args = parser.parse_args()
    helper = Path(__file__).resolve().parent
    root = helper / "studio-runtime"
    root.mkdir(exist_ok=True)
    config_path = helper / "studio-config.json"
    config = (
        json.loads(config_path.read_text(encoding="utf-8"))
        if config_path.exists()
        else {}
    )
    manifest = json.loads((helper / "studio-models.json").read_text(encoding="utf-8"))
    paths = {
        "Qwen3.5-4B-Q4_K_M.gguf": "directorModel",
        "flux-2-klein-4b-Q4_0.gguf": "fluxModel",
        "Qwen3-4B-Q4_0.gguf": "fluxEncoder",
        "flux2-vae.safetensors": "fluxVae",
        "mmproj-Qwen3.5-4B-BF16.gguf": "directorProjector",
    }
    runtimes = {
        "director.zip": ("directorExecutable", "director", "llama-server.exe"),
        "images.zip": ("imageExecutable", "images", "sd-server.exe"),
    }
    selected = [
        m for m in manifest if args.vision or not m["name"].startswith("mmproj-")
    ]
    missing = []
    for item in selected:
        key = paths.get(item["name"]) or runtimes[item["name"]][0]
        existing = Path(config.get(key, ""))
        if not existing.is_file():
            missing.append(item)
    required = sum(m["bytes"] for m in missing) + 256 * 1024**2
    if shutil.disk_usage(root).free < required:
        raise RuntimeError(
            f"Need {required/2**30:.1f} GB free for the missing local files. Existing models/environment were retained."
        )
    print(
        f'Missing files: {len(missing)}; download approximately {sum(m["bytes"] for m in missing)/2**30:.2f} GB. Existing configured files are reused.',
        flush=True,
    )
    provenance = []
    for item in selected:
        name = item["name"]
        key = paths.get(name) or runtimes[name][0]
        existing = Path(config.get(key, ""))
        if name in runtimes and existing.is_file():
            print("Reusing runtime " + str(existing), flush=True)
            continue
        path = existing if existing.is_file() else root / name
        if not path.is_file():
            partial = path.with_suffix(path.suffix + ".part")
            print("Downloading " + name, flush=True)
            with urllib.request.urlopen(
                item["url"], timeout=120
            ) as response, partial.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            partial.replace(path)
        with path.open("rb") as source:
            checksum = hashlib.file_digest(source, "sha256").hexdigest()
        if checksum != item["sha256"]:
            raise RuntimeError(
                "Checksum mismatch: "
                + name
                + ". File retained for inspection; no environment changes."
            )
        provenance.append(item | {"localPath": str(path)})
        if name in runtimes:
            key, folder, exe = runtimes[name]
            destination = root / folder
            destination.mkdir(exist_ok=True)
            with zipfile.ZipFile(path) as archive:
                if any(
                    not (destination / m.filename)
                    .resolve()
                    .is_relative_to(destination.resolve())
                    for m in archive.infolist()
                ):
                    raise ValueError("Unsafe runtime archive path.")
                archive.extractall(destination)
            config[key] = str(destination / exe)
        else:
            config[key] = str(path)
        print("Verified " + name, flush=True)
    # Native enumeration is explicit: an integrated GPU must not win by default.
    result = subprocess.run(
        [config["directorExecutable"], "--list-devices"], capture_output=True, text=True
    )
    devices = result.stdout + "\n" + result.stderr
    match = re.search(r"(Vulkan\d+):[^\n]*NVIDIA", devices, re.I)
    if not match:
        raise RuntimeError(
            "NVIDIA Vulkan device was not found. Check the driver and configure the device explicitly; downloaded files are retained."
        )
    device = match[1]
    config.setdefault("directorDevice", device)
    config.setdefault("imageBackend", device.lower())
    config.setdefault("directorGpuLayers", 99)
    config.setdefault("directorEndpoint", "http://127.0.0.1:8766")
    config.setdefault("comfyEndpoint", "http://127.0.0.1:8188")
    config.setdefault("fluxValidated", False)
    if not config.get("ffmpeg") and shutil.which("ffmpeg"):
        config["ffmpeg"] = shutil.which("ffmpeg")
    temporary = config_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2), encoding="utf-8")
    temporary.replace(config_path)
    (root / "PROVENANCE.json").write_text(
        json.dumps(provenance, indent=2), encoding="utf-8"
    )
    print(
        "Setup complete. Restart the shared helper. Configure your existing FFmpeg path in Advanced settings if needed. Python, .venv and Torch were unchanged.",
        flush=True,
    )


if __name__ == "__main__":
    main()
