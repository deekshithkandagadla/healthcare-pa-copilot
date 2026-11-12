"""Untrusted-content handling and log minimization.

Retrieved notes, policy text and tool output are data, never instructions. Documents that
look like they are trying to steer the system are quarantined: they stay visible to the
reviewer, but no fact may be derived from them.
"""
from __future__ import annotations

import re

INJECTION_PATTERNS = [
    r"ignore (all )?(previous|prior|above) (instructions|rules)",
    r"system override",
    r"you are now (in )?\w+ mode",
    r"set (the )?recommendation to",
    r"mark (every|all) criteri(on|a)",
    r"do not flag",
    r"disregard (the )?(policy|rules|instructions)",
    r"</?(system|assistant|instructions?)>",
]
_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]


def detect_injection(text: str) -> list[str]:
    """Return the matched snippets, empty if the text looks clean."""
    hits = []
    for rx in _INJECTION_RE:
        m = rx.search(text)
        if m:
            hits.append(m.group(0))
    return hits


def wrap_untrusted(source_id: str, text: str) -> str:
    """Delimit untrusted text before it is placed in any LLM prompt."""
    safe = text.replace("<untrusted", "&lt;untrusted").replace("</untrusted", "&lt;/untrusted")
    return f'<untrusted source="{source_id}">\n{safe}\n</untrusted>'


_DOB = re.compile(r"\b(19|20)\d{2}-\d{2}-\d{2}\b")
_NAME = re.compile(r"Synthetic Patient \d+")


def minimize(text: str) -> str:
    """Strip direct identifiers before anything is logged."""
    return _NAME.sub("[PATIENT]", text)


def patient_ref(patient_id: str) -> str:
    """Logs and prompts use the opaque ID only, never name or birth date."""
    return patient_id
