# Stage 2 -- Lint agreement study over production jobs

Generated 2026-07-10T22:10:10.663249+00:00 against `http://cardigan01:8100` (read-only GETs, 0.2s
between requests). 21 jobs studied, 21 with a stored
`validation_result`. House style rules: `config/house_style.yaml`.

Produced by `python -m scripts.lint_agreement_study --jobs all` (script + tests
committed alongside this report). Raw per-job/per-flag data lives in
`agreement.json` next to this file. Downloaded artifacts (job records, phase
outputs) are cached under `OUTPUT/eval/prod_artifacts/` -- git-ignored, not
committed; only short quoted excerpts appear below.

## Methodology

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

All 21 non-pending jobs in the production queue at run time were studied (14
`completed`, 7 `paused` -- the paused jobs had already run through
formatter/seo/validator before being paused, so all three phase outputs and a
`validation_result` existed for every one of them; none were excluded).

### Graceful degradation (no transcript text)

The REST API has no transcript-fetch endpoint, so raw transcript text is
never available to this study -- only `transcript_file` (the filename) is
passed through in context, unused. This turns out not to matter for any
currently-implemented lint check: `run_lint` reads `analyst_output`,
`formatter_output`, `seo_output`, `duration_minutes`, `content_type`, and
`program` from its context -- never `transcript` -- so nothing degrades.
`program` is also never populated here (not present on the Job record /
queue payload); `StyleRules.limits_for()`'s `program` argument is a
documented no-op today, so this has no effect on the limits actually
applied.

### Deterministic-category keyword map

Each LLM validator flag string is classified as zero-or-more deterministic
categories (substring match, case-insensitive) or SEMANTIC (no category
matched -- content-accuracy/relevance/quality judgment, out of lint's
scope by design):

| Category | Keywords (substring, case-insensitive) | Excludes | Corresponding lint rule_id |
|---|---|---|---|
| output_missing | `output missing`, `output is missing`, `missing or empty`, `empty output`, `missing or has fewer than` | -- | `lint.output_missing` |
| placeholder_text | `placeholder text`, `template artifact`, `{media_id}`, `[insert`, `{today`, `{model name`, `unfilled placeholder` | -- | `lint.placeholder_text` |
| review_notes | `review note`, `review notes`, `html comment`, `editorial instructions`, `appear in transcript body`, `appear in the transcript body`, `embedded in transcript body`, `embedded in the transcript body`, `editorial metadata must not appear`, `agent instructions` | -- | `lint.formatter.review_notes_in_body` |
| speaker_label_format | `single-word`, `single word label`, `labeled inconsistently`, `generic label`, `generic 'speaker`, `generic "speaker`, `honorific`, `speaker label` | `misattribut`, `attribut`, `unclear`, `ambigu`, `unverified`, `unconfirmed`, `unresolved`, `inverted` | `lint.formatter.speaker_label_inconsistent` |
| content_past_duration | `past the episode`, `past the content duration`, `exceeds the content duration`, `beyond the episode duration`, `content past duration`, `after the episode ends`, `past the video duration` | -- | `lint.formatter.content_past_duration` |
| truncation | `truncat`, `ends abruptly`, `mid-sentence`, `cut off`, `cuts off`, `abrupt end`, `ends mid-sentence`, `cutoff`, `missing from the formatted`, `content is missing from` | -- | `lint.formatter.truncation_suspect` |
| keyword_count | `keyword count`, `keywords recommended`, `tags recommended`, `expected 15-20`, `expected 5-10`, `too few keywords`, `too many keywords` | -- | `lint.seo.keywords_count` |
| char_limit | requires BOTH a `char`/`character(s)` mention AND an `exceed*`/`over`/`too long` violation word | -- | field-detected: `short description` -> `lint.seo.short_over_limit`, `long description` -> `lint.seo.long_over_limit`, `title` -> `lint.seo.title_over_limit`, none of those names -> no rule_id (always lands in llm_only_deterministic) |

The `speaker_label_format` category explicitly EXCLUDES flags that also
mention attribution/ambiguity/unresolved-identity language, because those
are judgments about whether a specific line of dialogue was assigned to the
right speaker (semantic -- requires understanding transcript content) as
opposed to judgments about label FORMAT (single-word label, honorific,
same person spelled two inconsistent ways) which is what
`lint.formatter.speaker_label_inconsistent` actually checks.

This is a plain-substring keyword map, not an NLP classifier, and it is not
perfect -- two of its misclassifications are called out explicitly in the
per-item analysis below (a "truncated in search previews" SEO stylistic note
that trips the `truncation` keyword, and an SEO-report-structure complaint
that trips `review_notes`). The per-item human read below is what corrects
for that, which is the point of doing it by hand rather than trusting the
keyword map's output verbatim.

### Matrix cells

- **both_caught** -- a deterministic-category LLM flag whose rule_id family
  has a corresponding lint violation on the same phase.
- **lint_only** -- a lint violation with no corresponding deterministic LLM
  flag.
- **llm_only_deterministic** -- a deterministic-category LLM flag lint
  missed. The critical cell for the Stage-2 acceptance criterion.
- **llm_semantic** -- LLM flags outside lint's scope, listed for context
  only (not part of the acceptance criterion).

Correspondence is at rule_id-family granularity (one deterministic LLM flag
claims at most one still-unclaimed lint violation of a matching rule_id per
phase), not exact text matching -- see `compare_phase()`'s docstring in the
script.

## Aggregate matrix

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

`output_missing`, `placeholder_text`, `content_past_duration`, and
`keyword_count` all show zero in both the both_caught and
llm_only_deterministic columns -- no LLM flag in this 21-job sample was ever
classified into any of those four categories (no job had a missing phase
output or a literal placeholder token flagged; no LLM flag ever complained
about a timecode past duration or an out-of-range keyword count). That's an
absence of test coverage for those specific categories in this sample, not a
claim they work -- flagged as a limitation below. (Lint itself *did* still
independently find a real `lint.seo.keywords_count` violation on job 4, via
the lint_only side -- see the spot-check -- just with no matching LLM flag
to classify against.)

## Per-job matrix

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

## llm_only_deterministic -- every miss, verbatim, with analysis

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

## lint_only spot-check (5 picks)

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

## Conclusion

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

### Limitations of this study

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
