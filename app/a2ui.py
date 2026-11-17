"""A2UI: declarative UI built from a fixed, validated component catalog.

Messages use the A2UI envelope (`surfaceUpdate` with a flat component list, then
`beginRendering`). The agent can only emit component types listed in CATALOG, every
component's props are validated by a Pydantic model, and the frontend refuses unknown
types. No code or HTML is ever generated.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.models import PAResult

# ---------------------------------------------------------------- catalog
class RecommendationBanner(BaseModel):
    recommendation: str
    policy_id: str | None
    requires_human_review: bool
    red_flag: bool = False
    summary: str
    summary_source: Literal["template", "llm"] = "template"
    disclaimer: str


class ChecklistItem(BaseModel):
    criterion_id: str
    description: str
    status: Literal["SUPPORTED", "NOT_SUPPORTED", "MISSING", "CONFLICTING"]
    observed: str | None = None
    threshold: str | None = None
    evidence_ids: list[str]
    rationale: str


class CriteriaChecklist(BaseModel):
    title: str
    items: list[ChecklistItem]


class EvidenceItem(BaseModel):
    evidence_id: str
    source_type: str
    quote: str | None
    date: str | None
    quarantined: bool = False


class EvidenceList(BaseModel):
    items: list[EvidenceItem]


class FormField(BaseModel):
    field_id: str                       # fact the reviewer is supplying
    label: str
    input: Literal["number", "boolean", "text"]
    min: float | None = None
    max: float | None = None
    required: bool = True


class MissingDocumentationForm(BaseModel):
    title: str
    case_id: str
    fields: list[FormField] = Field(min_length=1)
    requires_source_note: bool = True   # reviewer must say where the information came from
    submit_action: Literal["supply_data"] = "supply_data"


class AlertList(BaseModel):
    severity: Literal["info", "warning", "critical"]
    title: str
    items: list[str]


class ReviewerActions(BaseModel):
    case_id: str
    dispositions: list[Literal["APPROVE", "REQUEST_MORE_INFORMATION", "REFER_TO_PHYSICIAN_REVIEWER"]]
    allow_clarification: bool = True


CATALOG: dict[str, type[BaseModel]] = {m.__name__: m for m in (
    RecommendationBanner, CriteriaChecklist, EvidenceList, MissingDocumentationForm, AlertList, ReviewerActions)}


class A2UIError(ValueError):
    pass


def component(cid: str, type_: str, props: dict) -> dict:
    model = CATALOG.get(type_)
    if model is None:
        raise A2UIError(f"Component type {type_!r} is not in the catalog")
    try:
        model.model_validate(props)
    except ValidationError as e:
        raise A2UIError(f"{type_} props invalid: {e.errors()[0]['msg']}") from e
    return {"id": cid, "component": {type_: props}}


def validate_message(msg: dict) -> None:
    """Re-validate a surfaceUpdate (also used by tests and the API before sending)."""
    for c in msg["surfaceUpdate"]["components"]:
        (type_, props), = c["component"].items()
        component(c["id"], type_, props)


# ---------------------------------------------------------------- builders
FACT_FIELDS = {
    "pt_weeks_recent": ("number", 0, 52, "Weeks of physical therapy completed in the 6 months before the request"),
    "conservative_therapy_weeks": ("number", 0, 52, "Weeks of conservative treatment completed"),
    "pain_duration_weeks": ("number", 0, 520, "Weeks since symptom onset"),
    "radiculopathy": ("boolean", None, None, "Radicular symptoms or objective neurological findings documented?"),
    "prior_lumbar_mri_within_12m": ("boolean", None, None, "Lumbar MRI in the prior 12 months?"),
}


def build_surface(result: PAResult, policy: dict | None) -> list[dict]:
    sid = f"case-{result.case_id}"
    comps = [component("banner", "RecommendationBanner", {
        "recommendation": result.recommendation, "policy_id": result.applicable_policy_id,
        "requires_human_review": result.requires_human_review, "red_flag": result.red_flag_pathway,
        "summary": result.summary, "summary_source": result.summary_source,
        "disclaimer": result.disclaimer})]
    children = ["banner"]

    if result.security_flags:
        comps.append(component("security", "AlertList", {"severity": "critical", "title": "Untrusted content quarantined",
                                                         "items": result.security_flags}))
        children.append("security")
    applied = [x for x in result.exclusions if x.status == "APPLIES"]
    if applied:   # shown only when an exclusion applies; otherwise a one-line note under the checklist
        comps.append(component("exclusions", "AlertList", {
            "severity": "critical", "title": "Policy exclusion applies: physician review required",
            "items": [f"{x.exclusion_id}: {x.description} [{', '.join(x.evidence_ids)}]" for x in applied]}))
        children.append("exclusions")
    if result.contradictions or result.uncertainties:
        comps.append(component("uncertain", "AlertList", {"severity": "warning", "title": "Contradictions and uncertainties",
                                                          "items": result.contradictions + result.uncertainties}))
        children.append("uncertain")

    comps.append(component("checklist", "CriteriaChecklist", {
        "title": f"Criteria — {result.applicable_policy_id}" + (" (red-flag bypass)" if result.red_flag_pathway else ""),
        "items": [{"criterion_id": c.criterion_id, "description": c.description, "status": c.status,
                   "observed": None if c.observed is None else str(c.observed),
                   "threshold": None if c.threshold is None else str(c.threshold),
                   "evidence_ids": c.evidence_ids, "rationale": c.rationale} for c in result.criteria]}))
    children.append("checklist")
    if result.exclusions and not applied:
        comps.append(component("exclusions_checked", "AlertList", {
            "severity": "info", "title": "Policy exclusions checked",
            "items": ["None documented in the record (" + ", ".join(x.exclusion_id for x in result.exclusions)
                      + "). This means the record does not mention them, not that they are confirmed absent."]}))
        children.append("exclusions_checked")

    # Case-specific form: one field per criterion that is missing or conflicting.
    rules = {c["criterion_id"]: c["rule"]["fact"] for c in (policy or {}).get("criteria", [])}
    fields = []
    for c in result.criteria:
        fact = rules.get(c.criterion_id)
        if c.status in ("MISSING", "CONFLICTING") and fact in FACT_FIELDS and not result.red_flag_pathway:
            kind, lo, hi, label = FACT_FIELDS[fact]
            fields.append({"field_id": fact, "label": f"{c.criterion_id}: {label}", "input": kind, "min": lo, "max": hi})
    if fields:
        comps.append(component("missing_form", "MissingDocumentationForm", {
            "title": "Supply or correct documentation", "case_id": result.case_id, "fields": fields}))
        children.append("missing_form")

    comps.append(component("evidence", "EvidenceList", {"items": [
        {"evidence_id": e.evidence_id, "source_type": e.source_type, "quote": e.quote, "date": e.date,
         "quarantined": e.quarantined} for e in result.evidence.values()]}))
    children.append("evidence")
    comps.append(component("actions", "ReviewerActions", {
        "case_id": result.case_id,
        "dispositions": ["APPROVE", "REQUEST_MORE_INFORMATION", "REFER_TO_PHYSICIAN_REVIEWER"]
        if result.recommendation == "MEETS_CRITERIA" else ["REQUEST_MORE_INFORMATION", "REFER_TO_PHYSICIAN_REVIEWER"]}))
    children.append("actions")

    update = {"surfaceUpdate": {"surfaceId": sid, "components": comps,
                                "layout": {"root": "root", "children": children}}}
    validate_message(update)
    return [update, {"beginRendering": {"surfaceId": sid, "root": "root"}}]
