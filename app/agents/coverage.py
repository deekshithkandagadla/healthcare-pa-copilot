"""Coverage and eligibility agent.

Checks eligibility and prior authorization history through MCP tools. It runs as a separate
A2A service (app/services/coverage_a2a.py); the supervisor falls back to running this same
logic in-process if that service is unreachable.
"""
from __future__ import annotations

from datetime import date

from app import config
from app.agents.tools import McpTools

AGENT = "coverage_eligibility"


async def run_coverage(tools: McpTools, patient_id: str, service_code: str, service_date: str) -> dict:
    elig = await tools.call("check_eligibility", {"patient_id": patient_id, "service_date": service_date}, AGENT)
    hist = (await tools.call("get_authorization_history", {"patient_id": patient_id}, AGENT))["authorizations"]
    sd = date.fromisoformat(service_date)
    lookback = date.fromordinal(sd.toordinal() - config.PRIOR_IMAGING_LOOKBACK_DAYS)
    prior = [a for a in hist if a["service_code"] == service_code and a["status"] == "approved"
             and lookback <= date.fromisoformat(a["service_date"]) < sd]
    evidence = [{"evidence_id": f"AUTH/{a['auth_id']}", "source_type": "auth_history", "source_id": a["auth_id"],
                 "quote": f"{a['description']} ({a['service_code']}) {a['status']} for {a['service_date']}",
                 "date": a["service_date"]} for a in prior]
    if not prior:
        evidence.append({"evidence_id": f"AUTH/none-{patient_id}-{service_code}", "source_type": "auth_history",
                         "source_id": "authorization_history",
                         "quote": f"No approved {service_code} authorizations between {lookback} and {sd}",
                         "date": service_date})
    if elig.get("coverage_id"):
        evidence.append({"evidence_id": elig["coverage_id"], "source_type": "fhir", "source_id": elig["coverage_id"],
                         "quote": f"Active coverage, plan {elig['plan']}, period {elig['period']}", "date": None})
    return {
        "agent": AGENT,
        "eligible": elig["eligible"],
        "plan": elig.get("plan"),
        "coverage_id": elig.get("coverage_id"),
        "eligibility_reason": elig.get("reason"),
        "prior_lumbar_mri_within_12m": bool(prior),
        "prior_auth_ids": [a["auth_id"] for a in prior],
        "authorization_count": len(hist),
        "evidence": evidence,
    }
