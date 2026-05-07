"""
Vector Store Service - PostgreSQL (pgvector) implementation.

This module handles:
- Generating embeddings for transcript chunks (using SentenceTransformers)
- Storing vectors in PostgreSQL (Neon DB)
- Semantic search using pgvector
"""

import logging
import os
import asyncpg
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Global embedding model (lazy loaded)
_embedding_model = None


def _get_embedding_model():
    """Get or create the SentenceTransformer model."""
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer

            # Using all-MiniLM-L6-v2 (384 dimensions) - fast and efficient
            _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("✅ SentenceTransformer model loaded (all-MiniLM-L6-v2)")
        except ImportError:
            logger.error(
                "❌ sentence-transformers not installed. Run: pip install sentence-transformers"
            )
            return None
        except Exception as e:
            logger.error(f"❌ Failed to load embedding model: {e}")
            return None
    return _embedding_model


def chunk_transcript(text: str, chunk_size: int = 500, overlap: int = 100) -> List[str]:
    """Split transcript into overlapping chunks for embedding."""
    if not text or len(text) < chunk_size:
        return [text] if text else []

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            # Look for sentence endings
            for sep in [". ", "! ", "? ", "\n"]:
                last_sep = text.rfind(sep, start, end)
                if last_sep > start + chunk_size // 2:
                    end = last_sep + 1
                    break

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        start = end - overlap if end < len(text) else len(text)

    return chunks


async def _get_db_connection():
    """Get a connection to the PostgreSQL database."""
    # Use the same DATABASE_URL as the main app
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL environment variable is not set")
    return await asyncpg.connect(db_url)


async def store_meeting_embeddings(
    meeting_id: str,
    meeting_title: str,
    meeting_date: str,
    transcripts: List[Dict[str, Any]],
) -> int:
    """Store transcript embeddings for a meeting in Postgres."""
    model = _get_embedding_model()
    if model is None:
        return 0

    # Combine all transcripts
    full_text = "\n".join([t.get("text", "") for t in transcripts if t.get("text")])

    if not full_text.strip():
        logger.info(f"ℹ️ No transcript text for meeting {meeting_id}")
        return 0

    # Chunk the transcript
    chunks = chunk_transcript(full_text)
    if not chunks:
        return 0

    try:
        # Generate embeddings (sync call, might block event loop briefly, but fast for MiniLM)
        # For very large texts, consider running in a thread pool
        embeddings = model.encode(chunks)

        conn = await _get_db_connection()
        try:
            async with conn.transaction():
                # Delete existing embeddings for this meeting (full refresh)
                await conn.execute(
                    "DELETE FROM meeting_embeddings WHERE meeting_id = $1", meeting_id
                )

                # Insert new chunks
                records = []
                for i, (chunk, vector) in enumerate(zip(chunks, embeddings)):
                    # Convert vector to string format "[x, y, z]" for Postgres
                    # This avoids "no binary format encoder" error in asyncpg copy
                    vector_str = str(vector.tolist())
                    records.append((meeting_id, i, chunk, vector_str))

                # Use executemany which handles text-to-vector casting automatically
                await conn.executemany(
                    """
                    INSERT INTO meeting_embeddings (meeting_id, chunk_index, content, embedding)
                    VALUES ($1, $2, $3, $4)
                """,
                    records,
                )

            logger.info(
                f"✅ Stored {len(chunks)} chunks for meeting '{meeting_title}' ({meeting_id})"
            )
            return len(chunks)

        finally:
            await conn.close()

    except Exception as e:
        logger.error(f"❌ Failed to store embeddings: {e}")
        return 0


async def search_context(
    query: str, n_results: int = 5, allowed_meeting_ids: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Search for relevant context using vector similarity."""
    model = _get_embedding_model()
    if model is None:
        return []

    try:
        # Generate query vector
        query_vector = model.encode(query).tolist()

        conn = await _get_db_connection()
        try:
            # Build query dynamically based on filters
            # Note: <=> is the cosine distance operator in pgvector
            # We order by distance ASC (closest first)

            sql = """
                SELECT 
                    e.content,
                    e.meeting_id,
                    m.title as meeting_title,
                    m.created_at as meeting_date,
                    e.chunk_index,
                    1 - (e.embedding <=> $1) as similarity
                FROM meeting_embeddings e
                JOIN meetings m ON e.meeting_id = m.id
            """

            args = [
                str(query_vector)
            ]  # asyncpg requires vector/json as string or list?
            # asyncpg-pgvector usually expects string representation of list or native list if registered.
            # safe bet: pass list, let asyncpg handle it if type codec is set, or cast to vector explicitly.
            # Actually, standard way is just pass list if pgvector codec is registered, or string '[...]'
            # We'll try passing string representation which is robust.

            if allowed_meeting_ids:
                sql += " WHERE e.meeting_id = ANY($2::text[])"
                args.append(allowed_meeting_ids)

            sql += " ORDER BY e.embedding <=> $1 LIMIT $3"
            args.append(n_results)  # $2 or $3 depending on filter

            # Fix args index if no filter
            if not allowed_meeting_ids:
                # args was [vector, n_results]
                # query expects $3 for limit? No, if no filter, limit is $2
                sql = sql.replace("$3", "$2")

            rows = await conn.fetch(sql, *args)

            formatted = []
            for row in rows:
                formatted.append(
                    {
                        "text": row["content"],
                        "meeting_id": row["meeting_id"],
                        "meeting_title": row["meeting_title"],
                        "meeting_date": row["meeting_date"].isoformat()
                        if row["meeting_date"]
                        else "",
                        "similarity": float(row["similarity"]),
                        "chunk_index": row["chunk_index"],
                    }
                )

            logger.debug(
                f"🔍 Found {len(formatted)} results for query: '{query[:50]}...'"
            )
            return formatted

        finally:
            await conn.close()

    except Exception as e:
        logger.error(f"❌ Search failed: {e}")
        return []


def get_collection_stats() -> Dict[str, Any]:
    """Get statistics about the vector store."""
    # We can't make async calls from sync function easily without loop
    # For now, return a placeholder or refactor to async
    return {"status": "available (postgres)", "info": "Stats require async call"}
