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
| Make a small edit | Video | Replace one illustration, select its narration sentence, compare versions, remove subscribe reminders/ding, play the saved export or render a new bounded edit preview. |
| Project health | Above the editor | Uses the production preflight to check voice, renderer, references, recovery, provider configuration, temporary disk, cloud archive and saved caps. No generation or rental. |
| Search everything | Above the editor | Search characters, supporting people, story/dialogue, narration, scenes, prompts and the private file inventory. Results open the corresponding chapter/shot. Files need the helper connection. |
| Edit history / Undo | Above the editor | Records connected manual edits from this update onward. Revision and value/identity guards reject conflicting Undo. Keeps generated files; affected exports become stale and affected image acceptances need review. |
| Render short edit preview | Video → Make a small edit | Actually renders up to 30 seconds around the selected shot from saved audio/images. Preserves the chapter/full export. Uses the existing local renderer; no director/image purchases. |
| Completion report | Above the editor | Measured retained job intervals, process work, API receipts, GPU-window estimate, repair attempts and remaining findings. Exportable JSON; missing charges stay unknown. |

## Preservation and limits

- Version/setup mutations require an idle project queue and the current project revision.
- Delayed responses cannot apply an upload or setup to a newly selected project.
- Successful replacement narration retains the previous recording. Older overwritten
  recordings cannot be recovered if they were never retained.
- Restoring audio with a changed duration proportionally scales existing boundaries,
  saves the original timing and explicitly requests sentence-alignment review.
- Fixing a narration sentence regenerates the continuous chapter recording. It does
  not splice a disconnected audio clip into the finished track.
- Play saved preview seeks an existing export. Render short edit preview creates a new
  small MP4 so changes can be inspected before rebuilding a chapter/full story.
  It includes narration, pictures, motion and watermark; excludes intro, outro and
  subscribe reminders. A cropped long shot restarts its movement. Technical decode
  and existing original-shot visual gates remain active; no paid check is rerun.
- Undo retains at most 50 records and 2 MiB per project. Operations above 512 KiB
  or containing sensitive values are listed without a reversible payload. It does
  not reverse purchases, publishing or generation. Offline browser edits and edits
  predating this update cannot be reconstructed.
- The completion report covers all retained attempts, including failures/previews.
  Nested stage work overlaps and cannot be summed as elapsed time. Storage,
  external rentals, missing receipts and unrecorded historical repairs can be absent;
  this report is not an invoice or a visual quality certificate.
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

## Additional project-tools verification (October 16)

343 non-codec frontend tests and 86 focused backend tests passed; the final
25 frontend integration regressions also passed after navigation safeguards.
FFmpeg produced and decoded a real two-second 720p30 synthetic MP4. Chrome
verified an eight-second saved-media preview, Undo, health findings, cross-chapter
scene/shot and file search, and the report in an isolated 80-chapter fixture.
390-pixel mobile report layout had no horizontal overflow. Preview-only jobs cannot
change chapter completion/failure status or pollute chapter-render ETA samples.
No paid generation, model downloads or full-production run was performed.
