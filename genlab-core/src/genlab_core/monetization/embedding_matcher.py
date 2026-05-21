"""Embedding-based affiliate product matching using pgvector.

Semantic fallback when keyword matching fails. Uses OpenAI text-embedding-3-small
($0.02/M tokens) to embed content and find similar products via cosine similarity.

Usage:
    matcher = EmbeddingMatcher()
    if matcher.available:
        products = matcher.find_best_products("headphones for competitive gaming", "gaming")
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.4  # text-embedding-3-small cosine similarities are typically 0.3-0.6
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536


class EmbeddingMatcher:
    """Semantic product matching via pgvector cosine similarity."""

    def __init__(self):
        self._api_key = os.environ.get("OPENAI_API_KEY", "")
        self._dsn = os.environ.get("DATABASE_URL", "dbname=genlab")
        self._available = bool(self._api_key)
        if not self._available:
            logger.debug("[EmbeddingMatcher] OPENAI_API_KEY not set — embedding search disabled")

    @property
    def available(self) -> bool:
        return self._available

    def embed_text(self, text: str) -> list[float] | None:
        """Embed text using OpenAI text-embedding-3-small."""
        if not self._available or not text:
            return None
        try:
            import openai

            client = openai.OpenAI(api_key=self._api_key)
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=text[:8000],  # Max input length
            )
            return response.data[0].embedding
        except Exception as exc:
            logger.warning("[EmbeddingMatcher] Embed failed: %s", str(exc)[:60])
            return None

    def index_product(
        self,
        product_id: str,
        name: str,
        category: str = "",
        niche_ids: list[str] | None = None,
        description: str = "",
    ) -> bool:
        """Embed and upsert a product into product_embeddings."""
        text = f"{name} {category} {description}".strip()
        embedding = self.embed_text(text)
        if not embedding:
            return False

        try:
            import psycopg

            conn = psycopg.connect(self._dsn, autocommit=True)
            conn.execute(
                """INSERT INTO product_embeddings (product_id, product_name, product_category, niche_ids, embedding, updated_at)
                   VALUES (%s, %s, %s, %s, %s::vector, NOW())
                   ON CONFLICT (product_id) DO UPDATE SET
                     product_name = EXCLUDED.product_name,
                     embedding = EXCLUDED.embedding,
                     updated_at = NOW()""",
                (product_id, name, category, niche_ids or [], json.dumps(embedding)),
            )
            conn.close()
            return True
        except Exception as exc:
            logger.warning("[EmbeddingMatcher] Index failed for %s: %s", product_id, str(exc)[:60])
            return False

    def find_best_products(
        self, content_text: str, niche_id: str = "", top_k: int = 3
    ) -> list[dict[str, Any]]:
        """Find the most semantically similar products to content text."""
        embedding = self.embed_text(content_text)
        if not embedding:
            return []

        try:
            import psycopg
            from psycopg.rows import dict_row

            conn = psycopg.connect(self._dsn, row_factory=dict_row)
            cur = conn.cursor()

            # Cosine similarity search
            query = """
                SELECT product_id, product_name, product_category,
                       1 - (embedding <=> %s::vector) as similarity
                FROM product_embeddings
                WHERE 1 - (embedding <=> %s::vector) > %s
            """
            params: list = [json.dumps(embedding), json.dumps(embedding), SIMILARITY_THRESHOLD]

            if niche_id:
                query += " AND %s = ANY(niche_ids)"
                params.append(niche_id)

            query += " ORDER BY embedding <=> %s::vector LIMIT %s"
            params.extend([json.dumps(embedding), top_k])

            cur.execute(query, params)
            results = [dict(r) for r in cur.fetchall()]
            conn.close()

            if results:
                logger.info(
                    "[EmbeddingMatcher] Found %d products for '%s' (best=%.2f)",
                    len(results),
                    content_text[:40],
                    results[0]["similarity"],
                )
            return results

        except Exception as exc:
            logger.warning("[EmbeddingMatcher] Search failed: %s", str(exc)[:60])
            return []
