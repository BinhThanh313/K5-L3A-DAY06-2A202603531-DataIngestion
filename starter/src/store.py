"""Store and search vectors using PostgreSQL + pgvector with exact cosine distance and metadata filtering."""
from __future__ import annotations

import json
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .models import Chunk

PERMITTED_FILTER_FIELDS = {
    "document_id",
    "source",
    "page",
    "section",
    "sub_section",
    "printed_page",
    "year",
    "language",
    "document_type",
}


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.8f}" for x in vec) + "]"


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int) -> None:
        if not database_url:
            raise ValueError("database_url must be provided")
        if dimensions <= 0:
            raise ValueError("dimensions must be a positive integer")
        self.database_url = database_url
        self.dimensions = int(dimensions)

    def create_schema(self) -> None:
        """Create pgvector extension, chunks table with vector column, and metadata indexes."""
        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                cur.execute(
                    """
                    SELECT atttypmod
                    FROM pg_attribute
                    WHERE attrelid = to_regclass('public.document_chunks')
                      AND attname = 'embedding'
                      AND NOT attisdropped;
                    """
                )
                row = cur.fetchone()
                if row is not None and row[0] != -1 and row[0] != self.dimensions:
                    cur.execute("DROP TABLE IF EXISTS public.document_chunks;")

                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS public.document_chunks (
                        strategy TEXT NOT NULL,
                        chunk_id TEXT NOT NULL,
                        document_id TEXT NOT NULL,
                        source TEXT NOT NULL,
                        page INTEGER NOT NULL,
                        section TEXT,
                        year INTEGER,
                        text TEXT NOT NULL,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        embedding vector({self.dimensions}) NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (strategy, chunk_id)
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_document_chunks_strategy_doc
                    ON public.document_chunks (strategy, document_id);
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_document_chunks_strategy_section
                    ON public.document_chunks (strategy, section);
                    """
                )
            conn.commit()

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Validate lengths/dimensions, delete obsolete chunks for updated documents, and upsert without duplicates."""
        if not strategy:
            raise ValueError("strategy must be non-empty")
        if len(chunks) != len(vectors):
            raise ValueError(
                f"Length mismatch: {len(chunks)} chunks vs {len(vectors)} vectors"
            )
        if not chunks:
            return

        seen_ids: set[str] = set()
        doc_ids: set[str] = set()
        for chunk, vec in zip(chunks, vectors):
            if len(vec) != self.dimensions:
                raise ValueError(
                    f"Vector dimension mismatch for {chunk.chunk_id}: expected {self.dimensions}, got {len(vec)}"
                )
            if chunk.chunk_id in seen_ids:
                raise ValueError(f"Duplicate chunk_id in batch: {chunk.chunk_id}")
            seen_ids.add(chunk.chunk_id)
            doc_ids.add(chunk.document_id)

        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                # Remove obsolete chunks belonging to the re-ingested source documents
                cur.execute(
                    """
                    DELETE FROM public.document_chunks
                    WHERE strategy = %s
                      AND document_id = ANY(%s)
                      AND NOT (chunk_id = ANY(%s));
                    """,
                    (strategy, list(doc_ids), list(seen_ids)),
                )

                for chunk, vec in zip(chunks, vectors):
                    meta = dict(chunk.metadata or {})
                    meta.setdefault("document_id", chunk.document_id)
                    meta.setdefault("source", chunk.source)
                    meta.setdefault("page", chunk.page)
                    section_val = meta.get("section")
                    year_val = int(meta["year"]) if meta.get("year") is not None else None
                    vec_str = _vector_literal(vec)

                    cur.execute(
                        """
                        INSERT INTO public.document_chunks (
                            strategy, chunk_id, document_id, source, page,
                            section, year, text, metadata, embedding, updated_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s::jsonb, %s::vector, NOW()
                        )
                        ON CONFLICT (strategy, chunk_id) DO UPDATE SET
                            document_id = EXCLUDED.document_id,
                            source = EXCLUDED.source,
                            page = EXCLUDED.page,
                            section = EXCLUDED.section,
                            year = EXCLUDED.year,
                            text = EXCLUDED.text,
                            metadata = EXCLUDED.metadata,
                            embedding = EXCLUDED.embedding,
                            updated_at = NOW();
                        """,
                        (
                            strategy,
                            chunk.chunk_id,
                            chunk.document_id,
                            chunk.source,
                            chunk.page,
                            section_val,
                            year_val,
                            chunk.text,
                            json.dumps(meta, ensure_ascii=False),
                            vec_str,
                        ),
                    )
            conn.commit()

    def count_chunks(self, strategy: str) -> int:
        """Return total number of stored chunks for a given strategy."""
        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM public.document_chunks WHERE strategy = %s;",
                    (strategy,),
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0

    def search(
        self,
        strategy: str,
        query_vector: list[float],
        top_k: int = 3,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Filter permitted metadata in SQL, rank by exact cosine distance (<=>), and return top-k hits."""
        if len(query_vector) != self.dimensions:
            raise ValueError(
                f"Query vector dimension mismatch: expected {self.dimensions}, got {len(query_vector)}"
            )
        if top_k <= 0:
            raise ValueError("top_k must be positive")

        vec_str = _vector_literal(query_vector)
        where_clauses = ["strategy = %s"]
        params: list[Any] = [strategy]

        if filters:
            for key, val in filters.items():
                if val is None:
                    continue
                if key not in PERMITTED_FILTER_FIELDS:
                    raise ValueError(f"Unsupported filter field: {key}")
                if key in {"document_id", "source", "page", "section", "year"}:
                    where_clauses.append(f"{key} = %s")
                    params.append(val)
                else:
                    where_clauses.append("metadata ->> %s = %s")
                    params.extend([key, str(val)])

        where_sql = " AND ".join(where_clauses)
        sql = f"""
            SELECT
                chunk_id,
                document_id,
                source,
                page,
                section,
                year,
                text,
                metadata,
                (1.0 - (embedding <=> %s::vector)) AS score,
                (embedding <=> %s::vector) AS cosine_distance
            FROM public.document_chunks
            WHERE {where_sql}
            ORDER BY embedding <=> %s::vector ASC, chunk_id ASC
            LIMIT %s;
        """
        full_params = [vec_str, vec_str] + params + [vec_str, top_k]

        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, full_params)
                rows = cur.fetchall()

        results: list[dict[str, Any]] = []
        for rank_idx, r in enumerate(rows, start=1):
            meta = dict(r["metadata"] or {})
            meta.update(
                {
                    "document_id": r["document_id"],
                    "source": r["source"],
                    "page": r["page"],
                    "section": r["section"],
                    "year": r["year"],
                }
            )
            results.append(
                {
                    "rank": rank_idx,
                    "chunk_id": r["chunk_id"],
                    "document_id": r["document_id"],
                    "source": r["source"],
                    "page": int(r["page"]),
                    "text": r["text"],
                    "score": round(float(r["score"]), 6),
                    "cosine_distance": round(float(r["cosine_distance"]), 6),
                    "metadata": meta,
                }
            )
        return results
