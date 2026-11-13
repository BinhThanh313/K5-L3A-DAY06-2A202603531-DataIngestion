"""Hybrid Retrieval module: Combines dense vector similarity with BM25 lexical ranking.

Supports Reciprocal Rank Fusion (RRF) and linear score combination with metadata filtering.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any


def tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase alphanumeric words."""
    return re.findall(r"\w+", text.lower(), re.UNICODE)


class BM25Okapi:
    """Pure-Python BM25 Okapi implementation."""

    def __init__(
        self,
        corpus: list[dict[str, Any]],
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.k1 = k1
        self.b = b
        self.corpus = corpus
        self.doc_dict: dict[str, dict[str, Any]] = {c["chunk_id"]: c for c in corpus}
        self.doc_tokens: dict[str, list[str]] = {
            c["chunk_id"]: tokenize(c.get("text", "")) for c in corpus
        }
        self.doc_lens: dict[str, int] = {
            cid: len(toks) for cid, toks in self.doc_tokens.items()
        }
        self.avgdl: float = sum(self.doc_lens.values()) / max(len(self.doc_lens), 1)
        self.N: int = len(corpus)

        self.df: Counter[str] = Counter()
        for toks in self.doc_tokens.values():
            self.df.update(set(toks))

        self.idf: dict[str, float] = {
            term: math.log((self.N - freq + 0.5) / (freq + 0.5) + 1.0)
            for term, freq in self.df.items()
        }

    def score(
        self, query: str, candidate_ids: set[str] | None = None
    ) -> list[tuple[str, float]]:
        """Score candidate documents for the given query using BM25."""
        q_tokens = tokenize(query)
        if not q_tokens:
            return []

        candidates = (
            candidate_ids if candidate_ids is not None else set(self.doc_dict.keys())
        )
        scores: list[tuple[str, float]] = []

        for cid in candidates:
            toks = self.doc_tokens.get(cid, [])
            d_len = self.doc_lens.get(cid, 0)
            tf = Counter(toks)
            score = 0.0

            for qt in q_tokens:
                if qt in tf:
                    f = tf[qt]
                    denom = f + self.k1 * (1.0 - self.b + self.b * (d_len / self.avgdl))
                    score += self.idf.get(qt, 0.0) * (f * (self.k1 + 1.0)) / denom

            scores.append((cid, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores


def reciprocal_rank_fusion(
    dense_ranks: dict[str, int],
    bm25_ranks: dict[str, int],
    k: int = 60,
    dense_weight: float = 1.0,
    bm25_weight: float = 1.0,
) -> dict[str, float]:
    """Compute Reciprocal Rank Fusion (RRF) scores across dense and sparse rankings."""
    all_cids = set(dense_ranks.keys()) | set(bm25_ranks.keys())
    rrf_scores: dict[str, float] = {}

    max_rank = max(len(dense_ranks), len(bm25_ranks), 50) + 10

    for cid in all_cids:
        r_d = dense_ranks.get(cid, max_rank)
        r_b = bm25_ranks.get(cid, max_rank)
        score = (dense_weight / (k + r_d)) + (bm25_weight / (k + r_b))
        rrf_scores[cid] = score

    return rrf_scores
