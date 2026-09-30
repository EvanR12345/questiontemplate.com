# Text to Audio Studio

Live at https://questiontemplate.com/

A browser-based text-to-speech studio with no fixed script word limit. Kokoro voices generate downloadable 24 kHz mono PCM WAV audio locally in a Web Worker. Long scripts are chunked without silent truncation and divided into approximately 15-minute downloadable parts, plus a combined track where the WAV format allows. Processing time and available memory depend on the device.

The studio shows word/character counts, estimated duration and pacing, recorded duration, actual WPM (including pauses), and file size. Ranked recommendations are editorial starting picks, not measured quality scores. Device speech remains available as a separate non-downloadable preview. Sound effects are standalone previews, not mixed into the exported speech. The High-Energy Creator preset is original and does not imitate a real person.

The static HTML and JavaScript run on GitHub Pages without a server or API key. Kokoro.js 1.2.1 (Apache-2.0) loads from jsDelivr; the Apache-2.0 Kokoro model and voices load from Hugging Face on first use (about 90 MB plus runtime) and use browser caches when available. Script text is not sent to the voice service. Local history is best-effort: storage quotas do not restrict generation. The existing custom domain and AdSense publisher script are preserved.

Run helper tests with `node --test audio-core.test.mjs`.
