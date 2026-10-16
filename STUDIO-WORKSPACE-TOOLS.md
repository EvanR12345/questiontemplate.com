# Studio workspace tools

The existing Studio now includes these controls. No production model, image cadence,
voice environment, spending cap or existing generation is replaced by this update.

| Tool | Where | Behavior |
| --- | --- | --- |
| What's next | Above the editor | Opens the relevant chapter and stage from saved assets and queue state. |
| Production overview | Above the editor | Shows exported chapters, current task, queue ETA coverage, recorded API/GPU estimates and cloud-save status. Costs are retained estimates, not a complete account bill. |
| Impact preview | Before manual generation, selected-chapter work and setup changes | Explains affected work and reuse. Unknown timings/prices stay unknown rather than showing zero. |
| Problems inbox | Production overview | Opens blocking image findings, latest failed jobs, continuity/timing warnings and project warnings. Practical nonblocking findings do not become mandatory repairs. |
| Version comparison | Layout, Narration and Video | Compare earlier saved images or recordings, then explicitly choose one. Keeps both versions. Images require fresh QC; audio restoration requires a matching script. |
| My saved setups | Settings | Save named voice/style/image/check/finishing preferences; load from another project. New projects inherit the setup library. Secrets, spending caps and generated media are excluded. |
| Chapter tools | Chapters sidebar | Search story text, filter exported/unfinished, browse 20 at a time, select a range and analyze/narrate/export selected chapters in order. Full-story generation still includes every chapter. |
| File locations | Files | Distinguishes cloud-only, cloud plus helper, helper-only, pending upload and missing assets. An archive checksum is historical evidence, not fresh permission to delete a file. |
| Make a small edit | Video | Replace one illustration, select its narration sentence, compare versions, remove subscribe reminders/ding and preview a selected shot in the saved chapter export. |

## Preservation and limits

- Version/setup mutations require an idle project queue and the current project revision.
- Delayed responses cannot apply an upload or setup to a newly selected project.
- Successful replacement narration retains the previous recording. Older overwritten
  recordings cannot be recovered if they were never retained.
- Restoring audio with a changed duration proportionally scales existing boundaries,
  saves the original timing and explicitly requests sentence-alignment review.
- Fixing a narration sentence regenerates the continuous chapter recording. It does
  not splice a disconnected audio clip into the finished track.
- A selected-shot preview plays an existing video at its saved time. Edits become
  visible after rendering the affected chapter and assembling the story again.
- Uploading an image uses the existing authenticated upload route. The browser
  extension must allow file URL access for automated chooser testing; ordinary
  manual file selection remains available.
- No automatic media deletion or new paid generation was performed for this update.

## Verification

339 frontend regression tests passed. After final safeguards, the 28
focused frontend tests plus the legacy Video-tab regression and 48 backend/HTTP/storage/history tests passed. Browser
checks used a separate paused synthetic 80-chapter fixture: search, range selection,
three selected queued exports, presets, image/audio selection, saved-video playback,
narration selection, reminder removal and file badges. Mobile 390-pixel layout had
no horizontal overflow. Automated browser upload selection was blocked by Chrome's
extension permission; backend attachment, traversal rejection and missing-file
rejection passed. No production generation or cloud-render benchmark is implied.

Legacy validation/imported projects without audio/render objects now open their
Video workspace safely. Four unrelated codec tests need ffmpeg/ffprobe executables
on PATH and were excluded from the passing frontend suite.
