# vaarattu.tv enrichment contract

## Update: VOD matching, 2026-09-12

The new-run form automatically looks up a selected library recording. **Find matching stream** also accepts an editable VOD title. A clear identity fills the stream ID; ambiguous candidates remain available as **Use this stream** choices. **Check stream ID** loads a manually entered vaarattu.tv ID directly, including old streams outside the fallback catalog scan. Changing the video clears the mapping. Editing the stream or offset clears timing confirmation, and stale search responses cannot overwrite a later selection or manual input.

The clipper uses `GET /api/streams/search?q=...&youtubeId=...&limit=10` on the existing `dev.vaarattu.tv` API. The matching endpoint was implemented in the sibling vaarattu.tv repository, sharing its existing collector matcher. Exact saved YouTube associations take priority and return saved offsets for split recordings. Otherwise it compares Finnish recording dates and fuzzy segment/recording titles. It returns candidates and an unambiguous suggested identity, not a confidence probability or a measured alignment. No LLM requests or media downloads are used for matching.

The form fills a saved offset when provided, but the timing checkbox remains unchecked. Confirm timing before starting to enable chat-peak discovery. A fuzzy title match cannot determine the stream position at YouTube second zero. Missing matches and API outages retain transcript-only processing. Confirmed manual IDs fetch stream details and activity directly without scanning the catalog. The selected ID, offset and confirmation remain in the existing run configuration.

The new vaarattu.tv backend route requires that project's normal deployment; this task did not deploy it or change its database. Until then, the clipper falls back to the existing paginated stream list (up to 2,000 streams), labels the fallback, and offers local title/date suggestions. An incomplete scan never auto-selects a candidate. Live verification on September 12 matched `OJ-bDXbfEos`, “9.7.2026 - pushing 40(00 rio score)”, to stream 254 through this fallback. No timing offset was inferred.

Checks: `tests/test_stream_matching.py`, `tests/test_chat_peak_selection.py`, `tests/test_media_contracts.py`, and `tests/catalog_ui.test.cjs`; backend contract and rollout details are in vaarattu.tv's `docs/stream-search.md`.

The sections below describe the earlier integration and research; the matching UI and API availability notes above supersede their matching proposals.

Status at initial implementation: optional activity adapter and separate LLM peak discovery implemented, 2026-09-06.

## Current integration and activation

Rechecked the public API during this follow-up: both the stream list and `GET /api/streams/254/activity` returned HTTP 200. Activity returned 188 two-minute buckets. This supersedes the earlier missing-route observation below. Browser-tool requests failed, but direct read-only HTTP requests succeeded. No model or clipper server was started.

Previously, the clipper fetched activity **after** selection and only added a one-point ranking boost. In `conversation-v4`, it snapshots activity before LLM work, scans the entire transcript normally, then separately asks the LLM to examine speech around peaks. Suggestions from both discovery passes are merged and deduplicated before independent verification. The verifier sees original speech and proposed word anchors, without chat counts, discovery scores or source labels. New runs do not add a chat ranking bonus or relax any quality gate.

To enable the extra pass, expand **Optional chat peak review** in the run form, enter the stream ID and measured **stream seconds at YouTube time zero**, then confirm the mapping. For example, if the YouTube upload omits the first 30 seconds of the stream, enter `30`; a chat bucket at stream second 120 maps to YouTube second 90. Title/date matching produces identity suggestions only. Without confirmation, the full-transcript scan still runs, and the run reports why peak review was skipped. A missing/failed activity API likewise does not prevent normal discovery. There is no automatic title-to-timestamp alignment or calibration UI in this change.

Peak detection uses active-chatters counts against a surrounding median/MAD baseline. It requires at least four positive neighboring buckets within ten minutes (or five bucket widths for coarse data). A spike must exceed the baseline by at least the largest of three chatters, three MADs, or 50% of baseline. These are initial heuristics, not a measured optimum. Zero buckets are excluded from baseline evidence because collection coverage is unknown; zeros alone cannot create a convincing spike. Flat activity is not a peak, and no fixed number of peaks or clips is requested.

Each detected bucket contributes a candidate region from 90 seconds before it through 30 seconds after its end, clipped to VOD bounds. Overlapping regions merge. The full bucket width is retained: two-minute activity does not locate an event to a precise second. Peak requests use the same passage formatting and 90-second surrounding context as discovery, splitting ownership when needed to fit the model. Chat can lag speech or react to gameplay, spam or an unrelated event, so an empty candidate list remains valid.

`chat-input.json` freezes API data for resumed selection; `chat-peak-review.json` records regions, baseline evidence, checked/skipped requests and status. Completed selection embeds both the chat snapshot and review summary. Clip metadata records discovery sources. Unreadable peak responses or regions that cannot fit are recorded as partial **peak review**, not missing full-transcript coverage. API/model access or quota failures still stop for explicit retry. Extra model calls use the same provider, low reasoning where supported, usage ledger and paid-API spending cap; Codex uses its separate allowance.

Completed old selections retain their existing decisions and stage chain. No old run is silently rescanned. The run UI shows peak-review status/counts; rerendering retains them. Creator review on a new, correctly aligned VOD is still needed to measure whether this actually recovers better clips.

## What exists and what was verified live

The reference source at `C:/Users/Juha/Desktop/Projektit/vaarattu.tv` was clean at commit `96a2afa` when checked.

| Surface | Local implementation | Live observation |
|---|---|---|
| `GET /api/streams?page=1&limit=100` | Paginated stream list, limit capped at 100; titles live in `segments[]` | Works; two pages reported 199 streams in total |
| `GET /api/streams/:id` | Details include nullable `twitchVideoId` | Endpoint contents not separately probed in this planning pass |
| `GET /api/streams/:id/activity` | Implemented in routes/controller/service | Initially 277 returned `Route not found`; follow-up 254 returned HTTP 200, 188 points |
| Title/recording search | No dedicated search route found in stream routes | Not available as a verified contract |
| YouTube association | No YouTube video ID in the inspected Stream schema | Must be maintained by the clipper initially |

Public metadata sources checked directly: [stream list](https://dev.vaarattu.tv/api/streams?page=1&limit=2) and [activity URL tested](https://dev.vaarattu.tv/api/streams/277/activity). Browser research fetches failed for this host; direct read-only HTTP requests succeeded for the list. Live/source parity must be checked again before implementing an adapter.

Relevant source files, relative to the vaarattu.tv root:

- `backend/web/src/routes/stream.routes.ts` and `controllers/stream.controller.ts`: route registration and responses.
- `backend/web/src/services/stream.service.ts`: per-minute message count and distinct message-author query.
- `backend/web/src/utils/streamActivity.ts`: display bucket sizing/aggregation.
- `backend/web/src/types/api.types.ts`: `StreamActivity` response.
- `backend/shared/prisma/schema.prisma`: `Stream`, `StreamSegment`, `Message`, `ViewSession`, `StreamViewerSample`.
- `backend/twitch/src/services/chatMessage.service.ts`: message timestamp currently uses collector receipt time (`new Date()`).
- `docs/stream-recordings.md`: Twitch VOD mapping and sampled audience collection; this is reference documentation, not authorization to run its migration/deployment commands.

The local activity shape is `{viewerSource, intervalMinutes, points:[{time,endTime,viewers,messagesPerMinute,activeChatters}]}` inside the usual success/data envelope. It targets at most about 300 points by widening buckets for long streams. `activeChatters` in a widened display bucket is the **maximum minute-level distinct count**, not the number of distinct chatters across that entire bucket. `viewers` is peak Twitch audience when samples exist, otherwise tracked chat presence. Neither tracked presence nor `viewSessions` counts are interchangeable with unique people writing chat.

Current collection does not establish complete message coverage. An empty minute may mean no messages or missing collection. Do not turn unknown coverage into certainty, and do not call chat presence a historic Twitch audience measurement.

## Concrete identity example

[YouTube OJ-bDXbfEos](https://www.youtube.com/watch?v=OJ-bDXbfEos) was observed with title `9.7.2026 - pushing 40(00 rio score) - https://suomiwow.vaarattu.tv/`, upload date July 16, 2026, and hydrated duration 22,440 seconds. Flat-list metadata had reported 22,441 seconds; use hydrated/probed values for processing.

Stream 254 has a corresponding July 9 title, starts `2026-07-09T14:51:00Z` and ends `2026-07-09T21:05:31.793Z`, a span of 22,471.793 seconds. Its title contains `->` where the YouTube title has `-`, and the stream contains multiple game segments, including overlapping/open segment metadata. Match the whole stream, not one game segment.

This is a strong identity candidate. The 31.793-second duration difference does **not** establish whether the beginning, end, both, or polling delay accounts for the discrepancy. No offset has been measured and no final mapping is approved by this research. The distinction matters more than fuzzy-title precision.

## Matching strategy

1. Reuse an explicit, confirmed YouTube-ID-to-stream mapping when available.
2. Extract a recording date from a leading Finnish `d.m.yyyy` title pattern; retain its provenance and precision. Compare local dates in `Europe/Helsinki` (including daylight saving), while storing UTC instants. Never use the YouTube upload date as stream start.
3. Fetch/cache stream summaries and compare that date, normalized segment titles and duration. Normalize Unicode/case/whitespace and known trailing promotional links; preserve meaningful numbers/date/episode markers. A substring for `9.7` must not match `29.7` as a date.
4. Rank multiple possible streams and return reasons. Date+title+near duration can establish a likely identity, but a numeric similarity is not a confidence probability. Show ambiguous matches for a one-time manual association; no match means transcript-only processing.
5. Keep duplicate/restarted streams and partial uploads explicit. Never merge two streams solely to make durations fit. A later piecewise mapping can represent a VOD assembled from multiple streams, but v1 simply leaves such chat evidence disabled.

The first adapter can paginate the existing list and match locally. This allows useful progress before implementing any new public search endpoint.

## Timeline alignment is a separate state

For an uncut upload, define `offset_us` as the source stream time represented by YouTube time zero:

```text
stream_relative_us = youtube_us + offset_us
youtube_us = (event_utc - stream_start_utc) - offset_us
```

Positive offset means the upload omitted time from the start of the stream. Example: if the first YouTube frame is 42 seconds into the stream, a chat event at stream second 600 maps to YouTube second 558. This example is synthetic, not the measured offset of stream 254.

Store identity status separately from alignment status: `unmatched/ambiguous/likely/confirmed` versus `unknown/estimated/verified`. Record offset, method, uncertainty, mapping revision, valid interval and two or more measured anchors when verified. Check anchors near both beginning and end; disagreement can indicate edits, missing segments or drift. Metadata alone provides only an estimate.

Calibration can use a known visible stream event or a clearly observable chat reaction with manual source comparison. Chat reaction/transport delay makes the latter coarse; do not use it to claim frame accuracy. If timing cannot be checked, display activity as unaligned context and apply no candidate score boost. Later piecewise mappings must leave unmatched gaps, not stretch timestamps to fill them.

## Proposed public API extension in vaarattu.tv

Prefer a small read-only aggregate surface, with names/versioning agreed in that repository. Proposed routes below do not exist yet:

```text
GET /api/public/v1/streams/search?recorded_date=2026-07-09&q=pushing%2040&limit=10
GET /api/public/v1/streams/254/activity?bucket_seconds=15&from_seconds=0&to_seconds=22471.793
```

Search returns stream identity/start/end, duration seconds, segment titles/times, optional Twitch VOD ID and aggregate availability. It returns candidates, not a fabricated definitive YouTube match. Persist YouTube mapping locally so the public API need not store clipper-specific decisions.

Proposed activity response example (synthetic values):

```json
{
  "schema_version": 1,
  "stream_id": 254,
  "stream_start_utc": "2026-07-09T14:51:00Z",
  "time_basis": "stream_start",
  "bucket_seconds": 15,
  "message_timestamp_basis": "collector_received_at",
  "coverage_status": "unknown",
  "buckets": [
    {
      "start_seconds": 0,
      "end_seconds": 15,
      "message_count": 9,
      "unique_chatters": 5,
      "viewer_peak": null,
      "viewer_samples": 0
    }
  ]
}
```

Counts must come from each requested bucket directly: `COUNT(*)` and `COUNT(DISTINCT userId)` on message timestamps. Distinct counts cannot be recovered by summing minute values or subdividing the current chart response. Audience samples remain a separate nullable signal with a sample count. Missing audience is null, not zero. Unknown collection coverage must stay labelled even when a bucket has an observed message count of zero; confirmed collector outages should become explicit gaps if that evidence is added later.

Implementation proposal: allow only 15/30/60-second buckets, validate bounded stream-relative ranges, cap responses at 3,000 buckets, and return a continuation range for longer recordings. Use parameterized queries, an appropriate `(streamId, timestamp)` message index after query-plan review, and cached aggregate responses/ETags for ended streams. Bound query lengths and rate-limit expensive public aggregation. Return structured 400/404/429/503 errors. Do not expose message bodies, user IDs, subscriber profiles or per-person histories.

This requires a separately scoped implementation/change in vaarattu.tv. Verify its pending/local API rollout and any required migration before deploying there. The new clipper does not connect directly to its database or copy its credentials.

## How chat influences selection

Normalize active-chatters and message counts against a rolling local median and median absolute deviation (initial neighborhood: roughly ten minutes). Require a minimum absolute activity increase too, because tiny quiet baselines can exaggerate a one-message change. Deduplicate correlated signals instead of counting the same chat surge twice.

Chat reactions often follow the sentence that caused them. Evaluate a provisional preceding window of 0–90 seconds and tune on labelled examples; bucket timing is not a word boundary. Raids, giveaways, automated messages and moderation events can spike without a worthwhile spoken clip. The public aggregate alone cannot reliably identify all of these.

Use aligned spikes as extra candidate context/tiebreakers and, optionally, a bounded second look at nearby speech. The primary transcript scan still covers all speech. Existing coarse activity can be used as coarse evidence once deployed and aligned; report its actual bucket width and never upsample it into invented 15-second detail. Compare chat-on versus chat-off on matched held-out VODs before promoting the boost. The maximum initial ranking contribution is one point out of the 20-point editorial scale.

## Integration acceptance checks

- Real July example suggests stream 254; July 29 is not an accidental match. Duplicate titles return multiple candidates.
- Upload-date lag, Helsinki DST/midnight, stream restart and duration mismatch fixtures behave as specified.
- `offset=42s` maps stream 600s to YouTube 558s; unmapped intervals never receive activity.
- Known gaps, missing samples and unknown collection coverage are distinct from observed zero activity.
- Long-stream bucket limits/pagination and distinct-count semantics are tested at query and response levels.
- API outage, 404 activity, ambiguous identity or unverified alignment all retain a complete transcript-only path.
