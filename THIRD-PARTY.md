# Third-party components

- Kokoro.js 1.2.1 and the adapted `phonemize.mjs`: hexgrad/kokoro, Apache-2.0. Original source: https://github.com/hexgrad/kokoro/tree/main/kokoro.js. Local changes provide a lazy pinned phonemizer import and direct, token-checked batched inference. See LICENSE-KOKORO.txt.
- Kokoro model: onnx-community/Kokoro-82M-v1.0-ONNX, Apache-2.0; downloaded from Hugging Face at runtime. Transformers.js/ONNX Runtime and phonemizer run through their upstream browser packages.
- lamejs 1.2.1: https://github.com/zhuker/lamejs. LGPL; see vendor/LICENSE-LAME.txt. `vendor/lame.mjs` is the upstream minified browser source with an ES module export added. The public repository supplies the complete adapted module; upstream supplies the original editable sources. No private or server-only modifications.
