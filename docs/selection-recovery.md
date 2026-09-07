# Selection recovery and concise feedback

## Current recovery: conversation-v8 (7 September 2026)

v8 preserves the review-first behavior below with a three-second minimum and a leaner discovery schema. Saved full candidates remain supported by recovery; separate v8 checkpoints preserve earlier exports. Existing run reasoning, encoder and pacing settings stay as saved (missing settings mean low, CPU and original pacing). Use the queue/editor render controls to apply GPU encoding or pause trimming to existing clips. See [pacing and evaluation](pacing-and-evaluation.md). No live recovery or processing was started by the agent.

## Current recovery: conversation-v7 (7 September 2026)

This supersedes the v6 behavior described below. After restarting the app yourself, use **Recheck excluded moments** on a completed run to reconsider its saved proposals, including `outside_section` exclusions. Reject/needs_context verdicts and low scores become review notes; source-valid 2–90-second proposals can render. Source/media validation remains in place. Existing clips, edits and revisions are preserved, including exports from earlier recovery versions; intervals already represented are not regenerated. New candidates use the run's original layout.

Recovery writes separate `recovery-conversation-v7` checkpoints and inference artifacts. It does not rescan empty sections or alter original checkpoints. For the latest run, an offline replay of actual saved responses routed all 20 candidates, with 12 new mocked deliveries and eight existing clips unchanged. Real new model responses may refine cuts differently. No actual model call, download, render, or recovery was started; the user starts/restarts the app and processing. See [the audit](audits/2026-09-07-three-run-audit.md).

Implemented 2026-09-07 as conversation-v6. This supersedes the flag veto, five-second floor and same-passage-only refinement described by earlier versions.

## Changes

- Advisory flags no longer reject an otherwise accepted, sufficiently strong clip. Notes remain visible in selection decisions and are carried into final render metadata and UI. A genuine semantic/context problem still requires rejection or a low required score.
- Real word IDs inside displayed passages are normalized to passage boundaries for discovery. Original and normalized proposals are recorded. Missing IDs and invalid ordering are never guessed; ownership stays within the discovery section.
- Verification can refine to a later point inside the originally proposed interval, rather than requiring the original idea passage. The model is instructed to retain the same conversation/point. IDs outside the proposed interval still fail validation, and repair receives the specific safe validation error.
- Short jokes can pass at two seconds. The pre-verification shortlist accepts positive durations up to 90 seconds; final automatic acceptance remains 2–60 seconds, manual edits 2–90. No filler is added to meet duration.
- Every new discovery response requires a concrete Finnish feedback sentence, 1–240 characters, alongside its candidate list. Empty lists must explain what was discussed and why it did not qualify. Missing/invalid feedback gets the existing single bounded response repair; persistent failure records a coverage gap. It does not start an extra commentary-only model request.
- The UI's **Selection feedback and decisions** section shows section reasons, proposal counts and per-candidate outcome/reason/scores. These are explicit model explanations, not a record of all considered alternatives or proof of recall.

## Recover this existing VOD

1. Restart the application yourself and open the completed tYfuF30Gl08 run.
2. Click **Recheck excluded moments** once. It uses the run's saved provider, model configuration and spending limit. Codex subscription usage and token reporting remain unchanged.
3. The pipeline loads the verified cached metadata/audio/transcript/selection, preserves the two selected winners, and re-verifies the 14 saved exclusions (including the four invalid-anchor suggestions and two formerly too-short jokes). New accepted clips proceed through normal download/alignment/render/export. True editorial rejections remain available in the decisions list.
4. Existing clips, edits, revisions and exports are preserved. New IDs derive from source word boundaries, and intervals already represented by existing clips are skipped. New exports use the run's original layout preset; adjust individual new clips through Edit if the layout has changed.
5. If interrupted, use **Resume / retry**. The recovery intent survives pause, failure and worker restart. Completed recovery selection and inference responses are reused; previously produced clips are not duplicated.

Recovery writes `recovery-conversation-v6.checkpoint.json` and a separate `inference/recovery-conversation-v6-.../` folder. Original selection checkpoints and caches are unchanged. It uses the existing single-worker queue, request accounting and access controls. Once complete, this recovery version cannot be queued again through the UI/API. A future selection version can expose recovery again.

This is a recheck of saved suggestions, not a rescan of empty sections. The 47 historical empty discovery responses cannot gain retrospective model explanations without making new discovery calls; the UI says those older section explanations are unavailable. New full scans use the feedback contract automatically. No recovery, model inference, server startup, or rendering was run while implementing this feature.

## Verification

Offline tests cover passage-anchor recovery, invented/reversed ID rejection, in-interval refinement and out-of-interval rejection, short/advisory candidate acceptance, required bounded feedback and repair/cache reuse, recovery without discovery calls, immutable existing clips/checkpoints, repeat recovery idempotence, and API/queue/pause/resume guards. An additional routing check with this VOD's actual cached transcript and a mocked verifier retained all 16 suggestions, dispatched the 14 exclusions for verification, and preserved both original winners. This proves routing, not their editorial quality under a real new model response.

## Download-monitor interruption observed during recovery

On 2026-09-07, the saved tYfuF30Gl08 recovery checkpoint contained 16 verified candidates and 11 eligible selections, including the original two. One additional clip exported. The next section's FFmpeg/yt-dlp log ended with Download completed, but download.txt had not been written. No traceback had been retained, so the exact old exception cannot be proven from these artifacts.

The download-size monitor had a filesystem race: a temporary file could disappear between is_file() and stat() as yt-dlp renamed it. The resulting FileNotFoundError escaped the media-stage handling and terminated the owned downloader. The monitor now stats each entry once and ignores only entries that disappear; size limits and other access errors still apply. Unexpected worker errors now save exception type and frame locations in worker-error.json, excluding exception messages, source lines and locals that could reveal secrets.

The concurrency update removes the paused-run admission blocker. After restarting the launcher, use Resume / retry on this failed recovery directly; another paused run does not need to be cancelled. Set Concurrent jobs to 2 or more to run multiple resumed jobs together. Resume reuses the completed recovery selection and existing successful exports. The agent did not change either run's state or restart processing while investigating.
