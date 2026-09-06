# Audio, transcription and spoken-moment discovery

Status: proposed v1 contracts and algorithm. Parameter values below are initial experiment settings. [Evaluation](implementation-plan.md) decides which settings become defaults.

Current selection implementation: [passages-v2 design and measurements](selection-design.md) supersede earlier window/prompt experiments below.

Implementation update: STT is Turbo only by the user's latest instruction; alternative ASR and optional re-transcription described below remain future research. The current implementation uses source word IDs throughout and records its exact scope in [implementation-status.md](implementation-status.md).

## 1. Select one video now; discover the archive later

Pin the verified channel ID `UCUCV40VqBZqt83afjbbICvw` for [VaarattuVODs](https://www.youtube.com/@VaarattuVODs/videos). Initially accept one explicit video URL/ID and hydrate its full metadata, validating channel identity, duration and availability. Use no-playlist behavior: a video URL containing a playlist does not authorize processing the playlist. Persist this VOD and admit one run through the invariant in [architecture.md](architecture.md). No channel enumeration is required before pressing Run, and completion never admits another video automatically.

For the later archive milestone, enumerate the `/videos` tab with yt-dlp flat metadata, storing every YouTube ID once. Hydrate full metadata when a VOD approaches the work queue; flat entries can lack upload dates and differ slightly in duration.

In that later mode, maintain a durable inventory and pagination/checkpoint state for backfill. Rescan recent entries for new uploads independently of the older backlog. Queue by hydrated upload timestamp descending; use observed channel order provisionally until hydration. In both modes store a separate title-derived recording date for stream matching. Preserve raw metadata fields needed for provenance; do not persist ephemeral signed format URLs or browser cookies in manifests.

Reject currently live, upcoming and unavailable selections with a clear reason; videos still being processed can be retried later. In future backlog mode, a failed VOD must not block the remaining backlog, and enumeration must not stop at the first known ID assuming everything older is processed. No YouTube API key is necessary for initial yt-dlp metadata access; the official uploads-playlist API is an optional future alternative.

## 2. Acquire audio without a video download

Request an audio-only format, keep its original compressed container, verify that it has audio and no video, and retain metadata plus a hash. Use explicit audio selection rather than relying on the downloader's general default. Avoid a fallback that downloads a complete combined video just to extract its audio. If an audio-only representation is unavailable, hold that source with a clear reason.

Illustrative command shapes, not an installed CLI or commands executed during planning:

```text
yt-dlp --ignore-config --no-playlist -f bestaudio -o <id>.%(ext)s <vod-url>
yt-dlp --ignore-config --no-playlist --download-sections "*<start>-<end>" -f <selected-video-and-audio-formats> <vod-url>
```

Use the second form only after candidate selection. The [yt-dlp documentation](https://github.com/yt-dlp/yt-dlp) documents time-range acquisition through FFmpeg. Verify bandwidth and seek behavior on real VODs; the option alone is not proof of exact boundaries or minimal transfer.

Keep an isolated downloader config/cache. Preflight yt-dlp, FFmpeg/FFprobe and the required supported JavaScript runtime/EJS setup; the current [EJS guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS) recommends Deno. Pin a known-working set, record versions and update deliberately when extraction breaks. Do not inherit the niilo22 hardcoded extractor-client fallbacks or automatically use another project's cookies.

## 3. Canonical timeline and transcript

Use integer microseconds and half-open intervals `[start_us, end_us)` everywhere internally. Zero is the start of the YouTube VOD presentation timeline, not the first detected speech and not Twitch wall-clock time. Store original container start timestamps and any conversion offset in the audio manifest. Validate audio/video correspondence in the range-download milestone before relying on this mapping.

Decode PCM at the selected ASR model's required sample rate/channels (16 kHz mono for the initial Whisper profile) only for active chunks. For Whisper start with 20-minute owned cores and 5 seconds of context on both sides where available; other models may need different tested chunk geometry. Persist successful chunk results so a six-hour failure does not lose earlier work. Both unbatched and batched ASR must map chunk-local timestamps back to the canonical VOD timeline.

Current new-run ASR profile: faster-whisper `turbo`, Finnish transcription (`language=fi`, not translation), word timestamps, VAD enabled, FP16 on CUDA, beam size 5 and `BatchedInferencePipeline` batch size 16, matching niilo22. Original runs retain unbatched decoding for resume compatibility. Compare batch sizes with measured memory and accuracy; compare beam 1 versus 5 only after fixing the evaluation audio. Flash Attention is optional and disabled pending validation. The [faster-whisper documentation](https://github.com/SYSTRAN/faster-whisper) supports CUDA precision choices, batched inference, VAD and word timestamps. Its published throughput is not a promise for Finnish stream audio.

VAD detects speech for inference; it does not authorize shortening the source timeline. Keep original gaps, restore offsets after VAD, and never concatenate speech-only audio without an explicit mapping. Initial text-conditioning choices must be recorded; compare disabling previous-text conditioning if repetitions/hallucinations occur.

For chunk seams, keep tokens by owned core, reconcile matching overlap words using normalized text plus time proximity, and reprocess a seam locally if a sentence/word was split. Never deduplicate repeated words on text alone. Preserve words with their raw times/probabilities, original segment text and diagnostic fields; canonical merged output must have finite in-range bounds and ordered starts. Flag unexpected overlaps or missing seam speech instead of silently forcing invented timings.

The merger first tries to reconcile conflicting owned-core boundaries using the saved context on both sides. It requires three consecutive matching normalized words, at least two distinct words, and start/end agreement within 300 ms per word. It selects a handoff with non-overlapping original timestamps, preferring one before the disputed phrase. This is the preferred repair, but a missing shared phrase no longer aborts transcription.

## Non-fatal timestamp conflicts (2026-09-06)

The initial fixes were too narrow: phrase matching repaired one cross-chunk seam, and a later 300 ms tolerance covered one internal decoder-segment overlap. The current policy treats unresolved timestamp conflicts as local review issues, without any larger-overlap threshold that aborts an otherwise completed VOD:

- If shared-phrase reconciliation fails or its handoff falls outside the owned chunks, retain the original owned speech from both chunks. Record `chunk_seam_conflict` across the five-second context on both sides, expanded to cover the conflicting word intervals.
- Preserve overlapping words with original text, timestamps and probabilities. Overlaps beyond the existing 150 ms tolerance are recorded as `word_overlap`; the longest active word interval is tracked so nested overlaps cannot disappear behind a shorter preceding word. Each review range covers the full involved word intervals, including a clip boundary that includes only part of them.
- Deduplicate the same word only across overlapping source chunks when its text and timing match. Repeated words inside one decoder output remain intact.
- Save all issues in `transcript.json` under `timing_issues`, with source intervals, chunk indices and, for word overlaps, canonical word IDs and overlap duration. This records uncertainty; it does not claim to have corrected speech or verified acoustic timing.
- Continue normal transcript selection. Carry the issue intervals into clip metadata and the run report. Any rendered clip intersecting a review range stays held with a speech/cut/caption warning. This check uses intervals, not just the included caption words, and is reapplied after boundary edits and retries. Clips outside the ranges can be ready. A manual reviewed checkbox does not override this technical hold.
- The run completes with its timing warning count and ready/held results. Full scan coverage means all transcript windows were evaluated, not that every timestamp is reliable. Reports retain timing issues after rerendering.

No extra inference, timestamp clamping, fabricated silence or full-VOD re-transcription is used for this recovery. Raw chunk JSON and decoding fingerprints stay unchanged. After the user restarts the app, Retry reuses matching successful chunks. Invalid data, missing/failed decoding, cancellation, unavailable GPU, storage and other runtime failures still surface normally; only known merge conflicts are localized.

Read-only verification against both saved runs: `a10dd4555f3142fc938bc9035607285a` merged 19 chunks into 20,757 words with no remaining issues; `ed8155b3d8a24001be117a2f335bee78` merged 19 chunks into 19,984 words with one 230 ms overlap. All merged text/timestamp tuples matched raw entries, IDs were unique, starts ordered, and raw hashes unchanged. Synthetic tests cover larger/nested overlaps, unmatched seams, repeated words, affected versus unaffected renders, report preservation and cache-only retry. No live checkpoint modification, app restart, inference or automatic resume was performed.

Transcript artifact v1:

| Field | Meaning |
|---|---|
| `schema_version`, `vod_id`, `transcript_id` | Identity and independent ASR revision |
| `audio_artifact_id`, `audio_sha256`, `duration_us` | Input identity and time domain |
| `profile` | Exact model/revision, runtime, precision, language, VAD, decode options, chunk geometry |
| `segments[]` | Stable IDs within this revision, start/end, raw text, confidence diagnostics and chunk provenance |
| `words[]` | Stable IDs, parent segment, start/end, raw text, probability if available |
| `coverage[]` | Completed/failed chunk intervals and reasons |
| `gaps[]` | Observed inter-speech gaps and optional VAD regions; not hallucinated words |
| `quality_flags[]` | Repetition, speech in apparent silence, low confidence, language/speaker uncertainty |

Derived display punctuation or corrected captions live separately from raw ASR. Finnish names, negations, numbers, dialect and English game names require particular attention. A small confirmed glossary can guide ASR where the model supports it; do not send unsupported controls or bias it with an imagined transcript. Music/game dialogue/Discord voices can be transcribed as speech: a single mixed audio track does not establish which voice is Juha. Defer diarization until examples show it is needed; uncertain attribution goes to attention.

Every application candidate must carry `speaker_status` (`primary_assumed`, `primary_verified`, `other`, `mixed`, or `unknown`) and `speaker_basis` (a source-profile revision or an audio-review record). These fields are assigned by the application from that evidence, not inferred as identity facts by the text LLM. An initially uncalibrated source is `unknown`. A confirmed predominantly solo-host source profile may support `primary_assumed` for automatic processing when there are no contrary flags; only an actual audio check supports `primary_verified`. A text verifier may flag dialogue/quoted-media uncertainty, but cannot verify a voice. Other-speaker-only clips are rejected; mixed/unknown cases are held. Profile-based assumption can miss a guest or game voice, so report this limitation and measure attribution errors in the pilot; add audio speaker-change detection only if those errors justify it. This avoids pretending that diarization-free automation certifies identity or requiring a manual listen to every otherwise routine clip.

Compare Turbo, large-v3 and Finnish-NLP Whisper on the same audio, and test Parakeet TDT 0.6B v3 through native NeMo-Speech.cpp as a different model family. whisper.cpp is a runtime alternative, not automatically a recognition-quality improvement. Capability and timestamp validation details are in [models-and-providers.md](models-and-providers.md). Optional re-transcription of shortlisted ranges must occur after the LLM exits, with one ASR session for those ranges and another confirmed unload afterward.

## 4. Discover moments with full speech coverage

The default local direction is Gemma 4 through llama.cpp: test 31B Q4 first and 26B-A4B Q4 as the required comparison, with full GPU residency. The ASR process must exit before loading the LLM. Keep the chosen LLM loaded through discovery and verification, then unload it. API selection uses the same application output contract with provider-specific adapters; all five requested providers and their current limitations are in [models-and-providers.md](models-and-providers.md).

Scan all completed transcript regions, not just loud/chatty ones. Initial geometry: six-minute core windows with 90 seconds of surrounding context on each side. Emit a candidate only if its central idea/payoff anchor belongs to that core. This assigns ownership across overlapping windows while still exposing setup and ending.

For local inference, tokenize the actual prompt with output/schema room reserved inside the allocated 16K/32K context. APIs use an independent conservative input bound, including schema/control overhead, instead of the local GPU setting. If a dense window exceeds the budget, split ownership at a passage boundary with the full surrounding context retained and record both owned cores. Do not silently truncate or replace the transcript with a summary before discovery. Empty/no-speech cores may be skipped with recorded coverage; quality-flagged speech stays visible for diagnostics.

Provide compact utterance lines such as `seg_0123 | 00:14:21.300–00:14:27.100 | <verbatim Finnish speech>`, explicit long-gap markers, and quality flags. Full word detail is available for the later boundary pass. Include game/title context as metadata, not an instruction to favor game highlights.

Discovery asks for zero to three worthwhile moments per window. A request for zero is valid. For each, require source IDs, a central idea anchor, a concise Finnish summary, why it can stand on its own, and provisional scores using the five-dimension rubric below. Find a story, opinion, observation, joke or explanation. Do not reward emotional intensity by itself. Pure game callouts and replies that require missing chat should be rejected or flagged.

Save all outputs before ranking. Merge duplicate proposals across overlapping windows using shared source anchors and substantial interval overlap; retain alternative boundaries and provenance for inspection. Text similarity may help identify repeated versions of the same point, but avoid removing different anecdotes simply because they discuss the same topic. No embedding service is required in v1.

## 5. Verify meaning and select boundaries

After duplicate merging, sort discovery proposals by provisional rubric sum (topic-diversity ranking remains future work), and select at most 10 for verification per VOD. Keep every unselected proposal for recall analysis. This is a cost bound that can lose useful moments and must be measured separately from discovery coverage. For each shortlisted proposal, send original passages around it, initially 90 seconds before/after. Only the candidate and 15 seconds on either side include individual word IDs. Withhold discovery scores, summaries and rationales; the verifier makes its own assessment. A separately worded verification call checks what a viewer would actually hear and whether the excerpt preserves the speaker's position.

Verification outcomes are `accept`, `reject`, `needs_context`. On `needs_context`, permit one expansion to 180 seconds on either side if unshown neighboring speech exists. Otherwise hold/reject; never stretch the excerpt blindly to fit a target duration.

Proposed application output contract:

```json
{
  "schema_version": 1,
  "outcome": "accept",
  "start_word_id": "w_00410",
  "end_word_id": "w_00492",
  "idea_word_id": "w_00447",
  "category": "opinion",
  "summary_fi": "Illustrative summary; not an actual VOD quotation.",
  "title_fi": "Illustrative title",
  "scores": {
    "standalone": 4,
    "opening": 3,
    "substance": 3,
    "payoff": 3,
    "fidelity": 4
  },
  "flags": [],
  "reason": "Illustrative explanation tied to the supplied source anchors."
}
```

All referenced IDs must belong to the supplied transcript revision/context, with `start ≤ idea ≤ end`. The application derives bounds from the first word's start and last word's end. Reject unknown IDs, invalid scores, impossible order, out-of-range spans and unsupported categories. Free-floating LLM timestamps never become edit commands. Validate JSON shape and semantic references independently; constrained JSON does not establish truth.

Prompt data (transcript, titles and chat-derived labels) is untrusted quoted material. Models have no tools, filesystem/network access or authority to change pipeline instructions. Keep inference parameters, prompt hash, raw response, parse status and usage. Allow one repair for malformed/schema-invalid output, then record an explicit coverage gap and continue other windows. Invalid individual discovery anchors/ownership are logged and filtered without retrying valid siblings. Failed verification excludes that candidate. Operational and budget failures remain blocking.

Starting rubric, each dimension 0–4:

- Standalone: enough context within the excerpt for an unfamiliar viewer.
- Opening: an understandable question, claim or setup near the beginning.
- Substance: a specific thought, experience, insight or joke worth hearing.
- Payoff: the thought resolves, the explanation lands, or the joke finishes.
- Fidelity: wording, negation, qualifiers and stance remain faithful in context.

Provisional eligibility: total at least 15/20, standalone and fidelity at least 3, and no unresolved material flag. These are ordinal editorial judgments, not calibrated probabilities of success. Tune thresholds on development examples, then freeze them for holdout testing.

For candidates accepted by verification, rank by the verifier's rubric sum and apply only a small optional chat tiebreak/boost (maximum one point on the 20-point scale). Chat can never rescue a failed fidelity/standalone gate. Limit repeated source intervals/topics and prefer useful variety without forcing category quotas. Render up to 3 passing results from the verified shortlist. Persist rejected and pruned proposals for recall diagnosis.

## 6. Practical boundaries and export handoff

Begin with approximately 0.2 seconds before the first chosen word and 0.3 after the last, only inside observed non-speech gaps and real source bounds. If padding would include any neighboring speech, remove that padding or extend the selected anchors to include complete words and caption them; never retain an uncaptioned word tail. Snap only against observed words/gaps, and check a natural beginning/ending in the context pass. If no clean 20–90 second contiguous span exists, hold the candidate or reject it.

Long pauses are evidence for pacing, not an automatic removal command. Preserve hesitation or silence that makes a joke work. A candidate with a long unrelated interruption should normally fail; v1 does not stitch its two halves together.

Rendering receives a versioned edit recipe: VOD ID, transcript revision, exact source interval, rationale/flags, layout preset, caption revision, audio treatment and renderer profile. The [rendering contract](rendering.md) owns range verification, captions and delivery. Changing a downstream recipe never mutates the canonical transcript.
