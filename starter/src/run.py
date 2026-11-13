"""Day 06 Data Ingestion Pipeline entry point."""
from __future__ import annotations

import json
import os
import shutil
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .chunking import by_paragraph, fixed_size
from .embeddings import MyEmbedder
from .evaluation import evaluate_query, summarize
from .ingestion import LAST_INGESTION_REPORT, load_pdf
from .models import Chunk
from .store import PgVectorStore


def _chunk_distribution(chunks: list[Chunk]) -> dict[str, Any]:
    lengths = [len(c.text) for c in chunks]
    if not lengths:
        return {"count": 0}
    sorted_lens = sorted(lengths)
    p90_idx = min(int(0.9 * len(sorted_lens)), len(sorted_lens) - 1)
    return {
        "chunk_count": len(chunks),
        "total_characters": sum(lengths),
        "min_length": min(lengths),
        "max_length": max(lengths),
        "mean_length": round(statistics.mean(lengths), 2),
        "median_length": round(statistics.median(lengths), 2),
        "p90_length": sorted_lens[p90_idx],
    }


def find_repo_root() -> Path:
    cur = Path(__file__).resolve()
    for parent in cur.parents:
        if (parent / "STUDENT.md").exists():
            return parent
    return cur.parents[2]


def main() -> None:
    repo_root = find_repo_root()
    starter_dir = repo_root / "starter"

    env_path = starter_dir / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv(repo_root / ".env")

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set in .env")

    pdf_path = repo_root / "data" / "TAKING STOCK March 2025_VNM_FINAL.pdf"
    if not pdf_path.exists():
        pdf_path = starter_dir / "data" / "TAKING STOCK March 2025_VNM_FINAL.pdf"
    if not pdf_path.exists():
        raise FileNotFoundError(f"Corpus PDF not found at {pdf_path}")

    benchmark_path = repo_root / "benchmark" / "questions.json"
    if not benchmark_path.exists():
        raise FileNotFoundError(f"Benchmark file not found at {benchmark_path}")

    document_id = "wb-taking-stock-2025-03"
    source_rel = "data/TAKING STOCK March 2025_VNM_FINAL.pdf"

    print("1. Loading and normalizing PDF corpus...")
    t0 = time.perf_counter()
    pages = load_pdf(pdf_path, document_id=document_id, source=source_rel, only_valid_content=True)
    pdf_load_sec = round(time.perf_counter() - t0, 4)
    if len(pages) < 30:
        raise RuntimeError(f"Corpus must have at least 30 valid content pages; got {len(pages)}")
    print(f"   Loaded {len(pages)} valid content pages in {pdf_load_sec}s.")

    benchmark_data = json.loads(benchmark_path.read_text(encoding="utf-8"))
    questions: list[dict[str, Any]] = benchmark_data["questions"]
    if len(questions) < 20:
        raise RuntimeError(f"Benchmark must have at least 20 questions; got {len(questions)}")

    print("2. Building chunks for Strategies A, B, and C...")
    t_chunk_a = time.perf_counter()
    chunks_a = [c for p in pages for c in fixed_size(p, size=500, overlap=50)]
    chunk_a_sec = round(time.perf_counter() - t_chunk_a, 4)

    t_chunk_b = time.perf_counter()
    chunks_b = [c for p in pages for c in by_paragraph(p, max_size=500)]
    chunk_b_sec = round(time.perf_counter() - t_chunk_b, 4)

    # Strategy C uses the exact same chunks as Strategy B (controlled comparison for metadata filtering)
    chunks_c = [
        Chunk(
            chunk_id=c.chunk_id.replace("_para_", "_para_c_"),
            document_id=c.document_id,
            source=c.source,
            page=c.page,
            text=c.text,
            metadata=dict(c.metadata),
        )
        for c in chunks_b
    ]

    print(f"   Strategy A (fixed_size 500/50): {len(chunks_a)} chunks")
    print(f"   Strategy B (by_paragraph 500):  {len(chunks_b)} chunks")
    print(f"   Strategy C (by_paragraph 500 + filter): {len(chunks_c)} chunks")

    print("3. Initializing Embedder and PgVectorStore...")
    embedder = MyEmbedder()
    store = PgVectorStore(database_url=database_url, dimensions=embedder.dimensions)
    store.create_schema()

    # Ingest Strategy A
    t_ing_a = time.perf_counter()
    tokens_before_a = embedder.total_tokens_used
    vectors_a = embedder.embed_documents([c.text for c in chunks_a])
    tokens_a = embedder.total_tokens_used - tokens_before_a
    store.upsert("A", chunks_a, vectors_a)
    ingest_a_sec = round(time.perf_counter() - t_ing_a, 4)

    # Ingest Strategy B
    t_ing_b = time.perf_counter()
    tokens_before_b = embedder.total_tokens_used
    vectors_b = embedder.embed_documents([c.text for c in chunks_b])
    tokens_b = embedder.total_tokens_used - tokens_before_b
    store.upsert("B", chunks_b, vectors_b)
    ingest_b_sec = round(time.perf_counter() - t_ing_b, 4)

    # Ingest Strategy C
    t_ing_c = time.perf_counter()
    tokens_before_c = embedder.total_tokens_used
    vectors_c = embedder.embed_documents([c.text for c in chunks_c])
    tokens_c = embedder.total_tokens_used - tokens_before_c
    store.upsert("C", chunks_c, vectors_c)
    ingest_c_sec = round(time.perf_counter() - t_ing_c, 4)

    # Verify idempotent re-ingestion (no duplicates when re-ingesting same document version)
    count_b_before_reingest = store.count_chunks("B")
    t_reingest = time.perf_counter()
    store.upsert("B", chunks_b, vectors_b)
    reingest_b_sec = round(time.perf_counter() - t_reingest, 4)
    count_b_after_reingest = store.count_chunks("B")
    idempotent_ok = count_b_before_reingest == count_b_after_reingest == len(chunks_b)
    print(
        f"   Re-ingestion check on Strategy B: before={count_b_before_reingest}, "
        f"after={count_b_after_reingest}, idempotent={idempotent_ok}"
    )

    print("4. Embedding benchmark queries and running top-3 retrieval across A, B, C...")
    tokens_before_q = embedder.total_tokens_used
    query_vectors: dict[str, list[float]] = {}
    for q in questions:
        query_vectors[q["id"]] = embedder.embed_query(q["question"])
    tokens_queries = embedder.total_tokens_used - tokens_before_q

    all_traces: list[dict[str, Any]] = []
    traces_by_strategy: dict[str, list[dict[str, Any]]] = {"A": [], "B": [], "C": []}
    retrieval_times: dict[str, float] = {}

    for strat in ["A", "B", "C"]:
        t_ret = time.perf_counter()
        for q in questions:
            q_vec = query_vectors[q["id"]]
            active_filter = q.get("filter") if strat == "C" else None
            hits = store.search(
                strategy=strat,
                query_vector=q_vec,
                top_k=3,
                filters=active_filter,
            )
            trace = evaluate_query(q, hits)
            trace["strategy"] = strat
            trace["applied_filter"] = active_filter
            traces_by_strategy[strat].append(trace)
            all_traces.append(trace)
        retrieval_times[strat] = round(time.perf_counter() - t_ret, 4)

    metrics = summarize(all_traces)
    rate_per_1m = 0.02

    strategy_stats = {
        "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "corpus": {
            "document_id": document_id,
            "source": source_rel,
            "total_physical_pages": LAST_INGESTION_REPORT["total_physical_pages"],
            "valid_content_pages": len(pages),
            "empty_pages": [x["page"] for x in LAST_INGESTION_REPORT["empty_or_error_pages"]],
            "excluded_non_content_pages": [
                x["page"] for x in LAST_INGESTION_REPORT["excluded_non_content_pages"]
            ],
            "pdf_load_seconds": pdf_load_sec,
        },
        "embedding_model": embedder.usage_summary(),
        "vector_store": {
            "backend": "PostgreSQL 16.16 + pgvector 0.8.7",
            "table": "public.document_chunks",
            "distance_metric": "cosine (<=>)",
            "search_mode": "exact cosine similarity search",
            "idempotent_reingestion_verified": idempotent_ok,
            "count_before_reingest": count_b_before_reingest,
            "count_after_reingest": count_b_after_reingest,
            "reingest_upsert_seconds": reingest_b_sec,
        },
        "strategies": {
            "A": {
                "description": "Fixed-size chunking (size=500, overlap=50), no metadata filter at retrieval",
                "chunking": "fixed_size",
                "params": {"size": 500, "overlap": 50, "use_metadata_filter": False},
                **_chunk_distribution(chunks_a),
                "chunking_seconds": chunk_a_sec,
                "ingest_seconds": ingest_a_sec,
                "retrieval_seconds_20_queries": retrieval_times["A"],
                "embedding_tokens": tokens_a,
                "estimated_cost_usd": round(tokens_a * rate_per_1m / 1_000_000.0, 6),
            },
            "B": {
                "description": "Paragraph-aware chunking (max_size=500), no metadata filter at retrieval",
                "chunking": "by_paragraph",
                "params": {"max_size": 500, "use_metadata_filter": False},
                **_chunk_distribution(chunks_b),
                "chunking_seconds": chunk_b_sec,
                "ingest_seconds": ingest_b_sec,
                "retrieval_seconds_20_queries": retrieval_times["B"],
                "embedding_tokens": tokens_b,
                "estimated_cost_usd": round(tokens_b * rate_per_1m / 1_000_000.0, 6),
            },
            "C": {
                "description": "Paragraph-aware chunking (max_size=500) + SQL metadata filter on section when provided",
                "chunking": "by_paragraph",
                "params": {
                    "max_size": 500,
                    "use_metadata_filter": True,
                    "filter_fields": ["section"],
                },
                **_chunk_distribution(chunks_c),
                "chunking_seconds": chunk_b_sec,
                "ingest_seconds": ingest_c_sec,
                "retrieval_seconds_20_queries": retrieval_times["C"],
                "embedding_tokens": tokens_c,
                "estimated_cost_usd": round(tokens_c * rate_per_1m / 1_000_000.0, 6),
            },
        },
        "query_embedding_tokens_20_questions": tokens_queries,
    }

    # Prepare 10 pre-selected source positions for manual coherence audit (first 10 sorted valid pages)
    first_10_pages = sorted(pages, key=lambda p: p.page)[:10]
    coherence_samples: list[dict[str, Any]] = []
    for pos_idx, pg in enumerate(first_10_pages, start=1):
        # Anchor position: char offset 250 on each of the first 10 valid pages (after running header)
        anchor_offset = min(250, max(0, len(pg.text) // 3))
        anchor_snippet = pg.text[anchor_offset : anchor_offset + 90].strip()
        pg_chunks_a = [c for c in chunks_a if c.page == pg.page]
        pg_chunks_b = [c for c in chunks_b if c.page == pg.page]
        # Find the chunk covering the anchor snippet (or chunk index 1 / 0)
        sel_a = next((c for c in pg_chunks_a if anchor_snippet[:40] in c.text), pg_chunks_a[min(1, len(pg_chunks_a) - 1)])
        sel_b = next((c for c in pg_chunks_b if anchor_snippet[:40] in c.text), pg_chunks_b[min(1, len(pg_chunks_b) - 1)])
        coherence_samples.append(
            {
                "position_id": f"pos_{pos_idx:02d}",
                "page": pg.page,
                "printed_page": pg.metadata.get("printed_page"),
                "section": pg.metadata.get("section"),
                "anchor_snippet": anchor_snippet,
                "strategy_A_chunk": {
                    "chunk_id": sel_a.chunk_id,
                    "char_length": len(sel_a.text),
                    "text": sel_a.text,
                },
                "strategy_B_and_C_chunk": {
                    "chunk_id": sel_b.chunk_id,
                    "char_length": len(sel_b.text),
                    "text": sel_b.text,
                },
            }
        )

    root_results = repo_root / "results"
    root_results.mkdir(parents=True, exist_ok=True)

    files_to_write = {
        "ingestion_log.json": LAST_INGESTION_REPORT,
        "strategy_stats.json": strategy_stats,
        "metrics_summary.json": metrics,
        "traces_A.json": traces_by_strategy["A"],
        "traces_B.json": traces_by_strategy["B"],
        "traces_C.json": traces_by_strategy["C"],
        "all_traces.json": all_traces,
        "coherence_samples_raw.json": coherence_samples,
    }

    for fname, payload in files_to_write.items():
        text_data = json.dumps(payload, ensure_ascii=False, indent=2)
        (root_results / fname).write_text(text_data, encoding="utf-8")

    print("\n5. Summary of Retrieval Results:")
    for strat in ["A", "B", "C"]:
        d = metrics[strat]["dev"]
        t = metrics[strat]["test"]
        a = metrics[strat]["all"]
        print(
            f"   Strategy {strat} | "
            f"DEV Hit@1: {d['hit_at_1_fraction']} ({d['hit_at_1_pct']}%), "
            f"Hit@3: {d['evidence_hit_at_3_fraction']} ({d['evidence_hit_at_3_pct']}%) | "
            f"TEST Hit@1: {t['hit_at_1_fraction']} ({t['hit_at_1_pct']}%), "
            f"Hit@3: {t['evidence_hit_at_3_fraction']} ({t['evidence_hit_at_3_pct']}%) | "
            f"ALL Hit@3: {a['evidence_hit_at_3_fraction']} ({a['evidence_hit_at_3_pct']}%)"
        )
    print(f"\nAll traces and metrics saved to {root_results} and {starter_results}.")


if __name__ == "__main__":
    main()
