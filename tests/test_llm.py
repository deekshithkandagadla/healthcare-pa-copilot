"""LLM layer, tested with scripted fake models (no API key, deterministic).

A live smoke test against Anthropic is in scripts/llm_smoke.py.
"""
import json
import re

import httpx
import pytest

from app.agents.a2a_client import A2AClient
from app.agents.extract import extract_statements, parse_note
from app.agents.supervisor import Supervisor
from app.llm import Usage, check_summary, llm_extract
from app.services.mcp_server import mcp
from conftest import LABELS, load_case


def _note_from_prompt(user: str) -> tuple[str, str]:
    m = re.search(r'<untrusted source="([^"]+)">\n(.*)\n</untrusted>', user, re.DOTALL)
    return m.group(1), m.group(2)


class ScriptedLLM:
    """A well-behaved model: returns what a careful extractor would, plus a grounded summary."""

    def __init__(self, extra_items=None, summary=None, bad_json_notes=()):
        self.prompts: list[tuple[str, str]] = []
        self.extra_items = extra_items or {}
        self.summary = summary
        self.bad_json_notes = set(bad_json_notes)

    async def complete(self, system, user, max_tokens=1500):
        self.prompts.append((system, user))
        if user.startswith("<untrusted"):
            note_id, body = _note_from_prompt(user)
            if note_id in self.bad_json_notes:
                return "Sure! Here are the facts: {kind: pain", {"input_tokens": 300, "output_tokens": 20}
            text = "## X\n" + body  # re-wrap so the rules give us realistic items to echo back
            items = [{"kind": s.kind, "value": s.value, "quote": s.quote}
                     for s in extract_statements(parse_note({"note_id": note_id, "date": "", "text": text}))]
            items += self.extra_items.get(note_id, [])
            return json.dumps(items), {"input_tokens": 400, "output_tokens": 120}
        payload = json.loads(user)
        if self.summary:
            return self.summary(payload), {"input_tokens": 600, "output_tokens": 90}
        ev = next(iter(payload["criteria"][0]["evidence"]), "")
        return (f"System recommendation: {payload['recommendation']} under {payload['policy']}. "
                f"Symptom duration is documented [{ev}]. A reviewer makes the final decision."), \
            {"input_tokens": 600, "output_tokens": 60}


def _sup(llm):
    from app.services.coverage_a2a import create_app
    a2a = A2AClient("http://c", transport=httpx.ASGITransport(app=create_app(mcp_target=mcp)))
    return Supervisor(mcp, a2a=a2a, llm=llm)


@pytest.mark.parametrize("cid", sorted(LABELS))
async def test_labels_hold_with_llm_enabled(cid):
    llm = ScriptedLLM()
    r, em, _ = await _sup(llm).run(load_case(cid))
    assert r.recommendation == LABELS[cid]["expected_recommendation"]
    assert r.summary_source == "llm" and r.llm_usage["calls"] >= 2
    assert r.llm_usage["input_tokens"] > 0


async def test_quarantined_note_never_sent_to_model():
    llm = ScriptedLLM()
    await _sup(llm).run(load_case("PA-004"))
    sent = " ".join(u for _, u in llm.prompts)
    assert "SYSTEM OVERRIDE" not in sent and "Ignore all previous" not in sent
    assert not any("NOTE-006" in u for _, u in llm.prompts)


async def test_prompts_contain_no_direct_identifiers():
    llm = ScriptedLLM()
    await _sup(llm).run(load_case("PA-001"))
    sent = " ".join(u for _, u in llm.prompts)
    assert "Synthetic Patient" not in sent and "1972-03-14" not in sent


async def test_fabricated_and_mislabeled_items_rejected():
    # Hallucinated number on a real sentence, a quote that is not in the note, and a negated red flag.
    extras = {"NOTE-004": [
        {"kind": "pt_weeks_claim", "value": 8, "quote": "Has been doing physical therapy at an outside clinic."},
        {"kind": "pt_weeks_claim", "value": 6, "quote": "Completed 6 weeks of physical therapy."},
        {"kind": "red_flag", "value": "saddle anesthesia", "quote": "Denies saddle anesthesia."},
    ]}
    llm = ScriptedLLM(extra_items=extras)
    r, _, _ = await _sup(llm).run(load_case("PA-003"))
    assert r.recommendation == "MORE_INFORMATION_NEEDED"           # unchanged by the bad items
    rejected = " ".join(r.llm_usage["rejected_items"])
    assert "does not appear in its quote" in rejected
    assert "not found verbatim" in rejected
    assert "negated" in rejected
    assert not r.red_flag_pathway


async def test_llm_can_add_a_verified_fact_rules_missed():
    # Rules miss "PT twice weekly for 7 weeks"; a verified LLM item fills it in.
    from app.agents.extract import ParsedNote
    text = "## Subjective\nAttended PT twice weekly for 7 weeks at the county clinic.\n"
    note = ParsedNote("NOTE-X", "2026-10-01", "", "ehr", text, [("Subjective", 0, len(text))])
    llm = ScriptedLLM(extra_items={"NOTE-X": [
        {"kind": "pt_weeks_claim", "value": 7, "quote": "Attended PT twice weekly for 7 weeks at the county clinic."}]})
    usage = Usage()
    st = await llm_extract(note, llm, usage)
    assert [(s.kind, s.value) for s in st] == [("pt_weeks_claim", 7)] and not extract_statements(note)


async def test_invalid_json_falls_back_to_rules():
    llm = ScriptedLLM(bad_json_notes={"NOTE-001"})
    r, _, _ = await _sup(llm).run(load_case("PA-001"))
    assert r.recommendation == "MEETS_CRITERIA"
    assert len([f for f in r.llm_usage["failures"] if "NOTE-001" in f]) == 2     # retried once
    assert any("rule-based extraction used" in u for u in r.uncertainties)


class _Boom:
    async def complete(self, *a, **k):
        raise TimeoutError("model timeout")


async def test_model_outage_falls_back_everywhere():
    r, _, _ = await _sup(_Boom()).run(load_case("PA-005"))
    assert r.recommendation == "SPECIALIST_REVIEW" and r.summary_source == "template"


@pytest.mark.parametrize("bad, why", [
    (lambda p: f"{p['recommendation']}: see [NOTE-999#Subjective@1].", "unknown evidence"),
    (lambda p: "The MRI request is denied for lack of therapy.", "determination"),
    (lambda p: "Everything looks fine.", "does not state"),
])
async def test_ungrounded_summary_replaced_by_template(bad, why):
    llm = ScriptedLLM(summary=bad)
    r, _, _ = await _sup(llm).run(load_case("PA-003"))
    assert r.summary_source == "template"
    assert any(why in x for x in r.llm_usage["rejected_items"])


def test_check_summary_accepts_grounded_text():
    class R:
        recommendation = "MORE_INFORMATION_NEEDED"
        evidence = {"NOTE-004#Subjective@187": type("E", (), {"quarantined": False})()}
    assert check_summary("More information needed: C-02 therapy duration is missing [NOTE-004#Subjective@187].", R) == []


async def test_anthropic_client_parses_api_response():
    """Exercise the real SDK path against a mocked HTTP layer."""
    import httpx2

    from app.llm import AnthropicLLM

    def handler(request):
        body = json.loads(request.content)
        assert body["messages"][0]["role"] == "user" and body["system"] == "sys"
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": "[]"}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 321, "output_tokens": 4}})

    llm = AnthropicLLM(api_key="test-key", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    text, usage = await llm.complete("sys", "user")
    assert text == "[]" and usage == {"input_tokens": 321, "output_tokens": 4}
    assert llm.model == "claude-sonnet-5-5"


@pytest.mark.parametrize("raw", [
    '[{"kind": "pt_weeks_claim", "value": 7, "quote": "Attended PT twice weekly for 7 weeks at the county clinic."}]\n\nNote: nothing else applies.',
    '```json\n[{"kind": "pt_weeks_claim", "value": 7, "quote": "Attended PT twice weekly for 7 weeks at the county clinic."}]\n```',
    '[{"kind": "pt_weeks_claim", "value": 7, "quote": "Attended PT twice weekly for 7 weeks at the county clinic."}]\n[]',
])
async def test_trailing_text_after_json_is_tolerated(raw):
    # Seen live: Claude returned a valid array followed by extra data ("Extra data" JSON error).
    from app.agents.extract import ParsedNote
    text = "## Subjective\nAttended PT twice weekly for 7 weeks at the county clinic.\n"
    note = ParsedNote("NOTE-X", "2026-10-01", "", "ehr", text, [("Subjective", 0, len(text))])

    class Raw:
        async def complete(self, *a, **k):
            return raw, {"input_tokens": 1, "output_tokens": 1}
    usage = Usage()
    st = await llm_extract(note, Raw(), usage)
    assert [(s.kind, s.value) for s in st] == [("pt_weeks_claim", 7)] and usage.calls == 1


def test_summary_markdown_is_stripped():
    from app.llm import clean_summary
    out = clean_summary("**Summary for PA-001:** The system recommendation is MEETS_CRITERIA.\n\nAll four criteria are supported [X-1].")
    assert out == "Summary for PA-001: The system recommendation is MEETS_CRITERIA. All four criteria are supported [X-1]."
