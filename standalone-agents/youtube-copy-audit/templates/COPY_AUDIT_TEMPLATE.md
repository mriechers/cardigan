# Copy Audit Report Template

**Three-way comparison for YouTube-sourced jobs: what YouTube says now, what the
SST says now, what the pipeline generated — and one proposed combined version.**

Derived from `claude-desktop-project/templates/COPY_REVISION_TEMPLATE.md`; this
variant adds the YouTube column and the write-back diff. Follow it exactly.

```markdown
# Copy Audit Report

**Video**: [Title] — `[videoId]` (https://www.youtube.com/watch?v=[videoId])
**Media ID**: [Media ID or "not derived — see FINDINGS"]
**SST Record**: [Airtable URL or "no linked record"]
**Program**: [Program name, if known]
**Cardigan Job**: [job ID]
**Generated**: [Date]
**Agent**: youtube-copy-audit (POC)
**Revision**: [Rev 1, Rev 2, ...]

---

## Audit Summary

[2-4 sentences: how far apart are the three sources, what drives the proposed
combined copy, and anything that needs human judgment before write-back.]

---

## Title

| Source | Value | Chars |
|--------|-------|-------|
| YouTube (live) | [current YouTube title] | [XX / 100] |
| SST (Airtable) | [Release Title, or "—"] | [XX / 80] |
| Pipeline (SEO phase) | [generated title] | [XX / 80] |
| **Proposed (combined)** | **[proposed title]** | **[XX]** |

### Reasoning

- [What each source gets right/wrong]
- [Why the proposed version chose what it chose]
- [House-style issues found in the live copy: prohibited language, casing, etc.]

---

## Description

> YouTube has ONE description (5,000 chars); the SST splits short (≤90) and
> long (≤350). The proposed YouTube description typically leads with the
> SST-style long description, then keeps/repairs the boilerplate below the fold
> (links, credits, chapters).

**YouTube (live):**

[current full description — preserve line breaks]
— _[XX / 5000 chars]_

**SST Short Description:** [value] — _[XX / 90]_
**SST Long Description:** [value] — _[XX / 350]_

**Pipeline Short:** [value] — _[XX / 90]_
**Pipeline Long:** [value] — _[XX / 350]_

**Proposed (combined) YouTube description:**

[proposed full description]
— _[XX / 5000 chars]_

### Reasoning

- [What was kept from the live description (links, chapters, boilerplate) and why]
- [What was replaced and which source the replacement leans on]
- [Accuracy checked against the transcript at: [references]]

---

## Tags / Keywords

| Source | Values |
|--------|--------|
| YouTube tags (live) | [tag1, tag2, ...] — _[XX / 500 chars total]_ |
| SST Keywords | [kw1, kw2, ...] |
| Pipeline Keywords | [kw1, kw2, ...] |
| **Proposed tags** | **[tag1, tag2, ...]** — _[XX / 500]_ |

### Changes

- Added: [tag] — [reason]
- Removed: [tag] — [reason]
- Kept: [brief note on retained tags]

---

## Write-Back Diff (YouTube current → proposed)

> This is exactly what `videos.update` will change. Fields not listed are untouched.

```diff
- title: [current]
+ title: [proposed]
- description: [current, first ~200 chars ...]
+ description: [proposed, first ~200 chars ...]
- tags: [current]
+ tags: [proposed]
```

**op.json** (for `writeback.py`):

```json
{
  "op": "update_metadata",
  "target": "[videoId]",
  "changes": {
    "title": "[proposed]",
    "description": "[proposed]",
    "tags": ["[tag1]", "[tag2]"]
  }
}
```

---

## Validation Summary

| Check | Status | Notes |
|-------|--------|-------|
| YouTube limits met (100 / 5000 / 500) | ✅ / ⚠️ | |
| House limits met where SST-bound (80 / 90 / 350) | ✅ / ⚠️ | |
| Prohibited language removed | ✅ / ⚠️ | |
| Down style (first word + proper nouns) | ✅ / ⚠️ | |
| Boilerplate/links/chapters preserved | ✅ / ⚠️ / N/A | |
| Accuracy verified against transcript | ✅ / ⚠️ | |
| SST and YouTube copy will still agree after write | ✅ / ⚠️ | [note if an SST edit should accompany this] |

---

## Approval

- [ ] Human reviewed the diff above
- [ ] Dry-run output matches the diff
- [ ] **Approved for live write** (signature/date: ____________)

After approval: `python writeback.py runs/[videoId]/op.json --live --confirm`

---

## Revision History

| Version | Date | Changes | Feedback addressed |
|---------|------|---------|-------------------|
| Rev 1 | [Date] | Initial audit | — |
```
