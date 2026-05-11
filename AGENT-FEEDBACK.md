# Agent Feedback Log

Rolling log of issues, regressions, and ideas surfaced during day-to-day Cardigan use. Append new entries; don't rewrite old ones — the timeline matters for tracking whether issues are recurring or resolved.

Each entry: a short headline, the date observed, evidence (job ID, file path, screenshot, etc.), and a one-line recommendation. Severity tags: 🔴 critical (wrong output), 🟡 quality (degrades trust), 🟢 nice-to-have.

---

## 2026-05-07

### 🔴 Duration tracking is broken across three subsystems

**Where:** UI Processing Phases panel (per screenshot), `manifest.json` `tier_reason`, SEO output, manager output.

**What's wrong:** For 6POL0108, five reported durations disagree, and none match the actual SRT:
- SRT runtime (last cue): **00:18:34**
- `tier_reason` in routing: `duration 33min` (used to pick tier-1 standard)
- SEO agent's claim: `38:30`
- Manager output's claim: `32.5-minute duration`
- Analyst's structural breakdown extends to `35:35–38:30` for the closing segment
- `manifest.json.duration_minutes`: **null**

**Why it matters:** The 33-min figure is what the routing logic uses to decide model tier (`api/services/llm.py:511`). Looking at `_get_content_duration_minutes` in `worker.py:1143`, the system *can* parse SRT timestamps for accurate duration — but `llm.py` reads `transcript_metrics["estimated_duration_minutes"]` directly (probably word-count-derived), bypassing the SRT parser. So routing is making decisions on an estimate that's ~2× the true runtime, and downstream agents are inheriting/inventing different numbers.

**Recommendation:** Have the routing tier resolver call `_get_content_duration_minutes` (SRT-first, estimate-fallback) instead of reading the estimate directly. Persist the resolved value to `manifest.json.duration_minutes` so downstream agents stop hallucinating.

**Evidence:** `/data/output/6POL0108/manifest.json`, `/data/output/6POL0108/seo_output.md`, `/data/output/6POL0108/manager_output.md`, `/tmp/cardigan-compare/6POL0108.srt`

---

### 🔴 Timestamp phase only sees the first ~9 minutes of every episode

**Where:** `api/services/worker.py:2581`.

**The line:**
```python
{srt_content[:15000]}{"..." if len(srt_content) > 15000 else ""}
```

**What's wrong:** The SRT content is hard-truncated to the first 15,000 characters before being sent to the timestamp agent. For 6POL0108, that's the first 15k of 33,928 bytes — covering up to **00:08:09** of an 18:34 episode. The timestamp agent's note even reports this accurately: *"The SRT file provided covers only the first ~8.5 minutes of a ~32.5-minute episode."* It's not hallucinating — it's correctly describing what it was given.

This explains the user-observed pattern: *"didn't seem to read the entire file ... did the same thing as last week."* It's deterministic — happens for **every** episode whose SRT exceeds 15k chars (≈9 min of dialogue).

**Downstream effect:** Timestamp agent invents chapter boundaries for the unseen 60% of the episode based on the analyst summary, then flags them with "should be verified against the full video before publishing." For 6POL0108 the table includes `Campaign finance and court recusal | 0:26:00 | 0:32:30` — but the actual episode ends at 18:34, so those timestamps point past the end of the video.

**Recommendation:** Drop the truncation entirely or raise it to a safety limit that won't trip in normal use (e.g., 500k chars ≈ 5 hours). Full SRT for 6POL0108 is ~8k tokens — fits comfortably in any current model's context. This was leftover defensive code from a tighter-context era; it's now actively wrong.

**Coupled with duration fix:** Lifting the truncation alone wasn't enough — the agent kept claiming "SRT covers only first ~8.5 min" because the prompt also told it `**Estimated Duration:** 32.5 minutes` (the broken word-count figure). The agent saw 18 min of SRT and reasonably concluded the SRT must be partial. Tested locally with both fixes:

| | Original | After both fixes |
|---|---|---|
| Duration in prompt | 32.5 min (estimate) | **18.6 min** (parsed from SRT) |
| Output `**Duration:**` | ~32:30 | **18:38** |
| Final chapter ends | 0:32:30 (past video end) | 0:18:38 (matches SRT) |
| Chapter source | 4/5 invented | All anchored to real SRT cues |

The duration fix is local to the timestamp prompt builder (`worker.py:2562-2580`) — parses `srt_content` with `parse_srt` + `get_srt_duration` and falls back to estimate. Doesn't touch routing logic (the bigger 🔴 fix), so safe to ship in this PR alongside the truncation lift.

---

### 🟡 Hardcoded prompt truncations across multiple phases

**Where:** `api/services/worker.py` — at least four sites.

| Line | Slice | Effect |
|---|---|---|
| 1871 | `output[:800]` | Recovery/diagnosis context (probably fine) |
| 2437 | `formatted[:2000]` | SEO/copy-edit phase sees only first 2k chars of formatted transcript |
| 2532 | `transcript[:3000]` | Phase prompt sees only first 3k of raw transcript |
| 2581 | `srt_content[:15000]` | Timestamp phase — the 🔴 issue above |
| 2586 | `analysis[:4000]` | Timestamp's view of analyst output |

**Why it matters together:** The `formatted[:2000]` truncation likely explains the SEO agent's recurring "transcript is only 30% complete" complaint — it's looking at 2000 chars of a 30k-char formatted transcript and concluding the episode is incomplete. The pattern is the same across phases: defensive truncation from when models had small context windows, now causing systemic content-blindness.

**Recommendation:** Audit all `[:NNNN]` slices that feed prompt content. For each: either remove (modern context windows are huge), raise to a safety ceiling (e.g., 500k chars), or make it tier-dependent (cheapskate tier truncates, premium passes full content). Probably one focused PR.

---

### 🟡 Ingest scan cadence is too low; UI feels stale

**Where:** `/api/ingest/config`, `api/services/ingest_scheduler.py`.

**What's happening:** Scheduled scan runs **once per day at 07:00**. Manual scans walk 39,897 catalogued files (`/api/ingest/status`) and feel slow. With ~40k files in `files_by_status.new` and only 3 in `queued`, the dashboard doesn't reflect what's actually arrived between 7am ticks.

**Recommendation:** Easiest first move — drop `scan_interval_hours` to something like 1–4 hours via the existing config table. Separately worth investigating: why does a manual scan feel slow? Likely either remote fs walk over `mmingest.pbswi.wisc.edu` is the bottleneck or SQL upsert of 40k rows isn't batched. The cron infrastructure already exists; this is a config + perf question, not a new feature.

---

### 🟡 Formatter regression: multi-paragraph speaker blocks

**Where:** `/data/output/6POL0108/formatter_output.md`.

**What's wrong:** Established rule (memory `feedback_formatter_copy_rules.md` rule #9): *"No paragraph breaks within speaker turns — unless the transcript is essentially single-speaker throughout."* The formatter is violating this. Example, very first speaker block:

```
**Shawn Johnson:**
Do you have questions about Wisconsin government and politics? […] This is Inside Wisconsin Politics.

I'm Shawn Johnson, here with my colleague Zac Schultz and Rich Kremer in Eau Claire. Hey, guys.
```

That's two paragraphs under one speaker label. Should be one. The user also asked to confirm the speaker label is on its own line — it is in this file, but the prompt should make both rules explicit alongside each other.

**Recommendation:** Reinforce in the formatter prompt: (1) speaker label always on its own line, two-trailing-spaces or hard-wrap newline, (2) collapse internal paragraph breaks within a single speaker's turn into one continuous paragraph. Add a worked example showing a long Shawn Johnson block as one paragraph. Verify with a regression test that scans for `\n\n` *inside* a speaker block.

---

### 🟡 Hallucinated boilerplate in analyst/SEO/manager outputs

**Where:** Top of every phase output across all 6 POL episodes.

**What's wrong:** Outputs lead with hardcoded-looking metadata that contradicts reality:
- Analyst: `**Date Processed:** 2024-05-22` (or `2025-01-10`) — actual run was 2026-04 through 2026-05.
- Analyst: `**Model:** gpt-4o-2024-05-13 (OpenAI)` or `**Model:** Claude 3.5 Sonnet` — actual model was Gemini 3 Flash or Claude 4.5 Haiku. The HTML comment at line 1 has the right model; the visible metadata block lies about it.
- SEO: same pattern — `**Date Processed:** 2025-01-10` and `**Model:** Claude 3.5 Sonnet` baked in.
- Manager: `**Reviewed:** 2025-06-20` / `2025-07-10` / `2025-01-10` — never the real date.

Root cause: agent prompts (`.claude/agents/analyst.md:74`, `seo.md:54`, `formatter.md:85`) ask the model to fill `{TODAY'S DATE}` and `{model name you are running as}` — both of which the model doesn't reliably know. So it confabulates.

**Why it matters:** This metadata is the first thing a human editor sees. It will get copy-pasted into Airtable or trust signals downstream. Someone is going to argue with the model about a date the model doesn't actually know.

**Recommendation (refined per Mark, 2026-05-07):** Don't ask the agent to write the header at all. Build it programmatically at the file-write site (`worker.py:1414`) using:

1. **Transcript filename → media ID** (`utils.extract_media_id` already exists).
2. **Media ID → AirTable SST lookup** (already happening in `worker.py:_fetch_sst_context` at line 906; returns title, program, host, presenter, descriptions). Pull whatever else is useful from SST/Project — air date, episode title, segment list.
3. **Run-side known facts** — model ID, tier, cost, token count (already on `response`), and `datetime.now()` for processed timestamp.

Stitch into a deterministic block, e.g.:

```markdown
<!-- model: anthropic/claude-4.5-haiku-20251001 | tier: standard | cost: $0.0307 | tokens: 21386 -->
# Brainstorming Document
**Media ID:** 6POL0108
**Transcript File:** 6POL0108.srt
**Project:** Inside Wisconsin Politics — Student Questions Episode
**Program:** Inside Wisconsin Politics
**Host:** Shawn Johnson
**Presenters:** Zac Schultz, Rich Kremer, Anya van Wagtendonk
**Air Date:** [from SST if present]
**Processed:** 2026-05-07 20:55 UTC
**Phase:** analyst | **Model:** anthropic/claude-4.5-haiku-20251001 | **Tier:** standard

---
```

Then strip the header block from the agent prompts — instruct each agent to start at the first content section (`## Summary`, `## Optimized Metadata`, etc.) without reproducing project metadata. The LLM cannot lie about a date it never types.

This fix doubles as a partial fix for **🔴 duration tracking**: once `manifest.duration_minutes` is correctly populated, it lands in the header too.

---

### 🟡 SEO title length non-compliance is recurring

**Where:** SEO outputs across runs.

**What's wrong:** Manager flags this every time. Pattern across the 6POL episodes:
- 0103: 62 chars (over 60)
- 0105: 68 chars (over 60)
- 0107: 65 chars (over 60)
- 0108: 76 chars (over 60), "compliant alternative" 61 chars (still over 60)

The SEO prompt seems to use a 70-char ceiling internally, but the PBS standard the manager is enforcing is 60. They disagree.

**Recommendation:** Pin the SEO prompt to **60 characters** for the recommended title (no alternates over 60). Manager and SEO should be reading the same constant.

---

### 🟢 Idea: supplement live-caption SRT with WhisperX run on episode audio

**Source:** Editor (and Mark) on 2026-05-07.

**Context:** The current SRT comes from broadcast live captioning, which generates the bulk of the speaker-attribution swaps and missed words flagged in editor reviews. WhisperX (Whisper + forced alignment + speaker diarization) on the underlying audio would produce a far more accurate transcript with proper diarization.

**Pairs with:** existing memory `project_diarization_ingest_integration.md` — Ready for Work + ingest scanner already need media file support for diarization.

**Possible shapes:**
- **Replace path:** route audio through WhisperX, drop the broadcast SRT entirely. Maximum gain, maximum implementation cost (need audio in pipeline, GPU access, alignment code).
- **Augment path:** run both, then merge — use the broadcast SRT for confirmed timecodes and WhisperX for proper speaker labels and missed/paraphrased words. Lower risk, harder merge logic.
- **Sanity-check path:** run WhisperX in shadow, use it only to flag low-confidence segments in the broadcast SRT for human review. Cheap, reduces editor workload without touching the main pipeline.

**Open question:** where does the audio come from? Currently Cardigan only sees SRT. Need ingest pipeline updates first (per the existing diarization memory).

---

### 🟢 Ask John Dachik: half-speed / two-thirds-speed playback in DD media player

**Why:** During 6POL0108 review, Shawn Johnson and Zac Schultz were both talking unusually fast, making editor review (compare-against-audio) harder. A speed control in the DD media player would directly address this for the editor's workflow.

**Status:** Not a Cardigan code change — workflow ask for the broadcast/media-tools team. Logging here so it doesn't get lost in the rapid-fire session, not for the PR bundle.

---

### 🟢 Editor review of 6POL0108 transcript — net positive, three follow-ups

**Source:** Editorial review feedback from human editor, 2026-05-07.

**What's better:**
- Output is "a lot cleaner" — no misspellings, no errors in words that were included.
- Paraphrasing instances down to 3–4 from a typical 6+ — meaningful improvement.

**Three concrete issues to address:**

1. **🟡 Speaker name swaps still happening.** Editor flagged that names of speakers get mixed up and need to be fixed. This matches existing memory `feedback_formatter_copy_rules.md` rule #12 ("Speaker attribution can swap mid-transcript"). Editor's hedge: "*Maybe that's just something that is inherent to the tech at this point.*" — suggesting tolerance, but a recurring quality target.

2. **🟡 Final student labeled "Student" instead of their name.** Editor noted the final question's student was just slugged as `Student:` without a name. The 0108 manager output already flagged this as a known issue (third student, judicial recusal question — name not in transcript, recommend asking Prof. Chergosky). So the formatter *did* the right thing given the data — the gap is that the SST/Project record doesn't list the third student's name, and there's no scaffolding to flag this clearly to the editor in-line. Could be improved by surfacing "unknown speaker — review needed" inline in the transcript instead of just in review notes at the top.

3. **🟡 Possessive style: use `Evers'` and `Vos'`, not `Evers's` and `Vos's`.** This is house style not currently in the formatter rules. Already added to memory `feedback_formatter_copy_rules.md` as rule #18. Should propagate to formatter agent prompt.

4. **🟡 Em dashes used in "typically off AI sort of way" — dial them down.** Existing rule #7 in formatter memory already says *"Use sparingly and consistently. Don't over-apply them as a substitute for commas, colons, or parentheses."* — clearly not being enforced strongly enough. Editor's "AI-typical" framing suggests the model is using em dashes as a stylistic tic rather than for legitimate parenthetical asides. Recommend strengthening the prompt with concrete examples ("don't replace commas with em dashes; keep most punctuation conservative") and/or a post-processing check that flags transcripts with >N em dashes per 1k words.

---

## Comparison: Are 6POL outputs improving?

Quick verdict: **Yes on QA discipline, mixed on consistency.**

| Episode | Date | Cost | Analyst model | Manager verdict |
|---------|------|------|---------------|-----------------|
| 6POL0103 | 2026-04-02 | $0.113 | Gemini 3 Flash | NEEDS_REVISION (truncated transcript) |
| 6POL0104 | 2026-04-09 | $0.110 | Gemini 3 Flash | n/a |
| 6POL0105 | 2026-04-16 | $0.112 | Gemini 3 Flash | NEEDS_REVISION (truncated, title length) |
| 6POL0106 | 2026-04-23 | $0.109 | Gemini 3 Flash | n/a |
| 6POL0107_REV | 2026-05-06 | $0.148 | **Claude 4.5 Haiku** | **APPROVED** |
| 6POL0108 | 2026-05-07 | $0.147 | Claude 4.5 Haiku | NEEDS_REVISION (inline note, attribution, title length) |

**What's better:**
- Manager phase is *catching real issues* — speaker misattribution in 0108 (Rich → Shawn), inline review notes leaking into the body, character-limit violations. That's the QA layer earning its keep.
- 0107 is the first APPROVED run; manager validated 99.9% transcript coverage and clean speaker attribution. Real progress.
- Speaker tables in the analyst output got noticeably more thorough — 0108 lists 16 referenced figures with first-appearance times.

**What hasn't improved:**
- SEO title length compliance still bouncing 62–76 chars; this is solved by tightening the prompt, not by trying harder.
- Hallucinated `Date Processed` and `Model:` boilerplate persisted from 0103 → 0108 unchanged.
- Cost jumped ~30% (≈$0.11 → $0.15) when analyst/SEO moved from Gemini 3 Flash to Claude 4.5 Haiku. Quality is somewhat better, but not 30% better; worth A/B-ing whether Haiku is the right pick or whether Gemini 3 Flash + a better prompt would close the gap cheaper.
- Duration metric is wrong for *all* episodes (31–34 min reported vs ~18 min actual SRT) — no episode escapes this bug.
