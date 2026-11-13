"""Evaluate retrieved evidence and preserve per-query traces."""
from __future__ import annotations

import re
import unicodedata
from typing import Any


def _clean_for_match(text: str) -> str:
    """Normalize Unicode and collapse whitespace for deterministic quote verification."""
    t = unicodedata.normalize("NFC", text or "")
    return re.sub(r"\s+", " ", t).strip()


def _valid_gold_targets(question: dict[str, Any]) -> list[dict[str, Any]]:
    targets = [
        {
            "gold_source_id": question["gold_source_id"],
            "gold_page": int(question["gold_page"]),
            "evidence_quote": question["evidence_quote"],
        }
    ]
    for alt in question.get("alternative_gold") or []:
        targets.append(
            {
                "gold_source_id": alt.get("gold_source_id", question["gold_source_id"]),
                "gold_page": int(alt["gold_page"]),
                "evidence_quote": alt["evidence_quote"],
            }
        )
    return targets


def _check_hit(hit: dict[str, Any], targets: list[dict[str, Any]]) -> dict[str, Any]:
    hit_doc = hit.get("document_id") or (hit.get("metadata") or {}).get("document_id")
    hit_page = int(hit.get("page") or (hit.get("metadata") or {}).get("page") or -1)
    hit_text_clean = _clean_for_match(hit.get("text", ""))

    page_matched = False
    evidence_matched = False
    matched_target: dict[str, Any] | None = None

    for tgt in targets:
        if hit_doc == tgt["gold_source_id"] and hit_page == tgt["gold_page"]:
            page_matched = True
            quote_clean = _clean_for_match(tgt["evidence_quote"])
            if quote_clean and quote_clean in hit_text_clean:
                evidence_matched = True
                matched_target = tgt
                break

    return {
        "page_match": page_matched,
        "evidence_hit": evidence_matched,
        "matched_target": matched_target,
    }


def _check_filter_compliance(
    hit: dict[str, Any], question_filter: dict[str, Any] | None
) -> bool | None:
    if not question_filter:
        return None
    meta = dict(hit.get("metadata") or {})
    for k, v in question_filter.items():
        actual = hit.get(k, meta.get(k))
        if str(actual) != str(v):
            return False
    return True


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Check source/page and gold evidence on top-3 hits; preserve detailed per-query trace."""
    targets = _valid_gold_targets(question)
    q_filter = question.get("filter")
    top3 = hits[:3]

    annotated_hits: list[dict[str, Any]] = []
    for rank_idx, h in enumerate(top3, start=1):
        eval_info = _check_hit(h, targets)
        filt_ok = _check_filter_compliance(h, q_filter)
        annotated_hits.append(
            {
                "rank": rank_idx,
                "chunk_id": h["chunk_id"],
                "document_id": h.get("document_id"),
                "source": h.get("source"),
                "page": h.get("page"),
                "score": h.get("score"),
                "cosine_distance": h.get("cosine_distance"),
                "metadata": h.get("metadata", {}),
                "text": h.get("text", ""),
                "page_match": eval_info["page_match"],
                "evidence_hit": eval_info["evidence_hit"],
                "filter_compliant": filt_ok,
            }
        )

    hit_at_1 = bool(annotated_hits and annotated_hits[0]["evidence_hit"])
    hit_at_3 = any(item["evidence_hit"] for item in annotated_hits)
    page_hit_at_1 = bool(annotated_hits and annotated_hits[0]["page_match"])
    page_hit_at_3 = any(item["page_match"] for item in annotated_hits)

    # Secondary check: whether evidence split across multiple top-3 chunks of the same gold page
    multi_chunk_hit_at_3 = hit_at_3
    if not hit_at_3 and page_hit_at_3:
        for tgt in targets:
            same_page_texts = [
                _clean_for_match(item["text"])
                for item in annotated_hits
                if item.get("document_id") == tgt["gold_source_id"]
                and int(item.get("page") or -1) == tgt["gold_page"]
            ]
            combined = " ".join(same_page_texts)
            quote_clean = _clean_for_match(tgt["evidence_quote"])
            # Also check if at least 60% of quote prefix or suffix is present when split by fixed window
            if quote_clean in combined or (
                len(quote_clean) >= 40
                and (
                    quote_clean[: int(len(quote_clean) * 0.6)] in combined
                    or quote_clean[int(len(quote_clean) * 0.4) :] in combined
                )
            ):
                multi_chunk_hit_at_3 = True
                break

    if q_filter:
        top3_filter_compliant = bool(
            annotated_hits and all(item["filter_compliant"] is True for item in annotated_hits)
        )
    else:
        top3_filter_compliant = None

    failure_note = None
    if not hit_at_3:
        if page_hit_at_3:
            failure_note = "evidence_split_across_chunks_on_gold_page"
        else:
            failure_note = "gold_page_not_in_top3"

    return {
        "question_id": question["id"],
        "split": question["split"],
        "type": question.get("type", "unknown"),
        "question": question["question"],
        "expected_answer": question.get("expected_answer"),
        "gold_source_id": question["gold_source_id"],
        "gold_page": question["gold_page"],
        "evidence_quote": question["evidence_quote"],
        "alternative_gold": question.get("alternative_gold", []),
        "filter": q_filter,
        "hit_at_1": hit_at_1,
        "evidence_hit_at_3": hit_at_3,
        "page_hit_at_1": page_hit_at_1,
        "page_hit_at_3": page_hit_at_3,
        "multi_chunk_partial_or_combined_hit_at_3": multi_chunk_hit_at_3,
        "top3_filter_compliant": top3_filter_compliant,
        "failure_reason": failure_note,
        "top_3_hits": annotated_hits,
    }


def _aggregate_subset(items: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(items)
    if total == 0:
        return {
            "count": 0,
            "hit_at_1_count": 0,
            "hit_at_1_fraction": "0/0",
            "hit_at_1_pct": 0.0,
            "evidence_hit_at_3_count": 0,
            "evidence_hit_at_3_fraction": "0/0",
            "evidence_hit_at_3_pct": 0.0,
            "page_hit_at_3_count": 0,
            "page_hit_at_3_fraction": "0/0",
            "filter_questions_count": 0,
            "filter_compliant_count": 0,
            "filter_compliance_fraction": "N/A",
            "filter_compliance_pct": None,
        }

    h1 = sum(1 for x in items if x["hit_at_1"])
    h3 = sum(1 for x in items if x["evidence_hit_at_3"])
    ph3 = sum(1 for x in items if x["page_hit_at_3"])
    mch3 = sum(1 for x in items if x["multi_chunk_partial_or_combined_hit_at_3"])

    filt_items = [x for x in items if x.get("filter")]
    f_total = len(filt_items)
    if f_total > 0:
        f_ok = sum(1 for x in filt_items if x.get("top3_filter_compliant") is True)
        f_frac = f"{f_ok}/{f_total}"
        f_pct = round(100.0 * f_ok / f_total, 2)
    else:
        f_ok = 0
        f_frac = "N/A"
        f_pct = None

    return {
        "count": total,
        "hit_at_1_count": h1,
        "hit_at_1_fraction": f"{h1}/{total}",
        "hit_at_1_pct": round(100.0 * h1 / total, 2),
        "evidence_hit_at_3_count": h3,
        "evidence_hit_at_3_fraction": f"{h3}/{total}",
        "evidence_hit_at_3_pct": round(100.0 * h3 / total, 2),
        "page_hit_at_3_count": ph3,
        "page_hit_at_3_fraction": f"{ph3}/{total}",
        "page_hit_at_3_pct": round(100.0 * ph3 / total, 2),
        "multi_chunk_hit_at_3_count": mch3,
        "multi_chunk_hit_at_3_fraction": f"{mch3}/{total}",
        "filter_questions_count": f_total,
        "filter_compliant_count": f_ok,
        "filter_compliance_fraction": f_frac,
        "filter_compliance_pct": f_pct,
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate Hit@1 and Evidence Hit@3 by strategy and dev/test split."""
    strategies = sorted({t.get("strategy", "default") for t in traces})
    summary: dict[str, Any] = {}

    for strat in strategies:
        strat_traces = [t for t in traces if t.get("strategy", "default") == strat]
        dev_traces = [t for t in strat_traces if t.get("split") == "dev"]
        test_traces = [t for t in strat_traces if t.get("split") == "test"]
        summary[strat] = {
            "dev": _aggregate_subset(dev_traces),
            "test": _aggregate_subset(test_traces),
            "all": _aggregate_subset(strat_traces),
        }

    return summary
