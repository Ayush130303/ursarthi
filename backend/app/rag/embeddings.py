"""Embedding providers behind one interface.

All vectors are L2-normalised so cosine similarity == dot product.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
import time
from abc import ABC, abstractmethod

import numpy as np

from app.core.cache import get_cache
from app.core.config import get_settings

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    """a an the and or but if of to in on at for from by with about as is are was were be been being
    it its this that these those i me my we our you your he she they them their what which who whom
    how when where why can could should would will just do does did not no so than then there here
    very too also into over under up down out more most some any all each other such only own same
    get got have has had please tell give want like need know""".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


def _normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


class EmbeddingProvider(ABC):
    name: str
    dim: int

    @abstractmethod
    def _embed(self, texts: list[str]) -> np.ndarray: ...

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _normalize(self._embed(texts).astype(np.float32))

    def embed_query(self, text: str) -> np.ndarray:
        key = f"emb:{self.name}:{hashlib.sha1(text.encode()).hexdigest()}"
        cache = get_cache()
        cached = cache.get(key)
        if cached is not None:
            return np.asarray(cached, dtype=np.float32)
        vec = self.embed_documents([text])[0]
        cache.set(key, vec.tolist(), ttl=3600)
        return vec


class HashEmbedding(EmbeddingProvider):
    """Feature-hashing embedding (unigrams + bigrams, signed, sublinear TF).

    Zero dependencies and fully offline; captures lexical similarity well
    enough for development, CI and demos without API keys.
    """

    def __init__(self, dim: int = 384):
        self.dim = dim
        self.name = f"hash-{dim}"

    def _bucket(self, token: str) -> tuple[int, float]:
        h = int.from_bytes(hashlib.md5(token.encode()).digest()[:8], "little")
        return h % self.dim, 1.0 if (h >> 63) & 1 else -1.0

    def _embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            toks = tokenize(text)
            feats: dict[str, float] = {}
            for t in toks:
                feats[t] = feats.get(t, 0) + 1.0
                if len(t) > 5:  # light stemming signal: shared prefixes
                    feats["p:" + t[:5]] = feats.get("p:" + t[:5], 0) + 0.5
            for a, b in zip(toks, toks[1:]):
                feats[f"{a}_{b}"] = feats.get(f"{a}_{b}", 0) + 0.7
            for feat, tf in feats.items():
                idx, sign = self._bucket(feat)
                out[row, idx] += sign * (1 + math.log(tf))
        return out


class OpenAIEmbedding(EmbeddingProvider):
    def __init__(self, api_key: str, model: str, dim: int, base_url: str | None = None):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=60, max_retries=3)
        self.model = model
        self.dim = dim
        self.name = f"openai-{model}-{dim}"

    def _embed(self, texts: list[str]) -> np.ndarray:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), 96):
            batch = [t[:8000] for t in texts[i:i + 96]]
            kwargs = {"model": self.model, "input": batch}
            if self.model.startswith("text-embedding-3"):
                kwargs["dimensions"] = self.dim
            resp = self.client.embeddings.create(**kwargs)
            vectors.extend(d.embedding for d in resp.data)
        return np.asarray(vectors, dtype=np.float32)


class FastEmbedEmbedding(EmbeddingProvider):
    """Local ONNX sentence embeddings (pip install fastembed)."""

    def __init__(self, model: str):
        from fastembed import TextEmbedding

        self.model = TextEmbedding(model_name=model)
        probe = next(iter(self.model.embed(["probe"])))
        self.dim = len(probe)
        self.name = f"fastembed-{model}"

    def _embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(list(self.model.embed(texts)), dtype=np.float32)


_provider: EmbeddingProvider | None = None
_lock = threading.Lock()


def get_embedder() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        with _lock:
            if _provider is None:
                _provider = _build_provider()
                log.info("Embedding provider: %s (dim=%d)", _provider.name, _provider.dim)
    return _provider


def _build_provider() -> EmbeddingProvider:
    s = get_settings()
    choice = s.embedding_provider
    if choice == "auto":
        try:
            return FastEmbedEmbedding(s.fastembed_model)
        except Exception as exc:  # not installed or model download failed
            log.info("fastembed unavailable (%s); using offline hash embeddings", type(exc).__name__)
            return HashEmbedding(384)
    if choice == "fastembed":
        return FastEmbedEmbedding(s.fastembed_model)
    if choice == "openai":
        if not s.openai_api_key:
            raise RuntimeError("EMBEDDING_PROVIDER=openai requires OPENAI_API_KEY")
        return OpenAIEmbedding(s.openai_api_key, s.embedding_model, s.embedding_dim)
    return HashEmbedding(384)


def reset_embedder_for_tests(provider: EmbeddingProvider | None = None) -> None:
    global _provider
    _provider = provider


def timed_embed(texts: list[str]) -> np.ndarray:
    start = time.perf_counter()
    vecs = get_embedder().embed_documents(texts)
    log.debug("Embedded %d texts in %.0f ms", len(texts), (time.perf_counter() - start) * 1000)
    return vecs
