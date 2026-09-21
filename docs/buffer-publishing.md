# Buffer publishing

## Connect once

1. Sign in to [Buffer](https://publish.buffer.com/) and connect your YouTube,
   Instagram and TikTok accounts there. Buffer handles each platform's authorization.
   Use a professional Instagram account for automatic publication. No separate
   Google, Meta or TikTok developer application is needed in this project.
2. Create a personal key in [Buffer API settings](https://publish.buffer.com/settings/api).
   Paste it into **Publishing → Buffer accounts**. The app validates account access
   and saves `BUFFER_API_KEY` in the ignored project `.env`, preserving the R2 keys
   and other settings. The browser never receives the saved key back.
3. Select the organization and destination channel for each platform, then **Save
   channels**. Leaving a platform unselected excludes it from new submissions.
   **Refresh Buffer status** fetches connected channels and current delivery results.
4. Cloudflare storage must also show Connected. See [storage setup](cloudflare-publishing.md).

Buffer Free supports three connected channels and ten queued posts per channel.
The app checks queue availability before preview and again before each clip's
submission, including existing posts created outside this app. It reserves room for
locally recorded submissions whose outcome is uncertain. Buffer still enforces its
own account limits, permissions and media requirements.

## Publish or schedule

**Fill scheduled queue** uploads and schedules automatically when clicked. It uses only
current clips marked **Ready for posting**, fills the shared available capacity across
your selected channels (up to ten clips), and assigns one clip per day at a **random
minute between 19:00 and 21:00 Finnish time (Europe/Helsinki)**, including both endpoints.
Each clip gets its own random time, shared across its selected channels and saved with
the posting plan. Finnish daylight-saving changes are handled automatically. It starts
today if the window still has a time more than ten minutes away, choosing from the
remaining eligible minutes; otherwise it starts tomorrow. It appends after existing queued
posts and skips dates reserved by other local plan entries or already-used posting dates.
It does not require every other clip from
the recording to be reviewed first.

New recordings are ordered by the library's source-video publication date, falling back
to processing date when unavailable. Clips from the same source, including repeated runs,
are ordered by source start time. If a recording spans multiple fills, its remaining clips
continue before a new recording group. Newly finished clips earlier than that recording's
already-submitted source moments are left out to preserve order; the result reports this.

Existing Buffer submissions and manual posting records prevent resubmission, including
after a clip is edited. Matching render hashes and repeated source moments are also excluded.
Posts created directly in Buffer are recognized when their media URL matches this app's
hosted render. Unrelated uploads made directly to social platforms cannot be identified
automatically; keep their manual posting records here.

Filling uses each clip's title as its caption, Gaming as the YouTube category, and Not made
for kids. Use individual previews below when different metadata is needed. Existing remote
schedules stay unchanged; selected entries in the local plan that have never been submitted
can move to the new daily schedule. An incomplete or uncertain existing submission stops
filling until resolved. Accepted posts are retained if a later submission fails. The button
does not start a recurring worker: press it again as slots become available.

Only the current revision marked **Ready for posting** can start a new submission.
The Publishing page shows ten clips per page, with final-video playback, an editable
post caption, YouTube title, category and audience setting. The default category is
Gaming and the default audience is Not made for kids; check these before confirming.
YouTube visibility is public. Instagram videos use Reel format and share to the feed.

- **Preview publish now** submits one clip to the selected channels immediately.
- **Choose posting time** accepts a local date/time and an IANA time zone, defaulting
  to Europe/Helsinki. Missing or ambiguous daylight-saving times are rejected.
- **Fill scheduled queue** schedules one ready clip per day without a separate local
  planning step. **Ready for posting** clips appear in Publishing automatically.
- To choose dates with a local plan instead, expand **Manual posting & saved plans**
  within Publishing. Select up to ten entries, then **Preview selected daily posts**. The stored dates and source order are preserved; this does not use Buffer's
  own recurring time slots.

The individual and selected-batch options open a preview with the exact video revision, caption, destination
accounts and publishing time. Nothing uploads or posts until the preview is confirmed.
The app then uploads unchanged final MP4 bytes to R2 and submits their public HTTPS URLs
to Buffer. A previously verified upload is reused. Keep the page open while a batch is
being submitted. Each clip's submissions are persisted independently.

After Buffer accepts a schedule, it handles publication even if the local app is closed.
The local daily plan does not continuously refill Buffer: use **Fill scheduled queue** or
submit another selected batch as slots become available. Refresh Buffer status to see the current per-platform results.
An accepted/scheduled post is not counted as published until Buffer reports it sent.
Notification-only posts, drafts and provider errors are shown as needing attention.
Manage cancellations, remote edits and failed remote posts in Buffer, then refresh here.
Editing a clip locally does not replace videos already submitted to Buffer.

## Failures, retries and retained files

Each platform has a durable receipt written before and after its submission. Retrying
a partial request skips platforms Buffer already accepted. Reads use bounded backoff
for temporary network/server failures. Post creation is never retried blindly: Buffer's
documented create operation has no idempotency key, so a timeout might mean the post
was created but its response was lost.

For an uncertain outcome, **Refresh Buffer status** searches the channel for the matching
uploaded video, caption and scheduled time. Exactly one match can restore the receipt.
Otherwise inspect Buffer, then use **Resolve submission** to attach the matching Buffer
post ID or explicitly confirm that no post was created. Only then can remaining platforms
be retried. A wholly unsubmitted request can be discarded and previewed again with new
settings. Existing remote posts retain their records to prevent accidental duplicates.

Preview tickets expire after one hour. Changed files, withdrawn final approval, revised
clips, changed channels and changed daily-plan entries block stale submissions.
Publishing requests are serialized independently of video processing and review edits.
Their state lives in `buffer_publications` and `buffer_previews` in the existing SQLite
database; non-secret channel configuration lives in `workdir/buffer.json`.

Cleanup preserves submitted video revisions. **Remove hosted copy** becomes useful after
all tracked destinations have been confirmed published for 48 hours; it checks every local
request using the same URL and preserves the local video. Before confirming, check that no
other posts made directly in Buffer still depend on that URL. No automatic age-based cloud
deletion is enabled.

## Verification and first live test

Offline tests exercise the HTTP routes, R2 upload and Buffer GraphQL requests together,
including three-platform submissions, source-order batches, partial failures, uncertain
response recovery, revision checks, capacity, DST and retained files. UI tests cover
credential clearing, preview/confirmation, scheduling inputs and interrupted batches.
Live account authorization and actual publication still require testing with the connected
accounts after the user starts the server. No live posts are created by the tests.

References: [Buffer API setup](https://developers.buffer.com/guides/getting-started.html),
[video post example](https://developers.buffer.com/examples/create-video-post.html),
[create options](https://developers.buffer.com/types/CreatePostInput.html),
[channel connection](https://support.buffer.com/en-us/articles/connecting-your-channels-to-buffer-HvWLgAJvL9).


## Posting titles and captions

The Publishing tab saves a separate title and post caption for each final clip revision.
Generate posting copy uses the recording's configured LLM and the final edited words, excluding
removed speech. It asks for natural Finnish, a specific title and one or two short sentences;
no hashtags, engagement bait or invented details. Optional writing guidance is sent with the request.
Regenerate replaces the saved copy. You can edit and save both fields without rerendering or
clearing Ready for posting. Editing the video makes its old copy ineligible for the new revision.

Previewing a post saves your text first; blank fields trigger generation. Fill scheduled queue
saves entered text and generates missing copy for selected clips before submitting anything.
Generation errors stop the batch before submission, preserving text already prepared for retry.
Existing Buffer submissions keep their original text; manage those posts in Buffer.
