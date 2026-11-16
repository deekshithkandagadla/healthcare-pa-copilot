"""Quote-anchored fact extraction from clinical notes.

Every extracted statement carries the exact quote and its character offset, so its evidence
ID (NOTE-xxx#Section@offset) can be re-verified against the source in code. The default
extractor is rule-based and deterministic; `app/llm.py` adds an optional LLM extractor whose
output must pass the same quote verification.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

NEG = re.compile(r"\b(denies|no|not|without|negative for)\b", re.IGNORECASE)


@dataclass
class Statement:
    kind: str           # pain_weeks | pt_weeks_claim | pt_mentioned_no_duration | pt_not_started | pt_nonattendance
                        # nsaid_weeks | home_exercise_weeks | radic_pos | radic_neg | red_flag | no_prior_imaging | prior_mri
    value: object
    note_id: str
    section: str
    quote: str
    offset: int         # offset of the quote within the full note text

    @property
    def evidence_id(self) -> str:
        return f"{self.note_id}#{self.section}@{self.offset}"


@dataclass
class ParsedNote:
    note_id: str
    date: str
    author: str
    source: str
    text: str
    sections: list[tuple[str, int, int]]   # (name, start, end) offsets in text

    def section_at(self, pos: int) -> str:
        for name, s, e in self.sections:
            if s <= pos < e:
                return name
        return "Body"


def parse_note(note: dict) -> ParsedNote:
    text = note["text"]
    sections, matches = [], list(re.finditer(r"^## (.+)$", text, re.MULTILINE))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((m.group(1).strip(), m.start(), end))
    return ParsedNote(note["note_id"], note["date"], note.get("author") or "", note.get("source", "ehr"), text, sections)


_RULES: list[tuple[str, str, object]] = [
    # (kind, regex, value or group index)
    ("pain_weeks", r"(?:low )?back pain (?:for|x) (\d+) weeks", 1),
    ("pain_days", r"(?:back pain (?:for|x)|pain that began|began) (\d+) days", 1),
    ("pt_weeks_claim", r"completed (\d+) weeks of physical\s+therapy", 1),
    ("pt_nonattendance", r"attended (\d+) of (\d+) scheduled physical therapy sessions", None),
    ("pt_nonattendance", r"discharged for non-attendance", None),
    ("pt_not_started", r"has not started physical therapy", None),
    ("pt_mentioned_no_duration", r"has been doing physical\s+therapy", None),
    ("nsaid_weeks", r"(?:naproxen|ibuprofen|nsaids?) for (\d+) weeks", 1),
    ("home_exercise_weeks", r"home exercise program for (\d+) weeks", 1),
    ("radic_pos", r"radiating (?:down the \w+ leg )?below the (?:\w+ )?knee", True),
    ("radic_pos", r"positive straight leg raise", True),
    ("radic_pos", r"decreased sensation in the \w+ l\d dermatome", True),
    ("radic_pos", r"(?:reduced|diminished) \w+ (?:ankle|patellar) reflex", True),
    ("radic_pos", r"(?:bilateral |left |right )?leg weakness", True),
    ("radic_neg", r"negative straight leg raise", False),
    ("radic_neg", r"neurologically intact", False),
    ("radic_neg", r"no leg (?:pain|symptoms)", False),
    ("red_flag", r"saddle anesthesia", "saddle anesthesia"),
    ("red_flag", r"urinary retention", "urinary retention"),
    ("red_flag", r"cauda equina", "cauda equina"),
    ("red_flag", r"reduced perianal sensation", "perianal sensory loss"),
    ("mri_unsafe_device", r"(?:non-mri-conditional|mri-unsafe|non-mri-compatible) (?:cardiac )?(?:pacemaker|defibrillator|implant|device|neurostimulator)", True),
    ("device_cleared", r"(?:device|pacemaker) clearance (?:obtained|on file|documented)", True),
    ("medicolegal_only", r"(?:requested|needed|ordered) (?:solely |only )?for (?:legal|litigation|workers' compensation) (?:purposes|documentation)", True),
    ("no_prior_imaging", r"no prior imaging", False),
    ("prior_mri", r"prior (?:lumbar )?mri", True),
]
# Notes wrap lines anywhere, so a space in a pattern matches any run of whitespace.
_COMPILED = [(k, re.compile(rx.replace(" ", r"\s+"), re.IGNORECASE), v) for k, rx, v in _RULES]


def _sentence_bounds(text: str, pos: int, end_pos: int) -> tuple[int, int]:
    """Sentence around a match. Notes wrap lines, so single newlines are not boundaries."""
    starts = [text.rfind(". ", 0, pos) + 2, text.rfind(".\n", 0, pos) + 2, text.rfind("\n\n", 0, pos) + 2]
    h = text.rfind("## ", 0, pos)
    if h != -1:
        starts.append(text.find("\n", h) + 1)
    start = max(s for s in starts if s <= pos)
    ends = [i + 1 for i in (text.find(".", end_pos),) if i != -1] + \
           [i for i in (text.find("\n\n", end_pos),) if i != -1]
    return start, (min(ends) if ends else len(text))


def extract_statements(note: ParsedNote) -> list[Statement]:
    out: list[Statement] = []
    body_start = note.sections[0][1] if note.sections else 0
    for kind, rx, val in _COMPILED:
        for m in rx.finditer(note.text, body_start):
            s, e = _sentence_bounds(note.text, m.start(), m.end())
            prefix = note.text[s:m.start()]
            # Negated findings ("Denies saddle anesthesia", "no prior MRI") do not count.
            if kind in ("red_flag", "prior_mri", "radic_pos", "mri_unsafe_device", "device_cleared",
                        "medicolegal_only") and NEG.search(prefix):
                continue
            if isinstance(val, int) and not isinstance(val, bool):
                value = int(m.group(val))
            elif kind == "pt_nonattendance" and m.lastindex:
                value = {"attended": int(m.group(1)), "scheduled": int(m.group(2))}
            else:
                value = val
            if kind == "pain_days":
                kind, value = "pain_weeks", round(value / 7, 1)
            quote = note.text[s:e].strip()
            offset = note.text.find(quote, s)
            out.append(Statement(kind, value, note.note_id, note.section_at(m.start()), quote, offset))
    return out


def verify_quote(note_text: str, quote: str, offset: int) -> bool:
    """Citation check: the quote must appear verbatim at the recorded offset."""
    return offset >= 0 and note_text[offset:offset + len(quote)] == quote
