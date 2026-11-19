"""Vector stores behind one interface.

PgVectorStore    – PostgreSQL + pgvector: HNSW index for vectors, GIN index for
                   full-text keyword search. Used automatically when available.
NumpyVectorStore – fallback for PostgreSQL without the extension (or SQLite):
                   cosine similarity + BM25 in memory, reloaded when the index
                   version in sarthi_meta changes (safe with several workers).

A search is always *scoped*: either to a set of knowledge sources (the places
on one trip) or to one user's private history. That keeps retrieval relevant
and guarantees users never see each other's data.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass

import numpy as np
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Chunk, ChunkEmbedding, Meta
from app.rag.embeddings import get_embedder, tokenize

log = logging.getLogger(__name__)


@dataclass
class Scope:
    source_ids: list[int] | None = None  # public knowledge for these sources
    owner_id: int | None = None          # OR: this user's private history

    def __post_init__(self):
        if (self.source_ids is None) == (self.owner_id is None):
            raise ValueError("Scope needs exactly one of source_ids or owner_id")


def get_meta(db: Session, key: str) -> str | None:
    row = db.get(Meta, key)
    return row.value if row else None


def set_meta(db: Session, key: str, value: str) -> None:
    row = db.get(Meta, key)
    if row:
        row.value = value
    else:
        db.add(Meta(key=key, value=value))


class VectorStore(ABC):
    name: str

    def setup(self, db: Session) -> None:
        pass

    @abstractmethod
    def upsert(self, db: Session, chunk_ids: list[int], vectors: np.ndarray) -> None: ...

    @abstractmethod
    def vector_search(self, db: Session, q: np.ndarray, k: int, scope: Scope) -> list[tuple[int, float]]: ...

    @abstractmethod
    def keyword_search(self, db: Session, query: str, k: int, scope: Scope) -> list[tuple[int, float]]: ...

    @abstractmethod
    def get_vectors(self, db: Session, chunk_ids: list[int]) -> dict[int, np.ndarray]: ...

    @abstractmethod
    def indexed_ids(self, db: Session) -> set[int]: ...

    def invalidate(self, db: Session) -> None:
        set_meta(db, "index_version", str(time.time_ns()))

    def backfill(self, db: Session, batch: int = 64) -> int:
        """Embed chunks that have no vector (e.g. after switching embedding model)."""
        have = self.indexed_ids(db)
        missing = [cid for cid in db.scalars(select(Chunk.id)) if cid not in have]
        for i in range(0, len(missing), batch):
            rows = db.execute(select(Chunk.id, Chunk.section, Chunk.content).where(Chunk.id.in_(missing[i:i + batch]))).all()
            self.upsert(db, [r.id for r in rows], get_embedder().embed_documents([f"{r.section}: {r.content}" for r in rows]))
        if missing:
            db.commit()
            log.info("Backfilled %d embeddings", len(missing))
        return len(missing)


# ---------------------------------------------------------------- pgvector --

_SAFE = re.compile(r"^[a-z0-9]+$")


def _vec(v: np.ndarray) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in v) + "]"


class PgVectorStore(VectorStore):
    name = "pgvector"

    def setup(self, db: Session) -> None:
        dim = get_embedder().dim
        db.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        current = db.execute(text(
            "SELECT atttypmod FROM pg_attribute WHERE attrelid = to_regclass('sarthi_vectors') AND attname = 'embedding'"
        )).scalar()
        if current is not None and current != dim:
            log.warning("Embedding size changed (%s -> %s): rebuilding vectors", current, dim)
            db.execute(text("DROP TABLE sarthi_vectors"))
        db.execute(text(
            f"CREATE TABLE IF NOT EXISTS sarthi_vectors (chunk_id INTEGER PRIMARY KEY "
            f"REFERENCES sarthi_chunks(id) ON DELETE CASCADE, embedding vector({dim}) NOT NULL)"
        ))
        db.execute(text("CREATE INDEX IF NOT EXISTS sarthi_vectors_hnsw ON sarthi_vectors USING hnsw (embedding vector_cosine_ops)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS sarthi_chunks_fts ON sarthi_chunks USING gin (to_tsvector('english', content))"))
        db.commit()

    def upsert(self, db, chunk_ids, vectors):
        if chunk_ids:
            db.execute(text(
                "INSERT INTO sarthi_vectors (chunk_id, embedding) VALUES (:id, CAST(:v AS vector)) "
                "ON CONFLICT (chunk_id) DO UPDATE SET embedding = EXCLUDED.embedding"
            ), [{"id": c, "v": _vec(v)} for c, v in zip(chunk_ids, vectors)])

    @staticmethod
    def _where(scope: Scope, params: dict) -> str:
        if scope.owner_id is not None:
            params["uid"] = scope.owner_id
            return "c.owner_id = :uid"
        if not scope.source_ids:
            return "FALSE"
        params["sids"] = list(scope.source_ids)
        return "c.source_id = ANY(:sids) AND c.owner_id IS NULL"

    def vector_search(self, db, q, k, scope):
        params = {"q": _vec(q), "k": k}
        where = self._where(scope, params)
        rows = db.execute(text(
            f"SELECT v.chunk_id, 1 - (v.embedding <=> CAST(:q AS vector)) FROM sarthi_vectors v "
            f"JOIN sarthi_chunks c ON c.id = v.chunk_id WHERE {where} "
            f"ORDER BY v.embedding <=> CAST(:q AS vector) LIMIT :k"
        ), params).all()
        return [(r[0], float(r[1])) for r in rows]

    def keyword_search(self, db, query, k, scope):
        tokens = [t for t in tokenize(query) if _SAFE.match(t)][:12]
        if not tokens:
            return []
        params = {"tsq": " | ".join(tokens), "k": k}
        where = self._where(scope, params)
        rows = db.execute(text(
            "SELECT c.id, ts_rank_cd(to_tsvector('english', c.content), to_tsquery('english', :tsq)) AS s "
            f"FROM sarthi_chunks c WHERE to_tsvector('english', c.content) @@ to_tsquery('english', :tsq) AND {where} "
            "ORDER BY s DESC LIMIT :k"
        ), params).all()
        return [(r[0], float(r[1])) for r in rows]

    def get_vectors(self, db, chunk_ids):
        if not chunk_ids:
            return {}
        rows = db.execute(text("SELECT chunk_id, embedding::text FROM sarthi_vectors WHERE chunk_id = ANY(:ids)"),
                          {"ids": list(chunk_ids)}).all()
        return {r[0]: np.asarray(r[1].strip("[]").split(","), dtype=np.float32) for r in rows}

    def indexed_ids(self, db):
        return set(db.execute(text("SELECT chunk_id FROM sarthi_vectors")).scalars())


# ------------------------------------------------------------------- numpy --

class NumpyVectorStore(VectorStore):
    name = "numpy"

    def __init__(self):
        self._lock = threading.Lock()
        self._version: str | None = None
        self._ids = np.zeros(0, dtype=np.int64)
        self._mat = np.zeros((0, 1), dtype=np.float32)
        self._src = np.zeros(0, dtype=np.int64)
        self._owner = np.zeros(0, dtype=np.int64)
        self._tfs: list[Counter] = []
        self._len = np.zeros(0, dtype=np.float32)
        self._df: Counter = Counter()
        self._pos: dict[int, int] = {}

    def upsert(self, db, chunk_ids, vectors):
        if not chunk_ids:
            return
        db.execute(delete(ChunkEmbedding).where(ChunkEmbedding.chunk_id.in_(chunk_ids)))
        db.add_all(ChunkEmbedding(chunk_id=c, embedding=[round(float(x), 6) for x in v]) for c, v in zip(chunk_ids, vectors))
        self.invalidate(db)

    def _load(self, db: Session) -> None:
        version = get_meta(db, "index_version") or "0"
        if version == self._version:
            return
        with self._lock:
            if version == self._version:
                return
            rows = db.execute(select(Chunk.id, Chunk.source_id, Chunk.owner_id, Chunk.section, Chunk.content,
                                     ChunkEmbedding.embedding).join(ChunkEmbedding, ChunkEmbedding.chunk_id == Chunk.id)).all()
            self._ids = np.asarray([r.id for r in rows], dtype=np.int64)
            self._mat = np.asarray([r.embedding for r in rows], dtype=np.float32) if rows else np.zeros((0, 1), np.float32)
            self._src = np.asarray([r.source_id for r in rows], dtype=np.int64)
            self._owner = np.asarray([r.owner_id if r.owner_id is not None else -1 for r in rows], dtype=np.int64)
            self._tfs = [Counter(tokenize(f"{r.section} {r.content}")) for r in rows]
            self._len = np.asarray([sum(t.values()) for t in self._tfs], dtype=np.float32)
            self._df = Counter()
            for tf in self._tfs:
                self._df.update(tf.keys())
            self._pos = {int(c): i for i, c in enumerate(self._ids)}
            self._version = version

    def _mask(self, scope: Scope) -> np.ndarray:
        if scope.owner_id is not None:
            return self._owner == scope.owner_id
        return np.isin(self._src, scope.source_ids or []) & (self._owner == -1)

    def vector_search(self, db, q, k, scope):
        self._load(db)
        if not len(self._ids) or self._mat.shape[1] != len(q):
            return []
        mask = self._mask(scope)
        scores = np.where(mask, self._mat @ q, -np.inf)
        top = np.argsort(-scores)[:k]
        return [(int(self._ids[i]), float(scores[i])) for i in top if np.isfinite(scores[i])]

    def keyword_search(self, db, query, k, scope, k1=1.4, b=0.75):
        self._load(db)
        terms = set(tokenize(query))
        if not terms or not len(self._ids):
            return []
        idx = np.nonzero(self._mask(scope))[0]
        n, avgdl = len(self._ids), float(self._len.mean()) or 1.0
        scores: dict[int, float] = {}
        for term in terms:
            df = self._df.get(term, 0)
            if not df:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for i in idx:
                tf = self._tfs[i].get(term, 0)
                if tf:
                    scores[i] = scores.get(i, 0) + idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * self._len[i] / avgdl))
        best = sorted(scores.items(), key=lambda x: -x[1])[:k]
        return [(int(self._ids[i]), float(s)) for i, s in best]

    def get_vectors(self, db, chunk_ids):
        self._load(db)
        return {c: self._mat[self._pos[c]] for c in chunk_ids if c in self._pos}

    def indexed_ids(self, db):
        return set(db.scalars(select(ChunkEmbedding.chunk_id)))


# ------------------------------------------------------------------------------

_store: VectorStore | None = None


def init_vector_store(db: Session) -> VectorStore:
    global _store
    s = get_settings()
    store: VectorStore = NumpyVectorStore()
    if s.vector_backend in ("auto", "pgvector") and s.is_postgres:
        try:
            candidate = PgVectorStore()
            candidate.setup(db)
            store = candidate
        except Exception as exc:
            db.rollback()
            if s.vector_backend == "pgvector":
                raise RuntimeError("pgvector is not installed in PostgreSQL. Install it or set VECTOR_BACKEND=numpy") from exc
            log.warning("pgvector not available (%s): using the numpy vector store", type(exc).__name__)
    marker = get_embedder().name
    if get_meta(db, "embedding_model") != marker:  # model changed: old vectors are meaningless
        db.execute(delete(ChunkEmbedding))
        if isinstance(store, PgVectorStore):
            db.execute(text("TRUNCATE sarthi_vectors"))
        set_meta(db, "embedding_model", marker)
        store.invalidate(db)
        db.commit()
    _store = store
    return store


def get_vector_store() -> VectorStore:
    if _store is None:
        raise RuntimeError("Vector store not initialised")
    return _store
