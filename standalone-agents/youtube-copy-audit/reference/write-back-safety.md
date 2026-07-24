# Write-Back Safety, Quotas and Limits

Reference for the copy-audit POC. Sources: pbswi `youtube-post` skill
(`write_ops.py`), pbswi `docs/youtube-shows-strategy.md`, Cardigan
`config/house_style.yaml`.

## The triple lock (enforced by write_ops.py — never bypass)

1. **Dry-run is the default.** A live call happens only with BOTH
   `dry_run=False` (`--live`) and `confirm=True` (`--confirm`). Absent either,
   the op is previewed and no API call is made.
2. **Identity gate first.** On the live path the authed channel must be the PBS
   Wisconsin brand channel or `IdentityError` is raised before any write.
   History: pbswi issue #90 — the work token once authenticated as a personal
   channel; the gate exists so that failure mode is loud, not silent.
3. **Every committed write is logged** to `logs/mutations.jsonl` under the
   youtube-post skill (override: `$YOUTUBE_POST_LOG`) — the rollback trail.

Additional executor behavior worth knowing:

- `update_metadata` **reads the current snippet first** and merges only
  title/description/tags — categoryId, defaultLanguage, etc. are never
  clobbered.
- `deleteVideo`/`deletePlaylist` are hard-disabled upstream; uploads deferred.

## Channels

| Channel | ID | Writable? |
|---------|----|-----------|
| PBS Wisconsin (main) | `UCtnFS8kY2D3VLaEtt_Jk2ZA` | Yes — the ONLY writable target |
| PBS Wisconsin Education | `UC9hv0yH0UirvKjtD0xXEomg` | Never (sunset; read-only) |

## Quota budget (YouTube Data API v3 — 10,000 units/day, shared with pbswi skills)

| Call | Units | POC usage |
|------|-------|-----------|
| `playlistItems.list` | 1 / page (50 videos) | `fetch_video.py list` |
| `videos.list` | 1 / call (up to 50 ids) | list + fetch + write merge-read |
| `captions.list` | ~50 | per fetch |
| `captions.download` | ~200 | per fetch (the expensive one) |
| `videos.update` | 50 | per committed write |

Rule of thumb: **one full POC cycle ≈ 300 units** — comfortably ~30 videos/day
even sharing quota. Note real numbers in FINDINGS.md.

## Character limits

| Field | YouTube hard limit | PBS house limit (SST-bound copy) |
|-------|--------------------|----------------------------------|
| Title | 100 chars | 80 chars |
| Description | 5,000 chars | short ≤90 / long ≤350 |
| Tags | ~500 chars total | keywords 15–20 count |

House-style rules (prohibited language, down-style casing, program formulas)
are machine-authored in Cardigan's `config/house_style.yaml` — the audit report
checks proposed copy against both column sets: YouTube limits gate the write;
house limits gate any copy that should flow back to the SST.
