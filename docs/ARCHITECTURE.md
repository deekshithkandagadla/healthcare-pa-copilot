# Architecture (draft)

```mermaid
flowchart LR
  UI[Browser UI<br/>A2UI renderer] -- POST /investigate<br/>AG-UI SSE stream --> API[Copilot API<br/>Supervisor]
  API --> CE[Clinical evidence agent]
  API --> PR[Policy & RAG agent]
  API --> CQ[Criteria & quality agent]
  API -- A2A message/send --> COV[Coverage agent service :8103]
  API -. fallback .-> COVL[Coverage agent in-process]
  CE -- MCP --> MCP[MCP server :8102]
  PR -- MCP search_policies --> MCP
  COV -- MCP --> MCP
  COVL -- MCP --> MCP
  MCP -- REST --> FHIR[Mock FHIR API :8101]
  MCP --> IDX[(Hybrid index<br/>policies.md + exceptions.json)]
  API --> AUD[(Audit log)]
```

## Key decisions

1. **The LLM never decides.** Facts are quote-anchored and criteria are evaluated by fixed rules, so
   "never invent facts" and "never auto-deny" are enforced in code (`app/models.py` validators),
   not by prompting. Any language model output must pass the same quote verification.
2. **Missing is not negative.** `MISSING` and `NOT_SUPPORTED` are separate statuses. A not-met
   recommendation requires at least one affirmatively unmet criterion and always requires human review.
3. **Version selection is metadata-first, retrieval-second.** The policy in effect is chosen by
   family, service code, plan and service date. Retrieval ranks the whole corpus and then filters,
   so the trace shows which better-scoring superseded documents were excluded and why.
4. **Untrusted content is quarantined, not cleaned.** A document with instruction-like text stays
   visible to the reviewer but cannot contribute facts. The grounding validator rejects any
   criterion supported by quarantined evidence.
5. **Disagreement is rechecked once, then escalated.** It never gets "averaged away."
6. **Fail safe.** Tool budget, per-call timeouts, a bounded retry and an A2A fallback. If the
   pipeline cannot finish, the result is `MORE_INFORMATION_NEEDED` with no findings.

## LLM layer (Anthropic Claude)

`app/llm.py`. The model is used where language understanding helps (reading notes, writing the
summary), and it is kept out of the decision:

| Step | Model's job | Verified in code | If it fails |
|---|---|---|---|
| Fact extraction | Read one note, return JSON items with verbatim quotes | Allowed kind and type, quote found verbatim, number present in the quote, negation check | Retry once, then use the rule-based extractor for that note |
| Union with rules | Adds facts the rules missed | Same checks | Rules alone |
| Summary | 3–6 sentences from structured findings only (no demographics) | Cited IDs exist and are not quarantined, states the recommendation, no approve/deny wording | Template summary |

Hybrid extraction (rules ∪ verified LLM items) means a model outage or bad output can only
reduce recall back to the rule baseline. It can never add an unverified fact.

## Protocol boundaries

| Protocol | Where | What is real | Simplified |
|---|---|---|---|
| MCP | `mcp_server.py`, `agents/tools.py` | Official SDK, streamable HTTP, `tools/list` discovery, real calls from 3 agents | Read-only tools, no auth |
| A2A | `coverage_a2a.py`, `agents/a2a_client.py` | Agent card, JSON-RPC 2.0 `message/send`, Task with data artifact, separate process | No streaming, push or task persistence |
| AG-UI | `agui.py`, `api.py` | Standard event types and fields over SSE | Custom events for fallback, disagreement and A2UI |
| A2UI | `a2ui.py`, `frontend/` | `surfaceUpdate`/`beginRendering` envelope, fixed catalog, server and client validation | Custom catalog, no incremental data-model updates |
| FHIR | `fhir_store.py`, `fhir_api.py` | Resource types, ids, references, codings, periods, search by patient | No paging or modifiers; notes are Markdown files instead of base64 attachments |

## Known limitations (so far)

- The default semantic scorer is LSA (offline). A neural embedder is available with `EMBEDDINGS=sentence-transformers`.
- The rule-based extractor is tuned to the synthetic notes. The LLM extractor generalizes better, but its output is non-deterministic (this SDK version exposes no sampling controls), so evaluation reports results for both modes.
- Quote verification proves that a quote exists, not that the model labeled it correctly. The number and negation checks catch common mislabels, not all of them.
- One service (lumbar MRI) and one policy family. State is kept in memory, with the audit trail written to JSONL.
