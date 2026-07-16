#!/usr/bin/env python3
"""Fetch a PBS Wisconsin YouTube video's metadata + caption transcript for the
copy-audit POC.

Subcommands:
    list                 List recent channel uploads (owner-authed, includes
                         unlisted/scheduled) so a human can pick a video.
    fetch <videoId>      Download snippet.json + a transcript for one video.

Transcript strategy (mirrors the pbswi here-now-highlights gotcha): prefer the
MANUAL English caption track (broadcast-grade — PBS captions its broadcasts)
over the auto/ASR track. Primary path is the official captions API
(captions.list -> captions.download, owner-authed); fallback is yt-dlp when the
API has no downloadable track.

Outputs land in runs/<videoId>/:
    snippet.json                     full videos.list item (snippet,status,contentDetails)
    <MEDIAID>_YT_<videoId>.srt       transcript, named so Cardigan's
    (or YT_<videoId>.srt/.txt)       extract_media_id() finds the Media ID and
                                     auto-links the Airtable SST record

Media ID derivation is the POC's #1 open question: we regex the title,
description and tags for the PBS pattern (4 alphanumerics + 4 digits + optional
suffix, e.g. 2WLI1209HD). Use --media-id to override, and log hits/misses in
FINDINGS.md.

Auth: the pbswi read-write management token (authorized_user JSON). Path from
$YOUTUBE_TOKEN_PATH, defaulting to
$PBSWI_ROOT/station-analytics/credentials/work/token.json. Read-only calls
here; the identity gate still runs so a wrong-account token fails loudly
instead of listing a personal channel (pbswi issue #90).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# The PBS Wisconsin brand channel — the only channel this POC reads or writes.
BRAND_CHANNEL_ID = "UCtnFS8kY2D3VLaEtt_Jk2ZA"

# PBS Media ID: 4 alphanumeric program/episode chars + 4 digits + optional
# suffix (HD, SD, WEB02, ...). Matches api/services/utils.py extract_media_id().
MEDIA_ID_RE = re.compile(r"\b[0-9A-Z]{4}\d{4}[A-Z0-9]{0,10}\b")

RUNS_DIR = Path(__file__).resolve().parent / "runs"


class IdentityError(Exception):
    """Authenticated channel is not the PBS Wisconsin brand channel."""


def _token_path() -> Path:
    explicit = os.environ.get("YOUTUBE_TOKEN_PATH")
    if explicit:
        return Path(explicit).expanduser()
    pbswi_root = Path(os.environ.get("PBSWI_ROOT", "~/Developer/pbswi")).expanduser()
    return pbswi_root / "station-analytics" / "credentials" / "work" / "token.json"


def load_service():
    """Build an authed YouTube v3 service and enforce the brand identity gate."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token_path = _token_path()
    if not token_path.exists():
        sys.exit(f"OAuth token not found at {token_path}.\n" "Set YOUTUBE_TOKEN_PATH (or PBSWI_ROOT) — see README.md.")
    data = json.loads(token_path.read_text())
    creds = Credentials.from_authorized_user_info(data, data.get("scopes"))
    if not creds.valid:
        creds.refresh(Request())
    service = build("youtube", "v3", credentials=creds)

    resp = service.channels().list(part="id", mine=True).execute()
    items = resp.get("items") or []
    authed = items[0]["id"] if items else "<none>"
    if authed != BRAND_CHANNEL_ID:
        raise IdentityError(
            f"Token authenticates as channel {authed}, not the PBS Wisconsin "
            f"brand channel {BRAND_CHANNEL_ID}. Re-auth picking the brand "
            "account at the Google chooser (NOT a personal login)."
        )
    return service


def derive_media_id(snippet: dict) -> tuple[str | None, str | None]:
    """Search title, description and tags for a PBS Media ID.

    Returns (media_id, where_found). Case-sensitive uppercase match — Media IDs
    are uppercase in SST and in filenames.
    """
    for where, text in (
        ("title", snippet.get("title", "")),
        ("description", snippet.get("description", "")),
        ("tags", " ".join(snippet.get("tags") or [])),
    ):
        m = MEDIA_ID_RE.search(text)
        if m:
            return m.group(0), where
    return None, None


def cmd_list(args: argparse.Namespace) -> int:
    service = load_service()
    uploads = "UU" + BRAND_CHANNEL_ID[2:]
    ids: list[str] = []
    token = None
    while len(ids) < args.max:
        pl = (
            service.playlistItems()
            .list(part="contentDetails", playlistId=uploads, maxResults=50, pageToken=token)
            .execute()
        )
        ids += [i["contentDetails"]["videoId"] for i in pl["items"]]
        token = pl.get("nextPageToken")
        if not token:
            break
    ids = ids[: args.max]

    rows = []
    for i in range(0, len(ids), 50):
        resp = service.videos().list(part="snippet,status,contentDetails", id=",".join(ids[i : i + 50])).execute()
        rows += resp["items"]

    print(f"{'video id':<13} {'privacy':<9} {'published':<12} {'mediaId?':<16} title")
    print("-" * 100)
    for v in rows:
        sn, st = v["snippet"], v["status"]
        media_id, _ = derive_media_id(sn)
        published = (sn.get("publishedAt") or "")[:10]
        title = sn.get("title", "")[:55]
        print(f"{v['id']:<13} {st.get('privacyStatus', '?'):<9} {published:<12} " f"{media_id or '-':<16} {title}")
    return 0


def _pick_caption_track(tracks: list[dict]) -> dict | None:
    """Prefer a manual (non-ASR) English track; fall back to ASR English."""
    english = [t for t in tracks if (t["snippet"].get("language") or "").lower().startswith("en")]
    manual = [t for t in english if t["snippet"].get("trackKind") != "asr"]
    if manual:
        return manual[0]
    return english[0] if english else None


def _fetch_captions_api(service, video_id: str) -> tuple[bytes, str] | None:
    """captions.list -> captions.download (srt). Returns (data, kind) or None."""
    from googleapiclient.errors import HttpError

    resp = service.captions().list(part="snippet", videoId=video_id).execute()
    track = _pick_caption_track(resp.get("items") or [])
    if track is None:
        return None
    kind = "manual" if track["snippet"].get("trackKind") != "asr" else "auto"
    try:
        data = service.captions().download(id=track["id"], tfmt="srt").execute()
    except HttpError as exc:
        # ASR tracks return 403 even for the channel owner — not downloadable
        # via the API. Return None so the yt-dlp fallback engages.
        if exc.resp.status == 403:
            return None
        raise
    if isinstance(data, str):
        data = data.encode("utf-8")
    return data, kind


def _fetch_captions_ytdlp(video_id: str, out_dir: Path) -> Path | None:
    """yt-dlp fallback: manual track preferred, then auto; returns cleaned .txt."""
    if shutil.which("yt-dlp") is None:
        print("yt-dlp not on PATH — skipping fallback.", file=sys.stderr)
        return None
    subprocess.run(
        [
            "yt-dlp",
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            "en.*,en",
            "--sub-format",
            "vtt",
            "-o",
            str(out_dir / "captions.%(ext)s"),
            f"https://www.youtube.com/watch?v={video_id}",
            "--no-warnings",
        ],
        check=True,
    )
    manual = out_dir / "captions.en.vtt"
    vtt = manual if manual.exists() else next(iter(sorted(out_dir.glob("captions*.vtt"))), None)
    if vtt is None:
        return None
    txt = out_dir / "captions.clean.txt"
    txt.write_text(vtt_to_text(vtt.read_text(encoding="utf-8")), encoding="utf-8")
    return txt


def vtt_to_text(vtt: str) -> str:
    """Flatten VTT cues to prose, de-duping YouTube's rolling repeats.

    Port of pbswi here-now-highlights vtt2txt.py.
    """
    out: list[str] = []
    for ln in vtt.splitlines():
        if "-->" in ln or ln.strip() == "" or ln.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        ln = re.sub(r"<[^>]+>", "", ln)
        ln = re.sub(r"\s+", " ", ln).strip()
        if not ln or (out and out[-1] == ln):
            continue
        out.append(ln)
    clean: list[str] = []
    for ln in out:
        if clean and (ln in clean[-1] or clean[-1].endswith(ln)):
            continue
        clean.append(ln)
    return re.sub(r"\s+", " ", " ".join(clean)).strip()


def cmd_fetch(args: argparse.Namespace) -> int:
    service = load_service()
    video_id = args.video_id

    resp = service.videos().list(part="snippet,status,contentDetails", id=video_id).execute()
    items = resp.get("items") or []
    if not items:
        sys.exit(f"Video {video_id!r} not found (or not visible to this token).")
    video = items[0]
    snippet = video["snippet"]

    out_dir = RUNS_DIR / video_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "snippet.json").write_text(json.dumps(video, indent=2), encoding="utf-8")

    media_id, where = (args.media_id, "override") if args.media_id else derive_media_id(snippet)
    if media_id:
        print(f"Media ID: {media_id} (from {where})")
    else:
        print(
            "Media ID: NOT FOUND in title/description/tags — SST auto-link will "
            "not happen. Pass --media-id if you know it, and log this in FINDINGS.md."
        )

    # Transcript filename drives Cardigan's extract_media_id(): the Media ID
    # must lead the stem; the video id rides along for traceability.
    stem = f"{media_id}_YT_{video_id}" if media_id else f"YT_{video_id}"

    result = None if args.ytdlp else _fetch_captions_api(service, video_id)
    if result is not None:
        data, kind = result
        transcript = out_dir / f"{stem}.srt"
        transcript.write_bytes(data)
        print(f"Captions: {kind} track via captions API -> {transcript}")
    else:
        if not args.ytdlp:
            print("No caption track via the API — falling back to yt-dlp.")
        cleaned = _fetch_captions_ytdlp(video_id, out_dir)
        if cleaned is None:
            sys.exit(
                "No transcript available from either path. Fresh uploads may "
                "not have processed captions yet — retry later."
            )
        transcript = out_dir / f"{stem}.txt"
        transcript.write_text(cleaned.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Captions: yt-dlp fallback -> {transcript}")

    print(f"\nRun dir ready: {out_dir}")
    print(f"Next: python submit_and_wait.py {out_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List recent channel uploads")
    p_list.add_argument("--max", type=int, default=25, help="How many uploads to list (default 25)")
    p_list.set_defaults(func=cmd_list)

    p_fetch = sub.add_parser("fetch", help="Fetch snippet + transcript for one video")
    p_fetch.add_argument("video_id", help="YouTube video ID")
    p_fetch.add_argument("--media-id", help="Override the derived PBS Media ID")
    p_fetch.add_argument("--ytdlp", action="store_true", help="Skip the captions API, use yt-dlp directly")
    p_fetch.set_defaults(func=cmd_fetch)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
