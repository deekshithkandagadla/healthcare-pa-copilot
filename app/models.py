"""Output contract. Every material conclusion must point at evidence that exists."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Recommendation = Literal[
    "MEETS_CRITERIA", "MORE_INFORMATION_NEEDED",
    "DOES_NOT_APPEAR_TO_MEET_CRITERIA", "SPECIALIST_REVIEW",
]
CriterionStatus = Literal["SUPPORTED", "NOT_SUPPORTED", "MISSING", "CONFLICTING"]


class Evidence(BaseModel):
    """One citable piece of source material with a stable ID."""
    evidence_id: str                       # e.g. NOTE-001#Subjective, Encounter/ENC-001-03, POL-MRI-LUMBAR-V3#S3.2
    source_type: Literal["note", "fhir", "policy", "exception", "auth_history", "reviewer"]
    source_id: str
    section: str | None = None
    quote: str | None = None               # verbatim text the claim rests on
    date: str | None = None
    quarantined: bool = False              # true for documents flagged as untrusted


class Fact(BaseModel):
    """A normalized clinical or administrative fact extracted by an agent."""
    name: str                              # e.g. pt_weeks_recent
    value: Any
    evidence_ids: list[str]
    agent: str
    detail: str = ""


class CriterionResult(BaseModel):
    criterion_id: str
    description: str
    status: CriterionStatus
    evidence_ids: list[str] = Field(default_factory=list)
    threshold: Any = None
    observed: Any = None
    rationale: str = ""
    policy_section: str | None = None


class ExclusionResult(BaseModel):
    exclusion_id: str
    description: str
    status: Literal["APPLIES", "NOT_DOCUMENTED"]
    evidence_ids: list[str] = Field(default_factory=list)
    policy_section: str | None = None


class AgentStatus(BaseModel):
    agent: str
    status: Literal["ok", "degraded", "failed", "skipped"]
    detail: str = ""


class PAResult(BaseModel):
    case_id: str
    recommendation: Recommendation
    applicable_policy_id: str | None
    policy_selection_reason: str = ""
    exceptions_applied: list[str] = Field(default_factory=list)
    red_flag_pathway: bool = False
    criteria: list[CriterionResult] = Field(default_factory=list)
    exclusions: list[ExclusionResult] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    security_flags: list[str] = Field(default_factory=list)
    requires_human_review: bool = True
    summary: str = ""
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    agent_status: list[AgentStatus] = Field(default_factory=list)
    summary_source: Literal["template", "llm"] = "template"
    llm_usage: dict | None = None
    disclaimer: str = ("Decision support only. This is not a coverage determination. "
                       "A licensed reviewer makes the final decision.")

    @model_validator(mode="after")
    def _grounded(self) -> "PAResult":
        known = set(self.evidence)
        for c in self.criteria:
            dangling = [e for e in c.evidence_ids if e not in known]
            if dangling:
                raise ValueError(f"{c.criterion_id} cites unknown evidence {dangling}")
            if c.status in ("SUPPORTED", "NOT_SUPPORTED", "CONFLICTING") and not c.evidence_ids:
                raise ValueError(f"{c.criterion_id} is {c.status} without evidence")
            if c.status in ("SUPPORTED", "NOT_SUPPORTED") and any(self.evidence[e].quarantined for e in c.evidence_ids):
                raise ValueError(f"{c.criterion_id} relies on quarantined evidence")
        for x in self.exclusions:
            if x.status == "APPLIES" and not any(e in known and not self.evidence[e].quarantined
                                                 for e in x.evidence_ids if not e.startswith("POL-")):
                raise ValueError(f"Exclusion {x.exclusion_id} applies without clinical evidence")
            if [e for e in x.evidence_ids if e not in known]:
                raise ValueError(f"{x.exclusion_id} cites unknown evidence")
        if any(x.status == "APPLIES" for x in self.exclusions) and self.recommendation == "MEETS_CRITERIA":
            raise ValueError("An applicable exclusion cannot produce MEETS_CRITERIA")
        if self.recommendation == "DOES_NOT_APPEAR_TO_MEET_CRITERIA":
            if not any(c.status == "NOT_SUPPORTED" for c in self.criteria):
                raise ValueError("A not-met recommendation needs at least one affirmatively unmet criterion")
            if not self.requires_human_review:
                raise ValueError("Not-met recommendations always require human review")
        if self.recommendation != "MEETS_CRITERIA" and not self.requires_human_review:
            raise ValueError("Only MEETS_CRITERIA may skip the human-review flag")
        return self
