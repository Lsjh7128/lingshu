"""Local deterministic hashed n-gram embedder for the Mem0 comparison lab.

Why this exists (see PROTOCOL.md §4): the lab host has **no embedding API**.
DeepSeek does not serve embeddings, and the experimental environment is
offline for model downloads.  So Mem0's neural embedder is replaced by a
deterministic, network-free, CPU-only hashed character n-gram embedder.

This is **not** a neural embedder and must be labelled as such in the paper:

    Mem0 vX.Y.Z, official library; LLM=DeepSeek; embedder=local hashed bigram
    (not a neural embedder)

Algorithm (fully reproducible):
  1. normalise text: Unicode NFKC, lowercase, collapse whitespace;
  2. reuse word/marker tokens: split on non-alphanumeric-non-CJK boundaries,
     every token contributes char bigrams (and the token itself);
  3. each feature -> BLAKE2b-64 digest -> index (low 32 bits) and sign (bit 32);
     add sign * weight into a 256-dim float vector (signed feature hashing);
  4. L2-normalise.

Stability: BLAKE2b is platform independent, so the same text always maps to the
same vector, on every run and every machine.  No randomness, no network.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata

try:  # use mem0's own base class so the object is a first-class embedder
    from mem0.embeddings.base import EmbeddingBase  # type: ignore
except Exception:  # pragma: no cover - only if mem0 is absent
    EmbeddingBase = object  # type: ignore

DEFAULT_DIMS = 256
_CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_SPLIT_RE = re.compile(r"[^A-Za-z0-9%s]+" % _CJK)


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text)).lower()
    return re.sub(r"\s+", " ", text).strip()


def _features(text: str):
    """Yield (feature_string, weight) pairs."""
    norm = _normalise(text)
    # whole-string backoff feature keeps short queries from being all-zero
    yield "s:" + norm, 0.5
    for token in _SPLIT_RE.split(norm):
        token = token.strip()
        if not token:
            continue
        yield "w:" + token, 1.0
        if len(token) == 1:
            yield "c1:" + token, 0.75
            continue
        for i in range(len(token) - 1):
            yield "b:" + token[i : i + 2], 1.0


def _digest(feature: str) -> int:
    return int.from_bytes(hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(), "big")


def embed_text(text: str, dims: int = DEFAULT_DIMS) -> list:
    vec = [0.0] * dims
    for feature, weight in _features(text):
        d = _digest(feature)
        idx = d & 0xFFFFFFFF
        sign = 1.0 if (d >> 32) & 1 else -1.0
        vec[idx % dims] += sign * weight
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


class HashedBigramEmbedder(EmbeddingBase):  # type: ignore[misc]
    """mem0 `EmbeddingBase` implementation backed by `embed_text`."""

    def __init__(self, config=None):
        # mem0 passes its own `BaseEmbedderConfig` instance here; accept both
        # that and a plain dict so the class works either way.
        if hasattr(config, "get"):
            config = config or {}
            dims = config.get("embedding_dims", DEFAULT_DIMS)
        else:
            dims = getattr(config, "embedding_dims", None) or DEFAULT_DIMS
        try:
            super().__init__(config)
        except Exception:
            self.config = config
        self.dims = int(dims)
        # some mem0 paths read `embedding_dims` off the embedder instance
        self.embedding_dims = self.dims

    def embed(self, text, memory_action="add", **kwargs):
        return embed_text(text, self.dims)

    def embed_batch(self, texts, memory_action="add", **kwargs):
        return [embed_text(t, self.dims) for t in texts]


if __name__ == "__main__":  # tiny sanity check
    a = embed_text("我的项目代号是阿尔法。")
    b = embed_text("我的项目代号到底是什么？")
    c = embed_text("我平时喝美式咖啡。")
    dot = lambda x, y: sum(i * j for i, j in zip(x, y))
    print("dims", len(a), "self", round(dot(a, a), 4))
    print("alpha-vs-query", round(dot(a, b), 4))
    print("coffee-vs-query", round(dot(c, b), 4))
