"""End-to-end: every labeled case through the full multi-agent pipeline."""
import pytest

from conftest import LABELS, load_case


@pytest.mark.parametrize("cid", sorted(LABELS))
async def test_case_matches_label(supervisor, cid):
    label = LABELS[cid]
    result, emitter, calls = await supervisor.run(load_case(cid))
    assert result.recommendation == label["expected_recommendation"]
    assert result.applicable_policy_id == label["expected_policy_id"]
    got = {c.criterion_id: c.status for c in result.criteria}
    for crit, status in label["expected_criteria"].items():
        assert got[crit] == status, f"{cid} {crit}: {got[crit]} != {status}"
    for exc in label.get("expected_exceptions", []):
        assert exc in result.exceptions_applied
    applied = [x.exclusion_id for x in result.exclusions if x.status == "APPLIES"]
    assert applied == label.get("expected_exclusions", [])
    if label.get("expected_red_flag"):
        assert result.red_flag_pathway
    assert bool(result.security_flags) == label["injection_present"]
    # never an automated adverse determination
    if result.recommendation != "MEETS_CRITERIA":
        assert result.requires_human_review


async def test_policy_conflict_resolved_by_date(supervisor):
    r2, _, _ = await supervisor.run(load_case("PA-002"))   # 2026-02-10, before V3
    r1, _, _ = await supervisor.run(load_case("PA-001"))   # 2026-10-20, after V3
    assert r2.applicable_policy_id == "POL-MRI-LUMBAR-V2" and "V3" in r2.policy_selection_reason
    assert r1.applicable_policy_id == "POL-MRI-LUMBAR-V3" and "V2" in r1.policy_selection_reason


async def test_exception_applies_only_in_window(supervisor):
    r, _, _ = await supervisor.run(load_case("PA-007"))
    c02 = next(c for c in r.criteria if c.criterion_id == "C-02")
    assert c02.threshold == 4 and "EXC-2026-RURAL-PT#E1" in c02.evidence_ids
    assert "EXC-2025-GOLD-PT" not in r.exceptions_applied


async def test_disagreement_is_rechecked_then_escalated(supervisor):
    r, em, _ = await supervisor.run(load_case("PA-005"))
    ev = [e for e in em.events if e["type"] == "CUSTOM" and e["name"] == "agent_disagreement"]
    assert ev and ev[0]["value"]["rechecked"] and ev[0]["value"]["resolution"] == "escalated"
    assert r.recommendation == "SPECIALIST_REVIEW" and r.contradictions


async def test_stale_record_not_counted(supervisor):
    r, _, _ = await supervisor.run(load_case("PA-003"))
    assert any("stale" in u for u in r.uncertainties)
    c02 = next(c for c in r.criteria if c.criterion_id == "C-02")
    assert not any(e.startswith("NOTE-003") for e in c02.evidence_ids)


async def test_note_claim_wrapped_across_lines_is_extracted(supervisor):
    # NOTE-001 wraps "Completed 8 weeks of / physical therapy" across two lines.
    r, _, _ = await supervisor.run(load_case("PA-001"))
    c02 = next(c for c in r.criteria if c.criterion_id == "C-02")
    assert any(e.startswith("NOTE-001#") for e in c02.evidence_ids) and "note claims [8]" in c02.rationale


async def test_exclusion_needs_evidence_and_is_never_a_denial(supervisor):
    r, em, _ = await supervisor.run(load_case("PA-009"))
    x01 = next(x for x in r.exclusions if x.exclusion_id == "X-01")
    assert x01.status == "APPLIES" and any(e.startswith("NOTE-012#") for e in x01.evidence_ids)
    assert "POL-MRI-LUMBAR-V3#S5.1" in x01.evidence_ids
    assert r.recommendation == "SPECIALIST_REVIEW" and r.requires_human_review
    assert any(e["type"] == "CUSTOM" and e["name"] == "policy_exclusion" for e in em.events)
    # Absence of a documented exclusion is NOT_DOCUMENTED, not "applies"
    r1, _, _ = await supervisor.run(load_case("PA-001"))
    assert {x.status for x in r1.exclusions} == {"NOT_DOCUMENTED"}
