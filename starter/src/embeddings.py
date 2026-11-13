"""Real embedding provider adapter (OpenAI API / OpenRouter / local SentenceTransformers)."""
from __future__ import annotations

import os
from typing import Any, Protocol

from openai import OpenAI


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        ...

    def embed_query(self, question: str) -> list[float]:
        """Return a query vector compatible with the stored document vectors."""
        ...


MODEL_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "openai/text-embedding-3-small": 1536,
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": 384,
}

# Cost per 1M tokens in USD for OpenAI embedding models
MODEL_COST_PER_1M_TOKENS: dict[str, float] = {
    "text-embedding-3-small": 0.02,
    "openai/text-embedding-3-small": 0.02,
    "text-embedding-3-large": 0.13,
    "text-embedding-ada-002": 0.10,
}


class MyEmbedder:
    """Real embedding adapter using OpenAI API (or OpenRouter). Never falls back to mock vectors."""

    def __init__(self, model_name: str | None = None, batch_size: int = 100) -> None:
        resolved_model = (model_name or os.getenv("EMBEDDING_MODEL") or "text-embedding-3-small").strip()
        if not resolved_model:
            raise RuntimeError("EMBEDDING_MODEL is not configured.")

        self.model_name = resolved_model
        self.batch_size = batch_size
        self.total_tokens_used = 0
        self.api_calls = 0

        openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        openrouter_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()

        if openai_key:
            self.provider = "openai"
            self.client = OpenAI(api_key=openai_key)
        elif openrouter_key:
            self.provider = "openrouter"
            self.client = OpenAI(
                api_key=openrouter_key,
                base_url="https://openrouter.ai/api/v1",
            )
        else:
            raise RuntimeError(
                "No valid API key found in environment (OPENAI_API_KEY or OPENROUTER_API_KEY). "
                "Refusing to fall back to mock vectors."
            )

        if self.model_name in MODEL_DIMENSIONS:
            self.dimensions = MODEL_DIMENSIONS[self.model_name]
        else:
            probe = self.client.embeddings.create(model=self.model_name, input=["dimension probe"])
            if not probe.data or not probe.data[0].embedding:
                raise RuntimeError(f"Failed to obtain embedding from model {self.model_name}")
            self.dimensions = len(probe.data[0].embedding)
            if getattr(probe, "usage", None) and getattr(probe.usage, "total_tokens", None):
                self.total_tokens_used += int(probe.usage.total_tokens)
            self.api_calls += 1

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of document chunks in batches while preserving order."""
        if not texts:
            return []
        cleaned = [t.replace("\n", " ").strip() for t in texts]
        if any(not t for t in cleaned):
            raise ValueError("Cannot embed empty document text.")

        all_vectors: list[list[float]] = []
        for start in range(0, len(cleaned), self.batch_size):
            batch = cleaned[start : start + self.batch_size]
            resp = self.client.embeddings.create(model=self.model_name, input=batch)
            self.api_calls += 1
            if getattr(resp, "usage", None) and getattr(resp.usage, "total_tokens", None):
                self.total_tokens_used += int(resp.usage.total_tokens)
            ordered = sorted(resp.data, key=lambda item: item.index)
            batch_vecs = [list(item.embedding) for item in ordered]
            for vec in batch_vecs:
                if len(vec) != self.dimensions:
                    raise RuntimeError(
                        f"Dimension mismatch: expected {self.dimensions}, got {len(vec)}"
                    )
            all_vectors.extend(batch_vecs)

        if len(all_vectors) != len(texts):
            raise RuntimeError(
                f"Embedding count mismatch: expected {len(texts)}, got {len(all_vectors)}"
            )
        return all_vectors

    def embed_query(self, question: str) -> list[float]:
        """Embed a single search query."""
        q = question.replace("\n", " ").strip()
        if not q:
            raise ValueError("Query question cannot be empty.")
        resp = self.client.embeddings.create(model=self.model_name, input=[q])
        self.api_calls += 1
        if getattr(resp, "usage", None) and getattr(resp.usage, "total_tokens", None):
            self.total_tokens_used += int(resp.usage.total_tokens)
        vec = list(resp.data[0].embedding)
        if len(vec) != self.dimensions:
            raise RuntimeError(
                f"Query vector dimension mismatch: expected {self.dimensions}, got {len(vec)}"
            )
        return vec

    def usage_summary(self) -> dict[str, Any]:
        rate = MODEL_COST_PER_1M_TOKENS.get(self.model_name, 0.02)
        cost_usd = (self.total_tokens_used / 1_000_000.0) * rate
        return {
            "provider": self.provider,
            "model_name": self.model_name,
            "dimensions": self.dimensions,
            "api_calls": self.api_calls,
            "total_tokens": self.total_tokens_used,
            "rate_per_1m_tokens_usd": rate,
            "estimated_cost_usd": round(cost_usd, 6),
        }
