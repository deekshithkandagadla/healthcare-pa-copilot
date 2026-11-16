"""Clinical evidence agent.

Collects diagnoses, encounters, observations and note excerpts through MCP, then separates
facts from ambiguities and gaps. It never infers a clinical fact that is not in the record.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app import config
from app.agents.extract import ParsedNote, Statement, extract_statements, parse_note
from app.agents.tools import McpTools
from app.models import Evidence, Fact
from app.llm import LLM, Usage, llm_extract, merge
from app.security import detect_injection

AGENT = "clinical_evidence"
LBP_CODES = {"M54.50", "M54.5"}
RADIC_CODES = {"M54.16", "M54.17"}
RED_FLAG_CODES = {"G83.4"}


@dataclass
class ClinicalOutput:
    facts: dict[str, Fact] = field(default_factory=dict)
    conflicts: dict[str, dict] = field(default_factory=dict)   # fact name -> {detail, evidence_ids}
    gaps: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    security_flags: list[str] = field(default_factory=list)
    notes: dict[str, str] = field(default_factory=dict)          # note_id -> text (for citation checks)
    fhir_ids: set[str] = field(default_factory=set)


def _weeks(a: date, b: date) -> float:
    return round((b - a).days / 7, 1)


class ClinicalEvidenceAgent:
    def __init__(self, tools: McpTools, llm: LLM | None = None, usage: Usage | None = None):
        self.tools = tools
        self.llm = llm
        self.usage = usage or Usage()

    async def run(self, case: dict) -> ClinicalOutput:
        out = ClinicalOutput()
        pid, sd = case["patient_id"], date.fromisoformat(case["service_date"])

        conditions = (await self.tools.call("search_fhir", {"resource_type": "Condition", "patient_id": pid}, AGENT))["resources"]
        observations = (await self.tools.call("search_fhir", {"resource_type": "Observation", "patient_id": pid}, AGENT))["resources"]
        encounters = (await self.tools.call("search_fhir", {"resource_type": "Encounter", "patient_id": pid}, AGENT))["resources"]
        notes_raw = (await self.tools.call("get_clinical_notes", {"patient_id": pid}, AGENT))["notes"]

        # ---- notes: quarantine anything that tries to give instructions --------------
        statements: list[Statement] = []
        parsed: list[ParsedNote] = []
        for n in notes_raw:
            out.notes[n["note_id"]] = n["text"]
            hits = detect_injection(n["text"])
            if hits:
                out.security_flags.append(
                    f"{n['note_id']} ({n.get('source')}) contains instruction-like text {hits!r}; "
                    "quarantined, no facts taken from it")
                out.evidence[n["note_id"]] = Evidence(evidence_id=n["note_id"], source_type="note",
                                                      source_id=n["note_id"], date=n["date"], quarantined=True,
                                                      quote=hits[0])
                continue
            p = parse_note(n)
            parsed.append(p)
            rule_st = extract_statements(p)
            if self.llm:
                # Quarantined notes never reach the model. Model output is verified before use;
                # if it is unusable, this note falls back to the rule-based extractor.
                llm_st = await llm_extract(p, self.llm, self.usage)
                if llm_st is None:
                    out.ambiguities.append(f"LLM extraction failed for {p.note_id}; rule-based extraction used")
                    statements += rule_st
                else:
                    statements += merge(rule_st, llm_st)
            else:
                statements += rule_st
        note_date = {p.note_id: date.fromisoformat(p.date) for p in parsed}

        def cite(st: Statement) -> str:
            out.evidence.setdefault(st.evidence_id, Evidence(
                evidence_id=st.evidence_id, source_type="note", source_id=st.note_id, section=st.section,
                quote=st.quote, date=note_date[st.note_id].isoformat()))
            return st.evidence_id

        def cite_fhir(res: dict, quote: str, when: str | None) -> str:
            eid = f"{res['resourceType']}/{res['id']}"
            out.fhir_ids.add(eid)
            out.evidence.setdefault(eid, Evidence(evidence_id=eid, source_type="fhir", source_id=eid,
                                                  quote=quote, date=when))
            return eid

        by_kind: dict[str, list[Statement]] = {}
        for st in statements:
            by_kind.setdefault(st.kind, []).append(st)

        # ---- symptom duration -------------------------------------------------------
        lbp = [c for c in conditions if any(cd["code"] in LBP_CODES for cd in c["code"]["coding"])]
        if lbp:
            c = min(lbp, key=lambda c: c["onsetDateTime"])
            onset = date.fromisoformat(c["onsetDateTime"][:10])
            ev = [cite_fhir(c, f"{c['code']['coding'][0]['code']} onset {onset}", onset.isoformat())]
            ev += [cite(s) for s in by_kind.get("pain_weeks", [])]
            out.facts["pain_duration_weeks"] = Fact(
                name="pain_duration_weeks", value=_weeks(onset, sd), evidence_ids=ev, agent=AGENT,
                detail=f"Condition onset {onset} to service date {sd}")
        elif by_kind.get("pain_weeks"):
            s = by_kind["pain_weeks"][-1]
            out.facts["pain_duration_weeks"] = Fact(name="pain_duration_weeks", value=s.value,
                                                    evidence_ids=[cite(s)], agent=AGENT, detail="From note only")
        else:
            out.gaps.append("Symptom onset date or duration")

        # ---- physical therapy -------------------------------------------------------
        window_start = date.fromordinal(sd.toordinal() - config.PT_LOOKBACK_DAYS)
        pt_enc = [e for e in encounters if (e.get("serviceType") or {}).get("text") == "physical-therapy"]
        pt_recent = [e for e in pt_enc if window_start <= date.fromisoformat(e["period"]["start"]) <= sd]
        enc_weeks = len({date.fromisoformat(e["period"]["start"]).isocalendar()[:2] for e in pt_recent})
        enc_ev = [cite_fhir(e, f"Physical therapy visit {e['period']['start']}", e["period"]["start"]) for e in pt_recent]

        claims, stale = [], []
        for s in by_kind.get("pt_weeks_claim", []):
            (claims if note_date[s.note_id] >= window_start else stale).append(s)
        for s in stale:
            out.ambiguities.append(f"{s.note_id} ({note_date[s.note_id]}) reports therapy outside the 6-month "
                                   "window; treated as stale and not counted")
        nonattend = by_kind.get("pt_nonattendance", [])
        claim_ev = [cite(s) for s in claims]
        na_ev = [cite(s) for s in nonattend]

        conflict = []
        if claims and pt_recent and any(abs(s.value - enc_weeks) > config.NOTE_CLAIM_TOLERANCE_WEEKS for s in claims):
            conflict.append(f"notes claim {[s.value for s in claims]} weeks of therapy but only "
                            f"{enc_weeks} dated therapy encounter week(s) are on record")
        if claims and nonattend:
            conflict.append("a therapy discharge note reports non-attendance")
        if conflict:
            out.conflicts["pt_weeks_recent"] = {"detail": "; ".join(conflict), "evidence_ids": claim_ev + na_ev + enc_ev}
        elif pt_recent or claims:
            value = max([enc_weeks] + [s.value for s in claims])
            out.facts["pt_weeks_recent"] = Fact(name="pt_weeks_recent", value=value, evidence_ids=enc_ev + claim_ev,
                                                agent=AGENT, detail=f"{enc_weeks} encounter week(s); note claims {[s.value for s in claims]}")
        elif by_kind.get("pt_not_started"):
            out.facts["pt_weeks_recent"] = Fact(name="pt_weeks_recent", value=0, agent=AGENT,
                                                evidence_ids=[cite(s) for s in by_kind["pt_not_started"]],
                                                detail="Note states therapy not started")
        else:
            if by_kind.get("pt_mentioned_no_duration"):
                ids = [cite(s) for s in by_kind["pt_mentioned_no_duration"]]
                out.ambiguities.append(f"Physical therapy is mentioned without dates or duration ({', '.join(ids)})")
            out.gaps.append(f"Documented physical therapy duration (dated visits or number of weeks) "
                            f"between {window_start} and {sd}")

        # ---- conservative therapy (any modality; used by older policy versions) -------
        modal = [(m, s) for m in ("nsaid_weeks", "home_exercise_weeks") for s in by_kind.get(m, [])]
        if "pt_weeks_recent" in out.facts or modal:
            pt = out.facts.get("pt_weeks_recent")
            candidates = [(pt.value, pt.evidence_ids)] if pt else []
            candidates += [(s.value, [cite(s)]) for _, s in modal]
            best = max(candidates, key=lambda x: x[0])
            out.facts["conservative_therapy_weeks"] = Fact(name="conservative_therapy_weeks", value=best[0],
                                                           evidence_ids=best[1], agent=AGENT)
        elif "pt_weeks_recent" in out.conflicts:
            out.conflicts["conservative_therapy_weeks"] = out.conflicts["pt_weeks_recent"]

        # ---- radicular / neurological findings ---------------------------------------
        pos = [cite_fhir(c, f"{c['code']['coding'][0]['code']} {c['code']['text']}", c["onsetDateTime"])
               for c in conditions if any(cd["code"] in RADIC_CODES for cd in c["code"]["coding"])]
        pos += [cite_fhir(o, f"{o['code']['text']}: {o['valueString']}", o["effectiveDateTime"])
                for o in observations if "positive" in o.get("valueString", "").lower()]
        pos += [cite(s) for s in by_kind.get("radic_pos", [])]
        neg = [cite(s) for s in by_kind.get("radic_neg", [])]
        if pos:
            out.facts["radiculopathy"] = Fact(name="radiculopathy", value=True, evidence_ids=pos, agent=AGENT)
        elif neg:
            out.facts["radiculopathy"] = Fact(name="radiculopathy", value=False, evidence_ids=neg, agent=AGENT,
                                              detail="Record documents no radicular or neurological findings")
        else:
            out.gaps.append("Neurological examination findings")

        # ---- red flags ---------------------------------------------------------------
        rf = [cite_fhir(c, f"{c['code']['coding'][0]['code']} {c['code']['text']}", c["onsetDateTime"])
              for c in conditions if any(cd["code"] in RED_FLAG_CODES for cd in c["code"]["coding"])]
        rf += [cite(s) for s in by_kind.get("red_flag", [])]
        if rf:
            out.facts["red_flag"] = Fact(name="red_flag", value=True, evidence_ids=rf, agent=AGENT,
                                         detail=", ".join(sorted({s.value for s in by_kind.get('red_flag', [])})))

        # ---- policy exclusion facts (only when the record states them) -----------------
        if by_kind.get("mri_unsafe_device"):
            dev = [cite(s) for s in by_kind["mri_unsafe_device"]]
            cleared = [cite(s) for s in by_kind.get("device_cleared", [])]
            out.facts["mri_unsafe_device"] = Fact(
                name="mri_unsafe_device", value=not cleared, evidence_ids=dev + cleared, agent=AGENT,
                detail="Device clearance documented" if cleared else "No device clearance documented")
        if by_kind.get("medicolegal_only"):
            out.facts["medicolegal_only"] = Fact(name="medicolegal_only", value=True, agent=AGENT,
                                                 evidence_ids=[cite(s) for s in by_kind["medicolegal_only"]])

        # ---- prior imaging, as stated in the notes -----------------------------------
        if by_kind.get("prior_mri"):
            out.facts["prior_imaging_per_notes"] = Fact(name="prior_imaging_per_notes", value=True, agent=AGENT,
                                                        evidence_ids=[cite(s) for s in by_kind["prior_mri"]])
        elif by_kind.get("no_prior_imaging"):
            out.facts["prior_imaging_per_notes"] = Fact(name="prior_imaging_per_notes", value=False, agent=AGENT,
                                                        evidence_ids=[cite(s) for s in by_kind["no_prior_imaging"]])
        return out
