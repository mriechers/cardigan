# Stage 2 -- Lint agreement study over production jobs

**Run 2 (2026-07-10T22:49:59Z)** -- after task 2c2's two root-cause fixes. Run
1's original numbers and full analysis are preserved verbatim (not
overwritten) in the [Run 1 history appendix](#appendix-run-1-2026-07-10t2210z-pre-fix-history)
at the bottom of this document.

Generated against `http://cardigan01:8100` (read-only GETs, 0.2s between
requests -- served from the existing `OUTPUT/eval/prod_artifacts/` cache,
no `--refresh`, no new network calls). 21 jobs studied, 21 with a stored
`validation_result`. House style rules: `config/house_style.yaml`.

Produced by `python -m scripts.lint_agreement_study --jobs all` (script + tests
unchanged from Run 1). Raw per-job/per-flag data lives in `agreement.json`
next to this file. Downloaded artifacts (job records, phase outputs) are
cached under `OUTPUT/eval/prod_artifacts/` -- git-ignored, not committed;
only short quoted excerpts appear below.

## What changed since Run 1, and the headline result

Run 1 measured 55.6% raw recall (25/45) against the Stage-2 acceptance bar
and traced 10 of the 20 misses to two code-level root causes:
`lint.formatter.truncation_suspect` only checked the last visible line's
punctuation (0% hit rate across the whole sample), and
`_LOOSE_SPEAKER_LABEL_RE` couldn't collect numbered generic labels like
`**Speaker 1:**`. Task 2c2 fixed both:

1. **Truncation coverage-vs-duration path** (commit `41021d2`, refined in
   `01805ef`) -- compares the last parsed `(MM:SS)`/`(H:MM:SS)` timecode
   marker in the formatter body against `duration_minutes`, firing below an
   85% coverage floor. Verified correct against 10 new synthetic TDD tests
   (boundary math, independence from the punctuation path, HTML-comment
   exclusion). Read on real production artifacts, this surfaced a second bug
   mid-implementation: the first version scanned `raw_output` unstripped, so
   a timestamp mentioned in passing inside a `<!-- REVIEW NOTES: ... -->`
   aside was scored as if it were a real coverage marker -- producing one
   coincidentally-right match (job 12), one match against the *wrong* LLM
   flag by rule_id-family (job 8, detailed below), and a genuine new false
   positive (job 15, a 76-second clip flagged as truncated because a review
   note mentioned "(1:01)" while discussing unclear clip origin, not
   coverage). Commit `01805ef` strips HTML comments before scanning, closing
   that hole.
2. **Numbered generic speaker labels** (commit `41021d2`) -- extends
   `_LOOSE_SPEAKER_LABEL_RE`'s continuation token to also accept bare digits,
   so `**Speaker 1:**` / `**Reporter 2:**` now enter the candidate pool.
   Verified with a decisive regression test
   (`test_numbered_label_actually_enters_candidate_pool_via_superset_detection`,
   confirmed red against the pre-fix regex, green after) that proves
   collection actually happens, not just that no violation fires (which is
   true either way and would have been a vacuous test on its own).

**Headline result, honestly: the aggregate matrix on this 21-job sample is
byte-for-byte IDENTICAL to Run 1.** both_caught=25, lint_only=34,
llm_only_deterministic=20, llm_semantic=62 -- same totals, same by-phase
breakdown, same by-category breakdown, same per-job matrix, same 20
`llm_only_deterministic` items. **Raw recall is unchanged at 25/45 = 55.6%.**
This is not a failed fix -- both fixes address real, TDD-confirmed
correctness gaps in the detection logic itself -- but neither gap's
applicable surface intersects this specific 21-job sample's actual failure
modes, for two different, fully-diagnosed reasons documented below. Per the
task's honesty constraint, that is reported as-is, not massaged.

### Why the truncation fix measures zero movement here

Every `(MM:SS)`/`(H:MM:SS)` timecode marker found anywhere in this entire
21-job corpus's `formatter_output.md` files (3 total occurrences, in jobs 8,
12, and 15) lives inside a `<!-- REVIEW NOTES: ... -->` HTML comment --
**zero** appear in the visible transcript body across all 21 jobs. Confirmed
by direct audit:

```
job8:  total_markers=1 outside_comments=0   -- "(00:07:44)" in a review note about
                                                a speaker-attribution transition
job12: total_markers=1 outside_comments=0   -- "(00:11:10)" in a review note about
                                                the SRT cutoff point
job15: total_markers=1 outside_comments=0   -- "(1:01)" in a review note about
                                                unclear clip origin
```

The corrected (comment-stripping) implementation therefore finds **zero**
markers to compare against duration in all 21 jobs, and the coverage path
silently skips every time -- exactly the documented "no markers" behavior,
working as designed. This reveals something the design assumption in the
Task 2c2 brief didn't anticipate: PBS Wisconsin's formatter phase does not,
as an actual production convention, embed periodic in-body timecodes in the
visible transcript prose at all. The `(MM:SS)` markers that do appear only
ever show up when the model self-reports a cutoff or attribution concern
inside its own review-note commentary -- and that commentary is exactly the
kind of freeform, unstructured text a coverage check cannot safely trust
(see the job 8 and job 15 cases below).

This does **not** mean the fix was pointless: the mechanism is correct and
will fire on any future formatter output that *does* embed body timecodes,
and closing the "scans review-note noise" bug prevents the false positive
this same commit briefly introduced (job 15) from ever reaching production
if `qa_gate.merge_flags` is flipped on later. But as a *coverage-vs-duration*
signal specifically, it has near-zero applicable surface in this real
corpus. The dominant real gap identified in Run 1 -- 8 of 10 bucket-A misses,
all "closes with a punctuated sign-off despite content missing before it" --
remains genuinely uncaught by either the punctuation or the coverage path,
because none of those 8 transcripts cite a body timecode at all. See
"Follow-up" in the Conclusion below for what would actually catch them.

### Why the speaker-label fix measures zero movement here

The regex fix is independently verified correct (job 17's real
`**Speaker 1:**` / `**Speaker 2:**` labels are now collected -- confirmed by
running `_LOOSE_SPEAKER_LABEL_RE` directly against the cached file and by the
decisive superset-detection regression test). But per the Task 2c2 brief's
own prediction, a clean 2-token generic label with no honorific correctly
produces **no violation** by design -- generic labels are legitimate house
style, and the point of collecting them is superset detection (e.g. "Speaker"
and "Speaker 1" both present would now correctly flag as the same person
labeled two ways), not flagging every generic label as malformed.

Job 17's real LLM flag -- *"speaker names not identified (generic 'Speaker
1/2' labels used throughout)"* -- turns out, on this closer post-fix
inspection, not to be a label-FORMAT complaint at all. It's a
content-verification request ("please cross-reference with SST/program
credits before publication" per the formatter's own review note) that the
keyword classifier's `generic 'speaker` substring swept into the
`speaker_label_format` deterministic category. **This item is reclassified
below from Run 1's "bucket A confirmed real gap" to bucket C
(classifier/policy ambiguity)** -- it was an overreach in the original
per-item analysis to call it a code-fixable format gap; on inspection after
actually building and verifying the fix, it's a semantic identity-
verification concern outside what `lint.formatter.speaker_label_inconsistent`
is designed to judge, structurally identical to the other classifier-
ambiguity items already documented in Run 1 (the SEO "review notes" template-
structure false hit, the ellipsis "truncated" false hit).

## Methodology

Unchanged from Run 1 -- reproduced here for completeness.

`scripts/lint_agreement_study.py` pulls each studied job's record (for
`duration_minutes`, `content_type`, `transcript_file`, and the stored
`validation_result`) plus its `analyst_output.md` / `formatter_output.md` /
`seo_output.md` artifacts from the production API, builds the same context
dict the worker bus assembles, and runs
`api.services.style_engine.lint.run_lint` against the real
`config/house_style.yaml`. The lint suite is wired but OFF in production
(`routing.style_engine.qa_gate.merge_flags` defaults false) -- every
`validation_result` in this sample is the LLM validator's verdict alone,
untouched by lint. The two arms are independent.

All 21 non-pending jobs in the production queue at Run 1's fetch time were
studied (14 `completed`, 7 `paused`); Run 2 reuses the same cached job set
byte-for-byte (no `--refresh`), so the input corpus is identical between
runs -- any matrix difference between Run 1 and Run 2 is attributable
entirely to the lint.py code changes, not to production data drift.

### Graceful degradation (no transcript text)

The REST API has no transcript-fetch endpoint, so raw transcript text is
never available to this study -- only `transcript_file` (the filename) is
passed through in context, unused. `run_lint` still never reads `transcript`
from context, so nothing degrades for lint. Worth noting for the Conclusion's
follow-up discussion: **this is an offline-study-only limitation, not a
production one** -- `api/services/worker.py` (line ~411/895) *does* populate
`context["transcript"]` with real transcript content at production runtime,
and `api/services/completeness.py`'s `check_completeness()` (already wired
into the worker independently of lint, `DEFAULT_COVERAGE_THRESHOLD=0.70`)
already uses it for a parallel word-count-based truncation signal outside
this study's scope.

### Deterministic-category keyword map

Unchanged from Run 1 -- see the full table in the
[Run 1 history appendix](#appendix-run-1-2026-07-10t2210z-pre-fix-history);
not reproduced twice here for brevity. No category definitions or keywords
changed for this re-run.

### Matrix cells

- **both_caught** -- a deterministic-category LLM flag whose rule_id family
  has a corresponding lint violation on the same phase.
- **lint_only** -- a lint violation with no corresponding deterministic LLM
  flag.
- **llm_only_deterministic** -- a deterministic-category LLM flag lint
  missed. The critical cell for the Stage-2 acceptance criterion.
- **llm_semantic** -- LLM flags outside lint's scope, listed for context
  only (not part of the acceptance criterion).

## Aggregate matrix (Run 2)

| Cell | Run 1 | Run 2 | Delta |
|---|---|---|---|
| both_caught | 25 | 25 | +0 |
| lint_only | 34 | 34 | +0 |
| llm_only_deterministic | 20 | 20 | +0 |
| llm_semantic (out of lint scope) | 62 | 62 | +0 |

By phase (Run 2 == Run 1 in every cell):

| Phase | both_caught | lint_only | llm_only_deterministic | llm_semantic |
|---|---|---|---|---|
| analyst | 0 | 0 | 1 | 3 |
| formatter | 15 | 8 | 11 | 31 |
| seo | 10 | 26 | 8 | 28 |

By deterministic category (Run 2 == Run 1 in every cell):

| Category | both_caught | llm_only_deterministic |
|---|---|---|
| char_limit | 10 | 5 |
| review_notes | 15 | 4 |
| speaker_label_format | 0 | 1 |
| truncation | 0 | 10 |

`output_missing`, `placeholder_text`, `content_past_duration`, and
`keyword_count` remain untested in this sample for the same reason as Run 1
(no job exercises them) -- unchanged limitation, see below.

## Delta-vs-Run-1 table, per bucket

Bucket assignments from Run 1's per-item analysis, re-verified against the
fixed code and reclassified where the fix's actual behavior (rather than the
pre-fix prediction) warrants it:

| Bucket | Run 1 count | Run 2 count | What moved |
|---|---|---|---|
| A -- real lint code gap, still open | 10 | 9 | Speaker-label item (job 17/formatter) reclassified out of bucket A into bucket C (see above) -- it was never going to close via this fix, as the Task 2c2 brief itself predicted. All 9 remaining bucket-A items are the truncation root cause (8 items, unchanged -- 0 markers exist in any of their bodies to compare) plus the pre-existing, out-of-scope analyst-phase review-note gap (job 15, unaffected by this task). |
| B -- stale validator checklist, lint is correct | 5 | 5 | Unchanged -- all 5 are `title_over_limit` flags against a retired 60-char limit; unrelated to either fix. |
| C -- policy question / classifier ambiguity, not a code defect | 5 | 6 | +1: job 17/formatter speaker_label_format moves in from bucket A (see "Why the speaker-label fix measures zero movement" above). |

Bucket totals sum to 9+5+6=20, matching the unchanged
`llm_only_deterministic` total. **Adjusted recall** (both_caught + bucket A,
i.e. "what lint should catch once every code-fixable gap is closed") moves
from Run 1's 25/(25+10)=35 → 71.4% to Run 2's 25/(25+9)=34 → **73.5%** -- a
real but small improvement, entirely explained by the reclassification
above, not by either fix actually resolving a production case. Both the raw
(55.6%) and adjusted (73.5%) numbers remain well short of the ≥100%
acceptance bar.

## Per-job matrix (Run 2)

Identical to Run 1's per-job matrix in every cell (verified programmatically
against `agreement.json`, not just spot-checked) -- reproduced here as this
run's own record rather than cross-referencing the appendix:

| Job | Status | Content type | Duration (min) | Validation result | both_caught | lint_only | llm_only_det | llm_semantic |
|---|---|---|---|---|---|---|---|---|
| 1 | completed | full | 32.9 | present | 1 | 2 | 1 | 8 |
| 2 | completed | full | 1.8 | present | 3 | 1 | 0 | 4 |
| 3 | completed | short | 1.9 | present | 1 | 2 | 2 | 0 |
| 4 | completed | short | 1.6 | present | 1 | 3 | 0 | 1 |
| 5 | completed | short | 1.6 | present | 2 | 1 | 1 | 1 |
| 6 | completed | short | 1.8 | present | 0 | 4 | 0 | 0 |
| 7 | completed | full | 33.0 | present | 0 | 1 | 3 | 6 |
| 8 | completed | full | 32.3 | present | 1 | 2 | 2 | 1 |
| 9 | completed | full | 47.0 | present | 1 | 3 | 0 | 4 |
| 10 | completed | full | 32.5 | present | 2 | 0 | 2 | 3 |
| 11 | completed | full | 32.5 | present | 1 | 1 | 0 | 0 |
| 12 | completed | full | 18.3 | present | 1 | 2 | 1 | 3 |
| 13 | paused | full | 4.0 | present | 1 | 1 | 0 | 1 |
| 14 | completed | full | 4.0 | present | 0 | 1 | 0 | 0 |
| 15 | paused | short | 1.3 | present | 2 | 2 | 2 | 10 |
| 16 | paused | short | 1.0 | present | 2 | 2 | 0 | 7 |
| 17 | paused | full | 2.8 | present | 1 | 1 | 3 | 3 |
| 18 | paused | full | 2.8 | present | 1 | 2 | 1 | 0 |
| 19 | paused | full | 2.8 | present | 1 | 1 | 1 | 5 |
| 20 | completed | full | 18.5 | present | 0 | 2 | 0 | 0 |
| 21 | paused | full | 18.1 | present | 3 | 0 | 1 | 5 |

## llm_only_deterministic -- every miss, verbatim, with re-verified analysis

The same 20 flags as Run 1 (confirmed identical). Bucketed as **(A) real
lint gaps**, **(B) stale-limit mismatches** (lint's silence is correct), or
**(C) policy/scope disagreements** (not a code defect) -- see Run 1's
methodology above. Items unaffected by either fix are summarized briefly
with a pointer to the appendix for full original text; items the fixes
touch (truncation, speaker_label_format) get fresh, code-verified analysis
below.

**Truncation (10 items -- 8 bucket A, one root cause, unresolved; 1 bucket C
cross-phase meta-commentary; 1 keyword-map false hit) -- unchanged from Run
1, root cause confirmed and now precisely diagnosed:**

- Job 1 / formatter, Job 8 / formatter (both flags), Job 12 / formatter, Job
  17 / formatter, Job 18 / formatter, Job 19 / formatter, Job 21 / formatter
  -- same 8 verbatim flags as Run 1 (full text in the appendix). **Re-verified
  root cause, more precisely than Run 1 could establish**: none of these 8
  jobs' `formatter_output.md` files contain **any** `(MM:SS)`/`(H:MM:SS)`
  timecode marker anywhere in the document (comment or body) -- confirmed by
  direct regex audit against the cached artifacts. The coverage-vs-duration
  path therefore has literally nothing to compare against duration for any
  of these 8 cases and correctly, silently skips, exactly as its "no
  markers" contract specifies. Every one of these transcripts still closes
  with a complete, terminally-punctuated sign-off paragraph (unchanged
  observation from Run 1), so the punctuation path stays silent too. **These
  8 misses are the study's single largest remaining gap and neither existing
  truncation-detection path can see them** -- see Conclusion for what would
  actually need to change.
- Job 7 / seo -- ellipsis keyword-map false hit (unchanged, see appendix;
  not a code gap).
- Job 17 / seo -- cross-phase meta-commentary, bucket C (unchanged, see
  appendix; not actionable as a lint change).

**char_limit / title_over_limit (5 items, all bucket B) -- unchanged from
Run 1**, unaffected by either fix. Full analysis in the appendix: all 5 are
`prompts/validator.md` enforcing a stale 60-char title limit against titles
that are all correctly under `config/house_style.yaml`'s real 80-char limit.

**review_notes (4 items -- 2 bucket C policy disagreement, 1 bucket C
classifier ambiguity, 1 bucket A minor scope gap) -- unchanged from Run 1**,
unaffected by either fix (this task's scope was formatter-phase truncation
and speaker labels, not the review-notes placement-policy question or the
analyst-phase scope gap). Full analysis in the appendix.

**speaker_label_format (1 item) -- RECLASSIFIED from Run 1's bucket A to
bucket C:**

- Job 17 / formatter: *"speaker names not identified (generic 'Speaker 1/2'
  labels used throughout)"*

  **Bucket C (was bucket A in Run 1) -- classifier ambiguity, not a code
  defect, confirmed by building and testing the actual fix.** Run 1's
  analysis called this "a confirmed real gap" based on the observation that
  `_LOOSE_SPEAKER_LABEL_RE` couldn't even see `**Speaker 1:**`/`**Speaker
  2:**` in `job17/formatter_output.md`. That collection gap was real and is
  now fixed (`_LOOSE_SPEAKER_LABEL_RE.finditer()` against the cached file now
  correctly yields both labels -- verified directly). But once collected, a
  clean 2-token label with no honorific correctly produces **no violation**
  under `_check_speaker_label_inconsistent` -- and it shouldn't: house style
  explicitly permits generic numbered labels when real names aren't
  available (per the formatter's own review note on this job: *"Speaker
  names not provided in captions... Generic labels used -- please
  cross-reference with SST/program credits before publication"*). The LLM's
  flag isn't complaining about label *shape* (which is all
  `speaker_label_inconsistent` is designed to judge) -- it's asking someone
  to go find the real names, a content-verification task. The keyword
  classifier's `generic 'speaker` substring rule swept this semantic request
  into the deterministic bucket; it's the same class of false hit as the
  ellipsis/"truncated" and SEO-template-structure items already documented
  in Run 1, just not caught by hand until this fix was actually built and
  verified against it. No further lint code change indicated here -- the
  collection fix (which does newly enable superset detection, e.g. a
  document mixing bare `**Speaker:**` and `**Speaker 1:**` would now
  correctly flag as inconsistent) was worth making on its own merits, but it
  was never going to close this specific miss.

## lint_only spot-check (5 picks)

Unchanged from Run 1 -- lint_only's count and content are identical between
runs (verified programmatically), so Run 1's 5 hand-picked spot-checks (job
1 short/long description over-limit, job 9 long description blowout, job 2
"Narrator" single-word false positive, job 4 keywords-count) remain
representative. Full write-ups preserved in the appendix. **One update
worth flagging explicitly**: the pre-fix (uncommitted) version of this
commit briefly produced a *new*, genuine `lint_only` false positive on job
15/formatter (`lint.formatter.truncation_suspect`, "Last timecode marker
(1:01) covers only 80.8%..." against a 76-second clip, sourced from an
unrelated review note about clip origin) -- this was caught during this same
re-run process (not shipped) and closed by commit `01805ef` before the final
numbers above were produced. It's called out here for the audit trail, not
because it appears in the final `lint_only` set (it doesn't).

## Conclusion

**Raw recall against the Stage-2 acceptance criterion is unchanged at
25/45 = 55.6% in this 21-job sample after both root-cause fixes.** The
suite does not meet the ≥100% bar and stays off. Adjusted recall (excluding
bucket B and the now-reclassified bucket C item) moves marginally from
71.4% to 73.5%, entirely from the job-17 reclassification, not from either
fix resolving a production case.

This is not evidence the fixes were wrong or wasted -- both are
independently verified correct via dedicated TDD (10 new synthetic tests for
the coverage path covering the exact boundary math, independence between the
two truncation-detection paths, and HTML-comment exclusion; 7 new tests plus
one decisive red→green regression test for numbered speaker labels), and
both close real, confirmed defects in the detection *mechanism* itself. What
this re-run demonstrates is that **neither defect was actually the
proximate cause of any of the 20 `llm_only_deterministic` misses in this
specific real-world sample**:

- The truncation coverage-vs-duration mechanism requires a `(MM:SS)` marker
  in the formatter body to compare against duration. Across this entire
  21-job corpus, zero such markers exist anywhere in any visible transcript
  body -- the 3 markers that do exist all live inside freeform review-note
  commentary, which the corrected implementation now correctly excludes as
  noise (it was briefly *not* excluded mid-implementation, and that version
  produced one coincidentally-right match, one flag-mismatched match, and
  one new false positive -- all closed before the final run). PBS
  Wisconsin's formatter phase, as actually used in production, simply
  doesn't embed in-body timecodes as a routine convention this check can
  read.
- The speaker-label regex now correctly collects numbered generic labels,
  but house style correctly treats them as legitimate when unaccompanied by
  a conflicting label for the same apparent person -- so the one real-world
  case this fix targeted (job 17) was, on closer inspection, a semantic
  identity-verification request the keyword classifier over-eagerly swept
  into the deterministic bucket, not a label-format defect.

**Follow-up, updated from Run 1's recommendation given this new evidence:**
the 8-item truncation root cause (unchanged, still this study's single
largest gap by a wide margin) needs a fundamentally different signal than
"compare the last body timecode to duration," because body timecodes
essentially don't exist in this corpus. Two candidates, in order of
plausibility:

1. **Word-count coverage vs. source transcript** -- `api/services/
   completeness.py`'s `check_completeness()` (already wired into the worker
   independently, `DEFAULT_COVERAGE_THRESHOLD=0.70`) does exactly this
   comparison and *does* have access to real transcript text at production
   runtime (`context["transcript"]`, populated by `worker.py`) -- this
   offline study simply can't validate it because the REST API has no
   transcript-fetch endpoint (see Methodology). A `lint.*` check built on
   the same word-count-ratio approach, wired to run only where transcript
   text is actually available in the live worker context, is the most
   promising untested path and should be the next study iteration's target
   before further duration-marker approaches are attempted.
2. **Mid-document speaker-turn punctuation** (Run 1's original alternate
   suggestion, still untried) -- scan speaker turns for one that ends
   without terminal punctuation immediately followed by a new `**Name:**`
   header, catching a mid-document cutoff distinct from the existing
   end-of-document check. Doesn't require transcript text, so it's testable
   in this same offline harness, but based on the cached artifacts examined
   during this fix (jobs 1, 17, 18, 19, 21 all close with intact, punctuated
   turns even where content is genuinely missing before them) it's unclear
   this pattern actually occurs in practice either -- worth a quick audit
   before investing more implementation time.

Neither the stale-title-limit misses (bucket B) nor the review-notes-
placement policy question (bucket C) require lint changes, unchanged from
Run 1. `routing.style_engine.qa_gate.merge_flags` stays off pending a study
run that actually clears the truncation gap -- likely requiring the
word-count approach above, run against a version of this study wired to
real transcript text (a scope change beyond what this offline,
REST-API-only harness can do today).

### In-pipeline truncation coverage

Follow-up item 1 above turned out to already exist in production, just not
in a form `run_lint` consumed. `api/services/completeness.py`'s
`check_completeness()` and `api/services/seam_coverage.py`'s
`find_dropped_spans()` both run after every real formatter phase and stash
their verdicts on the job's `context` bus (`completeness_check`,
`seam_coverage`) -- unlike this offline study, the live worker has the
actual source transcript in hand, so it can compute the exact
word-count-vs-source and content-anchored seam-drop signals this study
identified as the dominant gap. `run_lint` now reads those two dict results
and surfaces them as `lint.formatter.truncation_suspect` (completeness) and
the new `lint.formatter.seam_gap` (seam) violations when the gate already
flagged a problem, so the merged QA verdict carries the deterministic
pipeline's own truncation finding rather than leaving lint blind to it. No
new detection logic was written -- this is pure consumption of an
already-computed result. Practically, this means the 8 `llm_only_deterministic`
truncation misses counted in Run 2's numbers above were an artifact of this
study's offline harness (no transcript-fetch endpoint, see Limitations)
rather than evidence that production lint itself misses truncation --
production lint now inherits the completeness/seam gates' verdicts
directly. This does not change the Run 1/Run 2 numbers recorded above,
which remain the offline-harness measurement as run; a future study
iteration wired to real transcript text (per Limitations, below) would be
needed to re-measure recall with this consumption path included.

### Limitations of this study (updated for Run 2)

- **Small, PBS-Wisconsin-specific sample** -- unchanged from Run 1 (21 jobs,
  two dominant programs); `output_missing`, `placeholder_text`,
  `content_past_duration`, and the SEO title-limit check's true-positive
  path remain untested here.
- **Keyword classifier is a blunt instrument** -- Run 1 caught 2 false hits
  by hand; this re-run surfaces a 3rd (job 17's speaker_label_format flag,
  reclassified above) that Run 1's initial pass didn't catch, discovered
  only by actually building and testing the fix it originally recommended.
  This is itself a small piece of evidence that the original study's
  bucket-A count may have had further, undiscovered classifier-ambiguity
  items beyond the two it flagged -- a caution for reading any single-pass
  hand analysis as final.
- **This offline study cannot exercise transcript-text-dependent checks** --
  confirmed concretely this run: the most promising fix for the dominant
  truncation gap (word-count coverage vs. source transcript, mirroring
  `api/services/completeness.py`) is unverifiable through this REST-API-only
  harness, because the harness has no transcript-fetch endpoint even though
  production's real worker context does carry transcript text. Any future
  study iteration targeting the truncation gap needs either a transcript-
  fetch endpoint added to the read-only REST surface, or a different
  offline-reproducible harness (e.g. running against a local copy of the
  transcripts directory rather than the production API).
- **The validator's own checklist is demonstrably stale** -- unchanged
  finding from Run 1 (60/160 char limits in `prompts/validator.md` vs.
  80/90/350 in `config/house_style.yaml`).
- **A code fix that measures zero movement on a fixed sample is still worth
  shipping** -- both fixes in this task close real, TDD-verified defects
  (confirmed independently of this study's aggregate numbers) and the
  HTML-comment-exclusion correction specifically prevents a genuine false
  positive (job 15) that the uncorrected version of this same commit
  produced. Recall movement on any single 21-job sample is a noisy signal
  for code correctness in either direction -- this run is a reminder to
  read the per-item root-cause analysis, not just the top-line percentage.

---

## Appendix: Run 1 (2026-07-10T22:10Z) -- pre-fix history

Preserved verbatim below for audit continuity -- this is Run 1's original
report, produced before either task-2c2 fix landed. Do not edit; superseded
by the sections above but kept complete so no history is lost.

### Run 1 aggregate matrix

| Cell | Count |
|---|---|
| both_caught | 25 |
| lint_only | 34 |
| llm_only_deterministic | 20 |
| llm_semantic (out of lint scope) | 62 |

By phase:

| Phase | both_caught | lint_only | llm_only_deterministic | llm_semantic |
|---|---|---|---|---|
| analyst | 0 | 0 | 1 | 3 |
| formatter | 15 | 8 | 11 | 31 |
| seo | 10 | 26 | 8 | 28 |

By deterministic category:

| Category | both_caught | llm_only_deterministic |
|---|---|---|
| char_limit | 10 | 5 |
| review_notes | 15 | 4 |
| speaker_label_format | 0 | 1 |
| truncation | 0 | 10 |

### Run 1 per-job matrix

| Job | Status | Content type | Duration (min) | Validation result | both_caught | lint_only | llm_only_det | llm_semantic |
|---|---|---|---|---|---|---|---|---|
| 1 | completed | full | 32.9 | present | 1 | 2 | 1 | 8 |
| 2 | completed | full | 1.8 | present | 3 | 1 | 0 | 4 |
| 3 | completed | short | 1.9 | present | 1 | 2 | 2 | 0 |
| 4 | completed | short | 1.6 | present | 1 | 3 | 0 | 1 |
| 5 | completed | short | 1.6 | present | 2 | 1 | 1 | 1 |
| 6 | completed | short | 1.8 | present | 0 | 4 | 0 | 0 |
| 7 | completed | full | 33.0 | present | 0 | 1 | 3 | 6 |
| 8 | completed | full | 32.3 | present | 1 | 2 | 2 | 1 |
| 9 | completed | full | 47.0 | present | 1 | 3 | 0 | 4 |
| 10 | completed | full | 32.5 | present | 2 | 0 | 2 | 3 |
| 11 | completed | full | 32.5 | present | 1 | 1 | 0 | 0 |
| 12 | completed | full | 18.3 | present | 1 | 2 | 1 | 3 |
| 13 | paused | full | 4.0 | present | 1 | 1 | 0 | 1 |
| 14 | completed | full | 4.0 | present | 0 | 1 | 0 | 0 |
| 15 | paused | short | 1.3 | present | 2 | 2 | 2 | 10 |
| 16 | paused | short | 1.0 | present | 2 | 2 | 0 | 7 |
| 17 | paused | full | 2.8 | present | 1 | 1 | 3 | 3 |
| 18 | paused | full | 2.8 | present | 1 | 2 | 1 | 0 |
| 19 | paused | full | 2.8 | present | 1 | 1 | 1 | 5 |
| 20 | completed | full | 18.5 | present | 0 | 2 | 0 | 0 |
| 21 | paused | full | 18.1 | present | 3 | 0 | 1 | 5 |

### Run 1 llm_only_deterministic -- every miss, verbatim, with analysis

Twenty flags. Read against the real cached artifacts (`OUTPUT/eval/prod_artifacts/`),
they fall into three buckets: **(A) real lint gaps** -- lint should have
caught these and didn't; **(B) stale-limit mismatches** -- the LLM validator
is checking against numbers `prompts/validator.md` still quotes that
`config/house_style.yaml` has since corrected, so lint's silence is *correct*,
not a miss; **(C) policy/scope disagreements** -- lint is doing exactly what
it's configured to do, and the disagreement is really about what the
checklist *should* require, not a code defect.

**Truncation (10 items -- 8 bucket A sharing one root cause, 1 bucket C cross-phase meta-commentary, 1 keyword-map false hit)**

- Job 1 / formatter: *"Transcript ends abruptly at 00:10:52 with incomplete speaker attribution and content that appears truncated mid-episode"*
- Job 8 / formatter: *"transcript ends abruptly mid-sentence at approximately 10:43 (Rich Kremer tuition freeze explanation); content documented in analyst output but missing from formatted transcript"*
- Job 8 / formatter: *"content truncation: UW tuition backstory continuation, Tom Tiffany tuition freeze discussion, conservative think tank analysis, Democratic framing, PFAS settlement section (Act 4), and closing remarks (Act 5) are missing from formatted output"*
- Job 12 / formatter: *"truncation evident: formatted transcript status field states 'needs_review' and explicitly notes incomplete coverage"*
- Job 17 / formatter: *"transcript ends mid-sentence at 1:15 mark — content appears truncated"*
- Job 18 / formatter: *"transcript ends mid-sentence and appears truncated"*
- Job 19 / formatter: *"Content appears truncated or incomplete - transcript ends abruptly without clear conclusion marker"*
- Job 21 / formatter: *"Mid-sentence cutoff at end ('targeted Republicans in') preserved verbatim without resolution indicator"*

  **Bucket A -- real lint gap.** Checked the actual cached `formatter_output.md`
  for every one of these jobs: in every case, the last substantive line of
  the document (before the `**Status:**` footer) ends with complete terminal
  punctuation -- e.g. job 1 ends "...or wherever you get your podcasts.",
  jobs 17/18/19 end "...was a place of honor as well.", job 21 ends
  "...or wherever you get your podcasts." `lint.formatter.truncation_suspect`
  only asks one question -- does the *last visible line* lack terminal
  punctuation? -- and every one of these transcripts happens to close with a
  clean, complete-sentence sign-off paragraph even though the *content*
  stops well short of the full episode (job 8's own flag names five
  missing acts; job 21's flag describes a mid-document caption dropout
  that a *later* speaker turn recovers from and then closes normally). The
  check has zero mechanism for "coverage ends early relative to duration" or
  "a mid-document speaker turn trails off with no terminal punctuation before
  the next turn begins" -- only for "the very last line lacks a period."
  Confirmed empirically: `lint.formatter.truncation_suspect` fired **zero
  times** across all 21 jobs in this sample (see the by-category table --
  0 both_caught for `truncation`), despite the LLM validator raising a
  truncation-shaped complaint in 7 of the 15 `full`-content-type jobs (8
  flags total -- job 8 raised two). **Follow-up:** file
  an issue to redesign or supplement this check -- e.g., compare the last
  parsed timecode/word-count coverage against `duration_minutes` (a
  completeness check, distinct from the existing punctuation check), and
  separately scan speaker turns for a turn that ends without terminal
  punctuation immediately followed by a new `**Name:**` header (mid-document
  cutoff, not just end-of-document).

- Job 7 / seo: *"Short description uses ellipsis ('...') which may be truncated in search previews; should end with complete phrase"*

  **Keyword-map false hit, not really about content truncation at all.**
  This is an SEO-copy stylistic complaint (ellipsis risks getting cut off in
  a search snippet), not a claim that the *document itself* was cut short.
  It tripped the `truncat` substring in the classifier. Recorded here for
  honesty about the classifier's limits; not counted as evidence either for
  or against the truncation_suspect check.

- Job 17 / seo: *"transcript truncation issue not flagged in SEO report — full content verification needed before metadata finalization"*

  **Bucket C -- cross-phase meta-commentary, not a measurable SEO-output
  defect.** The validator is criticizing the SEO agent for not *noticing* a
  formatter-phase truncation issue, which isn't something `lint.run_lint`'s
  seo-phase checks are designed to judge (they inspect the SEO document's own
  fields, not whether it cross-references known issues in a sibling phase).
  Not actionable as a lint change.

**char_limit / title_over_limit (5 items, all bucket B)**

- Job 3 / seo: *"Primary title recommendation exceeds 40-character limit by 9 characters (49 chars); violates stated constraint"*
- Job 5 / seo: *"title exceeds 60 character limit at 39 characters but includes #Shorts hashtag which is non-standard for SEO title fields"* (LLM's flagged count 39 vs. extracted title 46 chars — consistent with hashtag presence/absence variance; illustrates unreliable LLM char-counting noted in lint_only spot-check above)
- Job 7 / seo: *"Recommended title exceeds 60 characters (68 characters provided; limit is 60)"*
- Job 10 / seo: *"Recommended title is 66 characters, exceeding the 60-character limit specified in the validation checklist"*
- Job 15 / seo: *"Title length conflict — recommended title (44 chars) exceeds stated user hard cap of 40 characters"*

  **Bucket B -- stale validator checklist, not a lint gap.** Extracted the
  actual `**Recommended:**` title value from each cached `seo_output.md`:
  job 3 = 50 chars, job 5 = 46 chars, job 7 = 64 chars, job 10 = 72 chars, job
  15 = 46 chars. Every single one is under `config/house_style.yaml`'s
  `limits.fields.title.max: 80` -- the actual SST-write-enforced limit
  (confirmed against `mcp_server/server.py`'s `WRITABLE_FIELDS`, which lists
  `("Release Title", ..., 80)` since commit c6278b7, 2026-04-16). `prompts/validator.md`'s
  checklist still says *"Title is under 60 characters"* -- a line unchanged
  since at least commit 4dbb051 (2026-05-21), now stale relative to the
  long-standing 80-character SST limit. The LLM validator is enforcing a
  retired number. This is **evidence for lint**, not a gap: lint reads the
  limit from the single source of truth at call time, so it can never drift
  the way a hard-coded number baked into a prompt string does. No lint
  follow-up needed here; the follow-up (if any) is updating `prompts/validator.md`'s
  own checklist text (60/160→80/90) so the LLM validator agrees with the
  ground truth too, or retiring that checklist item once lint supersedes it.

**review_notes (4 items -- 2 bucket C policy disagreement, 1 bucket C
classifier ambiguity, 1 bucket A minor scope gap)**

- Job 7 / formatter: *"Review notes embedded in transcript body (lines marked <!-- REVIEW NOTES: ... -->)"*
- Job 10 / formatter: *"Review notes block (HTML comment) appears in the transcript body between the header and content — editorial metadata must not appear in the formatted transcript body"*

  **Bucket C -- policy disagreement, not a lint bug.** Checked both cached
  `formatter_output.md` files: in both, the `<!-- REVIEW NOTES: ... -->`
  block sits immediately after the document header and *before* the first
  `---` horizontal rule -- i.e., exactly at the top, which is what
  `config/house_style.yaml`'s `phases.formatter.review_notes: {placement:
  top}` calls compliant. `lint.formatter.review_notes_in_body` only flags a
  marker found *after* the first horizontal rule (misplaced), so it
  correctly stays silent here. `prompts/validator.md`'s checklist says a
  blanket *"No review notes, agent instructions, or metadata appear in the
  transcript body"* with no placement carve-out. That's a genuine policy gap
  between the validator's prompt and the house-style config lint enforces --
  worth a product decision (does PBS Wisconsin actually want top-placed
  review notes tolerated, as house_style.yaml currently says, or prohibited
  outright, as validator.md currently says?), not a lint code change.

- Job 3 / seo: *"SEO report contains internal review notes and metadata revision process that distract from final recommendations"*

  **Bucket C -- classifier ambiguity, arguably not the same concept at all.**
  `job3/seo_output.md` has no HTML-comment review-note block; this flag is
  criticizing the report's own designed template sections (`**Reasoning:**`,
  `## Next Steps`, alternate-version callouts) that every studied SEO report
  uses, including the ones the validator passed without complaint (e.g. job
  1). `lint.run_lint` also never runs `review_notes_in_body` against the
  `seo` phase at all (it's formatter-only by design -- see `lint.py`'s
  `elif phase == "formatter"` branch). Low-confidence miss; more likely a
  keyword-map false positive on "review notes" than a like-for-like check
  gap.

- Job 15 / analyst: *"Review notes and production blockers embedded in output — should be separate from analysis"*

  **Bucket A -- minor, real scope gap.** `job15/analyst_output.md` has no
  `<!-- REVIEW NOTES -->` HTML comment; it uses a different convention
  entirely -- blockquote callouts (`> ⚠️ **Production Note — Missing Media
  ID:** ...`, `> ⚠️ **Review Item — Speaker Identity Unknown:** ...`) plus a
  `## Production Notes` section. Two independent reasons lint doesn't catch
  it: `review_notes_in_body` never runs on the `analyst` phase at all
  (formatter-only), and even if it did, its marker regex
  (`<!--\s*review|NEEDS_REVIEW|^##\s*Review Notes`) doesn't match `##
  Production Notes` or a `>` blockquote callout. Low-priority follow-up --
  the analyst phase apparently has its own (currently unencoded) review-flag
  convention that would need its own detection pattern if this phase is ever
  brought into scope.

**speaker_label_format (1 item, bucket A -- real, confirmed lint gap)**

- Job 17 / formatter: *"speaker names not identified (generic 'Speaker 1/2' labels used throughout)"*

  **Bucket A -- confirmed real gap.** `job17/formatter_output.md` uses
  `**Speaker 1:**` / `**Speaker 2:**` labels. Ran
  `lint._LOOSE_SPEAKER_LABEL_RE` directly against the cached file to check:
  it does **not** match either label at all -- the regex's second-token
  continuation group requires an uppercase *letter* (`[A-Z]`), and `1`/`2`
  are digits, so the whole anchored pattern fails to match starting at
  `**Speaker`. These labels never even enter the candidate pool, so the
  existing single-word check (which *would* correctly flag a bare `Speaker`)
  never gets a chance to run on them. **Follow-up:** extend
  `_LOOSE_SPEAKER_LABEL_RE` (or the single-word check specifically) to also
  collect numeric-suffixed generic labels like `Speaker 1`/`Speaker 2` --
  currently a blind spot for exactly the placeholder-identity pattern the
  check exists to catch.

  > **Run 2 update:** this fix was made and independently verified correct
  > (see the main report above), but the underlying LLM flag turned out not
  > to close as a result -- reclassified to bucket C in Run 2. Left
  > unedited here for historical accuracy; see the main report for the
  > corrected analysis.

### Run 1 lint_only spot-check (5 picks)

Hand-picked for variety rather than document order -- covers a clear
severe true positive, a marginal true positive with an interesting
self-report angle, a config/prompt-drift true positive, and a likely false
positive.

1. **Job 1 / seo** -- `lint.seo.short_over_limit` (error)
   > "short_description is 164 chars (limit 90)"
   - **Judgment: true positive, and a good illustration of why lint beats
     both self-reported and LLM-validator character counts.** The SEO
     report itself claims `**Character Count:** 147/150` for this field --
     already wrong (the real string is 164 chars, not 147) and measured
     against the SEO prompt's own stale 150-char target, not the 90-char
     house-style limit. The LLM validator's `validation_result` for this job
     didn't flag the short description at all -- it apparently trusted the
     agent's self-reported 147 rather than counting. Lint's `len()` over the
     actual extracted field value is correct by construction; neither LLM in
     this pipeline counted correctly.

2. **Job 1 / seo** -- `lint.seo.long_over_limit` (error)
   > "long_description is 360 chars (limit 350)"
   - **Judgment: true positive, marginal (2.9% over), same self-report
     problem.** The report claims `**Character Count:** 298/300`; the real
     value is 360. The validator's own flag about this field ("contains
     redundant elements... within 298 chars") also cites the wrong
     (self-reported) count. Genuinely over the real limit, just not by much
     -- a good marginal case, not a blowout.

3. **Job 9 / seo** -- `lint.seo.long_over_limit` (error)
   > "long_description is 541 chars (limit 350)"
   - **Judgment: unambiguous true positive.** 541 vs. a 350-char limit is
     55% over -- and even against the *old*, stale 300-char number
     `prompts/validator.md` still quotes, this is 80% over. Not a
     drift-driven near-miss; a real, substantial quality problem the LLM
     validator's `validation_result` for job 9 never mentioned at all (its
     only flags were about the formatter phase).

4. **Job 2 / formatter** -- `lint.formatter.speaker_label_inconsistent` (warning)
   > `Speaker label "Narrator" is a single word (expected first + last name)`
   - **Judgment: likely false positive -- a stoplist gap, not a real
     name-formatting defect.** `job2/formatter_output.md` is a single
     voice-over segment with no on-camera or named speaker (the formatter's
     own review notes say so explicitly: *"No speaker/expert identified:
     Segment uses a single narrator voice-over; no on-screen or identified
     speaker"*). "Narrator:" is a legitimate, intentional documentary-style
     label here, not a malformed real name -- the "(expected first + last
     name)" message doesn't fit the actual situation. `lint.py` already
     stoplists non-name field labels like `note`/`status`/`warning`
     (`_FIELD_LABEL_STOPLIST`) for exactly this kind of shape collision;
     `Narrator` (and `job18`'s analogous `Interviewer` single-word flag,
     also in this sample's lint_only set) look like good stoplist
     additions, or a documented convention exception, before lint becomes
     authoritative.

5. **Job 4 / seo** -- `lint.seo.keywords_count` (warning)
   > "keywords has 20 items (expected 5-10)"
   - **Judgment: true positive per current config, but flags the same
     prompt/config sync gap as the char-limit findings above, not a distinct
     quality problem.** Job 4 is `content_type: short`, so
     `limits.content_type_overrides.short.keywords` (5-10) applies instead
     of the full-content default (15-20). The SEO output's own `### YouTube
     Tags (15-20 recommended)` template heading never differentiates by
     content type, so the agent produced a within-template-spec 20-item list
     that's correctly flagged as over the *short*-specific limit. Real
     signal per the config as written; the SEO prompt template just hasn't
     been updated to tell short-form jobs to target 5-10 instead of 15-20 --
     same underlying prompt/config synchronization gap as the title/
     description limits, applied to keyword count instead of characters.

### Run 1 conclusion (original text, preserved)

**Raw recall against the Stage-2 acceptance criterion ("lint catches ≥100%
of LLM-caught deterministic-category failures") is 25/45 = 55.6% in this
21-job sample -- the suite does not yet meet the bar, and should stay off
until it does.** But the 20 misses are not evenly weighted evidence against
lint. Bucketing all 20 by the per-item analysis above:

| Bucket | Count | What it means |
|---|---|---|
| A -- real lint code gap | 10 | 8 truncation (one root cause), 1 speaker-label regex blind spot, 1 analyst-phase review-note scope gap |
| B -- stale validator checklist, lint is correct | 5 | all 5 are title_over_limit flags against a retired 60-char limit; every actual title is under the real 80-char limit |
| C -- policy question / classifier ambiguity, not a code defect | 5 | 2 review-notes placement-policy disagreements, 1 review-notes classifier ambiguity (SEO template structure), 1 cross-phase meta-commentary, 1 keyword-map false hit on "truncated" (an SEO ellipsis note) |

Only **bucket A (10 of 20)** represents lint actually missing something it
was designed to catch, and 8 of those 10 are a single root cause: the
formatter-only truncation check tests *only* whether the document's last
visible line lacks terminal punctuation, and it fired **zero times** across
all 21 jobs in this sample -- even against transcripts the LLM validator
correctly identified as missing whole acts of content, because every one of
those transcripts happens to close with a complete, punctuated sign-off
paragraph regardless of how much content is actually missing before it.
Bucket B (5 items) is not lint failing -- it's the LLM validator enforcing
a `prompts/validator.md` checklist number (60 chars) that
`config/house_style.yaml` corrected to 80 earlier the same day these jobs
ran; lint's single-source-of-truth design means it can't drift the way a
number hard-coded into a prompt string does. Bucket C (5 items) is either a
genuine, undecided policy question (does PBS Wisconsin want top-placed
review notes tolerated, as `house_style.yaml` says, or banned outright, as
`validator.md`'s checklist says?) or a limit of the keyword-based
classifier this study uses, not a lint code defect.

The lint_only side reinforces the same story from the other direction: lint
found 34 violations the LLM validator missed entirely, heavily concentrated
in `short_description` (over the 90-char limit in nearly every job) and
`long_description` overages -- largely because the SEO-writer prompt itself
still targets the old ~150/300-char numbers, so the LLM validator, also
checking against old numbers, agreed with the writer instead of catching
the drift. The two spot-checked severe cases (job 9 at 541 chars, job 1
with a self-reported count that didn't even match its own text) show lint's
exact `len()` count catching real quality problems no LLM in the current
pipeline caught. The one clear lint_only false-positive pattern found
(`Narrator`/`Interviewer` single-word labels for legitimate no-named-speaker
segments) is a small, easily-fixed stoplist gap, not a design problem.

**Recommendation:** before flipping `routing.style_engine.qa_gate` on, do
two things: (1) fix or supplement `lint.formatter.truncation_suspect` -- it
is the study's single dominant gap by a wide margin (8 of 10 bucket-A
misses) and had a measured 0% hit rate on real production transcripts
across this entire sample; a content-coverage check (parsed timecode/word
coverage vs. `duration_minutes`) is a plausible next design, complementary
to the existing last-line-punctuation check rather than a replacement for
it. (2) extend the speaker-label collection regex to catch numeric-suffixed
generic labels (`Speaker 1`/`Speaker 2`), and add `Narrator`/`Interviewer`/
similar convention words to the field-label stoplist (also motivated by the
lint_only false-positive spot-checked above). Neither the stale-title-limit
misses (bucket B) nor the review-notes-placement policy question (bucket C)
require lint changes -- they're evidence the *prompts*
(`prompts/validator.md`, `prompts/seo.md`) haven't caught up to
`config/house_style.yaml`'s corrected numbers and documented placement
policy, which is itself a point in favor of moving enforcement into lint
rather than re-syncing three separate prompt files by hand every time a
limit changes.

If bucket B and C items are set aside as "lint was correctly silent" rather
than misses, the adjusted picture is both_caught (25) + bucket A (10) = 35
deterministic-category failures lint *should* catch once (1) and (2) land,
against which today's 25 is 71.4% recall -- not 55.6%. That is still short
of the ≥100% acceptance criterion, concentrated almost entirely in one
check. Once (1) and (2) are fixed, this study should be **re-run** (not
re-projected) to confirm the acceptance criterion is actually met before
`routing.style_engine.qa_gate` is switched on.

> **Run 2 update:** (1) and (2) were fixed and independently TDD-verified
> (see the main report above), and this study was re-run as specified
> rather than re-projected. The re-run shows the acceptance criterion is
> **not yet met** -- raw recall is unchanged at 55.6%, because this
> specific sample's failure modes don't intersect either fix's applicable
> surface (see "What changed since Run 1" above for the full diagnosis and
> updated follow-up recommendation).

### Run 1 limitations of this study (original text, preserved)

- **Small, PBS-Wisconsin-specific sample.** 21 jobs, dominated by two
  programs (`Inside Wisconsin Politics`, several `Digital Shorts`/education
  clips). `output_missing`, `placeholder_text`, `content_past_duration`, and
  the SEO title-limit check's *true-positive* path never got exercised here
  (no job had a missing output, a literal placeholder token, a timecode past
  duration, or a title that actually exceeded 80 chars) -- their absence
  from the matrix reflects absence of test cases, not proven correctness.
- **Keyword classifier is a blunt instrument.** Two false hits were caught
  by hand in this study (an SEO "ellipsis truncation" note, an SEO
  report-structure complaint) and corrected in the analysis above rather
  than the code -- a larger sample would need either a better classifier or
  more manual review time to stay this careful.
- **The validator's own checklist is demonstrably stale** (confirmed: 60/160
  char limits in `prompts/validator.md` vs. 80/90/350 in
  `config/house_style.yaml` and `mcp_server/server.py`'s `WRITABLE_FIELDS`,
  which has enforced the 80-char title limit since commit c6278b7, 2026-04-16,
  while validator.md's 60-char line dates to at least 4dbb051, 2026-05-21) -- so this
  study is partly measuring "does lint agree with an already-known-outdated
  LLM checklist," not just "does lint agree with correct human intent."
  That's exactly the trust-building question Stage 2 is meant to answer, but
  it means the raw 55.6% number understates lint's real-world readiness more
  than the bucket-corrected read above suggests, and overstates it less than
  a naive "lint agrees with the LLM" framing would.
