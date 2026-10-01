# Third-party components

- Kokoro.js 1.2.1 and the adapted `phonemize.mjs`: hexgrad/kokoro, Apache-2.0. Original source: https://github.com/hexgrad/kokoro/tree/main/kokoro.js. Local changes provide a lazy pinned phonemizer import and direct, token-checked batched inference. See LICENSE-KOKORO.txt.
- Kokoro model: onnx-community/Kokoro-82M-v1.0-ONNX, Apache-2.0; downloaded from Hugging Face at runtime. Transformers.js/ONNX Runtime and phonemizer run through their upstream browser packages.
- Optional local helper: Kokoro Python 0.9.4 and hexgrad/Kokoro-82M v1 model, Apache-2.0. PyTorch CUDA, Hugging Face Hub and Transformers are installed from their upstream distributions during local setup; their upstream licenses apply. The ZIP contains only this repository's helper source, requirements and Windows launcher, not dependency binaries or model weights.
- lamejs 1.2.1: https://github.com/zhuker/lamejs. LGPL; see vendor/LICENSE-LAME.txt. `vendor/lame.mjs` is the upstream minified browser source with an ES module export added. The public repository supplies the complete adapted module; upstream supplies the original editable sources. No private or server-only modifications.
