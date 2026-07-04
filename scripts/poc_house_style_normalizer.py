#!/usr/bin/env python3
"""PoC: deterministic house-style normalizer for the SEO role.

Demonstrates the "rule engine (code) + rule data (config) + computed per-job data"
design from the phase-scripting discussion. Takes the SEO phase's LLM output and
GUARANTEES house-style compliance (down-style casing, char limits, no clickbait)
regardless of which model produced it — so gemma's over-capitalization and
Mistral's over-lowercasing converge to the same correct copy.

This is a proof of concept, not production code: single file, no deps beyond stdlib.

Run:  ./venv/bin/python scripts/poc_house_style_normalizer.py
"""

import re
import sys
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# RULE DATA (the editable config — in production this is house_style.yaml).
# Editors change THIS; the engine below never changes.
# ─────────────────────────────────────────────────────────────────────────────
RULES = {
    "char_limits": {"title": 60, "short_desc": 90, "long_desc": 350},
    # House style §1: viewer directives, promises, CTAs, hype, sales language.
    "forbidden_phrases": [
        "watch as", "watch how", "see how", "discover", "explore", "find out",
        "don't miss", "tune in", "join us", "will show", "will reveal",
        "amazing", "incredible", "extraordinary", "free", "we break down",
        "we analyze", "what viewers can expect",
    ],
    # First-person / promotional voice (house style: third person, describe don't sell).
    "first_person": ["we ", "our ", "we'll", "we've", "us "],
    # Stable institutional proper nouns + party/gov terms (down style keeps these capped).
    "proper_nouns_seed": [
        "Wisconsin", "Madison", "Eau Claire", "Democratic", "Republican",
        "Supreme Court", "Congress", "Senate", "Legislature", "Capitol",
        "Governor", "Lt. Governor", "Attorney General", "Here & Now",
        "Inside Wisconsin Politics", "Wisconsin Life", "University Place",
    ],
    # Acronyms preserved as-is (upper).
    "acronyms": ["SCOTUS", "PBS", "WI", "US", "U.S.", "DSA", "ACLU", "CPC", "GOP"],
    # Casing variants / abbreviations that aren't full proper nouns (data, not code).
    "casing_variants": {"dem": "Dem", "dems": "Dems", "gov": "Gov.", "sen": "Sen.",
                        "rep": "Rep."},
    # Common surname-position words to NOT promote to proper nouns on their own.
    "surname_stoplist": ["van", "der", "de", "la", "the"],
}


# ─────────────────────────────────────────────────────────────────────────────
# COMPUTED PER-JOB DATA: the episode's proper nouns, extracted (not maintained).
# Pull person-names from the analyst speaker table; no hand-curated per-show list.
# ─────────────────────────────────────────────────────────────────────────────
def extract_proper_nouns(analyst_md: str, stoplist: list[str]) -> list[str]:
    names: set[str] = set()
    # Analyst speaker table rows: | Name | Role | Context | First Appearance |
    for line in analyst_md.splitlines():
        m = re.match(r"\|\s*([A-Z][a-zA-Z.'-]+(?: [A-Z][a-zA-Z.'-]+){1,3})\s*\|", line)
        if m:
            cand = m.group(1).strip()
            if cand.lower() in ("speaker", "role/title", "name"):
                continue
            names.add(cand)
            # Also register the surname alone (last-name-only references are common),
            # skipping particles like "van"/"de".
            parts = cand.split()
            if len(parts) >= 2 and parts[-1].lower() not in stoplist and len(parts[-1]) > 3:
                names.add(parts[-1])
    return sorted(names)


# ─────────────────────────────────────────────────────────────────────────────
# RULE ENGINE (the stable code — rarely changes).
# ─────────────────────────────────────────────────────────────────────────────
def build_canonical(rules: dict, per_job: list[str]) -> dict[str, str]:
    """lowercased term -> canonical cased form (multi-word aware)."""
    canon: dict[str, str] = {}
    for term in rules["proper_nouns_seed"] + per_job:
        canon[term.lower()] = term
    for ac in rules["acronyms"]:
        canon[ac.lower()] = ac
    for lc, cased in rules.get("casing_variants", {}).items():
        canon[lc] = cased
    return canon


def to_down_style(text: str, canon: dict[str, str]) -> str:
    """Down style: lowercase everything, then restore first word + proper nouns/acronyms."""
    result = text.lower()
    # Restore canonical casing, longest terms first (so "supreme court" wins over "court").
    for lc, cased in sorted(canon.items(), key=lambda kv: -len(kv[0])):
        result = re.sub(rf"\b{re.escape(lc)}\b", cased, result)
    # Capitalize the first alphabetic character of the string.
    m = re.search(r"[A-Za-z]", result)
    if m and result[m.start()].islower():
        i = m.start()
        result = result[:i] + result[i].upper() + result[i + 1:]
    return result


def check(text: str, limit: int, rules: dict) -> list[str]:
    flags = []
    if len(text) > limit:
        flags.append(f"OVER LIMIT: {len(text)}/{limit} chars")
    low = text.lower()
    hits = [p for p in rules["forbidden_phrases"] if p in low]
    if hits:
        flags.append(f"clickbait/hype: {hits}")
    fp = [p.strip() for p in rules["first_person"] if p in low]
    if fp:
        flags.append(f"first-person voice: {fp}")
    return flags


# ─────────────────────────────────────────────────────────────────────────────
# Parse the recommended fields out of a seo_output.md
# ─────────────────────────────────────────────────────────────────────────────
def extract_recommended(seo_md: str) -> dict[str, str]:
    out = {}
    fields = {"title": r"### Title", "short_desc": r"### Short Description",
              "long_desc": r"### Long Description"}
    for key, header in fields.items():
        m = re.search(header + r".*?\*\*Recommended:\*\*\s*\n+([^\n]+)", seo_md, re.DOTALL)
        if m:
            out[key] = m.group(1).strip()
    return out


def main() -> int:
    base = Path("OUTPUT/eval")
    analyst_md = (base / "baseline_20" / "analyst_output.md").read_text()
    per_job = extract_proper_nouns(analyst_md, RULES["surname_stoplist"])
    canon = build_canonical(RULES, per_job)

    print(f"Computed per-job proper nouns (from analyst table): {per_job}\n")

    sources = {
        "gemma (over-capitalized)": base / "local_gemma-isolated" / "seo_output.md",
        "mistral (over-lowercased)": base / "local_mistral24b" / "seo_output.md",
        "baseline cloud (sonnet-5)": base / "baseline_20" / "seo_output.md",
    }
    for label, path in sources.items():
        if not path.exists():
            continue
        rec = extract_recommended(path.read_text())
        title = rec.get("title", "")
        print("═" * 78)
        print(f"{label}")
        print("─" * 78)
        raw_flags = check(title, RULES["char_limits"]["title"], RULES)
        norm = to_down_style(title, canon)
        norm_flags = check(norm, RULES["char_limits"]["title"], RULES)
        print(f"  RAW title : {title}")
        print(f"              flags: {raw_flags or 'none'}")
        print(f"  NORMALIZED: {norm}")
        print(f"              flags: {norm_flags or 'none'}")
        if rec.get("short_desc"):
            sd_flags = check(rec['short_desc'], RULES['char_limits']['short_desc'], RULES)
            print(f"  short desc flags (raw): {sd_flags or 'none'}")
    print("═" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
