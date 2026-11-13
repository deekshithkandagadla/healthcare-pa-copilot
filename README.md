# Healthcare Multi-Agent Prior Authorization Copilot (POC)

Decision support for prior authorization review on **synthetic data only**. Given a case, a
supervisor and four specialist agents find the policy version in effect, gather clinical and
coverage evidence through MCP tools, map cited facts to the policy criteria, and hand a
human reviewer a recommendation in which every finding links to a source. The system never
issues a denial: it can only recommend `MEETS_CRITERIA`, `MORE_INFORMATION_NEEDED`,
`DOES_NOT_APPEAR_TO_MEET_CRITERIA` (always routed to a human) or `SPECIALIST_REVIEW`.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/generate_data.py      # regenerate the synthetic dataset (already committed)
pytest                               # 62 tests
scripts/run_all.sh                   # 4 services, UI at http://127.0.0.1:8100
```

Or with Docker: `docker compose up --build`, then open http://localhost:8100.

No API key is needed: without one, the pipeline runs rule-based and fully deterministic.

### Turning on Claude (Anthropic)

```bash
cp .env.example .env            # add ANTHROPIC_API_KEY; never commit .env
export $(grep -v '^#' .env | xargs)
python scripts/llm_smoke.py     # live check on PA-001, PA-003, PA-004
scripts/run_all.sh              # the UI now shows "AI summary · citations verified"
```

With `LLM_PROVIDER=anthropic`, Claude (default `claude-sonnet-5-5`, set `LLM_MODEL` to change)
does two jobs: it extracts facts from clinical notes, and it writes the reviewer summary. The model
never decides the recommendation. Its output is checked in code before use:

- Every extracted fact must quote the note verbatim, and a numeric value must appear in its quote.
- Negated findings (for example "Denies saddle anesthesia") cannot become red flags.
- Invalid JSON is retried once. After that, the note falls back to the rule-based extractor.
- Quarantined (injected) documents are never sent to the model.
- A summary is rejected, and the template used instead, if it cites unknown or quarantined
  evidence, omits the recommendation, or reads as an approval or denial.
- Token usage is recorded per case. Dollar cost is reported only if you set the per-token prices in `.env`.

## Services

| Port | Service | Protocol role |
|---|---|---|
| 8100 | `app/api.py`: copilot API + UI | AG-UI event stream (SSE), A2UI surfaces, reviewer actions |
| 8101 | `app/services/fhir_api.py`: mock FHIR API | Synthetic Patient, Condition, Observation, Encounter, DocumentReference, Coverage |
| 8102 | `app/services/mcp_server.py`: MCP server | 6 discoverable healthcare tools over streamable HTTP |
| 8103 | `app/services/coverage_a2a.py`: coverage agent | Separate A2A service (agent card + JSON-RPC `message/send`) |

## Agents

| Agent | Scope | Code |
|---|---|---|
| Supervisor | Routing, shared state, retry limit, tool budget, disagreement detection, final validated result | `app/agents/supervisor.py` |
| Clinical evidence | Diagnoses, encounters, observations and quote-anchored note facts; separates facts, ambiguities and gaps; quarantines injected documents | `app/agents/clinical.py`, `extract.py` |
| Policy and RAG | Version selection by service date and plan, hybrid retrieval, exceptions | `app/agents/policy.py`, `app/retrieval/index.py` |
| Coverage and eligibility | Eligibility and authorization history via MCP; runs as an A2A service with an in-process fallback | `app/agents/coverage.py` |
| Criteria and quality | Rules that map facts to criteria, citation verification, schema and grounding checks | `app/agents/criteria.py`, `app/models.py` |

## Demo scenarios (cases)

| Case | Scenario | Expected |
|---|---|---|
| PA-001 | Complete case (54-year-old, lumbar MRI) | MEETS_CRITERIA under V3 |
| PA-002 | Service date before V3 → V2 governs | MEETS_CRITERIA under V2 |
| PA-003 | Therapy mentioned without duration; stale 2024 record ignored | MORE_INFORMATION_NEEDED + case-specific form |
| PA-004 | Faxed document with a prompt injection | Quarantined; DOES_NOT_APPEAR_TO_MEET_CRITERIA on the real facts |
| PA-005 | Notes contradict encounters; notes contradict authorization history | Agent disagreement → SPECIALIST_REVIEW |
| PA-006 | Coverage A2A service down (toggle in the UI) | Fallback, flagged for review |
| PA-007 | Rural plan exception within its date window | MEETS_CRITERIA with EXC-2026-RURAL-PT |
| PA-008 | Cauda equina red flags | Red-flag bypass, expedite |

Human review (scenario 7): run PA-003, enter "7 weeks" plus a source in the form, and re-run.
The case moves to MEETS_CRITERIA, and the reviewer's input becomes cited evidence (`REVIEW/...`) in the audit trail.

## Status

Done: synthetic data and generator, mock FHIR API, MCP server, A2A coverage service with fallback,
hybrid version-aware retrieval, the five agent roles, AG-UI streaming, A2UI catalog and renderer,
reviewer actions with an audit trail, the Anthropic LLM layer with verification and fallbacks, and 62 tests.

Next: an evaluation runner and report (accuracy, retrieval, latency, token cost), the full
architecture note, and the demo video.

See `docs/ARCHITECTURE.md` for design decisions and known limitations.
