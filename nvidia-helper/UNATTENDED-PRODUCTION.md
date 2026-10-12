# Automatic full-video production

The existing Generate full video action defaults to unattended completion.
Settings → Spending limits and automatic production can disable it. This is
implemented in the existing queue, not a separate demo or a second worker.

The action saves the editor first, checks current readiness, then prepares
continuous clean narration, canonical chapter direction, references and images.
It repairs eligible major findings, renders the story and waits for verified
cloud publication before reporting completion. YouTube/Patreon publishing remains
an explicit separate action. Audio and normal rendering still run on the laptop.

## Quality and spending

- Practical checks keep minor and uncertain findings visible as warnings. They
  are not changed into passing AI reviews. Confirmed major findings get bounded
  repairs. Strict unattended checks retain their stricter failure gate.
- Automatic repairs use the existing API/GPU project caps. No cap, ledger,
  reservation or rental history is reset. Where a total cap is saved, declared
  API/GPU partitions cannot exceed it.
- Each shot consumes a durable repair attempt before dispatch. Repeated Generate
  clicks cannot reset that count for the same specification. A changed story or
  shot specification gets its own bounded counter, still under the same caps.
- Original images and superseded generations remain in history. Matching manual
  fields survive automatic replanning. An unmatched manual edit or a locked image
  with an unresolved major error causes a clear failure; it is never silently
  discarded or falsely certified.
- Source ownership conflicts invalidate stale saved analysis. Canonical facts
  remain application data, not model chat memory.
- GPU ownership and the rental watchdog remain authoritative. The managed rental
  stops before video rendering, and on failures/cancellation. Unknown remote
  requests block duplicate purchasing until their saved receipts are reconciled.

## Recovery and estimates

Serial and overlapping production both refresh stale saved-image checks.
Initial image work collects findings; eligible automatic repair follows that
phase instead of jumping ahead of fresh shots. Repairs remain measured separately.
Cloud publication retries transient upload failures twice, waits for the required
revision, honors cancellation and has a bounded deadline. Failed uploads preserve
the completed local video and the archive retry state.

An active full-production/preparation/render queue item makes a temporary Windows
idle-sleep request. It releases that request after cleanup, does not change the
power plan, and lets the display sleep. Deliberate sleep, closing the laptop lid,
power loss and network failure can still interrupt a run.

The planner projects this same automatic policy and keeps incompatible old timing
observations separate. A retry ceiling is not a measured error rate. The current
production adapter dispatches one ComfyUI queue; faster multi-copy benchmark
configurations are comparison targets, not confirmed production throughput.

## Free validation on October 12, 2026

Deterministic providers test the two-chapter workflow, durable retries, archive
failures/cancellation, source and image publication guards, manual-edit retention,
strict/advisory handling and the Windows request lifecycle. No paid regeneration
was used to validate these changes; existing visual problems remain honestly saved.

An isolated 5.65 MB project-copy benchmark with eight alternating paired repeats
reduced median progress persistence from 0.683 to 0.419 seconds (about 39%) by
removing a discarded deep copy. Public save returns remain detached; atomic files
and exact previous-version backups remain. A synthetic 80-chapter/1,600-shot
manual-plan reconciliation fixture fell from 11.70 to 0.038 seconds after indexing
narration matches. Neither result measures a whole-video or Luna speedup.

Primary Windows behavior reference:
https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate
