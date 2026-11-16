"""Criteria and quality agent.

Maps cited facts to the policy's requirements with fixed rules, then checks the result:
schema validity, citations that resolve to real source text, and no conclusion without
evidence. Missing evidence is reported as MISSING, never as NOT_SUPPORTED.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass, field

from app.agents.extract import verify_quote
from app.models import CriterionResult, Evidence, ExclusionResult, Fact
from app.retrieval.index import PolicyIndex

AGENT = "criteria_quality"
OPS = {">=": operator.ge, ">": operator.gt, "==": operator.eq, "<=": operator.le}


@dataclass
class CriteriaOutput:
    results: list[CriterionResult] = field(default_factory=list)
    exclusions: list[ExclusionResult] = field(default_factory=list)
    red_flag: bool = False
    red_flag_evidence: list[str] = field(default_factory=list)


def evaluate(policy: dict, exceptions: list[dict], facts: dict[str, Fact],
             conflicts: dict[str, dict]) -> CriteriaOutput:
    out = CriteriaOutput()
    pid = policy["policy_id"]
    if facts.get("red_flag") and facts["red_flag"].value:
        out.red_flag = True
        out.red_flag_evidence = facts["red_flag"].evidence_ids + [f"{pid}#{policy['red_flag_section']}"]

    # Exclusions apply only on affirmative, cited evidence; absence is NOT_DOCUMENTED, never "applies".
    for x in policy.get("exclusions", []):
        f = facts.get(x["rule"]["fact"])
        applies = f is not None and OPS[x["rule"]["op"]](f.value, x["rule"]["value"])
        out.exclusions.append(ExclusionResult(
            exclusion_id=x["exclusion_id"], description=x["description"], policy_section=x["section"],
            status="APPLIES" if applies else "NOT_DOCUMENTED",
            evidence_ids=(f.evidence_ids + [f"{pid}#{x['section']}"]) if applies else []))

    overrides = {e["criterion_id"]: e for e in exceptions}
    for cr in policy["criteria"]:
        rule = cr["rule"]
        threshold = rule["value"]
        pol_ev = [f"{pid}#{cr['section']}"]
        note = ""
        if cr["criterion_id"] in overrides:
            ex = overrides[cr["criterion_id"]]
            threshold = ex["override"]["value"]
            pol_ev.append(f"{ex['exception_id']}#{ex['section']}")
            note = f"Threshold {rule['value']} -> {threshold} under {ex['exception_id']}. "
        name = rule["fact"]
        base = dict(criterion_id=cr["criterion_id"], description=cr["description"], threshold=threshold,
                    policy_section=cr["section"])
        if name in conflicts:
            c = conflicts[name]
            out.results.append(CriterionResult(**base, status="CONFLICTING", evidence_ids=c["evidence_ids"] + pol_ev,
                                               rationale=note + c["detail"]))
        elif name in facts:
            f = facts[name]
            ok = OPS[rule["op"]](f.value, threshold)
            out.results.append(CriterionResult(
                **base, status="SUPPORTED" if ok else "NOT_SUPPORTED", observed=f.value,
                evidence_ids=f.evidence_ids + pol_ev,
                rationale=note + f"Observed {f.value} vs required {rule['op']} {threshold}. {f.detail}".strip()))
        else:
            out.results.append(CriterionResult(**base, status="MISSING", evidence_ids=pol_ev,
                                               rationale=note + "No documentation found in the record."))
    return out


def verify_citations(evidence: dict[str, Evidence], notes: dict[str, str], fhir_ids: set[str],
                     index: PolicyIndex) -> list[str]:
    """Return a list of problems; empty means every citation resolves to its source."""
    problems = []
    for eid, ev in evidence.items():
        if ev.source_type == "note" and not ev.quarantined:
            text = notes.get(ev.source_id)
            offset = int(eid.rsplit("@", 1)[1]) if "@" in eid else -1
            if text is None or not verify_quote(text, ev.quote or "", offset):
                problems.append(f"{eid}: quote not found at the cited location")
        elif ev.source_type == "fhir" and eid not in fhir_ids:
            problems.append(f"{eid}: resource was not returned by the FHIR tools")
        elif ev.source_type in ("policy",) and index.chunk_text(eid) is None:
            problems.append(f"{eid}: policy section does not exist")
        elif ev.source_type == "exception" and index.chunk_text(eid) is None:
            problems.append(f"{eid}: exception not in register")
    return problems
