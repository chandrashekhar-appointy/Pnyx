import psycopg2
import os

# Use the same logic as migrate_to_neon.py to get the URL
NEON_URL = os.getenv("DATABASE_URL")
if not NEON_URL:
    raise ValueError("DATABASE_URL environment variable is not set")


def setup_vector_table():
    print("🚀 Setting up vector storage in Neon DB...")

    try:
        conn = psycopg2.connect(NEON_URL)
        cursor = conn.cursor()

        # 1. Enable pgvector extension
        print("🔌 Enabling vector extension...")
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")

        # 2. Create embeddings table
        # We use 384 dimensions for all-MiniLM-L6-v2 (default in vector_store.py)
        # If using OpenAI (text-embedding-3-small), it would be 1536.
        # We'll default to 384 based on current code.
        print("📦 Creating 'meeting_embeddings' table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS meeting_embeddings (
                id SERIAL PRIMARY KEY,
                meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding vector(384),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 3. Create index for faster search (IVFFlat or HNSW)
        # HNSW is generally better for performance/recall trade-off
        print("⚡ Creating HNSW index for fast similarity search...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS meeting_embeddings_embedding_idx 
            ON meeting_embeddings 
            USING hnsw (embedding vector_cosine_ops);
        """)

        conn.commit()
        print("✅ Vector table setup complete!")

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"❌ Error setting up vector table: {e}")


if __name__ == "__main__":
    setup_vector_table()
