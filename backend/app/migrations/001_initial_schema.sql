-- Migration 001: Initial Schema
-- Purpose: Create base tables for fresh installations

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
    email TEXT PRIMARY KEY,
    name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
    PRIMARY KEY (workspace_id, user_id)
);

CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    folder_path TEXT,
    owner_id TEXT,
    workspace_id TEXT
);

CREATE TABLE IF NOT EXISTS meeting_permissions (
    meeting_id TEXT NOT NULL REFERENCES meetings(id),
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('participant', 'viewer')),
    PRIMARY KEY (meeting_id, user_id)
);

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
    duration DOUBLE PRECISION,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

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

CREATE TABLE IF NOT EXISTS user_api_keys (
    user_email TEXT NOT NULL,
    provider TEXT NOT NULL,
    api_key TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_email, provider)
);
