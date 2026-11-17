"""Embedding models behind one small interface.

- HashingEmbedder: no download, deterministic. Word and word-pair features hashed into
  a fixed vector. Used by unit tests and CI jobs that must not fetch models. It captures
  shared words, not meaning, and is reported as such.
- FastEmbedEmbedder: a real local model (BAAI/bge-small-en-v1.5, 384 dimensions, ONNX,
  runs on CPU). Weights are cached on drive D. No data leaves the machine.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Protocol

import numpy as np

TOKEN = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)*")


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


def _normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class HashingEmbedder:
    name = "hashing-512"

    def __init__(self, dim: int = 512):
        self.dim = dim

    def _vector(self, text: str) -> np.ndarray:
        words = TOKEN.findall(text.lower())
        features = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
        vec = np.zeros(self.dim, dtype=np.float32)
        for feature in features:
            digest = hashlib.md5(feature.encode()).digest()
            index = int.from_bytes(digest[:4], "little") % self.dim
            vec[index] += 1.0 if digest[4] % 2 else -1.0
        return vec

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return _normalise(np.stack([self._vector(t) for t in texts])) if texts else np.zeros((0, self.dim), np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return _normalise(self._vector(text)[None, :])[0]


class FastEmbedEmbedder:
    def __init__(self, model: str = "BAAI/bge-small-en-v1.5", cache_dir: str | None = None):
        from fastembed import TextEmbedding

        cache = cache_dir or os.environ.get("FIELD_MODEL_CACHE", str(Path("D:/cache/fastembed")))
        self._model = TextEmbedding(model_name=model, cache_dir=cache)
        self.name = model
        self.dim = len(next(iter(self._model.embed(["dimension probe"]))))

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), np.float32)
        return _normalise(np.array(list(self._model.passage_embed(texts)), dtype=np.float32))

    def embed_query(self, text: str) -> np.ndarray:
        return _normalise(np.array(list(self._model.query_embed(text)), dtype=np.float32))[0]


def build_embedder(kind: str | None = None) -> Embedder:
    kind = (kind or os.environ.get("FIELD_EMBEDDER", "hashing")).lower()
    if kind == "hashing":
        return HashingEmbedder()
    if kind == "fastembed":
        return FastEmbedEmbedder()
    raise ValueError(f"unknown embedder {kind!r}")
