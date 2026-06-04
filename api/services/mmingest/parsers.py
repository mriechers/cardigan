"""mmingest parsers — Sprint 1B.

Two parsers for the mmingest crawler:

1. ``AutoindexParser`` — parses Apache 2.4 mod_autoindex HTML directory listings
   into ``DirEntry`` objects.  Driven off a real server snapshot (fixture at
   tests/services/mmingest/fixtures/autoindex_snapshot.html) — never trust the
   format blindly.

2. ``parse_filename`` — parses a PBS Wisconsin asset filename stem into structured
   components per the Media ID grammar.

Grammar (from ~/Developer/pbswi/docs/media-id.md, ported from
~/Developer/pbswi/.claude/skills/reference/media-id/parse_media_id.py,
sprint-0/media-id-resolver branch, synced 2026-06-04):

    <PREFIX><SSEE>[HD][_REV<YYYYMMDD>][_<TAG>...]

    PREFIX  exactly 4 characters (alphanumeric uppercase), atomic
    SSEE    4-digit season+episode: SS=season (zero-padded), EE=episode
    HD      literal "HD", indicates high-definition
    _REV    revision/re-delivery marker + 8-digit YYYYMMDD date
    _<TAG>  optional trailing tag segment

Variant-selection rule (per spec):

    _REV<YYYYMMDD>              Iterative revision.  ``revision_date`` is set;
                                ``variant_tag`` is None.  Use ``select_primary``
                                to find the winner within a (media_id, variant_tag)
                                group.

    _<UPPERCASE_TAG> where TAG  True variant from known vocabulary.  ``variant_tag``
    is in KNOWN_VARIANT_VOCAB   is set; coexists with primary; NOT superseded.

    _<TAG> (other)              Unknown tag — do NOT collapse silently.  Parser
                                sets ``unknown_tag=<TAG>`` so the indexer (S2) can
                                log it for vocabulary growth.

Prefix resolution: the canonical prefix table is vendored as
``media_id_prefixes.yaml`` in this package directory (source of truth is
~/Developer/pbswi/.claude/skills/reference/media-id/media_id_prefixes.yaml,
synced 2026-06-04).  Behavior is consistent with the skill's parser:
  * 6POL -> "Inside Wisconsin Politics" (non-broadcast)
  * 2WLI -> "Wisconsin Life" (broadcast)
  * 6WLI -> "Wisconsin Life Digital Shorts" (non-broadcast)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

try:
    import yaml
except ImportError as exc:
    raise ImportError("PyYAML is required for mmingest parsers. " "Install it: pip install pyyaml") from exc

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_YAML_PATH = Path(__file__).parent / "media_id_prefixes.yaml"

# Known variant vocabulary.  Tags in this set are treated as true variants
# that coexist with the primary; they are NOT superseded.  Unknown tags are
# preserved as ``unknown_tag`` for indexer logging / vocabulary growth.
KNOWN_VARIANT_VOCAB: frozenset[str] = frozenset({"PLEDGE", "DS"})

# Compiled grammar pattern (matches the stem, i.e. filename without extension).
# Groups:
#   1  PREFIX  — 4-char alphanumeric uppercase
#   2  SS      — 2-digit season
#   3  EE      — 2-digit episode
#   4  HD flag — optional literal "HD"
#   5  REV date — optional 8-digit YYYYMMDD (after _REV)
#   6  trailing tag — optional _<TAG> after REV (or after SSEE[HD])
_MEDIA_ID_RE = re.compile(
    r"^([A-Z0-9]{4})"  # PREFIX
    r"(\d{2})"  # SS
    r"(\d{2})"  # EE
    r"(HD)?"  # optional HD
    r"(?:_REV(\d{8}))?"  # optional _REV<YYYYMMDD>
    r"(_[A-Za-z0-9_]+)?"  # optional trailing _<TAG> (may be mixed-case from server)
    r"$",
    re.ASCII,
)

# Apache autoindex date format used in the table rows
_APACHE_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DirEntry:
    """One entry from an Apache mod_autoindex directory listing."""

    name: str
    is_dir: bool
    url: str
    modified: Optional[datetime] = None
    size_bytes: Optional[int] = None


@dataclass
class ParsedFilename:
    """Structured parse result for a PBS Wisconsin asset filename.

    Most fields are populated from the standard grammar.  When the grammar
    fails but the first 4 characters match a registered prefix (e.g. the
    editor-inserted ``6POLS*`` shorts pattern), a *nonstandard* result is
    returned instead of a ``ParseError``.  In that case:

    * ``nonstandard`` is ``True``
    * ``nonstandard_remainder`` holds the raw string after the 4-char prefix
    * ``media_id``, ``season``, ``episode``, ``hd`` are all ``None``
    * ``revision_date``, ``variant_tag``, ``unknown_tag`` are all ``None``
    * ``prefix``, ``prefix_category``, ``show_name`` are resolved normally

    S2 should log nonstandard parses for hygiene reporting rather than dropping
    them — these files are real assets belonging to a known show.
    """

    # Original stem (filename without extension) as passed to the parser
    stem: str
    # File extension including leading dot, e.g. ".srt", ".mp4"
    file_type: str

    # Core grammar fields (None for nonstandard parses)
    media_id: Optional[str]  # e.g. "6POL0101" (prefix+SSEE, no HD/suffix); None if nonstandard
    prefix: str  # 4-char prefix, e.g. "6POL"
    season: Optional[int]  # None if nonstandard
    episode: Optional[int]  # None if nonstandard
    hd: Optional[bool]  # None if nonstandard

    # Revision / variant fields
    revision_date: Optional[str]  # ISO date string "YYYY-MM-DD", or None
    variant_tag: Optional[str]  # Known variant from KNOWN_VARIANT_VOCAB, or None
    unknown_tag: Optional[str]  # Unrecognised trailing tag (for vocab growth)

    # Prefix lookup results
    prefix_category: str  # "broadcast" | "non-broadcast" | "unknown"
    show_name: Optional[str]  # Human-readable show name, or None if prefix unknown

    # Nonstandard parse flag (editor-inserted suffix variants, e.g. 6POLS* shorts)
    nonstandard: bool = False
    nonstandard_remainder: Optional[str] = None  # Raw string after the 4-char prefix


@dataclass
class ParseError:
    """Returned instead of ParsedFilename when the filename does not match the grammar."""

    stem: str
    file_type: str
    reason: str


# ---------------------------------------------------------------------------
# Prefix table (module-level cache)
# ---------------------------------------------------------------------------

_PREFIX_TABLE: Optional[dict[str, dict]] = None


def _get_prefix_table() -> dict[str, dict]:
    """Load the prefix YAML once and cache it for the process lifetime."""
    global _PREFIX_TABLE
    if _PREFIX_TABLE is None:
        if not _YAML_PATH.exists():
            raise FileNotFoundError(
                f"Media ID prefix YAML not found at {_YAML_PATH}. "
                "This file should be vendored in the mmingest package directory."
            )
        with _YAML_PATH.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not data or "prefixes" not in data:
            raise ValueError(f"Unexpected YAML structure in {_YAML_PATH}")
        _PREFIX_TABLE = {entry["prefix"]: entry for entry in data["prefixes"]}
    return _PREFIX_TABLE


# ---------------------------------------------------------------------------
# Parser 1: Apache mod_autoindex HTML listing parser
# ---------------------------------------------------------------------------


class AutoindexParser:
    """Parse Apache 2.4 mod_autoindex HTML directory listings.

    Driven against real server output (Apache/2.4.46 Win32, mod_autoindex).
    The listing uses a <table> with columns: [icon] | Name | Last modified | Size | Desc.

    Usage::

        parser = AutoindexParser(base_url="https://mmingest.pbswi.wisc.edu/IWP/")
        entries = parser.parse(html_text)
        files = [e for e in entries if not e.is_dir]
        subdirs = [e for e in entries if e.is_dir]
    """

    # Sort-control query params that appear as column header links
    _SORT_PARAMS = frozenset(
        {
            "?C=N;O=D",
            "?C=N;O=A",
            "?C=M;O=A",
            "?C=M;O=D",
            "?C=S;O=A",
            "?C=S;O=D",
            "?C=D;O=A",
            "?C=D;O=D",
            "?",
            "",
        }
    )

    def __init__(self, base_url: str) -> None:
        """
        Args:
            base_url: URL of the directory being parsed (used to resolve
                      relative hrefs to absolute URLs).
        """
        # Ensure trailing slash so urljoin resolves relative links correctly
        self.base_url = base_url.rstrip("/") + "/"

    def parse(self, html: str) -> list[DirEntry]:
        """Parse HTML directory listing into DirEntry objects.

        Skips parent-directory links, sort-column links, and icon/blank
        anchors.  Returns all files and subdirectories as DirEntry objects.
        The caller can filter by ``is_dir``.

        Args:
            html: Raw HTML text of an Apache mod_autoindex listing.

        Returns:
            List of DirEntry objects (files and directories).
        """
        soup = BeautifulSoup(html, "html.parser")
        entries: list[DirEntry] = []

        for link in soup.find_all("a"):
            href = link.get("href", "")
            if not href:
                continue

            # Skip sort column links and empty hrefs
            if href in self._SORT_PARAMS or href.startswith("?"):
                continue

            # Skip parent directory
            if href in ("../", "..", "/", "./"):
                continue

            # The "Parent Directory" text link has href="/"
            link_text = link.get_text(strip=True)
            if link_text == "Parent Directory":
                continue

            # Resolve to absolute URL
            url = urljoin(self.base_url, href)

            # Subdirectory — href ends with /
            if href.endswith("/"):
                name = href.rstrip("/").split("/")[-1]
                if not name or name in ("..", "."):
                    continue
                modified, _ = self._extract_row_metadata(link)
                entries.append(
                    DirEntry(
                        name=name,
                        is_dir=True,
                        url=url,
                        modified=modified,
                        size_bytes=None,
                    )
                )
                continue

            # File entry
            name = href.split("/")[-1]
            modified, size_bytes = self._extract_row_metadata(link)
            entries.append(
                DirEntry(
                    name=name,
                    is_dir=False,
                    url=url,
                    modified=modified,
                    size_bytes=size_bytes,
                )
            )

        return entries

    def _extract_row_metadata(self, link_el) -> tuple[Optional[datetime], Optional[int]]:
        """Extract modification time and size from the table row containing link_el.

        Apache table format (from real server):
          <tr>
            <td>[icon]</td>
            <td><a href="...">name</a></td>
            <td align="right">2026-03-19 21:52  </td>
            <td align="right"> 96K</td>
            <td>&nbsp;</td>
          </tr>
        """
        modified: Optional[datetime] = None
        size_bytes: Optional[int] = None

        try:
            parent_td = link_el.find_parent("td")
            if parent_td is None:
                return modified, size_bytes

            tr = parent_td.find_parent("tr")
            if tr is None:
                return modified, size_bytes

            tds = tr.find_all("td")
            for td in tds:
                text = td.get_text(strip=True)
                if not text or text == "-":
                    continue

                # Try date parse: "2026-03-19 21:52"
                if modified is None:
                    m = _APACHE_DATE_RE.search(text)
                    if m:
                        try:
                            modified = datetime.strptime(m.group(1).strip(), "%Y-%m-%d %H:%M").replace(
                                tzinfo=timezone.utc
                            )
                        except ValueError:
                            pass

                # Try size parse: "96K", "1.8G", "0"
                if size_bytes is None:
                    parsed = _parse_size(text)
                    if parsed is not None:
                        size_bytes = parsed

        except Exception:
            # Metadata extraction is best-effort; never break the main parse
            pass

        return modified, size_bytes


# ---------------------------------------------------------------------------
# Parser 2: Media ID filename parser
# ---------------------------------------------------------------------------


def parse_filename(filename: str) -> ParsedFilename | ParseError:
    """Parse a PBS Wisconsin asset filename into structured components.

    Strips the file extension, normalises to uppercase, then matches the
    Media ID grammar.  Returns a ``ParsedFilename`` on success or a
    ``ParseError`` if the filename does not match (e.g. freeform names like
    "INSIDE_WI_INTRO_20260409.srt").

    Variant-selection rule:
      - ``_REV<YYYYMMDD>`` sets ``revision_date``; ``variant_tag`` is None.
      - ``_<TAG>`` where TAG is in KNOWN_VARIANT_VOCAB sets ``variant_tag``.
      - Any other ``_<TAG>`` sets ``unknown_tag`` (NOT silently dropped).

    Args:
        filename: Asset filename, e.g. "6POL0101_REV20260319.srt" or
                  "2WLI0501HD_PLEDGE.mp4".

    Returns:
        ``ParsedFilename`` if the grammar matched, ``ParseError`` otherwise.
    """
    # Split extension
    if "." in filename:
        stem, ext = filename.rsplit(".", 1)
        file_type = "." + ext.lower()
    else:
        stem = filename
        file_type = ""

    stem_upper = stem.upper()

    m = _MEDIA_ID_RE.match(stem_upper)
    if m is None:
        # Grammar failed — check if the first 4 chars are a registered prefix.
        # If yes, return a nonstandard result rather than a ParseError so that
        # S2 can index the file under the correct show (e.g. 6POLS* shorts
        # produced by IWP editors map to Inside Wisconsin Politics).
        # ParseError is reserved for filenames whose prefix is genuinely unknown.
        candidate_prefix = stem_upper[:4] if len(stem_upper) >= 4 else ""
        table = _get_prefix_table()
        if candidate_prefix and candidate_prefix in table:
            entry = table[candidate_prefix]
            return ParsedFilename(
                stem=stem,
                file_type=file_type,
                media_id=None,
                prefix=candidate_prefix,
                season=None,
                episode=None,
                hd=None,
                revision_date=None,
                variant_tag=None,
                unknown_tag=None,
                prefix_category=entry["category"],
                show_name=entry["show"],
                nonstandard=True,
                nonstandard_remainder=stem[4:],  # preserve original case
            )
        return ParseError(
            stem=stem,
            file_type=file_type,
            reason=f"Filename stem {stem!r} does not match <PREFIX><SSEE>[HD][_REV<YYYYMMDD>][_TAG] grammar",
        )

    raw_prefix, raw_ss, raw_ee, hd_flag, rev_str, trailing_str = m.groups()

    prefix = raw_prefix
    season = int(raw_ss)
    episode = int(raw_ee)
    hd = hd_flag is not None

    # Build the bare media_id (prefix + SSEE, no HD, no suffixes)
    media_id = f"{prefix}{raw_ss}{raw_ee}"

    # Parse revision date
    revision_date: Optional[str] = None
    if rev_str:
        try:
            # Validate the date is real
            dt = datetime(int(rev_str[:4]), int(rev_str[4:6]), int(rev_str[6:8]))
            revision_date = dt.strftime("%Y-%m-%d")
        except ValueError:
            return ParseError(
                stem=stem,
                file_type=file_type,
                reason=f"Invalid revision date {rev_str!r} in filename {filename!r}",
            )

    # Classify trailing tag
    variant_tag: Optional[str] = None
    unknown_tag: Optional[str] = None

    if trailing_str:
        # trailing_str starts with "_", e.g. "_PLEDGE" or "_NoBugTest"
        tag = trailing_str.lstrip("_").upper()
        if tag in KNOWN_VARIANT_VOCAB:
            variant_tag = tag
        else:
            # Preserve original case from stem for the unknown tag
            unknown_tag = trailing_str.lstrip("_")

    # Prefix lookup
    table = _get_prefix_table()
    entry = table.get(prefix)
    if entry:
        prefix_category: str = entry["category"]
        show_name: Optional[str] = entry["show"]
    else:
        prefix_category = "unknown"
        show_name = None

    return ParsedFilename(
        stem=stem,
        file_type=file_type,
        media_id=media_id,
        prefix=prefix,
        season=season,
        episode=episode,
        hd=hd,
        revision_date=revision_date,
        variant_tag=variant_tag,
        unknown_tag=unknown_tag,
        prefix_category=prefix_category,
        show_name=show_name,
    )


# ---------------------------------------------------------------------------
# Variant-selection helper
# ---------------------------------------------------------------------------


@dataclass
class GroupSelectionResult:
    """Result for a single ``(media_id, variant_tag)`` group.

    Attributes:
        group_key  — ``(media_id, variant_tag)`` that identifies this group.
        primary    — the winning ParsedFilename, or None if the group is empty.
        superseded — older REV entries that lost to primary within this group.
        variants   — entries with ``unknown_tag`` (not part of the REV race);
                     S2 should route these to their own group rather than
                     treating them as superseded.
    """

    group_key: tuple[Optional[str], Optional[str]]
    primary: Optional[ParsedFilename]
    superseded: list[ParsedFilename]
    variants: list[ParsedFilename]


def select_primary(
    entries: list[ParsedFilename],
) -> list[GroupSelectionResult]:
    """Group a flat list of ParsedFilename objects by ``(media_id, variant_tag)``
    and select the winning REV within each group.

    This function enforces the grouping contract internally — callers supply a
    flat list and receive one :class:`GroupSelectionResult` per distinct
    ``(media_id, variant_tag)`` key.

    Grouping rules:
      * ``(media_id, variant_tag)`` is the group key.  ``media_id`` or
        ``variant_tag`` may be None (treated as a distinct value).
      * ``unknown_tag`` entries are NOT part of the REV race within any group;
        they are collected in ``variants`` for the caller to handle separately.

    REV winner rules (within each group):
      * If any candidates have a ``revision_date``, the one with the latest
        ISO date string wins (lexicographic comparison is chronological).
      * Remaining REV entries go to ``superseded``.
      * Candidates without a ``revision_date`` that lose to a REV entry are
        also placed in ``superseded``.
      * If no candidates have a ``revision_date``, the first candidate (by
        input order) is primary and the rest go to ``superseded``.

    Args:
        entries: Flat list of ParsedFilename objects.  May mix multiple
                 media_ids and/or variant_tags — the function groups them.

    Returns:
        One :class:`GroupSelectionResult` per distinct ``(media_id, variant_tag)``
        key, in the order the key first appears in *entries*.
    """
    if not entries:
        return []

    # Preserve insertion order of group keys
    groups: dict[tuple[Optional[str], Optional[str]], list[ParsedFilename]] = {}
    for entry in entries:
        key: tuple[Optional[str], Optional[str]] = (entry.media_id, entry.variant_tag)
        groups.setdefault(key, []).append(entry)

    results: list[GroupSelectionResult] = []
    for key, group_entries in groups.items():
        # Separate unknown-tag entries — they are bystanders in the REV race
        unknown_entries = [e for e in group_entries if e.unknown_tag is not None]
        candidates = [e for e in group_entries if e.unknown_tag is None]

        if not candidates:
            results.append(GroupSelectionResult(
                group_key=key,
                primary=None,
                superseded=[],
                variants=unknown_entries,
            ))
            continue

        if len(candidates) == 1:
            results.append(GroupSelectionResult(
                group_key=key,
                primary=candidates[0],
                superseded=[],
                variants=unknown_entries,
            ))
            continue

        # Multiple candidates: pick by revision_date (latest wins)
        with_rev = [e for e in candidates if e.revision_date is not None]
        without_rev = [e for e in candidates if e.revision_date is None]

        if not with_rev:
            # No revision dates; first entry wins (preserve input order)
            results.append(GroupSelectionResult(
                group_key=key,
                primary=candidates[0],
                superseded=candidates[1:],
                variants=unknown_entries,
            ))
            continue

        # Sort descending by revision_date (ISO lexicographic == chronological)
        with_rev_sorted = sorted(with_rev, key=lambda e: e.revision_date or "", reverse=True)
        results.append(GroupSelectionResult(
            group_key=key,
            primary=with_rev_sorted[0],
            superseded=with_rev_sorted[1:] + without_rev,
            variants=unknown_entries,
        ))

    return results


# ---------------------------------------------------------------------------
# Size parsing utility (shared with AutoindexParser internals)
# ---------------------------------------------------------------------------


def _parse_size(size_str: str) -> Optional[int]:
    """Parse Apache human-readable size string to bytes.

    Handles: "96K", "1.8G", "159M", "0", "  - " (dashes return None).
    """
    try:
        s = size_str.strip().upper()
        if not s or s in ("-", "  -  ", "--"):
            return None
        if s.endswith("K"):
            return int(float(s[:-1]) * 1024)
        if s.endswith("M"):
            return int(float(s[:-1]) * 1024 * 1024)
        if s.endswith("G"):
            return int(float(s[:-1]) * 1024 * 1024 * 1024)
        val = int(s)
        return val if val >= 0 else None
    except (ValueError, AttributeError):
        return None
