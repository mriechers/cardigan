# Spike: whisperX SRT → Cardigan agents — findings

**Date:** 2026-06-25
**Branch:** `spike/whisperx-srt`
**Question:** Is a whisperX-generated SRT good enough to feed Cardigan's 4-phase
agents and get usable metadata — with no human caption file?

## Verdict: ✅ GO

A whisperX SRT is a transparent substitute for a caption file. The agents
produced publishable metadata; the pipeline needed **zero new code** for the spike.

## What was run

- **Input:** `~/Downloads/6HNS20260619HoChunkLand.mp4` (~166 MB, ~90 s news segment).
- **Transcription:** `whisper-transcribe.sh --engine whisperx --model large-v3 --no-diarize`
  with a PBS glossary `--prompt`. ~90 s wall time on CPU. Produced a clean 13-cue SRT
  (sequential numbering, continuous timecodes, full coverage).
- **Pipeline:** uploaded the SRT to a **local** Cardigan stack (isolated DB, port 8101)
  via `POST /api/upload/transcripts` → worker ran analyst → formatter → seo → validator.

## Results

- **Cost:** $0.0596 total (haiku for analyst/seo/validator, sonnet for formatter).
- **SRT quality:** proper nouns all correct — *Ho-Chunk, Dejope, Dane County,
  Groundswell Conservancy, Bill Quackenbush, Erika Aisi, pbswisconsin.org*.
- **Agent output quality:** strong. Title "Ho-Chunk Nation Receives 165 Acres in
  Historic Land Restoration" (57/60); full keyword strategy; coherent short/long
  descriptions. Validator correctly **failed** the SEO phase for over-length
  descriptions (198/150 and 324/300) — QC working as designed.
- **Speakers without diarization:** the formatter recovered speaker labels
  (Erika Aisi / Bill Quackenbush) from the transcript's own self-identifying cues.
  → For interview/news content where speakers self-identify, pyannote diarization
  may be optional.

## Key architecture finding (for deployment)

**whisperX is already containerized in prod.** `diarization/app.py` is a whisperX
service (loads a whisperX model at startup, `POST /diarize` runs transcribe → align →
optional diarize). It's the `cardigan-diarization` image, behind the `diarization`
compose profile, reached via `api/services/diarization_client.py`. So "run whisperX
in a container like prod" is **already solved**.

The one real gap: `DiarizationSegment` returns `start/end/speaker/confidence` but
**no `text`** — the transcription text is computed and discarded. The feature mostly
needs to surface that text and format it as SRT.

### Recommended build (post-spike)

1. Extend the whisperX service: add `text` to segments and/or a `POST /transcribe`
   that returns SRT-ready segments. Consider renaming the concept "transcription".
2. Add `transcribe(file_path)` to `diarization_client.py`.
3. New `POST /api/upload/media` (audio/video allow-list) → call service → write SRT to
   `transcripts/` → create job via existing `JobCreate` path → return 202 + job_id.
4. **SRT download (required)** — backend + frontend:
   - *Backend:* add `GET /api/jobs/{id}/transcript` (or `/srt`) returning the generated SRT
     as `application/x-subrip` with a `Content-Disposition: attachment` filename. Mirror the
     existing `get_job_output` pattern (`api/routers/jobs.py:510`, `_make_download_filename`),
     but resolve the SRT in BOTH `transcripts/` and `transcripts/archive/` — the worker moves
     the file to `archive/` after processing.
   - *Frontend:* a "Download SRT" action in two places — on the media-upload tab right after a
     job is created, and on the job-detail page (`JobDetail.tsx`) alongside the output downloads.
5. Optional QC pass (wc-transcribe-style sampling / proper-noun / coverage checks).
6. Frontend: new tab + media uploader cloned from `TranscriptUploader.tsx`, plus the SRT-download
   action above.

### Deployment decisions to settle

- **Model size:** prod's service runs `WHISPER_MODEL_SIZE=base` (lower accuracy);
  this spike used `large-v3`. Decide base vs large-v3 vs turbo on CPU-only homelab
  (no GPU passthrough today). Quality here was driven by large-v3 — worth comparing.
- Whether transcription shares or splits from the diarization service.
- Timeouts/resource limits for long media on CPU.

## Friction encountered (not workflow problems)

- **Dead OpenRouter key:** the key in macOS Keychain *and* the canonical 1Password
  "OpenRouter API Key" both returned `401 User not found` (account rotated/recreated).
  Resolved with a new dev-scoped key stored in 1Password item
  **"OpenRouter API Key - Cardigan Mac Studio - Dev Server"**, injected as an env var
  for the local run (no Keychain writes).
- **Secrets resolver reads Keychain, not 1Password** → filed as a follow-up issue.

## Repro (local)

```bash
# 1. transcribe
whisper-transcribe.sh --engine whisperx --model large-v3 --no-diarize \
  --prompt "PBS Wisconsin. Key terms: …, Ho-Chunk Nation, …" \
  ~/Downloads/6HNS20260619HoChunkLand.mp4

# 2. local stack (isolated DB + dev key via env, no keychain)
export OPENROUTER_API_KEY_DEV="$(op item get 'OpenRouter API Key - Cardigan Mac Studio - Dev Server' --fields credential --reveal)"
export DATABASE_PATH=$PWD/spike.db TRANSCRIPTS_DIR=$PWD/transcripts OUTPUT_DIR=$PWD/OUTPUT
export LLM_CONFIG_PATH=<scratch config with openrouter backends repointed to OPENROUTER_API_KEY_DEV>
uvicorn api.main:app --port 8101 &   # + python run_worker.py &

# 3. upload + watch
curl -F "files=@6HNS20260619HoChunkLand.srt" http://127.0.0.1:8101/api/upload/transcripts
curl http://127.0.0.1:8101/api/jobs/1
```

---

# Vertical slice — raw media upload (2026-06-26)

Built and tested the thin real-feature path end-to-end (branch `spike/whisperx-srt`):

**Code added** (all `ruff` clean):
- `diarization/app.py` — new `POST /transcribe` (whisperX transcribe + align, no
  diarize) returning segments with text + duration + language.
- `api/services/diarization_client.py` — `transcribe(file_path)` mirroring `diarize()`.
- `api/services/utils.py` — `segments_to_srt()` (unit-tested, `tests/api/test_segments_to_srt.py`).
- `api/routers/upload.py` — `POST /api/upload/media`: accepts audio/video → calls the
  service → builds SRT → writes to `transcripts/` → creates a job (dedup + SST auto-link).

**Run:** the whisperX service ran in the pipx whisperx venv (fastapi/uvicorn added
via `pipx runpip whisperx install`), API+worker in the cardigan venv, talking over
HTTP — mirroring prod's two-container split. `curl -F file=@…mp4 /api/upload/media`
→ 16 segments → SRT → job #1 → all 4 phases **completed** for **$0.0608**. Title
produced: "Ho-Chunk Land Return: Dane County Restores Sacred Ancestral Home".

**✅ Verdict: the feature path is viable.** Raw media in → metadata out, no caption file.

**⚠️ Key finding — raw service output needs anti-hallucination guards.** The service
calls `_model.transcribe(audio)` with **default** settings, so over a ~4.5s near-silent
b-roll gap whisperX's VAD hallucinated 3 garbled cues ("But we didn't" ×2, "deadfall
or tree falls"). The CLI `whisper-transcribe.sh` avoids this — it sets
`NO_SPEECH_THRESHOLD`, `HALLUCINATION_SILENCE_THRESHOLD`, `CONDITION_ON_PREVIOUS_TEXT=False`.
The Cardigan **validator correctly flagged** the fragments (`formatter: fail`), but the
formatter passed them through. → Before shipping: (a) pass those thresholds in the
service's `transcribe()` call, and (b) add the planned wc-transcribe-style QC pass.

**⚠️ Glossary not wired into the service path.** The slice's `/transcribe` calls
`_model.transcribe(audio)` with **no `initial_prompt`**, so the PBS glossary corrections
do NOT apply on the feature path — they only worked in the spike's CLI run
(`whisper-transcribe.sh --prompt`). Concretely: whisperX heard the reporter as "Erika Aisi";
correct is **"Erica Ayisi"** (added to `automations/transcripts/glossary.md` on 2026-06-26,
and the formatter had already flagged it for review). For the feature, `/transcribe` must
accept an `initial_prompt`/hotwords built from the glossary so recurring on-air names are
fixed at the source; the QC pass then cross-checks proper nouns against the same list.

**Glossary mechanism does NOT exist in the Cardigan app yet (verified).** No `api/` code
references "glossary"/"initial_prompt". The transcription-side glossary lives only in the
the-lodge `whisper-transcribe.sh` (CLI). Cardigan has TWO glossary *files* but neither is
wired into code: `automations/transcripts/glossary.md` (parent; CLI initial_prompt — where
"Erica Ayisi" was added) and `cardigan-v4/knowledge/glossary.md` (a Claude Desktop knowledge
doc, not loaded by the API/worker). The phase prompts do proper-noun *detection/flagging*
only, not glossary auto-correction. Integration must: (a) pick a canonical glossary (or merge
the two), (b) wire it into `/transcribe` as whisper `initial_prompt`, and optionally (c) inject
corrections into the formatter prompt.

**Decided glossary architecture (layered base → project):**
```
automations/transcripts/glossary.md   ← BASE (workspace SoT, structured: Correct ↔ misspellings ↔ role)
        │  merged at CONSUMPTION time; project overrides base on conflict
        ├─ cardigan-v4/knowledge/glossary.md      ← Cardigan-only terms/overrides
        └─ wonder-cabinet/.../glossary.json       ← Wonder Cabinet-only terms/overrides (same drift risk)
```
Rules that make it work (docs alone are not enough):
1. **Precedence:** project entry wins over base on conflict.
2. **Real merge at consumption:** the whisper-prompt builder / LLM phase loads `base + project` and
   dedupes — not just a doc cross-reference, or it drifts.
3. **One source, two derived shapes:** keep the base *structured*; derive the flat whisper
   `initial_prompt` from its "Correct" column, and feed the correction tables to the formatter.
4. **Document the relationship** in each file's header (where the base lives, that project files
   extend-not-replace, precedence). Base is workspace-level (3 consumers: Cardigan, Wonder Cabinet,
   ad-hoc `whisper-transcribe.sh`).

Drift already observed: "Erica Ayisi" was added to the base flat list but the richer Cardigan
`knowledge/glossary.md` (which carries disambiguation) didn't get it — exactly the failure mode
the merge mechanism prevents. (Now synced to both, and relationship headers added to the base
and Cardigan files on 2026-06-26.)

**TODO (Wonder Cabinet):** add the same base→project relationship header to
`wonder-cabinet/podcast-publishing-suite/shows/wonder-cabinet/glossary.json` (or its README)
and have the WC formatter merge base + WC corrections — next time that repo is touched.

**Notes / cleanup:**
- Added `fastapi/uvicorn/python-multipart` to the pipx whisperx venv for the local run;
  `pipx reinstall whisperx` resets it if undesired.
- `torchcodec` dylib warnings at service startup are non-fatal (whisperX uses the ffmpeg
  CLI; audio loaded fine).
- Slice is uncommitted on `spike/whisperx-srt` (4 files modified + 2 new).
