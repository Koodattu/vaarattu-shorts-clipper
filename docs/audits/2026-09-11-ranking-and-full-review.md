# Ranking repair and full review — 11 September 2026

Requested scope: repair ranking, expose all saved suggestions from the four videos in the discovery audit, and show ten clips per page in Runs & clips. Discovery and verification prompts, score gates, reasoning effort, and the normal ten-clip export cap remain unchanged.

## Implementation

Ranking uses bounded comparisons (at most sixteen candidates, reduced further to fit the existing input budget) and merges the ranked groups without discarding candidates. Each request uses dense local IDs constrained by its response schema, fixed list length, and complete-permutation validation. Duplicate-ID repair reports exactly which supplied IDs are missing/repeated. Successful responses remain cached and auditable. Operational errors stop the job; malformed output still permits a visible score fallback in ordinary processing. A requested full-review refresh refuses that fallback and can be resumed to retry. The ranking checkpoint version is `ranked-v2`.

`python -m vaarattu_shorts review-all RUN_ID ...` queues an explicit full-review refresh for completed ranked runs. It reuses saved transcripts, discovery and verification; it does not rescan speech or generate new ideas. The normal worker handles ranking, final transcription and rendering, with its existing concurrency and GPU ownership rules. Existing clips retain their IDs, revisions, edits and human reviews. The fresh top picks sort first; the rest are additional suggestions available in the normal unreviewed queue once rendered. Duplicate source cuts share a preview. Model reasons and exclusions are retained as review notes.

Source-valid suggestions outside the normal 3–90-second range receive explicit original audit bounds. These previews can retain the full original duration; normal clips retain their duration limits. No arbitrary truncation or silence-detector changes were made. An audit preview whose large-v3 caption refinement cannot align uses the saved Turbo words/timing and displays a caption warning. Normal production refinement failures still hold the clip. Existing held clips without a preview are retried for this audit; ready revisions are preserved.

## Queued videos

| Run | Existing clips | New previews | Distinct clips after audit |
|---|---:|---:|---:|
| 32917ac59bd942488098eca116eaf7c4 — 2 July | 10 | 73 | 83 |
| 2ff31ffeab094ae3ad7f29f358a257dd — 3 July | 10 | 86 | 96 |
| 462b57889cab44658844bef2097aa24d — 12 April | 10 | 39 | 49 |
| 24fb2eeb510545df997d6f013e04ba5a — 4 July | 10 | 58 | 68 |
| Total | 40 | 256 | 296 |

The original audit counted 299 raw responses / 297 saved proposals. Repeated identical proposals/cuts do not need separate renders. The two existing held clips are included among the forty and will also be retried.

The SQLite backup is `.cache/review-all-2026-09-11/state-before.sqlite3`. The pre-run clip/review snapshot and original selection-checkpoint hashes are in `.cache/review-all-2026-09-11/before.json`. Original source checkpoints and inference artifacts are preserved.

## Verification

- Full Python suite: 271 passed, one unrelated pre-existing failure. `test_codex_admission_needs_no_platform_key_and_snapshots_no_secrets` passes an empty layout to `add_layout`, which requires a name. Reproduced with the original HEAD storage implementation; left unchanged.
- Focused ranking, export, recovery, captions, gallery and media checks: 62 passed; the subsequently added audit-caption fallback test also passed with all ten final-transcription tests.
- JavaScript UI suite: seven passed, including ten-item pagination.
- Ruff: passed.
- Live app started hidden on the user’s authorization. The four requests are durably queued; live completion is recorded below after verification.

Model comparisons are subjective and can be inconsistent across batches. Bounded merging repairs transport/size/ID failures; it does not prove that the resulting clips match the creator’s taste. These additional human reviews will be the evidence for later prompt changes.


## Live verification before handoff

The 4 July run completed all twelve model comparisons with `method=comparison`, `version=ranked-v2`, and no warning. Its live API returns ten selected clips first and fifty-eight additional suggestions. Six of the refreshed top ten reuse existing previews; four need new renders. Large-v3 is processing the fifty-eight new previews (18/58 completed at the last snapshot). The other three jobs are durably queued under the existing one-job concurrency setting. Rendering is not yet complete and no new preview is claimed as verified at this handoff.

All forty original clip revisions, titles, source boundaries and words were checked against the backup snapshot. All forty saved review statuses and notes also match. Both discovery and ranking system prompt strings were compared against HEAD and are unchanged. `git diff --check` passed using the repository's normal Windows line-ending settings. The application remains running in the background; it was not stopped at handoff.
