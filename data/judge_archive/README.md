# Judge Archive — Inherited from ai-editorial-assistant

This directory holds **legacy data** inherited from `ai-editorial-assistant`,
the predecessor project that cardigan-v4 superseded. The findings document
hallucination failure modes observed in production prompts that are
conceptually similar to (and may overlap with) cardigan-v4's current
prompts.

> **Treat these findings as starting hypotheses, not confirmed bugs in
> cardigan-v4.** The agents and prompts have evolved; some failure modes may
> be fixed, others may persist, others may have new variants. Verify against
> current cardigan-v4 behavior before acting.

## Provenance

A Langfuse `Hallucination` LLM-as-judge ran for ~3 months (2026-02 →
2026-05) against an OpenRouter API key shared — inadvertently — across
crows-nest (a content-capture service in Mark's second-brain monorepo),
the legacy ai-editorial-assistant pipeline, and briefly cardigan-v4 itself.

The judge scored every model call on a 0.0 (fully grounded) → 1.0 (fully
fabricated) scale with free-text reasoning. After the unintended forwarding
was discovered and fixed, the dataset was salvaged and split by source
project. This is the editorial-assistant slice.

The cardigan-v4 slice itself is empty (0 traces in this 90-day window) —
either cardigan-v4 wasn't using the leaky key during the window, or its
output schema didn't match the classifier. If new judge data is generated
in the future via a properly-scoped key, that data should land here.

## Window and shape

- **Span**: 90 days ending 2026-05-04
- **Editorial-assistant traces flagged at score ≥ 0.5**: 92
- **Score distribution**:
  - score ≥ 0.9 (extreme hallucination): 57
  - score 0.7–0.9: 31
  - score 0.5–0.7: 4

## Top failure patterns from the legacy data

### 1. Filename / Media ID missing → fabricated metadata

When transcript-analyst or transcript-formatter agents received a
transcript without a usable filename or Media ID, they wrote `**Project:**
UNKNOWN` in the document header but proceeded to generate full
brainstorming documents / formatted transcripts using **fabricated
structural context** (program name, date, episode framing). The actual
transcript content was sometimes real, but the surrounding scaffolding was
invented to satisfy the agent's output template.

**For cardigan-v4 to verify**: Does the current pipeline accept a
transcript without metadata? If so, does it elide unknown fields cleanly,
fail-fast, or paper over the gap with placeholder text?

### 2. Input-echo failures (cross-project pattern)

A non-trivial fraction of flagged traces showed the model returning the
input **verbatim** instead of producing the requested analysis. The same
pattern appears in crows-nest's findings (private second-brain repo) —
suggests something at the OpenRouter integration layer rather than
per-prompt. Worth checking whether cardigan-v4's OpenRouter calls have any
defensive validation against this case.

### 3. Judge over-flagging on transformation tasks (caveat, not bug)

Some flagged Transcript Formatter outputs look correct on inspection — they
ARE faithful, lightly-edited copies of the input transcript, which is the
formatter's job. The Hallucination evaluator appears to flag structural
similarity to input as "duplicate of query" in some of these cases.
**Treat score ≥ 0.5 as evidence to read, not a verdict to act on.** The
judge's `comment` field is the actual signal — confirm it describes a real
problem before acting.

## How to access the raw data

The raw JSONL with inputs/outputs/judge reasoning lives in the **private**
second-brain repo (it contains transcript content from PBS Wisconsin
production work and shouldn't be committed to a public repo):

```bash
# From a machine with access to second-brain:
cd ~/Developer/second-brain
ls services/crows-nest/data/judge_archive/
cat services/crows-nest/data/judge_archive/editorial_assistant_findings.jsonl
```

Each row has: `trace_id`, `timestamp`, `score_value`, `score_comment`
(judge reasoning), `input_str`, `output_str`, `classifier_evidence`.

## How to regenerate

The export script lives in second-brain:

```bash
cd ~/Developer/second-brain
python3 services/crows-nest/scripts/langfuse_export_findings.py \
  --days 90 --threshold 0.5
```

Reads Langfuse credentials from macOS Keychain
(`developer.workspace.LANGFUSE_*`).

## See also

- GitHub issues filed against this dataset on cardigan-v4: search this
  repo's open issues for label `legacy-data`.
- Originating canonical README in second-brain:
  `services/crows-nest/data/judge_archive/README.md`.
- The API key scoping convention this archive's existence prompted:
  `~/Developer/the-lodge/conventions/SECRETS_MANAGEMENT.md`,
  section "Key Scoping and Lifecycle."
