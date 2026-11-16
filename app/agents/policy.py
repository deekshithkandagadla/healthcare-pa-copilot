"""Policy and RAG agent: picks the policy version in effect, its criteria, and any exceptions."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.tools import McpTools
from app.models import Evidence
from app.retrieval.index import PolicyIndex

AGENT = "policy_rag"
FAMILY_BY_SERVICE = {"72148": "POL-MRI-LUMBAR"}


@dataclass
class PolicyOutput:
    policy: dict | None = None
    reason: str = ""
    exceptions: list[dict] = field(default_factory=list)
    exception_notes: list[str] = field(default_factory=list)
    retrieval: dict = field(default_factory=dict)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    uncertainties: list[str] = field(default_factory=list)


class PolicyAgent:
    def __init__(self, index: PolicyIndex, tools: McpTools | None = None):
        self.index = index
        self.tools = tools

    async def run(self, case: dict, plan: str | None) -> PolicyOutput:
        out = PolicyOutput()
        family = FAMILY_BY_SERVICE.get(case["service_code"])
        if not family:
            out.uncertainties.append(f"No policy family is registered for service {case['service_code']}")
            return out
        sd = case["service_date"]
        plans = [plan] if plan else sorted({p for m in self.index.policies.values() for p in m["plans"]})
        if not plan:
            out.uncertainties.append("Member plan unverified; policy selected on service date only")

        chosen, notes = [], []
        for pl in plans:
            c, n = self.index.applicable_policies(family, case["service_code"], pl, sd)
            chosen += [m for m in c if m not in chosen]
            notes += n
        if not chosen:
            out.uncertainties.append(f"No version of {family} is in effect on {sd}")
            out.reason = "; ".join(notes)
            return out
        chosen.sort(key=lambda m: m["version"], reverse=True)
        pol = chosen[0]
        if len(chosen) > 1:
            out.uncertainties.append(f"Overlapping effective windows: {[m['policy_id'] for m in chosen]}; "
                                     f"highest version used")
        out.policy = pol
        out.reason = (f"{pol['policy_id']} is in effect on the service date {sd} "
                      f"({pol['effective_start']} to {pol['effective_end'] or 'open'}). "
                      + " ".join(f"Not used: {n}." for n in sorted(set(notes))))

        # Hybrid retrieval over the full corpus (via MCP when available) to surface the
        # criteria text and show any better-scoring documents that were filtered out.
        query = "lumbar spine MRI for low back pain criteria conservative treatment physical therapy"
        args = {"query": query, "service_code": case["service_code"], "plan": plan or pol["plans"][0],
                "service_date": sd, "k": 6}
        if self.tools:
            out.retrieval = await self.tools.call("search_policies", args, AGENT)
        else:
            out.retrieval = self.index.search(**{**args, "query": query}).model_dump()

        for cr in pol["criteria"]:
            cid = f"{pol['policy_id']}#{cr['section']}"
            out.evidence[cid] = Evidence(evidence_id=cid, source_type="policy", source_id=pol["policy_id"],
                                         section=cr["section"], quote=self.index.chunk_text(cid))
        for x in pol.get("exclusions", []):
            xid = f"{pol['policy_id']}#{x['section']}"
            out.evidence[xid] = Evidence(evidence_id=xid, source_type="policy", source_id=pol["policy_id"],
                                         section=x["section"], quote=self.index.chunk_text(xid))
        rf = f"{pol['policy_id']}#{pol['red_flag_section']}"
        out.evidence[rf] = Evidence(evidence_id=rf, source_type="policy", source_id=pol["policy_id"],
                                    section=pol["red_flag_section"], quote=self.index.chunk_text(rf))

        if plan:
            out.exceptions, out.exception_notes = self.index.applicable_exceptions(pol["policy_id"], plan, sd)
            for e in out.exceptions:
                eid = f"{e['exception_id']}#{e['section']}"
                out.evidence[eid] = Evidence(evidence_id=eid, source_type="exception", source_id=e["exception_id"],
                                             section=e["section"], quote=e["text"])
        return out
