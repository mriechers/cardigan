# PBS Wisconsin Transcript Glossary — Cardigan Project Layer

Authoritative spelling and naming reference for all transcript processing agents (analyst, formatter, copy-editor). When in doubt, this file is correct — do NOT "autocorrect" names to more common spellings.

> **Glossary architecture (base → project).** This is the **Cardigan project layer**. It
> **extends** the workspace **base** glossary at `automations/transcripts/glossary.md`
> (relative to the pbswi workspace root) with Cardigan-specific terms and overrides. On
> conflict, entries here **win** over the base. Put cross-project PBS Wisconsin proper
> nouns in the base; keep Cardigan-only names here.
>
> Build task (not yet wired — see `SPIKE-NOTES.md`): the pipeline should **merge base +
> this file** when constructing whisper `initial_prompt` for `/transcribe` and the formatter
> correction context. Today no code loads this file; it is consumed manually by the Claude
> Desktop editor workflow.

## How to Use This Glossary

- **Before formatting**: Scan the glossary for names and terms that appear in the transcript
- **When a name has multiple common spellings**: Use the spelling listed here, not the one the model "prefers"
- **After editor review**: New corrections should be added to the appropriate section

---

## Place Names

| Correct | Common Misspellings |
|---------|-------------------|
| Manitowoc | Manitowac, Mannitowoc |
| Waukesha | Wakesha, Walkeesha |
| Sheboygan | Sheyboygan, Sheboygen |
| Oconomowoc | Oconowomoc, Oconomowac |
| Fond du Lac | Fond de Lac, Fondalac |
| Wauwatosa | Wawatosa, Wauwautosa |
| Menominee | Menomonie, Menomonee |
| Ashwaubenon | Ashwaubanon |
| Wausau | Wasau |
| Eau Claire | Eau Clair |
| La Crosse | Lacrosse, La Cross |
| Kenosha | Kanosha |
| Oshkosh | Oshcosh |
| Appleton | (rarely misspelled) |
| Green Bay | (rarely misspelled) |

## Political Figures (Current/Recent)

| Correct | Role | Common Misspellings |
|---------|------|-------------------|
| Janet Protasiewicz | Supreme Court Justice | Protasewicz, Protasavich |
| Brian Hagedorn | Former Supreme Court Justice | Hagadorn, Hagedoorn |
| Michael Gableman | Former Supreme Court Justice | Gabbleman, Gabellman |
| Jim Troupis | Attorney | Troupes, Troopis |
| Brad Schimel | Former Atty. Gen. | Schimmel, Shimel |
| Jill Karofsky | Supreme Court Justice | Karovsky, Karofski |
| Rebecca Dallet | Supreme Court Justice | Dallett, Dalet |
| David Prosser | Former Supreme Court Justice | Prossar |
| Tony Evers | Governor | (rarely misspelled) |
| Robin Vos | Assembly Speaker | (rarely misspelled) |
| Josh Kaul | Attorney General | Kohl, Call |
| Dan Kelly | Former Supreme Court Justice | (rarely misspelled) |
| Eric Toney | Politician | Tony, Toni |
| Sean Duffy | Former U.S. Rep. / Secretary of Transportation | Shawn Duffy |

## Legal Cases

| Correct | Common Misspellings |
|---------|-------------------|
| Kaul v. Urmanski | Kohl v. Urmanski, Call vs Urmanski |
| Clarke v. WEC | Clark v. WEC |
| Trump v. Biden (WI) | (rarely misspelled) |

## Institutions

| Correct | Abbreviation | Notes |
|---------|-------------|-------|
| Wisconsin Public Radio | WPR | |
| PBS Wisconsin | | Formerly WPT/Wisconsin Public Television |
| UW-Madison | | Not "University of Wisconsin Madison" in running text |
| Marquette Law School | | Often "Marquette poll" |
| Wisconsin Elections Commission | WEC | |
| Department of Justice | DOJ | Wisconsin state DOJ, not federal |

## PBS Wisconsin Programs & Hosts

| Program | Host/Anchor | Regular Panelists |
|---------|------------|-------------------|
| Inside Wisconsin Politics | Shawn Johnson | Zac Schultz, Rich Kremer, Anya van Wagtendonk |
| Here & Now | Frederica Freyberg | |
| Wisconsin Life | | (various segment producers) |
| University Place | | (various lecturers) |

## Wisconsin-Specific Terms

| Term | Notes |
|------|-------|
| Capitol | The building/district in Madison (not "capital" unless referring to money) |
| Act 10 | 2011 law restricting public employee unions |
| Tavern League | Tavern League of Wisconsin (lobbying group) |
| Dells | Wisconsin Dells (tourism area) |
| Up North | Colloquial for northern Wisconsin |
| FIBs | Colloquial for Illinois visitors (use cautiously) |

## Editor Corrections

Names and terms corrected during human editorial review. These represent cases where the model consistently gets the wrong spelling or the caption source is unreliable.

| Correct | Model Tendency | Context |
|---------|---------------|---------|
| Sean Duffy | Shawn Duffy | Former WI congressman; model confuses with IWP host Shawn Johnson |
| Erica Ayisi | Erika Aisi | PBS Wisconsin reporter (Here & Now); whisperX phonetic miss |

## Name Disambiguation

Names that appear in PBS Wisconsin transcripts where the model may confuse similar-sounding or similar-looking names. Pay special attention when both names could plausibly appear.

| Name | Role | Do NOT confuse with |
|------|------|-------------------|
| Shawn Johnson | IWP host (PBS Wisconsin) | Sean Duffy (politician) |
| Sean Duffy | Former U.S. Rep. / Sec. of Transportation | Shawn Johnson (IWP host) |
| Anya van Wagtendonk | IWP panelist | — |
