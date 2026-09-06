# Clip quality and media fixes — 2026-09-06

## Selection

`conversation-v3` prefers one self-contained idea for a general Finnish audience, usually 15–45 seconds, at most 60 seconds. Short complete jokes can qualify; no padding to the previous 20-second minimum. The technical automatic range is 5–60 seconds. Existing saved clips and manual edits remain supported at 5–90 seconds.

Game-related stories or jokes can work, but routine mechanics, crafting/build advice and complaints that need knowledge of the game are explicitly excluded in both discovery and independent verification. Quiet personal observations remain eligible. No visual event or payoff may be invented from the transcript.

Removed the three-proposals-per-window schema limit, ten-candidate verification cutoff and three-export UI/API quota. All distinct proposals reach verification; all passing, non-overlapping clips can render. Zero clips is a valid result. Requests remain sequential with low OpenAI reasoning and the original spending cap. Verification cost now varies with the number of proposals. Response/context limits and at most one repair per request remain; no unlimited spending or guarantee of exhaustive recall is implied.

Completed selections are preserved, including old clip identities and their saved count limits. Retry render does not apply new editorial rules to an old selection. New selections use v3; completed runs are not silently rescanned or recharged. See [selection design](selection-design.md).

## Why the first VOD had no vertical outputs

Run `a10dd4555f3142fc938bc9035607285a` generated 34 proposals, verified ten and accepted two. The selected spans were approximately 22 and 66 seconds including handles around speech. Both clips were held before rendering: one unusable audio sample and one inconsistent audio mapping. No vertical MP4 had been produced. This was a media failure, not an empty selection.

The downloaded section streams began at:

| Clip | Audio start | Video start |
|---|---:|---:|
| `4fb502f13663c07e79383860853fbe0b` | −0.007 s | 2.566 s |
| `595b46f6d05660791239ca1b0ef8c308` | −0.007 s | 4.451 s |

This accounts for the audio-only lead-in in the downloaded sources. New section acquisition uses `--force-keyframes-at-cuts`, explicit H.264/AAC encoding and Matroska output. It still requests only the bounded section plus ten-second handles; this trades some section encoding time for clean cuts. [yt-dlp documents the re-encoding tradeoff](https://github.com/yt-dlp/yt-dlp#post-processing-options).

The old alignment code sought separately into the combined video/audio file. With these files, video keyframe seeks shifted the audio origin; even `-ss 0` skipped early audio. The corrected code decodes the entire short section once **without any seek**, then slices the PCM samples. Full-VOD reference extraction remains bounded audio-only seeking. [FFmpeg documents input seeking behavior](https://ffmpeg.org/ffmpeg.html#Main-options).

If a sample is silent or ambiguous, alignment tries other positions (at most five). It still requires two matches separated by at least six seconds and 35% of the section, and consistent origins within 80 ms. Contradictory reliable matches still hold the clip. `alignment.json` retains successful anchors and unusable sample reasons. No caption retiming is used to hide drift.

The corrected function was exercised on both existing files using isolated diagnostics under `.cache/fixed-timing-check/`. It recovered origins 18,210.028167 and 22,230.027750 source seconds, with correlations approximately 1.0 and anchor disagreement below 1 ms. Neither the source media nor production run records were changed. No download, inference or final render was run during this fix.

## Using the changes

Juha starts/restarts the app himself, then reloads the page. Held clips now have **Retry render**. This queues only a new media revision and preserves transcript, selection, usage and review status. It does not start another model request. The first failed clips have no saved mapping, so their retry downloads fresh sections with the corrected cut options. Retry one clip at a time because the app permits only one active run.

Successful vertical packages remain in `workdir/ready/<clip_id>/<revision>/short.mp4` with captions, title and metadata. The clip card offers **Download vertical video**. Run status explicitly counts ready and held clips, and cards display clip duration separately from original VOD timestamps. Rendered previews remain accessible if a later validation step holds the output. Layout, caption and technical checks still apply; a held preview is not promoted as ready.

The renderer rejects a selection whose picture begins too late and checks final audio/video start agreement in addition to duration agreement. The new downloader options were checked against the installed yt-dlp argument handling and offline regression tests; a real new download and final visual/caption QC remain for Juha's next manual run. Revised editorial quality has not yet been benchmarked with paid inference.

Verification: 100 offline Python tests passed, including unlimited-count orchestration, sub-20-second rendering, silent-sample fallback, real-drift rejection, no-seek PCM arguments, delayed-picture rejection and authenticated revision-guarded media retry. The Node UI test, JavaScript syntax check, Ruff lint/format and Git diff whitespace check also passed. Two existing third-party deprecation warnings remain.
