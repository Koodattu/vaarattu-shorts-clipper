# Final review, posting plans and recording cleanup

## Review workflow

The review queue now offers **Unreviewed**, **Approved · needs finishing**, and **Unreviewed + approved**.
The gallery also has **Review approved clips** and a **Ready for posting** filter.

**Approved** means the content is worth keeping. **Ready for posting** means the current rendered
revision has passed the final human check. Press **P** or the matching queue button to stamp it.
Final clips leave both working queues. Undo restores the previous decision, including Approved.

Content approval survives a new revision; Ready for posting does not. A revised final clip returns
as Approved and needs another final check. Existing approvals are not promoted to final status.
Missing previews, unfinished rendering and pending checks cannot receive the final stamp.

Caption checks have optional guidance for names, spellings and terminology. Opening the dialog
waits for that input before making a model request. Guidance does not authorize insertion,
deletion or timing changes. Existing per-word validation and individual acceptance still apply.
The tighter-edit dialog embeds and reloads the current revision's video for checking passages.

## Publishing

Marking a clip **Ready for posting** puts it in **Publishing** automatically.
Cleanup and a local posting plan are not prerequisites. **Fill scheduled queue**
schedules eligible ready clips directly; individual clips can also be published
now or scheduled for a specific time while other clips still await review.

**Manual posting & saved plans** is an optional section within Publishing.
It preserves local planning, video downloads and manual posted-link tracking.
**Accounts & storage connections** contains account setup and channel selection.

### Optional local posting plan

The daily plan accepts a start date, posting time and IANA time zone such as Europe/Helsinki.
It includes only final clips from recordings whose clips are all either Not approved or Ready
for posting. This prevents scheduling a recording's ending while an earlier clip still awaits
review. Selected recordings are appended in displayed order; clips from the same source recording
are sorted by source start time, including when more than one selected run used that recording.
Existing planned clips prevent adding earlier source moments behind them: remove unposted entries
and rebuild the group if necessary.

The plan stores one clip per calendar day and the exact revision. Existing dates are not overwritten.
Local time remains stable through daylight saving changes; ambiguous/missing local hours are
rejected. Each clip has independent YouTube, Instagram and TikTok delivery records. Editing or
withdrawing final approval makes the existing planned revision unavailable for new posting.
Historical/scheduled video revisions remain preserved during cleanup.

The plan remains local until explicitly submitted from **Publishing**. The
[Buffer integration](buffer-publishing.md) can submit selected daily entries, publish one clip
now, or schedule a custom time. Buffer executes accepted schedules without a local worker.
Clips managed by Buffer show its delivery receipts; their local plan entries cannot be removed
or overwritten with manual receipts. Manage their remote schedules in Buffer.
For other clips, downloads and manual published-link tracking remain available. Recording a
manual URL is a human assertion, separate from Buffer-confirmed publication status.

## Cleanup behavior

Cleanup is explicit and applies to one completed recording. Every saved clip must have a final
decision. The **Cleanup** page lists eligible recordings first and keeps unfinished
recordings in a separate disclosure. Open a recording to inspect held clips and make
their review decisions. Completed recordings with no candidates can also
be cleaned. Other recordings can continue processing.

**Preview cleanup** lists the exact media files, recoverable bytes and number of kept videos.
The confirmation checkbox and **Delete listed media** apply only that preview. The backend checks
the recording state, decisions, revisions, file sizes/times and final video hashes again. A stale
preview or missing/changed final video blocks deletion. Paths must remain within workdir, and
symlinks/junctions are refused.

Deletion covers source audio, downloaded video sections, rejected renders, old revisions and
staging media belonging to that recording. Final and scheduled video files remain, along with
small captions, transcripts, metadata, review decisions, model responses and logs. Shared models,
the database and media belonging to other runs remain untouched.

Before deletion starts, a durable intent record prevents later source edits even if the app stops
mid-cleanup. Failed file deletions are reported and can be retried with a fresh preview. Editing,
context requests and reprocessing a cleaned run are blocked; start a new run to regain source
editing. Cleanup has no automatic trigger. No production cleanup was performed during implementation.

## Direct publishing feasibility (checked September 14, 2026)

- **YouTube:** the Data API supports video upload using account OAuth authorization. A registered
  Google API project and OAuth client are required. Uploads from unverified API projects are
  restricted to private visibility until the project passes the relevant audit. Native scheduled
  publication uses `status.publishAt` on an eligible private video.
  [OAuth authorization](https://developers.google.com/youtube/v3/guides/authentication),
  [video API](https://developers.google.com/youtube/v3/docs/videos).
- **Instagram Reels:** Meta provides content publishing for professional (Creator/Business)
  accounts. Instagram Login supports `instagram_business_content_publish` and does not require
  a linked Facebook Page. A Meta developer app, authorization and appropriate access are required.
  [Meta's Instagram Login documentation](https://www.postman.com/meta/instagram/folder/6raa77c/instagram-api-with-instagram-login),
  [publishing API](https://www.postman.com/meta/instagram/request/gabnx7r/publish-reel).
- **TikTok:** Direct Post exists, but unaudited clients have private-viewership restrictions.
  Its intended-use rules explicitly exclude internal/private utilities for accounts the developer
  or team manages. This project's current personal-tool scope therefore cannot be assumed eligible
  for public Direct Post approval. A suitable approved publishing service or manual posting is
  the practical route unless the project changes scope and satisfies review requirements.
  [Direct Post setup](https://developers.tiktok.com/docs/en/content-posting-api-get-started),
  [intended use and required posting UI](https://developers.tiktok.com/docs/en/content-sharing-guidelines).

Registered developer applications, redirect configuration, intended account types and an eligible
TikTok route must be settled before implementing and verifying live connections. OAuth allows
account authorization without giving this app the user's platform password. Nothing in this
implementation assumes that a local login alone grants publishing access.

## Verification and rollout

Backend tests cover approval/final-stamp revisions, guidance, scheduling order and DST, individual
delivery records, cleanup ownership/fingerprints, preservation of final files, concurrent unrelated
work and recovery after interrupted deletion. UI tests cover queue modes, undo, guidance, embedded
video reload, scheduling inputs and explicit cleanup confirmation.

The existing app/worker process was not stopped or restarted. Backend routes and database table
initialization become available at the next normal app start. Until then, the running backend
still uses its previously loaded code; refreshing static UI files alone does not activate the
new backend features.
