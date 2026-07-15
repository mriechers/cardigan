# POC Findings Log

Fill this in as you run the POC. These findings gate the full-feature build
(`planning/YOUTUBE_METADATA_FEATURE.md`) — be specific and honest about
friction.

## Videos processed

| Date | Video ID | Title | Media ID derived? (where) | SST linked? | Captions (manual/auto/none) | Job | Written back? |
|------|----------|-------|---------------------------|-------------|------------------------------|-----|----------------|
| | | | | | | | |

## Media-ID ↔ video mapping

_The #1 question. How often was the Media ID derivable, from where, and how
often was it wrong? Does the full build need an SST YouTube-URL field or a
human-confirmed match step?_

-

## Caption quality

_Manual vs auto track availability per show; transcript cleanliness; videos
with no captions._

-

## Pipeline fit

_Did the 4 phases handle caption-style transcripts (no speaker labels)? Was the
SEO output sufficient for the audit, or was the raw transcript needed?_

-

## Limits & style collisions

_Where the 80/90/350 house limits and YouTube's 100/5000/500 pulled apart;
boilerplate handling in descriptions._

-

## Quota reality

_Actual units per cycle; any contention with pbswi skills._

-

## Time & value

_Wall-clock per video; which steps needed the human; which felt automatable._

-

## Verdict for the full build

_Go / no-go / go-with-changes, and what Phase B should do differently from the
plan._

-
