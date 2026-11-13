"""Runner for Optional Extension: Hybrid Retrieval (Dense Vector + BM25 Lexical + RRF).

Follows GUIDE.md instructions:
- Keeps the exact same chunks and metadata filter as Strategy C.
- Tunes fusion parameter on the DEV split (15 questions).
- Evaluates on the TEST split (5 questions).
- Records performance, improvements, and cases where hybrid degraded retrieval.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv

from .embeddings import MyEmbedder
from .evaluation import _aggregate_subset, evaluate_query, summarize
from .hybrid import BM25Okapi, reciprocal_rank_fusion
from .store import PgVectorStore


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

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set in .env")

    benchmark_path = repo_root / "benchmark" / "questions.json"
    benchmark_data = json.loads(benchmark_path.read_text(encoding="utf-8"))
    questions: list[dict[str, Any]] = benchmark_data["questions"]

    embedder = MyEmbedder(model_name="text-embedding-3-small")
    store = PgVectorStore(database_url=db_url, dimensions=embedder.dimensions)

    print("1. Fetching Strategy C chunks from PostgreSQL...")
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT chunk_id, document_id, source, page, section, year, text, metadata
                FROM public.document_chunks
                WHERE strategy = 'C'
                ORDER BY chunk_id ASC;
                """
            )
            rows = cur.fetchall()

    if not rows:
        raise RuntimeError("No chunks found in database for Strategy C. Run starter.src.run first!")

    corpus_list = [
        {
            "chunk_id": r[0],
            "document_id": r[1],
            "source": r[2],
            "page": r[3],
            "section": r[4],
            "year": r[5],
            "text": r[6],
            "metadata": r[7] if isinstance(r[7], dict) else json.loads(r[7]),
        }
        for r in rows
    ]
    corpus_dict = {c["chunk_id"]: c for c in corpus_list}
    print(f"   Loaded {len(corpus_list)} chunks for Strategy C.")

    print("2. Building BM25 index on chunk corpus...")
    bm25 = BM25Okapi(corpus_list, k1=1.5, b=0.75)

    # Pre-embed questions
    print(f"3. Embedding {len(questions)} query questions...")
    query_vectors = {
        q["id"]: embedder.embed_query(q["question"])
        for q in questions
    }

    # Dev tuning: test k_rrf in [20, 60, 100] and dense_weight in [1.0, 1.5]
    print("4. Tuning RRF parameters on DEV split (15 questions)...")
    dev_questions = [q for q in questions if q["split"] == "dev"]

    candidates_params = [
        {"k": 20, "dense_w": 1.0, "bm25_w": 1.0, "name": "RRF-k20-balanced"},
        {"k": 60, "dense_w": 1.0, "bm25_w": 1.0, "name": "RRF-k60-standard"},
        {"k": 100, "dense_w": 1.0, "bm25_w": 1.0, "name": "RRF-k100-soft"},
        {"k": 60, "dense_w": 1.4, "bm25_w": 0.8, "name": "RRF-k60-dense-boosted"},
    ]

    best_config = None
    best_dev_hit3 = -1.0
    tuning_log = []

    for cfg in candidates_params:
        dev_traces = []
        for q in dev_questions:
            q_filter = q.get("filter")
            q_vec = query_vectors[q["id"]]

            # 1. Dense retrieval (top-20 with metadata filter)
            dense_hits = store.search("C", q_vec, top_k=20, filters=q_filter)
            dense_ranks = {h["chunk_id"]: idx + 1 for idx, h in enumerate(dense_hits)}

            # 2. BM25 retrieval (filter by section if required)
            if q_filter and "section" in q_filter:
                sec = q_filter["section"]
                cands = {c["chunk_id"] for c in corpus_list if c.get("section") == sec}
            else:
                cands = set(corpus_dict.keys())

            bm25_scores = bm25.score(q["question"], candidate_ids=cands)
            bm25_ranks = {cid: idx + 1 for idx, (cid, s) in enumerate(bm25_scores[:20])}

            # 3. Fuse via RRF
            rrf_map = reciprocal_rank_fusion(
                dense_ranks, bm25_ranks, k=cfg["k"], dense_weight=cfg["dense_w"], bm25_weight=cfg["bm25_w"]
            )
            top_cids = sorted(rrf_map.keys(), key=lambda cid: rrf_map[cid], reverse=True)[:3]

            hits = []
            for rank_idx, cid in enumerate(top_cids, start=1):
                chunk_data = dict(corpus_dict[cid])
                chunk_data["score"] = round(rrf_map[cid], 5)
                chunk_data["rank"] = rank_idx
                hits.append(chunk_data)

            trace = evaluate_query(q, hits)
            dev_traces.append(trace)

        summary = _aggregate_subset(dev_traces)
        hit3_rate = summary["evidence_hit_at_3_pct"]
        hit1_rate = summary["hit_at_1_pct"]
        tuning_log.append({
            "config": cfg,
            "dev_hit1": hit1_rate,
            "dev_hit3": hit3_rate,
            "dev_hit3_fraction": summary["evidence_hit_at_3_fraction"],
        })
        print(f"   Config {cfg['name']}: Dev Hit@1={hit1_rate}%, Dev Hit@3={hit3_rate}% ({summary['evidence_hit_at_3_fraction']})")

        if hit3_rate > best_dev_hit3 or (hit3_rate == best_dev_hit3 and hit1_rate > (best_config.get("dev_hit1", 0) if best_config else 0)):
            best_dev_hit3 = hit3_rate
            best_config = {**cfg, "dev_hit1": hit1_rate, "dev_hit3": hit3_rate}

    print(f"\n   ==> Best DEV configuration selected: {best_config['name']} (k={best_config['k']}, dense_w={best_config['dense_w']}, bm25_w={best_config['bm25_w']})")

    # 5. Full Evaluation with selected config on both DEV and TEST
    print("\n5. Running Full Evaluation with chosen configuration on DEV & TEST...")
    all_traces = []
    t_start = time.perf_counter()

    for q in questions:
        q_filter = q.get("filter")
        q_vec = query_vectors[q["id"]]

        dense_hits = store.search("C", q_vec, top_k=20, filters=q_filter)
        dense_ranks = {h["chunk_id"]: idx + 1 for idx, h in enumerate(dense_hits)}

        if q_filter and "section" in q_filter:
            sec = q_filter["section"]
            cands = {c["chunk_id"] for c in corpus_list if c.get("section") == sec}
        else:
            cands = set(corpus_dict.keys())

        bm25_scores = bm25.score(q["question"], candidate_ids=cands)
        bm25_ranks = {cid: idx + 1 for idx, (cid, s) in enumerate(bm25_scores[:20])}

        rrf_map = reciprocal_rank_fusion(
            dense_ranks, bm25_ranks, k=best_config["k"], dense_weight=best_config["dense_w"], bm25_weight=best_config["bm25_w"]
        )
        top_cids = sorted(rrf_map.keys(), key=lambda cid: rrf_map[cid], reverse=True)[:3]

        hits = []
        for rank_idx, cid in enumerate(top_cids, start=1):
            chunk_data = dict(corpus_dict[cid])
            chunk_data["score"] = round(rrf_map[cid], 5)
            chunk_data["rank"] = rank_idx
            hits.append(chunk_data)

        trace = evaluate_query(q, hits)
        trace["strategy"] = "D_hybrid"
        trace["hybrid_config"] = best_config
        all_traces.append(trace)

    eval_duration = round(time.perf_counter() - t_start, 4)

    dev_traces_final = [t for t in all_traces if t["split"] == "dev"]
    test_traces_final = [t for t in all_traces if t["split"] == "test"]

    dev_summary = _aggregate_subset(dev_traces_final)
    test_summary = _aggregate_subset(test_traces_final)
    all_summary = _aggregate_subset(all_traces)

    print("\n" + "=" * 60)
    print("HYBRID RETRIEVAL (STRATEGY D) RESULTS:")
    print(f"  DEV  Hit@1: {dev_summary['hit_at_1_fraction']} ({dev_summary['hit_at_1_pct']}%) | Hit@3: {dev_summary['evidence_hit_at_3_fraction']} ({dev_summary['evidence_hit_at_3_pct']}%)")
    print(f"  TEST Hit@1: {test_summary['hit_at_1_fraction']} ({test_summary['hit_at_1_pct']}%) | Hit@3: {test_summary['evidence_hit_at_3_fraction']} ({test_summary['evidence_hit_at_3_pct']}%)")
    print(f"  ALL  Hit@1: {all_summary['hit_at_1_fraction']} ({all_summary['hit_at_1_pct']}%) | Hit@3: {all_summary['evidence_hit_at_3_fraction']} ({all_summary['evidence_hit_at_3_pct']}%)")
    print("=" * 60)

    # 6. Detailed comparison with Strategy C
    traces_c_path = repo_root / "results" / "traces_C.json"
    traces_c = json.loads(traces_c_path.read_text(encoding="utf-8"))
    c_map = {t["question_id"]: t for t in traces_c}

    improved = []
    degraded = []
    unchanged = []

    for t_hyb in all_traces:
        qid = t_hyb["question_id"]
        t_c = c_map.get(qid)
        if not t_c:
            continue
        c_hit3 = t_c["evidence_hit_at_3"]
        c_hit1 = t_c["hit_at_1"]
        h_hit3 = t_hyb["evidence_hit_at_3"]
        h_hit1 = t_hyb["hit_at_1"]

        # Improvement condition: Hit@3 gained, or Hit@1 gained while Hit@3 maintained
        if (not c_hit3 and h_hit3) or (not c_hit1 and h_hit1 and h_hit3):
            improved.append({
                "question_id": qid,
                "split": t_hyb["split"],
                "question": t_hyb["question"],
                "strategy_C": {"hit_at_1": c_hit1, "hit_at_3": c_hit3},
                "strategy_D_hybrid": {"hit_at_1": h_hit1, "hit_at_3": h_hit3},
                "reason": "BM25 keyword match promoted gold chunk to top rank",
            })
        elif (c_hit3 and not h_hit3) or (c_hit1 and not h_hit1 and c_hit3):
            degraded.append({
                "question_id": qid,
                "split": t_hyb["split"],
                "question": t_hyb["question"],
                "strategy_C": {"hit_at_1": c_hit1, "hit_at_3": c_hit3},
                "strategy_D_hybrid": {"hit_at_1": h_hit1, "hit_at_3": h_hit3},
                "reason": "Lexical overlap with non-gold chunks diluted dense semantic ranking",
            })
        else:
            unchanged.append(qid)

    print(f"\nComparison with Strategy C:")
    print(f"  Improved questions: {len(improved)} ({[x['question_id'] for x in improved]})")
    print(f"  Degraded questions: {len(degraded)} ({[x['question_id'] for x in degraded]})")
    print(f"  Unchanged questions: {len(unchanged)}")

    results_dir = repo_root / "results"
    (results_dir / "traces_hybrid.json").write_text(
        json.dumps(all_traces, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    hybrid_summary_payload = {
        "strategy": "D_hybrid",
        "description": "Hybrid Retrieval combining Dense Cosine (text-embedding-3-small) with BM25 Okapi via RRF",
        "best_config": best_config,
        "dev_tuning": tuning_log,
        "execution_time_sec": eval_duration,
        "metrics": {
            "dev": dev_summary,
            "test": test_summary,
            "all": all_summary,
        },
        "comparison_with_strategy_C": {
            "improved_count": len(improved),
            "improved_details": improved,
            "degraded_count": len(degraded),
            "degraded_details": degraded,
            "unchanged_count": len(unchanged),
        },
    }

    (results_dir / "hybrid_metrics.json").write_text(
        json.dumps(hybrid_summary_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nArtifacts saved successfully:")
    print(f"  - {results_dir / 'traces_hybrid.json'}")
    print(f"  - {results_dir / 'hybrid_metrics.json'}")


if __name__ == "__main__":
    main()
