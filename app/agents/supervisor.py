"""Supervisor: routes work, holds shared case state, limits retries, detects disagreement
between agents, and composes the final, validated result.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app import config
from app.agents.a2a_client import A2AClient, A2AUnavailable
from app.agents.clinical import ClinicalEvidenceAgent, ClinicalOutput
from app.agents.coverage import run_coverage
from app.agents.criteria import evaluate, verify_citations
from app.agents.policy import PolicyAgent
from app.agents.tools import McpTools, ToolBudgetExceeded, ToolError
from app.agui import EventEmitter, Sink
from app.models import AgentStatus, CriterionResult, Evidence, Fact, PAResult
from app.retrieval.index import PolicyIndex, get_index

REVIEWABLE_FACTS = {
    "pt_weeks_recent": int, "conservative_therapy_weeks": int, "pain_duration_weeks": float,
    "radiculopathy": bool, "prior_lumbar_mri_within_12m": bool,
}


class Supervisor:
    def __init__(self, mcp_target: Any, a2a: A2AClient | None = None, index: PolicyIndex | None = None,
                 sink: Sink | None = None):
        self.mcp_target = mcp_target
        self.a2a = a2a
        self.index = index or get_index()
        self.sink = sink

    # ------------------------------------------------------------------ helpers
    async def _with_retry(self, name: str, fn, emitter: EventEmitter):
        last = None
        for attempt in range(config.MAX_AGENT_RETRIES + 1):
            try:
                return await fn()
            except ToolBudgetExceeded:
                raise
            except (ToolError, asyncio.TimeoutError, KeyError) as e:
                last = e
                await emitter.custom("agent_retry", {"agent": name, "attempt": attempt + 1, "error": str(e)})
        raise ToolError(f"{name} failed after {config.MAX_AGENT_RETRIES + 1} attempts: {last}")

    async def _coverage(self, case: dict, tools: McpTools, emitter: EventEmitter, status: list[AgentStatus]) -> dict | None:
        args = {"patient_id": case["patient_id"], "service_code": case["service_code"], "service_date": case["service_date"]}
        if self.a2a:
            tid = await emitter.tool_start("a2a:coverage-eligibility-agent/message/send", args, "supervisor")
            try:
                res = await self.a2a.send(args, context_id=case["case_id"])
                await emitter.tool_result(tid, {k: v for k, v in res.items() if k != "evidence"})
                status.append(AgentStatus(agent="coverage_eligibility", status="ok", detail="remote A2A service"))
                return res
            except A2AUnavailable as e:
                await emitter.tool_result(tid, f"A2A unavailable: {e}", ok=False)
                await emitter.custom("fallback", {"agent": "coverage_eligibility",
                                                  "from": "remote A2A service", "to": "in-process agent via MCP",
                                                  "reason": str(e)})
        try:
            res = await self._with_retry("coverage_eligibility", lambda: run_coverage(
                tools, case["patient_id"], case["service_code"], case["service_date"]), emitter)
            status.append(AgentStatus(agent="coverage_eligibility", status="degraded" if self.a2a else "ok",
                                      detail="in-process fallback" if self.a2a else "in-process"))
            return res
        except ToolError as e:
            status.append(AgentStatus(agent="coverage_eligibility", status="failed", detail=str(e)))
            await emitter.run_error(f"Coverage agent unavailable: {e}", code="COVERAGE_UNAVAILABLE")
            return None

    # ------------------------------------------------------------------ main
    async def run(self, case: dict, reviewer_inputs: list[dict] | None = None,
                  emitter: EventEmitter | None = None) -> tuple[PAResult, EventEmitter, list[dict]]:
        emitter = emitter or EventEmitter(thread_id=case["case_id"], sink=self.sink)
        status: list[AgentStatus] = []
        uncertainties: list[str] = []
        contradictions: list[str] = []
        await emitter.run_started()
        await emitter.state_snapshot({"case_id": case["case_id"], "stage": "investigating",
                                      "agents": {a: "pending" for a in ("clinical_evidence", "coverage_eligibility",
                                                                        "policy_rag", "criteria_quality")}})
        tools = McpTools(self.mcp_target, emitter)
        try:
            async with tools:
                await emitter.custom("mcp_tools_discovered", await tools.list_tools())
                result = await self._investigate(case, reviewer_inputs or [], tools, emitter, status,
                                                 uncertainties, contradictions)
        except Exception as e:  # MCP server down, budget exhausted, unexpected error -> safe fallback
            await emitter.run_error(f"Investigation could not complete: {type(e).__name__}: {e}", code="SAFE_FALLBACK")
            result = PAResult(case_id=case["case_id"], recommendation="MORE_INFORMATION_NEEDED",
                              applicable_policy_id=None, requires_human_review=True,
                              uncertainties=[f"Automated investigation failed ({type(e).__name__}); "
                                             "no findings were produced. Manual review required."],
                              agent_status=status + [AgentStatus(agent="supervisor", status="failed", detail=str(e))],
                              summary="The system could not complete the investigation and made no clinical findings.")
        await emitter.state_snapshot({"case_id": case["case_id"], "stage": "ready_for_review",
                                      "recommendation": result.recommendation})
        await emitter.run_finished({"recommendation": result.recommendation})
        return result, emitter, tools.calls

    async def _investigate(self, case, reviewer_inputs, tools, emitter, status, uncertainties, contradictions) -> PAResult:
        # 1) clinical + coverage in parallel
        await emitter.step("clinical_evidence")
        await emitter.step("coverage_eligibility")
        clinical_task = self._with_retry("clinical_evidence", lambda: ClinicalEvidenceAgent(tools).run(case), emitter)
        coverage_task = self._coverage(case, tools, emitter, status)
        clinical_res, coverage = await asyncio.gather(clinical_task, coverage_task, return_exceptions=True)
        if isinstance(clinical_res, BaseException):
            raise clinical_res
        clinical: ClinicalOutput = clinical_res
        if isinstance(coverage, BaseException):
            coverage = None
        status.append(AgentStatus(agent="clinical_evidence", status="ok",
                                  detail=f"{len(clinical.facts)} facts, {len(clinical.gaps)} gaps"))
        await emitter.step("clinical_evidence", started=False)
        await emitter.step("coverage_eligibility", started=False)
        await emitter.state_delta([{"op": "replace", "path": "/agents/clinical_evidence", "value": "done"},
                                   {"op": "replace", "path": "/agents/coverage_eligibility",
                                    "value": "done" if coverage else "failed"}])

        evidence: dict[str, Evidence] = dict(clinical.evidence)
        facts: dict[str, Fact] = dict(clinical.facts)
        conflicts: dict[str, dict] = dict(clinical.conflicts)
        fhir_ids = set(clinical.fhir_ids)
        plan = None

        # 2) merge coverage, detect cross-agent disagreement on prior imaging
        if coverage:
            for e in coverage["evidence"]:
                evidence[e["evidence_id"]] = Evidence(**e)
                if e["source_type"] == "fhir":
                    fhir_ids.add(e["evidence_id"])
            plan = coverage.get("plan")
            if any(s.agent == "coverage_eligibility" and s.status == "degraded" for s in status):
                uncertainties.append("Remote coverage agent unavailable; coverage verified through the "
                                     "in-process fallback")
            if not coverage["eligible"]:
                uncertainties.append(f"Eligibility not confirmed: {coverage.get('eligibility_reason')}")
            cov_ids = [e["evidence_id"] for e in coverage["evidence"] if e["source_type"] == "auth_history"]
            note_view = clinical.facts.get("prior_imaging_per_notes")
            cov_view = coverage["prior_lumbar_mri_within_12m"]
            if note_view is not None and note_view.value != cov_view:
                # Re-verify once before escalating (rules out a transient or stale read).
                recheck = await run_coverage(tools, case["patient_id"], case["service_code"], case["service_date"])
                resolved = recheck["prior_lumbar_mri_within_12m"] == note_view.value
                detail = (f"Clinical note says prior imaging = {note_view.value}; authorization history "
                          f"({', '.join(coverage['prior_auth_ids']) or 'none'}) says prior lumbar MRI within "
                          f"12 months = {cov_view}")
                await emitter.custom("agent_disagreement", {
                    "fact": "prior_lumbar_mri_within_12m", "agents": ["clinical_evidence", "coverage_eligibility"],
                    "detail": detail, "rechecked": True, "resolution": "resolved" if resolved else "escalated"})
                if not resolved:
                    conflicts["prior_lumbar_mri_within_12m"] = {"detail": detail,
                                                                "evidence_ids": note_view.evidence_ids + cov_ids}
                    contradictions.append(detail)
            if "prior_lumbar_mri_within_12m" not in conflicts:
                facts["prior_lumbar_mri_within_12m"] = Fact(
                    name="prior_lumbar_mri_within_12m", value=cov_view, agent="coverage_eligibility",
                    evidence_ids=cov_ids + (note_view.evidence_ids if note_view else []))
        else:
            uncertainties.append("Coverage, eligibility and authorization history could not be verified "
                                 "(coverage agent and fallback unavailable)")
            if "prior_imaging_per_notes" in clinical.facts:
                n = clinical.facts["prior_imaging_per_notes"]
                facts["prior_lumbar_mri_within_12m"] = Fact(name="prior_lumbar_mri_within_12m", value=n.value,
                                                            evidence_ids=n.evidence_ids, agent="clinical_evidence",
                                                            detail="From notes only; claims history unverified")

        for fname, c in clinical.conflicts.items():
            if fname == "conservative_therapy_weeks":
                continue  # derived from pt_weeks_recent; reported once
            contradictions.append(f"{fname}: {c['detail']}")

        # 3) human-supplied corrections (from a reviewer action) take precedence, with audit IDs
        gaps = list(clinical.gaps)
        for i, ri in enumerate(reviewer_inputs, start=1):
            fname = ri["fact"]
            if fname not in REVIEWABLE_FACTS:
                continue
            eid = f"REVIEW/{ri.get('review_id', i)}"
            evidence[eid] = Evidence(evidence_id=eid, source_type="reviewer", source_id=ri.get("reviewer", "reviewer"),
                                     quote=ri.get("note", ""), date=ri.get("timestamp"))
            facts[fname] = Fact(name=fname, value=REVIEWABLE_FACTS[fname](ri["value"]), evidence_ids=[eid],
                                agent="human_reviewer", detail=f"Supplied by reviewer: {ri.get('note', '')}")
            conflicts.pop(fname, None)
            if fname == "pt_weeks_recent":
                conflicts.pop("conservative_therapy_weeks", None)
                if "conservative_therapy_weeks" not in facts or facts["conservative_therapy_weeks"].value < facts[fname].value:
                    facts["conservative_therapy_weeks"] = facts[fname].model_copy(update={"name": "conservative_therapy_weeks"})
                gaps = [g for g in gaps if "physical therapy" not in g.lower()]
            await emitter.custom("reviewer_input_applied", {"fact": fname, "evidence_id": eid})

        # 4) policy
        await emitter.step("policy_rag")
        pol = await self._with_retry("policy_rag", lambda: PolicyAgent(self.index, tools).run(case, plan), emitter)
        evidence.update(pol.evidence)
        uncertainties += pol.uncertainties
        status.append(AgentStatus(agent="policy_rag", status="ok" if pol.policy else "degraded", detail=pol.reason[:200]))
        await emitter.custom("retrieval", {"hits": [h["chunk_id"] for h in pol.retrieval.get("hits", [])],
                                           "excluded_better_ranked": pol.retrieval.get("excluded_better_ranked", [])})
        await emitter.step("policy_rag", started=False)
        if not pol.policy:
            return self._finish(case, "MORE_INFORMATION_NEEDED", None, pol.reason, [], [], False, [], gaps,
                                uncertainties, contradictions, clinical, evidence, status)

        # 5) criteria + quality
        await emitter.step("criteria_quality")
        crit = evaluate(pol.policy, pol.exceptions, facts, conflicts)
        problems = verify_citations(evidence, clinical.notes, fhir_ids, self.index)
        if problems:
            uncertainties += [f"Citation check failed: {p}" for p in problems]
        status.append(AgentStatus(agent="criteria_quality", status="ok" if not problems else "degraded",
                                  detail=f"{len(crit.results)} criteria evaluated; {len(problems)} citation problems"))
        await emitter.step("criteria_quality", started=False)

        rec = self._decide(crit, coverage, contradictions, problems)
        if crit.red_flag:
            await emitter.custom("red_flag_pathway", {"evidence_ids": crit.red_flag_evidence})
        exc_ids = [e["exception_id"] for e in pol.exceptions]
        reason = pol.reason + (f" Exceptions applied: {exc_ids}." if exc_ids else "") + \
            (" Exceptions considered and not applied: " + "; ".join(pol.exception_notes) + "." if pol.exception_notes else "")
        return self._finish(case, rec, pol.policy["policy_id"], reason, exc_ids, crit.results, crit.red_flag,
                            crit.red_flag_evidence, gaps, uncertainties, contradictions, clinical, evidence, status)

    @staticmethod
    def _decide(crit, coverage, contradictions, problems) -> str:
        statuses = [r.status for r in crit.results]
        if problems:
            return "SPECIALIST_REVIEW"
        if crit.red_flag:
            return "MEETS_CRITERIA"
        if coverage is None or not coverage.get("eligible"):
            return "MORE_INFORMATION_NEEDED"
        if "CONFLICTING" in statuses:
            return "SPECIALIST_REVIEW"
        if all(s == "SUPPORTED" for s in statuses):
            return "MEETS_CRITERIA"
        if "NOT_SUPPORTED" in statuses:
            return "DOES_NOT_APPEAR_TO_MEET_CRITERIA"
        return "MORE_INFORMATION_NEEDED"

    def _finish(self, case, rec, policy_id, reason, exc_ids, criteria: list[CriterionResult], red_flag, rf_ev,
                gaps, uncertainties, contradictions, clinical: ClinicalOutput, evidence, status) -> PAResult:
        missing = [f"{r.criterion_id}: {r.description}" for r in criteria if r.status == "MISSING"] + gaps
        missing = [] if red_flag else list(dict.fromkeys(missing))  # standard criteria bypassed
        uncertainties = list(dict.fromkeys(uncertainties + clinical.ambiguities))
        human = not (rec == "MEETS_CRITERIA" and not uncertainties and not clinical.security_flags and not contradictions)
        summary = self._summary(case, rec, policy_id, criteria, red_flag, missing, contradictions, clinical)
        try:
            return PAResult(case_id=case["case_id"], recommendation=rec, applicable_policy_id=policy_id,
                            policy_selection_reason=reason, exceptions_applied=exc_ids, red_flag_pathway=red_flag,
                            criteria=criteria, missing_information=missing, uncertainties=uncertainties,
                            contradictions=contradictions, security_flags=clinical.security_flags,
                            requires_human_review=human, summary=summary, evidence=evidence, agent_status=status)
        except ValidationError as e:
            # The quality gate rejected the draft: never return an ungrounded conclusion.
            return PAResult(case_id=case["case_id"], recommendation="SPECIALIST_REVIEW",
                            applicable_policy_id=policy_id, policy_selection_reason=reason,
                            uncertainties=uncertainties + [f"Output failed validation and was withheld: {e.errors()[0]['msg']}"],
                            security_flags=clinical.security_flags, requires_human_review=True,
                            summary="Draft findings failed the grounding check; routed to specialist review.",
                            evidence=evidence, agent_status=status + [AgentStatus(agent="criteria_quality", status="failed",
                                                                                     detail="schema/grounding validation")])

    @staticmethod
    def _summary(case, rec, policy_id, criteria, red_flag, missing, contradictions, clinical) -> str:
        parts = [f"{case['case_id']}: {rec.replace('_', ' ').lower()} under {policy_id}."]
        if red_flag:
            parts.append("Red-flag findings documented; policy allows imaging without the standard criteria. Expedite.")
        for r in criteria:
            parts.append(f"{r.criterion_id} {r.status.lower().replace('_', ' ')}"
                         + (f" (observed {r.observed}, required {r.threshold})" if r.observed is not None else "") + ".")
        if missing:
            parts.append("Missing: " + "; ".join(missing) + ".")
        if contradictions:
            parts.append("Contradictions: " + "; ".join(contradictions) + ".")
        if clinical.security_flags:
            parts.append(f"{len(clinical.security_flags)} document(s) quarantined as untrusted.")
        return " ".join(parts)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
