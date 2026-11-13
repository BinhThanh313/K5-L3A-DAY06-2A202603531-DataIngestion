"""Implement fixed-size and structure-aware paragraph chunking."""
from __future__ import annotations

import re
from .models import Chunk, DocumentPage


def fixed_size(page: DocumentPage, size: int = 500, overlap: int = 50) -> list[Chunk]:
    """Split text into overlapping character windows and retain page metadata."""
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must satisfy 0 <= overlap < size")

    text = page.text.strip()
    if not text:
        return []

    chunks: list[Chunk] = []
    step = size - overlap
    start = 0
    idx = 0
    n = len(text)

    while start < n:
        end = min(start + size, n)
        window = text[start:end].strip()
        if window:
            meta = dict(page.metadata)
            meta.update(
                {
                    "chunk_index": idx,
                    "char_start": start,
                    "char_end": end,
                    "char_length": len(window),
                    "chunking": "fixed_size",
                }
            )
            chunks.append(
                Chunk(
                    chunk_id=f"{page.document_id}_p{page.page:02d}_fix_{idx:02d}",
                    document_id=page.document_id,
                    source=page.source,
                    page=page.page,
                    text=window,
                    metadata=meta,
                )
            )
            idx += 1
        if end >= n:
            break
        start += step

    return chunks


def _split_oversized_paragraph(paragraph: str, max_size: int) -> list[str]:
    """Split a paragraph larger than max_size along sentence boundaries (with 1-sentence context overlap if needed), falling back to word boundaries."""
    sentences = [
        s.strip()
        for s in re.split(r"(?<=[\.\!\?\:;])\s+", paragraph.strip())
        if s.strip()
    ]
    if not sentences:
        return []

    segments: list[str] = []
    cur_sents: list[str] = []
    cur_len = 0

    for sent in sentences:
        if len(sent) > max_size:
            if cur_sents:
                segments.append(" ".join(cur_sents))
                cur_sents = []
                cur_len = 0
            words = sent.split(" ")
            w_buf: list[str] = []
            w_len = 0
            for w in words:
                add_len = len(w) if not w_buf else len(w) + 1
                if w_buf and w_len + add_len > max_size:
                    segments.append(" ".join(w_buf))
                    w_buf = [w]
                    w_len = len(w)
                else:
                    w_buf.append(w)
                    w_len += add_len
            if w_buf:
                segments.append(" ".join(w_buf))
            continue

        add_len = len(sent) if not cur_sents else len(sent) + 1
        if cur_sents and cur_len + add_len > max_size:
            segments.append(" ".join(cur_sents))
            # Retain the last sentence if it fits with the next sentence to preserve intra-paragraph context
            last_sent = cur_sents[-1]
            if len(last_sent) + 1 + len(sent) <= max_size:
                cur_sents = [last_sent, sent]
                cur_len = len(last_sent) + 1 + len(sent)
            else:
                cur_sents = [sent]
                cur_len = len(sent)
        else:
            cur_sents.append(sent)
            cur_len += add_len

    if cur_sents:
        segments.append(" ".join(cur_sents))

    return segments


def by_paragraph(page: DocumentPage, max_size: int = 500) -> list[Chunk]:
    """Group paragraphs up to max_size, split oversized paragraphs along sentences, and retain metadata."""
    if max_size <= 0:
        raise ValueError("max_size must be positive")

    raw_paragraphs = [p.strip() for p in page.text.split("\n\n") if p.strip()]
    if not raw_paragraphs:
        return []

    units: list[str] = []
    for para in raw_paragraphs:
        if len(para) <= max_size:
            units.append(para)
        else:
            units.extend(_split_oversized_paragraph(para, max_size))

    grouped_texts: list[str] = []
    cur_group: list[str] = []
    cur_len = 0

    for unit in units:
        sep_len = 2 if cur_group else 0
        if cur_group and cur_len + sep_len + len(unit) > max_size:
            grouped_texts.append("\n\n".join(cur_group))
            cur_group = [unit]
            cur_len = len(unit)
        else:
            cur_group.append(unit)
            cur_len += sep_len + len(unit)

    if cur_group:
        grouped_texts.append("\n\n".join(cur_group))

    chunks: list[Chunk] = []
    search_pos = 0
    for idx, chunk_text in enumerate(grouped_texts):
        meta = dict(page.metadata)
        meta.update(
            {
                "chunk_index": idx,
                "char_length": len(chunk_text),
                "chunking": "by_paragraph",
            }
        )
        chunks.append(
            Chunk(
                chunk_id=f"{page.document_id}_p{page.page:02d}_para_{idx:02d}",
                document_id=page.document_id,
                source=page.source,
                page=page.page,
                text=chunk_text,
                metadata=meta,
            )
        )
        search_pos += len(chunk_text)

    return chunks
