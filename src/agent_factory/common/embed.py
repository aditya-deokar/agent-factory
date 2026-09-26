"""Embeddings: OpenAI-compatible client, a deterministic offline embedder, and a disk cache.

Audits never fail because embeddings are unavailable: the caller catches
`EmbeddingUnavailable` and continues with full-text + graph retrieval only.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
import sqlite3
import struct
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

_WORD = re.compile(r"[A-Za-z][a-z0-9]*|[0-9]+")


class EmbeddingUnavailable(RuntimeError):
    pass


class Embedder(Protocol):
    model: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def stem(word: str) -> str:
    """A deliberately small stemmer: tokens/validates/expiring/expired -> token/validate/expir/expir."""
    for suffix, keep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", "e"), ("s", "")):
        if len(word) > len(suffix) + 2 and word.endswith(suffix) and not word.endswith(("ss", "us", "is")):
            word = word[: -len(suffix)] + keep
            break
    return word.rstrip("e") if len(word) > 4 else word


class HashEmbedder:
    """Hashed bag of stemmed words (camelCase aware) + word bigrams. Lexical overlap -> similarity; no network."""

    model = "hash-bow-v2"

    def __init__(self, dimensions: int = 1536):
        self.dimensions = dimensions

    def _vector(self, text: str) -> list[float]:
        words = [stem(w.lower()) for w in _WORD.findall(text)]
        v = [0.0] * self.dimensions
        grams = words + [f"{a}_{b}" for a, b in itertools.pairwise(words)]
        for g in grams:
            h = int.from_bytes(hashlib.sha256(g.encode()).digest()[:8], "big")
            v[h % self.dimensions] += -1.0 if (h >> 63) & 1 else 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class OpenAIEmbedder:
    def __init__(self, model: str, dimensions: int, api_key: str, base_url: str | None = None, batch: int = 100):
        from openai import OpenAI

        self.model = model
        self.dimensions = dimensions
        self.batch = batch
        self._client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=60)

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            chunk = texts[i : i + self.batch]
            try:
                resp = self._client.embeddings.create(model=self.model, input=chunk)
            except Exception as error:  # proxy idle, auth, network...
                body = getattr(error, "body", None)
                msg = body.get("message") if isinstance(body, dict) and body.get("message") else str(error)
                raise EmbeddingUnavailable(f"{type(error).__name__}: {msg}") from error
            vectors = [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
            if vectors and len(vectors[0]) != self.dimensions:
                raise EmbeddingUnavailable(
                    f"{self.model} returned {len(vectors[0])} dimensions; config says {self.dimensions}"
                )
            out.extend(vectors)
        return out


class CachedEmbedder:
    """Content-addressed cache: unchanged texts are never re-embedded."""

    def __init__(self, inner: Embedder, path: Path):
        self.inner = inner
        self.model = inner.model
        self.dimensions = inner.dimensions
        self.hits = 0
        self.misses = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.execute("CREATE TABLE IF NOT EXISTS emb (key TEXT PRIMARY KEY, dims INTEGER, vec BLOB)")

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}\x1f{self.dimensions}\x1f{text}".encode()).hexdigest()

    def embed(self, texts: list[str]) -> list[list[float]]:
        keys = [self._key(t) for t in texts]
        found: dict[str, list[float]] = {}
        for i in range(0, len(keys), 500):
            part = keys[i : i + 500]
            rows = self._db.execute(
                f"SELECT key, dims, vec FROM emb WHERE key IN ({','.join('?' * len(part))})", part
            ).fetchall()
            for key, dims, blob in rows:
                found[key] = list(struct.unpack(f"{dims}f", blob))
        missing = [(k, t) for k, t in zip(keys, texts, strict=True) if k not in found]
        self.hits += len(texts) - len(missing)
        self.misses += len(missing)
        if missing:
            vectors = self.inner.embed([t for _, t in missing])
            with self._db:
                for (key, _), vec in zip(missing, vectors, strict=True):
                    self._db.execute(
                        "INSERT OR REPLACE INTO emb VALUES (?, ?, ?)",
                        (key, len(vec), struct.pack(f"{len(vec)}f", *vec)),
                    )
                    found[key] = vec
        return [found[k] for k in keys]

    def close(self) -> None:
        self._db.close()


def make_embedder(
    provider: str, model: str, dimensions: int, env: Mapping[str, str], cache_dir: Path | None
) -> Embedder | None:
    """The configured embedder wrapped in the cache, or None when no credentials are available."""
    inner: Embedder
    if provider == "hash":
        inner = HashEmbedder(dimensions)
    elif provider == "openai":
        key = env.get("OPENAI_API_KEY")
        if not key:
            return None
        inner = OpenAIEmbedder(model, dimensions, key, env.get("OPENAI_BASE_URL"))
    else:
        raise ValueError(f"unknown embeddings provider {provider!r} (openai | hash)")
    return CachedEmbedder(inner, cache_dir / "embeddings.sqlite") if cache_dir else inner


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)
