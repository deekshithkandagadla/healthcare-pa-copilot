"""Retrieval quality and the human-in-the-loop API."""
import json

from fastapi.testclient import TestClient

from app.retrieval.index import get_index


def test_superseded_version_ranks_higher_but_loses():
    r = get_index().search("lumbar spine MRI for low back pain after conservative treatment",
                           service_code="72148", plan="PPO-GOLD", service_date="2026-10-20", k=3)
    top_excluded = r.excluded_better_ranked[0]
    assert top_excluded.doc_id == "POL-MRI-LUMBAR-V2" and top_excluded.rank_unfiltered == 1
    assert "not in effect" in top_excluded.reason
    assert all(h.doc_id != "POL-MRI-LUMBAR-V2" for h in r.hits)


def test_hybrid_finds_the_therapy_criterion():
    r = get_index().search("how many weeks of physical therapy are required",
                           service_code="72148", plan="HMO-STD", service_date="2026-10-01", k=3)
    assert r.hits[0].chunk_id == "POL-MRI-LUMBAR-V3#S3.2"


def test_two_document_types_ingested():
    types = {c.doc_type for c in get_index().chunks}
    assert types == {"policy_md", "exception_json"}


def test_plan_filter_excludes_other_plans_exception():
    r = get_index().search("rural physical therapy exception", service_code="72148", plan="PPO-GOLD",
                           service_date="2026-08-10", k=3)
    assert all(not h.doc_id.startswith("EXC-2026-RURAL") for h in r.hits)


def _run(c, cid):
    with c.stream("POST", f"/api/cases/{cid}/investigate") as r:
        return [json.loads(l[6:]) for l in r.iter_lines() if l.startswith("data: ")]


def test_human_review_updates_case_state():
    from app.api import app
    c = TestClient(app)
    events = _run(c, "PA-003")
    assert any(e["type"] == "CUSTOM" and e["name"] == "a2ui" for e in events)
    assert c.get("/api/cases/PA-003").json()["result"]["recommendation"] == "MORE_INFORMATION_NEEDED"
    # approval is blocked until the record supports it
    assert c.post("/api/cases/PA-003/review", json={"action": "record_disposition", "disposition": "APPROVE",
                                                   "message": "x"}).status_code == 422
    # a source note is mandatory
    assert c.post("/api/cases/PA-003/review", json={"action": "supply_data", "fact": "pt_weeks_recent",
                                                   "value": 7, "note": ""}).status_code == 422
    c.post("/api/cases/PA-003/review", json={"action": "supply_data", "fact": "pt_weeks_recent", "value": 7,
                                             "note": "Outside PT records: 7 weekly visits"})
    _run(c, "PA-003")
    case = c.get("/api/cases/PA-003").json()
    assert case["result"]["recommendation"] == "MEETS_CRITERIA"
    assert any(e["source_type"] == "reviewer" for e in case["result"]["evidence"].values())
    d = c.post("/api/cases/PA-003/review", json={"action": "record_disposition", "disposition": "APPROVE",
                                                 "message": "Criteria met with outside records"}).json()
    assert d["system_recommendation"] == "MEETS_CRITERIA" and d["timestamp"]
    actions = [a["action"] for a in c.get("/api/cases/PA-003/audit").json()]
    assert actions == ["investigation_completed", "data_supplied", "investigation_completed", "disposition_recorded"]


def test_clarification_request():
    from app.api import app
    c = TestClient(app)
    r = c.post("/api/cases/PA-005/review", json={"action": "request_clarification",
                                                "message": "Please send PT attendance records"}).json()
    assert r["clarification_id"].startswith("CLR-")
    assert c.get("/api/cases/PA-005").json()["case"]["status"] == "pending_clarification"


def test_ui_outage_modes_drive_fallback_and_error_state():
    from app.api import app
    c = TestClient(app)
    try:
        assert c.post("/api/admin/outage", json={"mode": "a2a"}).json()["mcp_failing_tools"] == []
        ev = _run(c, "PA-006")
        assert any(e["type"] == "CUSTOM" and e["name"] == "fallback" for e in ev)
        assert not any(e["type"] == "RUN_ERROR" for e in ev)
        assert c.get("/api/cases/PA-006").json()["result"]["recommendation"] == "MEETS_CRITERIA"

        c.post("/api/admin/outage", json={"mode": "full"})
        ev = _run(c, "PA-006")
        assert any(e["type"] == "RUN_ERROR" and e["code"] == "COVERAGE_UNAVAILABLE" for e in ev)
        res = c.get("/api/cases/PA-006").json()["result"]
        assert res["recommendation"] == "MORE_INFORMATION_NEEDED" and res["requires_human_review"]
        assert c.get("/api/admin/outage").json()["mode"] == "full"
    finally:
        c.post("/api/admin/outage", json={"mode": "none"})
    assert _run(c, "PA-006")[-1]["type"] == "RUN_FINISHED"


def test_agui_stream_ends_with_run_finished_after_a2ui():
    from app.api import app
    ev = _run(TestClient(app), "PA-001")
    types = [e["type"] for e in ev]
    assert types[0] == "RUN_STARTED" and types[-1] == "RUN_FINISHED"
    assert any(e["type"] == "CUSTOM" and e["name"] == "a2ui" for e in ev)
