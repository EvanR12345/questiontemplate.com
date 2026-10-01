# Local NVIDIA engine

Runs the same Kokoro v1 voices in full precision through PyTorch CUDA, directly on your NVIDIA GPU. The website still handles text, pronunciation, MP3/WAV encoding, checkpointing and downloads. No remote speech API, account, or subscription is needed. This is an optional engine; browser generation remains available.

## Windows setup

1. Install Python 3.11 from https://www.python.org/downloads/release/python-3119/ (64-bit Windows installer). Leave the Python launcher enabled.
2. Extract the helper ZIP into a folder. Double-click `start-windows.bat`.
3. First setup downloads several GB of PyTorch dependencies and about 330 MB of model weights. Allow disk space and time. Later launches reuse them. An up-to-date NVIDIA driver is required; a separate CUDA Toolkit installation is not required for the supplied PyTorch wheel.
4. When ready, the helper opens the website with a temporary pairing key in its URL fragment. Click **Connect NVIDIA**. Allow the website's **Local network access** prompt if Chrome asks. Keep the helper window open.
5. Preview your script, then generate. The site shows the NVIDIA GPU name and measured speed. This engine does not secretly switch to CPU. Close the helper window to stop the service. Reopen the printed link after a page reload to pair again.

If the automatic link does not open, copy the entire link printed in the helper window into your browser. Do not share that link. Keys are held in memory, removed from the visible website URL immediately and excluded from recordings, scripts and checkpoints. The listener binds only to 127.0.0.1. CORS allows questiontemplate.com and local preview origins. Every API request requires the temporary pairing key.

Generation uses bounded, token-checked sections and a single cached model/voice cache. Only one synthesis request runs at a time. Pause/cancel happen after the current section; existing completed sections remain downloadable. If the helper stops, restore the last saved checkpoint, launch/reconnect the helper and resume. In-flight sections are regenerated.

## Speed and quality

10× is a target, not a guaranteed GTX 1650 benchmark. Two hours at 10× takes 12 minutes. Preview speed can be noisier than a multi-minute recording. Model and first voice loading happen before the generation timer starts; elapsed time includes setup. Full precision and the existing phonemizer preserve the voice setup without speeding up playback or trimming word endings. Actual CUDA speed and memory use must be measured on the user's hardware. This helper has not been GPU-benchmarked on a GTX 1650.

## Developer checks

`python -m unittest discover -s nvidia-helper -p '*_test.py'` tests the local HTTP bridge with a fake engine. It checks authentication, origin restrictions, bounded payloads, binary sample transport, voice validation and concurrent request rejection. It does not benchmark synthesis.

Upstream model/API: https://github.com/hexgrad/kokoro (Apache-2.0); model https://huggingface.co/hexgrad/Kokoro-82M (Apache-2.0). CUDA wheel setup: https://pytorch.org/get-started/previous-versions/. Local browser permissions: https://developer.chrome.com/blog/local-network-access/.
