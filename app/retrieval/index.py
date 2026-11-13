"""Hybrid, version-aware policy retrieval.

* Ingests two document types: Markdown policies (with a JSON front-matter block) and the JSON
  exception register.
* Ranks with BM25 (keyword) and a dense vector score, fused with reciprocal rank fusion.
  The dense scorer is LSA (TF-IDF + truncated SVD) so the demo runs offline with no API key;
  set EMBEDDINGS=sentence-transformers to use a neural embedder instead.
* Filters by policy family, plan, service code and effective date *after* ranking the full
  corpus, so the result can show exactly which better-scoring documents were excluded and why.
* Every chunk has a stable ID: <doc_id>#<section>.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np
from pydantic import BaseModel
from rank_bm25 import BM25Okapi
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer

from app import config

_TOKEN = re.compile(r"[a-z0-9]+")
_SECTION = re.compile(r"^#{2,3}\s+(S\d+(?:\.\d+)?)\s+(.*)$")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    doc_type: str               # policy_md | exception_json
    section: str
    heading: str
    text: str
    family: str
    plans: list[str]
    service_codes: list[str]
    effective_start: str
    effective_end: str | None
    version: int | None = None


class Hit(BaseModel):
    chunk_id: str
    doc_id: str
    section: str
    heading: str
    text: str
    score: float
    rank_unfiltered: int
    bm25_rank: int
    dense_rank: int


class Excluded(BaseModel):
    chunk_id: str
    doc_id: str
    rank_unfiltered: int
    reason: str


class SearchResult(BaseModel):
    query: str
    filters: dict
    hits: list[Hit]
    excluded_better_ranked: list[Excluded]


def load_policy_md(path: Path) -> tuple[dict, list[Chunk]]:
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    _, fm, body = raw.split("---", 2)
    meta = json.loads(fm)
    chunks: list[Chunk] = []
    current, heading, buf = None, "", []

    def flush():
        if current and "".join(buf).strip():
            chunks.append(Chunk(
                chunk_id=f"{meta['policy_id']}#{current}", doc_id=meta["policy_id"], doc_type="policy_md",
                section=current, heading=heading, text=" ".join(l.strip() for l in buf if l.strip()),
                family=meta["policy_family"], plans=meta["plans"], service_codes=meta["service_codes"],
                effective_start=meta["effective_start"], effective_end=meta["effective_end"],
                version=meta["version"]))

    for line in body.splitlines():
        m = _SECTION.match(line)
        if m:
            flush()
            current, heading, buf = m.group(1), m.group(2), []
        elif current:
            buf.append(line)
    flush()
    return meta, chunks


def load_exceptions(path: Path) -> tuple[list[dict], list[Chunk]]:
    items = json.loads(path.read_text(encoding="utf-8"))
    chunks = [Chunk(
        chunk_id=f"{e['exception_id']}#{e['section']}", doc_id=e["exception_id"], doc_type="exception_json",
        section=e["section"], heading=f"Exception {e['exception_id']}", text=e["text"],
        family=e["policy_family"], plans=e["plans"], service_codes=["72148"],
        effective_start=e["effective_start"], effective_end=e["effective_end"]) for e in items]
    return items, chunks


def in_effect(start: str, end: str | None, on: str) -> bool:
    d = date.fromisoformat(on)
    return date.fromisoformat(start) <= d and (end is None or d <= date.fromisoformat(end))


class _LsaEmbedder:
    def __init__(self, texts: list[str]):
        self.vec = TfidfVectorizer(sublinear_tf=True, ngram_range=(1, 2), stop_words="english")
        m = self.vec.fit_transform(texts)
        self.svd = TruncatedSVD(n_components=max(2, min(24, m.shape[0] - 1, m.shape[1] - 1)), random_state=7)
        self.doc = self._norm(self.svd.fit_transform(m))

    @staticmethod
    def _norm(x):
        return x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-9)

    def score(self, query: str) -> np.ndarray:
        q = self._norm(self.svd.transform(self.vec.transform([query])))
        return self.doc @ q[0]


class _StEmbedder:  # optional neural embedder
    def __init__(self, texts: list[str]):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2"))
        self.doc = self.model.encode(texts, normalize_embeddings=True)

    def score(self, query: str) -> np.ndarray:
        return self.doc @ self.model.encode([query], normalize_embeddings=True)[0]


class PolicyIndex:
    RRF_K = 60

    def __init__(self, data_dir: Path = config.DATA_DIR):
        self.policies: dict[str, dict] = {}
        self.chunks: list[Chunk] = []
        for p in sorted((data_dir / "policies").glob("*.md")):
            meta, ch = load_policy_md(p)
            self.policies[meta["policy_id"]] = meta
            self.chunks += ch
        self.exceptions, ex_chunks = load_exceptions(data_dir / "policies" / "exceptions.json")
        self.chunks += ex_chunks
        self.by_id = {c.chunk_id: c for c in self.chunks}
        texts = [f"{c.heading}. {c.text}" for c in self.chunks]
        self.bm25 = BM25Okapi([tokenize(t) for t in texts])
        self.dense = _StEmbedder(texts) if os.getenv("EMBEDDINGS") == "sentence-transformers" else _LsaEmbedder(texts)

    # ---- ranking -------------------------------------------------------------------------
    def _rank(self, query: str) -> list[tuple[int, float, int, int]]:
        bm = self.bm25.get_scores(tokenize(query))
        dn = self.dense.score(query)
        bm_rank = {i: r for r, i in enumerate(np.argsort(-bm), start=1)}
        dn_rank = {i: r for r, i in enumerate(np.argsort(-dn), start=1)}
        fused = [(i, 1 / (self.RRF_K + bm_rank[i]) + 1 / (self.RRF_K + dn_rank[i]), bm_rank[i], dn_rank[i])
                 for i in range(len(self.chunks))]
        return sorted(fused, key=lambda x: -x[1])

    def _exclusion_reason(self, c: Chunk, family, service_code, plan, service_date) -> str | None:
        if family and c.family != family:
            return f"different policy family ({c.family})"
        if service_code and service_code not in c.service_codes:
            return f"service code {service_code} not covered"
        if plan and plan not in c.plans:
            return f"plan {plan} not listed ({', '.join(c.plans)})"
        if service_date and not in_effect(c.effective_start, c.effective_end, service_date):
            return (f"not in effect on {service_date} "
                    f"(effective {c.effective_start} to {c.effective_end or 'open'})")
        return None

    def search(self, query: str, *, service_code: str | None = None, plan: str | None = None,
               service_date: str | None = None, family: str | None = None, k: int = 5) -> SearchResult:
        ranked = self._rank(query)
        hits, excluded = [], []
        for pos, (i, score, br, dr) in enumerate(ranked, start=1):
            c = self.chunks[i]
            reason = self._exclusion_reason(c, family, service_code, plan, service_date)
            if reason:
                if len(hits) < k:  # it outranked something we will return
                    excluded.append(Excluded(chunk_id=c.chunk_id, doc_id=c.doc_id, rank_unfiltered=pos, reason=reason))
                continue
            if len(hits) < k:
                hits.append(Hit(chunk_id=c.chunk_id, doc_id=c.doc_id, section=c.section, heading=c.heading,
                                text=c.text, score=round(score, 5), rank_unfiltered=pos, bm25_rank=br, dense_rank=dr))
        return SearchResult(query=query, filters={"service_code": service_code, "plan": plan,
                                                  "service_date": service_date, "family": family},
                            hits=hits, excluded_better_ranked=excluded)

    # ---- version selection and exceptions ---------------------------------------------
    def applicable_policies(self, family: str, service_code: str, plan: str, service_date: str) -> tuple[list[dict], list[str]]:
        chosen, notes = [], []
        for pid, meta in sorted(self.policies.items()):
            if meta["policy_family"] != family or service_code not in meta["service_codes"]:
                continue
            if plan not in meta["plans"]:
                notes.append(f"{pid}: plan {plan} not listed")
            elif not in_effect(meta["effective_start"], meta["effective_end"], service_date):
                notes.append(f"{pid}: effective {meta['effective_start']} to {meta['effective_end'] or 'open'}, "
                             f"does not cover {service_date}")
            else:
                chosen.append(meta)
        return chosen, notes

    def applicable_exceptions(self, policy_id: str, plan: str, service_date: str) -> tuple[list[dict], list[str]]:
        applied, notes = [], []
        for e in self.exceptions:
            if e["applies_to_policy"] != policy_id:
                notes.append(f"{e['exception_id']}: applies to {e['applies_to_policy']}, not {policy_id}")
            elif plan not in e["plans"]:
                notes.append(f"{e['exception_id']}: plan {plan} not eligible")
            elif not in_effect(e["effective_start"], e["effective_end"], service_date):
                notes.append(f"{e['exception_id']}: outside window {e['effective_start']} to {e['effective_end']}")
            else:
                applied.append(e)
        return applied, notes

    def chunk_text(self, chunk_id: str) -> str | None:
        c = self.by_id.get(chunk_id)
        return c.text if c else None


@lru_cache(maxsize=1)
def get_index() -> PolicyIndex:
    return PolicyIndex()
