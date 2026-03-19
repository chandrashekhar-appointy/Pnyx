import sqlite3
import psycopg2
from psycopg2.extras import execute_values
import os
import json
from urllib.parse import urlparse

# --- CONFIGURATION ---
SQLITE_DB_PATH = "backend/app/data/meeting_minutes.db" # Adjusted path based on typical docker setup, but checking local first
if not os.path.exists(SQLITE_DB_PATH):
    # Try alternate path if running from root
    SQLITE_DB_PATH = "backend/data/meeting_minutes.db"
    if not os.path.exists(SQLITE_DB_PATH):
         # Try another one
         SQLITE_DB_PATH = "data/meeting_minutes.db"

# Neon Connection String
NEON_URL = os.getenv("DATABASE_URL")
if not NEON_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

def migrate():
    print(f"🚀 Starting migration from {SQLITE_DB_PATH} to Neon DB...")

    # 1. Connect to SQLite
    if not os.path.exists(SQLITE_DB_PATH):
        print(f"❌ SQLite database not found at {SQLITE_DB_PATH}")
        return

    sq_conn = sqlite3.connect(SQLITE_DB_PATH)
    sq_conn.row_factory = sqlite3.Row
    sq_cursor = sq_conn.cursor()

    # 2. Connect to Postgres (Neon)
    try:
        pg_conn = psycopg2.connect(NEON_URL)
        pg_cursor = pg_conn.cursor()
        print("✅ Connected to Neon DB")
    except Exception as e:
        print(f"❌ Failed to connect to Neon DB: {e}")
        return

    # 3. Create Extension & Tables (Schema Migration)
    print("📦 Creating schema in Neon...")
    
    # Enable pgvector
    pg_cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # Meetings
    pg_cursor.execute("""
        CREATE TABLE IF NOT EXISTS meetings (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            folder_path TEXT,
            owner_id TEXT,
            workspace_id TEXT
        );
    """)

    # Transcript Segments (Formerly transcripts)
    pg_cursor.execute("""
        CREATE TABLE IF NOT EXISTS transcript_segments (
            id SERIAL PRIMARY KEY,
            meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
            transcript TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            summary TEXT,
            action_items TEXT,
            key_points TEXT,
            audio_start_time DOUBLE PRECISION,
            audio_end_time DOUBLE PRECISION,
            duration DOUBLE PRECISION
        );
    """)

    # Summary Processes
    pg_cursor.execute("""
        CREATE TABLE IF NOT EXISTS summary_processes (
            meeting_id TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            error TEXT,
            result JSONB,
            start_time TIMESTAMP,
            end_time TIMESTAMP,
            chunk_count INTEGER DEFAULT 0,
            processing_time DOUBLE PRECISION DEFAULT 0.0,
            metadata JSONB
        );
    """)

    # Full Transcripts (Formerly transcript_chunks)
    pg_cursor.execute("""
        CREATE TABLE IF NOT EXISTS full_transcripts (
            meeting_id TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
            meeting_name TEXT,
            transcript_text TEXT NOT NULL,
            model TEXT NOT NULL,
            model_name TEXT NOT NULL,
            chunk_size INTEGER,
            overlap INTEGER,
            created_at TIMESTAMP NOT NULL
        );
    """)

    # Settings
    pg_cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            whisperModel TEXT NOT NULL,
            groqApiKey TEXT,
            openaiApiKey TEXT,
            anthropicApiKey TEXT,
            ollamaApiKey TEXT,
            geminiApiKey TEXT
        );
    """)

    # Transcript Settings
    pg_cursor.execute("""
        CREATE TABLE IF NOT EXISTS transcript_settings (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            whisperApiKey TEXT,
            deepgramApiKey TEXT,
            elevenLabsApiKey TEXT,
            groqApiKey TEXT,
            openaiApiKey TEXT
        );
    """)

    # User API Keys
    pg_cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_api_keys (
            user_email TEXT NOT NULL,
            provider TEXT NOT NULL,
            api_key TEXT NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_email, provider)
        );
    """)
    
    # Workspaces (Missing from previous plan, but present in SQLite)
    pg_cursor.execute("""
        CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Workspace Members
    pg_cursor.execute("""
        CREATE TABLE IF NOT EXISTS workspace_members (
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            user_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
            PRIMARY KEY (workspace_id, user_id)
        );
    """)

    # Meeting Permissions
    pg_cursor.execute("""
        CREATE TABLE IF NOT EXISTS meeting_permissions (
            meeting_id TEXT NOT NULL REFERENCES meetings(id),
            user_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('participant', 'viewer')),
            PRIMARY KEY (meeting_id, user_id)
        );
    """)

    pg_conn.commit()

    # 4. Migrate Data
    # Keep track of migrated meeting IDs to prevent FK violations
    valid_meeting_ids = set()

    # Order matters for foreign keys!
    # Map Source Table Name (SQLite) -> Target Table Name (Postgres)
    tables_map = {
        "workspaces": "workspaces",
        "meetings": "meetings",
        "settings": "settings",
        "transcript_settings": "transcript_settings",
        "user_api_keys": "user_api_keys",
        "transcripts": "transcript_segments",  # RENAME
        "summary_processes": "summary_processes",
        "transcript_chunks": "full_transcripts", # RENAME
        "workspace_members": "workspace_members",
        "meeting_permissions": "meeting_permissions"
    }

    for source_table, target_table in tables_map.items():
        print(f"🔄 Migrating table: {source_table} -> {target_table}...")
        try:
            # Get data from SQLite
            try:
                sq_cursor.execute(f"SELECT * FROM {source_table}")
                rows = sq_cursor.fetchall()
            except sqlite3.OperationalError:
                print(f"   ⚠️ Table {source_table} not found in SQLite. Skipping.")
                continue
            
            if not rows:
                print(f"   Skipping (empty): {source_table}")
                continue

            # Prepare columns and placeholders
            columns = list(rows[0].keys()) # Convert to list to modify if needed
            
            # Special handling for transcripts ID
            if source_table == "transcripts" and "id" in columns:
                # Remove 'id' from columns to let Postgres SERIAL generate it
                columns.remove("id")
            
            cols_str = ",".join(columns)
            # IMPORTANT: Postgres uses VALUES %s for execute_values
            
            # Convert SQLite rows to list of tuples
            values = []
            for row in rows:
                row_dict = dict(row)
                
                # Capture valid meeting IDs
                if source_table == "meetings":
                    valid_meeting_ids.add(row_dict['id'])

                # Filter orphaned records for dependent tables
                if source_table in ["transcripts", "summary_processes", "transcript_chunks", "meeting_permissions"]:
                    if row_dict.get('meeting_id') and row_dict['meeting_id'] not in valid_meeting_ids:
                        print(f"   ⚠️ Skipping orphaned record for meeting_id: {row_dict['meeting_id']}")
                        continue

                # JSON Handling for Postgres
                if source_table == "summary_processes":
                    # Fix result field
                    if row_dict.get('result') and isinstance(row_dict['result'], str):
                        try:
                            json.loads(row_dict['result']) # Just verify
                        except:
                            pass 
                    
                    # Fix metadata field
                    if row_dict.get('metadata') and isinstance(row_dict['metadata'], str):
                        try:
                            json.loads(row_dict['metadata'])
                        except:
                            pass

                # Fix Boolean handling for user_api_keys
                if source_table == "user_api_keys":
                    if 'is_active' in row_dict:
                        # Convert 0/1 integer to Boolean
                        row_dict['is_active'] = bool(row_dict['is_active'])

                # Reconstruct list with modified values in correct column order
                # NOTE: We use 'columns' list which might have 'id' removed
                val_list = [row_dict[c] for c in columns]
                values.append(tuple(val_list))

            if not values:
                print(f"   Skipping {source_table} (no valid rows after filtering).")
                continue

            # Insert into Postgres
            insert_query = f"INSERT INTO {target_table} ({cols_str}) VALUES %s ON CONFLICT DO NOTHING"
            execute_values(pg_cursor, insert_query, values)
            print(f"   ✅ Migrated {len(values)} rows.")

        except Exception as e:
            print(f"   ❌ Error migrating {source_table}: {e}")
            pg_conn.rollback()
            continue

    pg_conn.commit()
    print("\n🎉 Migration completed successfully!")
    
    sq_conn.close()
    pg_conn.close()

if __name__ == "__main__":
    migrate()
