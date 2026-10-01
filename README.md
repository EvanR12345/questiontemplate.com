# Text to Audio Studio

Live at https://questiontemplate.com/

A browser-based text-to-speech studio with no fixed script word limit. Optimized for long recordings: 48 kbps mono MP3 is the default (about 32–43 MB for 90–120 minutes), with 32/64 kbps options and optional uncompressed 24 kHz mono PCM WAV. Each completed approximately five-minute part is checkpointed in IndexedDB. After a reload, restore the saved recording and resume at the last completed part. In-flight sections since that checkpoint are regenerated. Storage is best-effort; browser data clearing or quota failures can remove checkpoints.

Automatic WebGPU fp32 inference uses compatible graphics hardware, with a q8 WASM CPU fallback. Users can choose the smaller CPU model explicitly. Direct token-checked batches avoid redundant inference and preserve the script without truncation. One upcoming section is prepared ahead; audio is compressed immediately in the worker and raw PCM is released. The loaded engine is reused, players do not preload every part, and combined downloads reference existing compressed blobs. Generation can be paused or canceled safely at section boundaries. A screen wake lock is requested when available, but mobile background suspension can still interrupt work.

The studio shows words, estimated pacing/duration/size, actual exported WPM/duration/size, elapsed time, measured generation speed, and estimated time remaining. ETA is based on completed sections, not a guaranteed completion time. Seven voice recommendations are editorial starting picks, not measured audience scores or voice matches. Michael is the default; Heart and Bella are alternative female voices. Accent labels, sample previews and literal pronunciation overrides help with difficult words. The main flow is paste/import text, select a voice, preview, and generate/download. Advanced output/engine settings, device speech, sound effects, and script history are collapsed by default. Selecting a voice keeps the chosen speed unchanged. Studio previews attempt playback when ready and preserve recovery access to the prior saved full recording. Device speech is a separate non-downloadable preview; sound effects are not mixed into exports.

Static GitHub Pages; no server/API key. Model downloads are about 330 MB for GPU fp32 or 90 MB for CPU q8, plus runtime, cached when available. Text stays on-device. The custom domain and AdSense script are preserved. See THIRD-PARTY.md for source/license notices.

Run helper tests with `node --test audio-core.test.mjs`.

`node benchmark-exports.mjs` encodes a synthetic minute and constructs a two-hour MP3 export for format/size validation. It does not benchmark two hours of neural speech synthesis.
