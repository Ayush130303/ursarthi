"""Hybrid, preference-aware retrieval.

  question ─┬─ place knowledge (sources of this trip): vector search + keyword search
            └─ the user's own history (earlier questions & answers): vector search
                         │
               Reciprocal Rank Fusion (rank-based, so different score scales mix safely)
                         │
               re-rank: + semantic similarity, + interest cue / category match,
                        - chunks mentioning things the user avoids
                         │
               Maximal Marginal Relevance → k diverse passages, numbered [1]..[k]
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Chunk, Source
from app.rag.embeddings import get_embedder
from app.rag.memory import Profile
from app.rag.vector_store import Scope, get_vector_store


@dataclass
class Passage:
    id: int
    content: str
    section: str
    category: str
    title: str
    url: str | None
    kind: str                # wikipedia | wikivoyage | history
    source_id: int
    lat: float | None = None
    lon: float | None = None
    relevance: float = 0.0
    boost: float = 0.0
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def to_source(self, n: int) -> dict:
        snippet = self.content if len(self.content) < 280 else self.content[:280].rsplit(" ", 1)[0] + "…"
        return {"n": n, "title": self.title, "section": self.section, "kind": self.kind, "url": self.url,
                "snippet": snippet, "score": round(self.score, 3), "reasons": self.reasons}


def _hits_to_rank(lists: list[tuple[float, list[tuple[int, float]]]], k_rrf: int) -> dict[int, float]:
    fused: dict[int, float] = defaultdict(float)
    for weight, hits in lists:
        for rank, (cid, _) in enumerate(hits):
            fused[cid] += weight / (k_rrf + rank + 1)
    return fused


def retrieve(db: Session, query: str, profile: Profile, source_ids: list[int], user_id: int | None,
             k: int | None = None, history: bool = True) -> list[Passage]:
    s = get_settings()
    k = k or s.retrieval_top_k
    store = get_vector_store()
    q = get_embedder().embed_query(query)

    lists = []
    if source_ids:
        place = Scope(source_ids=source_ids)
        lists += [(1.0, store.vector_search(db, q, s.retrieval_candidates, place)),
                  (0.8, store.keyword_search(db, query, s.retrieval_candidates, place))]
    if history and user_id:
        lists.append((0.6, store.vector_search(db, q, 6, Scope(owner_id=user_id))))
    fused = _hits_to_rank(lists, s.rrf_k)
    if not fused:
        return []

    ids = [c for c, _ in sorted(fused.items(), key=lambda x: -x[1])[: s.retrieval_candidates]]
    rows = db.execute(select(Chunk, Source).join(Source, Source.id == Chunk.source_id).where(Chunk.id.in_(ids))).all()
    vectors = store.get_vectors(db, ids)
    top = max(fused.values())
    passages = []
    for chunk, src in rows:
        p = Passage(id=chunk.id, content=chunk.content, section=chunk.section, category=chunk.category,
                    title=src.title, url=src.url, kind=src.kind, source_id=src.id, lat=src.lat, lon=src.lon)
        p.relevance = fused[chunk.id] / top
        if (v := vectors.get(chunk.id)) is not None:
            p.relevance = 0.7 * p.relevance + 0.3 * max(0.0, float(v @ q))
        p.boost, p.reasons = preference_boost(chunk.content, chunk.category, profile)
        if src.kind == "history":
            p.reasons.insert(0, "from your earlier questions")
        p.score = p.relevance + p.boost
        passages.append(p)
    return mmr(passages, vectors, k, s.mmr_lambda)


def preference_boost(text: str, category: str, profile: Profile) -> tuple[float, list[str]]:
    lowered = text.lower()
    boost, reasons = 0.0, []
    for interest, cues in profile.cues():
        if any(re.search(rf"\b{re.escape(c)}", lowered) for c in cues):
            boost += 0.06
            reasons.append(f"matches your interest in {interest}")
    if profile.food not in ("anything", "non-vegetarian") and category in ("food", "nightlife") and profile.food in lowered:
        boost += 0.06
        reasons.append(f"mentions {profile.food} food")
    for thing in profile.avoid:
        if thing and re.search(rf"\b{re.escape(thing)}", lowered):
            boost -= 0.15
            reasons.append(f"mentions {thing}, which you avoid")
    return min(boost, 0.2), reasons[:3]


def mmr(cands: list[Passage], vectors: dict[int, np.ndarray], k: int, lam: float) -> list[Passage]:
    pool = sorted(cands, key=lambda c: -c.score)
    if not pool:
        return []
    best = pool[0].score or 1.0
    chosen: list[Passage] = []
    while pool and len(chosen) < k:
        def value(c: Passage) -> float:
            v = vectors.get(c.id)
            sims = [float(v @ vectors[x.id]) for x in chosen if v is not None and x.id in vectors]
            return lam * c.score / best - (1 - lam) * (max(sims) if sims else 0.0)
        pick = max(pool, key=value)
        chosen.append(pick)
        pool.remove(pick)
    return chosen


def format_context(passages: list[Passage], max_chars: int = 6500) -> str:
    out, used = [], 0
    for n, p in enumerate(passages, 1):
        block = f"[{n}] {p.title} — {p.section}\n{p.content}"
        if used + len(block) > max_chars:
            break
        out.append(block)
        used += len(block)
    return "\n\n".join(out)
