"""Build the WhisperX initial_prompt for audio transcription jobs.

Whisper's initial_prompt conditions the decoder toward known spellings, but
only the tail of it fits the context window — ~500 characters is the
practical budget (matches the workspace transcription tooling). Priority
when trimming: speaker names > per-job context terms > glossary terms.
"""

from typing import Iterable, List, Optional

DEFAULT_PROMPT_BUDGET = 500
STATION_PREFIX = "PBS Wisconsin."


def build_initial_prompt(
    speakers: Optional[Iterable[str]] = None,
    context_terms: Optional[Iterable[str]] = None,
    glossary_terms: Optional[Iterable[str]] = None,
    budget: int = DEFAULT_PROMPT_BUDGET,
) -> str:
    """Compose an initial_prompt from speakers, context terms, and glossary.

    Terms are deduplicated case-insensitively across all three inputs
    (speakers win, then context terms). Key terms are appended whole until
    the budget would be exceeded — never truncated mid-term. Speaker names
    are always kept even if they alone exceed the budget.
    """
    seen: set = set()

    def clean(items: Optional[Iterable[str]]) -> List[str]:
        out = []
        for item in items or []:
            normalized = " ".join(str(item).split())
            if normalized and normalized.lower() not in seen:
                seen.add(normalized.lower())
                out.append(normalized)
        return out

    speaker_list = clean(speakers)
    terms = clean(context_terms) + clean(glossary_terms)

    prompt = STATION_PREFIX
    if speaker_list:
        prompt += f" Speakers: {', '.join(speaker_list)}."

    if terms and len(prompt) < budget:
        base = prompt + " Key terms: "
        length = len(base)
        kept: List[str] = []
        for term in terms:
            extra = len(term) + (2 if kept else 0)  # ", " separator
            if length + extra + 1 > budget:  # +1 for the closing "."
                break
            kept.append(term)
            length += extra
        if kept:
            prompt = base + ", ".join(kept) + "."

    return prompt
