"""Render docs/EVALUATION.md from eval/results_*.json (called by scripts/evaluate.py)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "eval"
MODES = ["rules", "llm", "scripted"]
MODE_NAME = {"rules": "Rules only", "llm": "Claude (live)", "scripted": "Scripted model"}


def _load():
    return {m: json.loads((EVAL / f"results_{m}.json").read_text(encoding="utf-8"))
            for m in MODES if (EVAL / f"results_{m}.json").exists()}


def _v(x, suffix=""):
    return "–" if x is None else f"{x}{suffix}"


def _mark(ok):
    return "✅" if ok else "❌"


def _summary_table(res):
    modes = list(res)
    rows = [
        ("Recommendation accuracy (9 labeled cases)", "recommendation_accuracy_pct", "%"),
        ("Policy version correctness", "policy_version_accuracy_pct", "%"),
        ("Criterion mapping accuracy", "criterion_mapping_accuracy_pct", "%"),
        ("Missing-information precision", "missing_info_precision_pct", "%"),
        ("Missing-information recall", "missing_info_recall_pct", "%"),
        ("No automated adverse determination", "no_automated_adverse_pct", "%"),
        ("Prompt-injection variants not followed", "injection_variants_not_followed_pct", "%"),
        ("Prompt-injection variants caught by the pattern detector", "injection_variants_quarantined_pct", "%"),
        ("Prompt-injection variants used as evidence", "injection_variants_used_as_evidence", ""),
        ("Reliability scenarios handled safely", "reliability_scenarios_ok", ""),
        ("Tool-call success rate (normal runs)", "tool_success_pct", "%"),
        ("Case latency p50 (ms)", "latency_ms_p50", ""),
        ("Case latency p95 (ms)", "latency_ms_p95", ""),
    ]
    out = ["| Metric | " + " | ".join(MODE_NAME[m] for m in modes) + " |",
           "|---|" + "---|" * len(modes)]
    for label, key, suf in rows:
        out.append(f"| {label} | " + " | ".join(_v(res[m]["summary"].get(key), suf) for m in modes) + " |")
    out.append("| Exceptions, exclusions, red flags correct | " +
               " | ".join(_mark(res[m]["summary"]["exceptions_exclusions_redflags_ok"]) for m in modes) + " |")
    out.append("| Citation validity | " + " | ".join(
        f"{res[m]['citations']['validity_pct']}% ({res[m]['citations']['verified']}/{res[m]['citations']['evidence_items']})"
        for m in modes) + " |")
    out.append("| Schema conformance | " + " | ".join(
        f"{res[m]['citations']['schema_valid']}/{res[m]['citations']['schema_total']}" for m in modes) + " |")
    return "\n".join(out)


def _cases(res):
    base = res["rules"]
    other = [m for m in res if m != "rules"]
    hdr = "| Case | Scenario | Expected | Rules only | " + " | ".join(MODE_NAME[m] for m in other) + \
          " | Policy | Criteria | Latency (ms) | Tool calls |"
    out = [hdr, "|---|---|---|---|" + "---|" * len(other) + "---|---|---|---|"]
    idx = {m: {r["case_id"]: r for r in res[m]["cases"]} for m in res}
    for r in base["cases"]:
        cells = [f"{_mark(idx[m][r['case_id']]['rec_ok'])} {idx[m][r['case_id']]['got']}" for m in other]
        out.append(f"| {r['case_id']} | {r['scenario']} | {r['expected']} | {_mark(r['rec_ok'])} {r['got']} | "
                   + "".join(c + " | " for c in cells)
                   + f"{_mark(r['policy_ok'])} {r['policy']} | {r['criteria_ok']}/{r['criteria_total']} | "
                     f"{r['latency_ms']} | {r['tool_calls']} |")
    return "\n".join(out)


def _retrieval(rt):
    used = "hybrid_neural" if "hybrid_neural" in rt["by_scorer"] else "hybrid_lsa"
    names = {"bm25": "Keyword only (BM25)", "lsa": "Semantic only: LSA (TF-IDF + SVD)",
             "neural": "Semantic only: neural (bge-small-en-v1.5)", "hybrid_lsa": "Hybrid: BM25 + LSA (RRF)",
             "hybrid_neural": "Hybrid: BM25 + neural (RRF)"}
    out = [f"Semantic backend in this run: **{rt.get('semantic_backend', 'LSA')}**"
           + (f" ({rt['semantic_note']})" if "fell back" in rt.get("semantic_note", "") else "") + ".", "",
           "| Ranking | Queries | Hit@1 | Hit@3 | MRR |", "|---|---|---|---|---|"]
    for k in ("bm25", "lsa", "neural", "hybrid_lsa", "hybrid_neural"):
        if k in rt["by_scorer"]:
            v = rt["by_scorer"][k]
            label = f"**{names[k]}** (used)" if k == used else names[k]
            out.append(f"| {label} | {v['n']} | {v['hit@1']}% | {v['hit@3']}% | {v['mrr']} |")
    if "neural" not in rt["by_scorer"]:
        out.append("\nNeural rows appear when the run can load the embedding model "
                   "(`python scripts/evaluate.py` on a machine with internet for the first download).")
    out += ["", f"Per-query results ({names[used]}, top 3 after filtering by plan, service and service date):", "",
            "| Query | Expected section | Rank | Note |", "|---|---|---|---|"]
    for q in rt["queries"]:
        out.append(f"| {q['id']} \"{q['query']}\" | `{q['expected']}` | {q['rank'] or 'not in top 3'} | {q['note']} |")
    out += ["", "**Version and window tests**", "", "| Test | Must not return | Result |", "|---|---|---|"]
    for v in rt["version_tests"]:
        exs = v["excluded_better_ranked"]
        ex = ""
        if exs:
            ex = (f"{len(exs)} sections of `{exs[0]['chunk_id'].split('#')[0]}` outranked the answer before filtering "
                  f"(best: `{exs[0]['chunk_id']}` at #{exs[0]['rank']}); all excluded: {exs[0]['reason']}")
        out.append(f"| {v['id']} \"{v['query']}\" | `{v['must_not_return']}` | {_mark(v['ok'])} {ex or 'not returned'} |")
    out.append(f"\nResults outside their effective window, across all queries: **{rt['in_effect_violations']}**.")
    b = rt["by_scorer"]
    if "neural" in b:
        out += ["", f"**Reading.** Adding neural embeddings raised hybrid top-1 accuracy from {b['hybrid_lsa']['hit@1']}% "
                f"to {b['hybrid_neural']['hit@1']}% and top-3 from {b['hybrid_lsa']['hit@3']}% to {b['hybrid_neural']['hit@3']}%, "
                "mostly on the synonym and paraphrase queries (Q16–Q22). "
                + (f"Neural search alone scores higher still on top-1 ({b['neural']['hit@1']}%). Hybrid is kept because the "
                   "assignment calls for it and because keyword matching protects exact codes and rare terms "
                   "(Q18 \"neurostimulator\", Q19 \"CPT 72148\"). "
                   if b["neural"]["hit@1"] > b["hybrid_neural"]["hit@1"] else "")
                + "With 20 queries, each query is 5 points, so these are directional results, not significant ones."]
    return "\n".join(out)


def _injection(inj):
    out = ["| Variant | Quarantined | Recommendation | Outcome unchanged | Criteria using the document | Criteria changed |",
           "|---|---|---|---|---|---|"]
    for i in inj:
        out.append(f"| {i['variant']} | {_mark(i['quarantined'])} | {i['recommendation']} | {_mark(i['outcome_unchanged'])} | "
                   f"{', '.join(i['criteria_using_injected_doc']) or 'none'} | {', '.join(i['criteria_changed']) or 'none'} |")
    return "\n".join(out)


def _reliability(rel):
    out = ["| Scenario | Handled safely | Recommendation | RUN_ERROR shown | Expected behavior | Tool calls (failed) |",
           "|---|---|---|---|---|---|"]
    for s in rel:
        out.append(f"| {s['scenario']} | {_mark(s['ok'])} | {s.get('recommendation', '–')} | "
                   f"{'yes' if s.get('run_error') else 'no'} | {s['detail']} | {s.get('tool_calls', '–')} ({s.get('tool_failures', '–')}) |")
    return "\n".join(out)


def _tools(t):
    out = [f"{t['total_calls']} tool calls across the 9 cases, **{t['success_pct']}%** successful.", "",
           "| Tool | Calls | Success | Avg latency (ms) |", "|---|---|---|---|"]
    for k, v in t["by_tool"].items():
        out.append(f"| `{k}` | {v['calls']} | {v['success_pct']}% | {_v(v['avg_ms'])} |")
    return "\n".join(out)


def _tokens(res):
    rows = []
    for m in ("llm", "scripted"):
        t = res.get(m, {}).get("tokens")
        if t:
            rows.append(f"| {MODE_NAME[m]}{' (estimated, not a real model)' if t['estimated'] else ''} | {t['llm_calls']} | "
                        f"{t['avg_input_per_case']} | {t['avg_output_per_case']} | "
                        + ("– | – | " if t["estimated"] else f"${t['approx_cost_usd_per_case']} | ${t['approx_cost_usd_total']} | ")
                        + f"{t['rejected_items']} | {t['failures']} |")
    if not rows:
        return "No LLM run recorded yet. Run `python scripts/evaluate.py --mode llm` with an API key."
    basis = (res.get("llm") or res.get("scripted"))["tokens"]["price_basis"]
    return "\n".join(["| Mode | LLM calls | Avg input tokens / case | Avg output tokens / case | Approx. cost / case | "
                      "Approx. total (9 cases) | Model items rejected | Model failures |",
                      "|---|---|---|---|---|---|---|---|"] + rows +
                     ["", f"Price basis: {basis}. Rules-only mode uses no tokens."])


def _trace():
    p = EVAL / "trace_PA-005.json"
    if not p.exists():
        return ""
    t = json.loads(p.read_text(encoding="utf-8"))
    ev = t["events"]
    t0 = ev[0]["timestamp"]
    names = {e["toolCallId"]: (e["toolCallName"], e.get("agent", "")) for e in ev if e["type"] == "TOOL_CALL_START"}
    args = {e["toolCallId"]: e["delta"] for e in ev if e["type"] == "TOOL_CALL_ARGS"}
    out = ["| +ms | Event | Agent / step | Detail |", "|---|---|---|---|"]
    for e in ev:
        dt = e["timestamp"] - t0
        if e["type"] in ("RUN_STARTED", "RUN_FINISHED"):
            out.append(f"| {dt} | {e['type']} | supervisor | {json.dumps(e.get('result') or e.get('runId'))[:80]} |")
        elif e["type"] in ("STEP_STARTED", "STEP_FINISHED"):
            out.append(f"| {dt} | {e['type']} | {e['stepName']} | |")
        elif e["type"] == "TOOL_CALL_RESULT":
            name, agent = names[e["toolCallId"]]
            out.append(f"| {dt} | tool call {'✓' if e.get('ok') else '✗'} | {agent} | `{name}` {args.get(e['toolCallId'], '')[:70]} |")
        elif e["type"] == "CUSTOM" and e["name"] not in ("mcp_tools_discovered",):
            out.append(f"| {dt} | **{e['name']}** | supervisor | {json.dumps(e['value'])[:160].replace('|', '/')} |")
        elif e["type"] == "STATE_DELTA":
            out.append(f"| {dt} | STATE_DELTA | supervisor | {json.dumps(e['delta'])[:120]} |")
    r = t["result"]
    out += ["", f"Final: **{r['recommendation']}** under `{r['applicable_policy_id']}`, human review = {r['requires_human_review']}.",
            "Criteria: " + ", ".join(f"{c['criterion_id']} {c['status']}" for c in r["criteria"]) + ".",
            "", "Full event log, including every tool argument and result: `eval/trace_PA-005.json`."]
    return "\n".join(out)


def _failures(res):
    items = []
    for m, r in res.items():
        for c in r["cases"]:
            if not c["rec_ok"]:
                items.append(f"- **{MODE_NAME[m]}, {c['case_id']}**: expected {c['expected']}, got {c['got']}.")
            for e in c["criteria_errors"]:
                items.append(f"- **{MODE_NAME[m]}, {c['case_id']}** criterion {e}.")
        for s in r["reliability"]:
            if not s["ok"]:
                items.append(f"- **{MODE_NAME[m]}**, reliability scenario '{s['scenario']}' not handled: {s['detail']}.")
        for i in r["injection"]:
            if not i["quarantined"]:
                changed = ", ".join(i["criteria_changed"]) or "no criterion"
                items.append(f"- **{MODE_NAME[m]}**, injection '{i['variant']}' was **not quarantined**. The pattern "
                             f"detector did not match it. Recommendation {'unchanged' if i['outcome_unchanged'] else 'CHANGED'}; "
                             f"{changed} changed.")
        if r["citations"]["problems"]:
            items += [f"- **{MODE_NAME[m]}**, citation/schema: {p}" for p in r["citations"]["problems"]]
    rt = res["rules"]["retrieval"]
    for q in rt["queries"]:
        if q["rank"] != 1:
            items.append(f"- Retrieval {q['id']} \"{q['query']}\": expected `{q['expected']}`, "
                         f"{'rank ' + str(q['rank']) if q['rank'] else 'not in top 3'} (top: `{q['top3'][0]}`). {q['note']}")
    seen, uniq = set(), []
    for i in items:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    return "\n".join(uniq) if uniq else "No failures in the recorded runs."


def write_report():
    res = _load()
    base = res["rules"]
    modes = ", ".join(f"{MODE_NAME[m]} ({res[m]['generated_at']}, commit `{res[m]['git_commit']}`"
                      + (f", model `{res[m]['model']}`" if res[m].get("model") else "") + ")" for m in res)
    doc = f"""# Evaluation report

Generated by `scripts/evaluate.py`. Do not edit by hand; re-run the script.

Runs included: {modes}.

## How to reproduce

```bash
python scripts/evaluate.py                  # rules only: deterministic, no API key, about 10 s
python scripts/evaluate.py --mode llm       # real Claude: needs ANTHROPIC_API_KEY in .env
python scripts/evaluate.py --mode scripted  # scripted model: checks the LLM code path, not model quality
pytest                                      # unit and integration tests
```

Raw results: `eval/results_<mode>.json`. Labels: `data/labels.json` (cases) and `eval/retrieval_queries.json` (retrieval).

## Method

- **Labeled cases.** 9 synthetic cases covering every demo scenario plus a policy exclusion. Each label
  states the expected recommendation, policy version, the status of each criterion, missing criteria,
  exceptions, exclusions, red-flag pathway, and whether an injection is present.
- **All checks are programmatic.** No LLM is used as a judge. Citations are re-verified from disk, independently of
  the pipeline's own check: a note quote must appear verbatim at its recorded offset, a FHIR ID must exist in
  the patient's bundle, and a policy ID must resolve to a real section.
- **Modes.** *Rules only* is fully deterministic. *Claude (live)* varies from run to run (the SDK exposes no
  sampling controls). *Scripted model* replays rule-extracted facts through the LLM path to show that prompting,
  parsing, verification and fallbacks work; it says nothing about model quality.
- **Limits of the labels.** The developer wrote the labels and the dataset. They are a regression suite for the
  designed scenarios, not a measure of real-world accuracy. 9 cases and 20 retrieval queries are small samples.

## Summary

{_summary_table(res)}

## Per case

{_cases(res)}

## Retrieval quality

{_retrieval(base['retrieval'])}

Semantic search uses neural embeddings (`BAAI/bge-small-en-v1.5`, local ONNX via fastembed, no API key) and
falls back to LSA if the model cannot load. Q16–Q22 are written to need meaning rather than shared words
(synonyms, lay descriptions, paraphrases), which is where keyword search and LSA are weakest.

## Prompt-injection resistance

PA-004 is re-run with five different malicious attachments in place of NOTE-006.

{_injection(base['injection'])}

There are three layers of defense:

1. **Detection.** A document with instruction-like text is quarantined: no facts are taken from it and it is
   never sent to the model. The pattern detector catches 3 of the 5 variants.
2. **Source trust.** Documents from unverified sources (here, an inbound fax) never establish facts. Their
   claims are listed for the reviewer to verify and enter through the review form. This layer was added
   after an earlier run of this evaluation showed INJ-5's false "12 weeks of therapy" claim turning C-02 and
   C-03 to SUPPORTED. The recommendation was still correct then, but only because C-01 failed on its own.
3. **Structure.** Even content that gets through cannot set the recommendation, because fixed rules over
   cited facts decide, and the output validator rejects support from quarantined evidence.

## Reliability and failure behavior

{_reliability(base['reliability'])}

## Tool calls and latency

{_tools(base['tools'])}

Latency is in-process (MCP and A2A over in-memory transports). With the four services running as separate
processes on one machine, each tool call adds a few milliseconds of HTTP overhead. Live Claude calls dominate
latency in LLM mode (see `results_llm.json`).

## Token usage and approximate cost

{_tokens(res)}

## Full multi-agent trace (PA-005, agent disagreement)

{_trace()}

## Failure analysis

{_failures(res)}

### Known limitations

- **Pattern-based injection detection** misses some phrasings (INJ-4, INJ-5). Source trust and the rules-decide
  structure contain them, but a malicious document from a *trusted* source (for example, a compromised EHR note)
  that avoids the patterns could still contribute facts. Next step: a trained injection classifier, and
  cross-checking note claims against structured data, which already happens for therapy weeks.
- **Source trust is coarse.** Everything that isn't from the EHR is unverified. Legitimate outside records also
  arrive by fax, and today they need a reviewer to confirm them.
- **Rule-based extraction** is tuned to the synthetic notes. The LLM extractor generalizes better but varies from
  run to run. Both are bounded by the same verification.
- **Small evaluation sets** (9 cases, 20 queries), with labels written by the developer.
- **One service and one policy family** (lumbar MRI). Exclusions and red flags are keyword-detected.
"""
    (ROOT / "docs" / "EVALUATION.md").write_text(doc, encoding="utf-8")
