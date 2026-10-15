# Cloud recovery and shared voice startup

## Behavior

- Whole-story preparation/production can share one Kaggle model load across
  1–4 chapters (default ceiling: two; at most 4,000 words per group). Each chapter
  still receives a separate continuous WAV with its own sentence offsets.
  A manual one-chapter regeneration stays isolated. Model/speaker selection and
  licensing checks remain in force; the existing local Kokoro path is preserved.
- Verified voice receipts and audio can be restored from the private archive
  before retrying. A partially completed group submits only missing chapters.
  Generated narrator anchors are normal cloud-saveable project assets.
- Shared notebook waits do not train isolated chapter estimates. Kaggle has no
  validated long-form planner calibration yet: the planner discloses that instead
  of substituting local Kokoro speed. Short auditions are not production evidence.
- Cloud rendering requests source media lazily with fresh signed URLs, checks
  immutable byte identities, and saves completed outputs incrementally. Exact
  content-keyed chapters can be reused after an edit; ordinary cloud project saves
  preserve that index. Originals and manual selections are retained.
- A free working-space plan includes all retained source bytes, temporary output
  copies, and split-output allowance. Fixed-QP output size is variable: this is a
  planning estimate, not a size guarantee or measured elapsed-time prediction.
- A real one-frame NVENC probe runs before source transfer. A confirmed encoder
  failure permits trying another offer only after the owned allocation is released.
  Lost creation/shutdown replies block fallback. The reservation watchdog holds
  an idle-sleep request on Windows and monitors startup while creation is pending.
  Deliberate sleep, network loss, or computer shutdown can still interrupt control.
- Files now exposes an optional Vimeo connection and explicit cloud-pull upload.
  Only control metadata passes through the laptop; Google supplies the video.
  Receipts stay private in cloud storage. Unknown submission results are never
  replayed automatically. Confirmed API rejections are reported separately.
  Vimeo requires an upload-capable account/application and private token; those
  are not configured by this release. Patreon still requires a final post/embed
  action: its documented public API does not provide general post creation.

## Real evidence and limits

One free Qwen Ryan test shared a notebook across two synthetic chapter WAVs
(3.68 and 6.4 seconds). Total wall time was 147.234 seconds, including 101.581
seconds of setup/model loading and 13.054 seconds of speech generation. This
demonstrates shared loading and separate outputs, not long-form voice quality or
a measured end-to-end speedup.

The new US$1 cloud render study did not complete its approximately ten-minute
export. Two attempts encountered a host NVENC failure; a no-allocation attempt
cost zero. Another startup was prolonged while the laptop entered Modern
Standby. The provider billing statement records US$0.760688 in compute, with
US$0.10 still reserved for other operations. All study rentals were stopped.
The discovered startup/power and early-encoder checks were corrected and tested
with free fixtures; a new successful long export has not been measured.
Normal marketplace rendering remains limited to its verified short duration.
This release does not certify a 12-hour cloud render or change the image model,
reference conditioning, cadence, 720p30 format, or Practical visual-check defaults.
