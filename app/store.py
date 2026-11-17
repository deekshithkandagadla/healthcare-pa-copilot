"""Simulated case state, reviewer actions and an append-only audit trail.

State lives in memory; every change is also appended to .runtime/audit.jsonl. Audit
entries hold IDs, actions and recommendation codes only, no clinical text or demographics.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from app import config
from app.models import PAResult

Disposition = Literal["APPROVE", "REQUEST_MORE_INFORMATION", "REFER_TO_PHYSICIAN_REVIEWER"]
CaseStatus = Literal["new", "investigating", "ready_for_review", "pending_clarification", "closed"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class CaseState:
    case: dict
    status: CaseStatus = "new"
    runs: list[dict] = field(default_factory=list)            # {run_id, result, trace, tool_calls, a2ui}
    reviewer_inputs: list[dict] = field(default_factory=list)
    clarifications: list[dict] = field(default_factory=list)
    disposition: dict | None = None

    @property
    def latest(self) -> PAResult | None:
        return self.runs[-1]["result"] if self.runs else None


class CaseStore:
    def __init__(self, persist: bool = True):
        self.cases: dict[str, CaseState] = {}
        self.audit: list[dict] = []
        self.persist = persist
        for f in sorted((config.DATA_DIR / "cases").glob("*.json")):
            c = json.loads(f.read_text(encoding="utf-8"))
            self.cases[c["case_id"]] = CaseState(case=c)

    def log(self, case_id: str, actor: str, action: str, **detail) -> dict:
        entry = {"audit_id": f"AUD-{uuid.uuid4().hex[:10]}", "timestamp": _now(), "case_id": case_id,
                 "actor": actor, "action": action, **detail}
        self.audit.append(entry)
        if self.persist:
            config.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            with open(config.RUNTIME_DIR / "audit.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        return entry

    def submit_case(self, patient_id: str, service_code: str, service_date: str, summary: str,
                    actor: str = "reviewer-demo") -> dict:
        nums = [int(c[3:]) for c in self.cases if c[3:].isdigit()]
        case_id = f"PA-{max([100] + [n for n in nums if n >= 100]) + 1:03d}"
        case = {"case_id": case_id, "patient_id": patient_id, "service_code": service_code,
                "service_date": service_date, "requested_by": "Submitted in UI (synthetic)",
                "summary": summary or f"Lumbar MRI request for {patient_id}", "scenario": "user_submitted"}
        self.cases[case_id] = CaseState(case=case)
        self.log(case_id, actor, "case_submitted", patient_id=patient_id, service_date=service_date)
        return case

    def get(self, case_id: str) -> CaseState:
        if case_id not in self.cases:
            raise KeyError(case_id)
        return self.cases[case_id]

    def record_run(self, case_id: str, run: dict) -> None:
        st = self.get(case_id)
        st.runs.append(run)
        st.status = "ready_for_review"
        r: PAResult = run["result"]
        self.log(case_id, "system", "investigation_completed", run_id=run["run_id"],
                 recommendation=r.recommendation, policy_id=r.applicable_policy_id,
                 requires_human_review=r.requires_human_review, security_flags=len(r.security_flags))

    # ---- reviewer actions ------------------------------------------------------------
    def request_clarification(self, case_id: str, reviewer: str, message: str) -> dict:
        st = self.get(case_id)
        item = {"clarification_id": f"CLR-{uuid.uuid4().hex[:6]}", "message": message, "timestamp": _now(),
                "reviewer": reviewer, "status": "sent_to_provider (simulated)"}
        st.clarifications.append(item)
        st.status = "pending_clarification"
        self.log(case_id, reviewer, "clarification_requested", clarification_id=item["clarification_id"])
        return item

    def supply_data(self, case_id: str, reviewer: str, fact: str, value, note: str) -> dict:
        if not note.strip():
            raise ValueError("A source note is required when supplying data")
        st = self.get(case_id)
        item = {"review_id": f"RV-{uuid.uuid4().hex[:6]}", "fact": fact, "value": value, "note": note,
                "reviewer": reviewer, "timestamp": _now()}
        st.reviewer_inputs.append(item)
        self.log(case_id, reviewer, "data_supplied", review_id=item["review_id"], fact=fact, value=value)
        return item

    def record_disposition(self, case_id: str, reviewer: str, disposition: Disposition, rationale: str) -> dict:
        st = self.get(case_id)
        latest = st.latest
        if latest is None:
            raise ValueError("Run an investigation before recording a disposition")
        if disposition == "APPROVE" and latest.recommendation != "MEETS_CRITERIA":
            raise ValueError("Approval requires a MEETS_CRITERIA finding; supply data and re-run, "
                             "or refer to a physician reviewer")
        st.disposition = {"disposition": disposition, "rationale": rationale, "reviewer": reviewer,
                          "timestamp": _now(), "based_on_run": st.runs[-1]["run_id"],
                          "system_recommendation": latest.recommendation}
        st.status = "closed" if disposition == "APPROVE" else (
            "pending_clarification" if disposition == "REQUEST_MORE_INFORMATION" else "ready_for_review")
        self.log(case_id, reviewer, "disposition_recorded", disposition=disposition,
                 system_recommendation=latest.recommendation, run_id=st.runs[-1]["run_id"])
        return st.disposition
