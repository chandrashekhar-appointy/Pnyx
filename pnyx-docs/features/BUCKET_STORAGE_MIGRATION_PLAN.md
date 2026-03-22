# Bucket Storage Migration Plan

## Goal

Reduce primary database growth by moving large content blobs out of Postgres and keeping Postgres focused on structured, queryable application state.

For this phase, the design principle is:

- **Postgres** stores structured facts, indexes, permissions, workflow state, and object references.
- **Bucket storage** stores large transcript and notes content.
- **Redis / in-memory state** remains the right home for transient live session state, but that is out of scope for this bucket migration plan.

## Scope

This plan covers moving large content that should live in object storage now:

- Full transcript bodies
- Transcript snapshots / transcript version payloads
- Generated notes bodies

This plan does **not** include:

- Notes versioning
- Generic AI artifacts
- Live session metadata cleanup
- Ledger redesign
- Redis migration work

Those can be handled separately later.

## Current State

### Already bucket-friendly today

The codebase already uses bucket/GCS-style storage for audio pipeline artifacts:

- Uploaded PCM chunks
- Merged WAV files
- Recording archival flows

Relevant paths:

- [post_recording.py](/home/gagansharma/Projects/meeting-co-pilot/backend/app/services/audio/post_recording.py)
- [audio.py](/home/gagansharma/Projects/meeting-co-pilot/backend/app/api/routers/audio.py)
- [audio_pipeline.py](/home/gagansharma/Projects/meeting-co-pilot/backend/app/tasks/audio_pipeline.py)

So the bucket pattern already exists in the app. We are extending it to transcripts and notes content.

### Current DB content that is a fit for bucket

- `full_transcripts.transcript_text`
  Full concatenated transcript text in Postgres.

- `transcript_versions.content_json`
  Full transcript snapshot arrays stored directly in Postgres.

- `summary_processes.result`
  Potentially large generated note/summary payloads in JSON.

These fields are bulky, largely retrieved by meeting ID, and do not need relational joins at full body level.

## What Stays In Postgres

The following still make sense in Postgres and are not part of this migration:

- `meetings`
- permissions / sharing tables
- `shared_meeting_notes`
- `user_credits`, `credit_purchases`
- `calendar_events`
- `feedback`
- workflow tables such as `summary_processes` and diarization job status rows, but only for compact status metadata
- `transcript_segments` as the structured transcript index for search, display, timestamps, and retrieval

## What Moves To Bucket

### 1. Full transcript bodies

Move:

- `full_transcripts.transcript_text`

Keep in DB:

- `meeting_id`
- transcript source / model metadata
- created timestamp
- object path
- optional excerpt / preview
- optional checksum / size / content type

Why:

- Large text blob
- Natural fit for object storage
- Typically fetched by meeting ID, not by relational filters on the full body

### 2. Transcript snapshot payloads

Move:

- `transcript_versions.content_json`

Keep in DB:

- `meeting_id`
- `version_num`
- `source`
- `is_authoritative`
- `created_at`
- `alignment_config`
- `confidence_metrics`
- object path
- optional content hash / byte size

Why:

- This is the largest likely future source of silent DB growth
- Snapshot arrays are excellent object-storage content
- The DB only needs version metadata and lookup

### 3. Generated notes bodies

Move:

- large note/summary payloads currently inside `summary_processes.result`

Keep in DB:

- `meeting_id`
- status
- timestamps
- error
- template name
- processing metadata
- latest note object path
- small preview / excerpt

Why:

- Generated notes can get long quickly
- Notes are usually retrieved as a document, not joined relationally at full payload level
- This keeps processing/job state compact while moving the bulky output out of Postgres

## Target Data Model

### A. `full_transcripts`

Current role:

- Stores full transcript text directly

Target role:

- Metadata + object reference only

Suggested shape:

- `meeting_id`
- `transcript_object_path`
- `transcript_preview`
- `model`
- `model_name`
- `chunk_size`
- `overlap`
- `content_sha256`
- `byte_size`
- `created_at`
- `updated_at`

Bucket object example:

- `meetings/{meeting_id}/transcripts/full/latest.txt`

Optional JSON alternative:

- `meetings/{meeting_id}/transcripts/full/latest.json`

### B. `transcript_versions`

Current role:

- Stores version metadata and full `content_json`

Target role:

- Version manifest table

Suggested shape:

- `id`
- `meeting_id`
- `version_num`
- `source`
- `is_authoritative`
- `alignment_config`
- `confidence_metrics`
- `content_object_path`
- `content_sha256`
- `byte_size`
- `created_at`

Bucket object example:

- `meetings/{meeting_id}/transcripts/versions/{version_num}.json`

### C. `summary_processes`

Current role:

- Workflow state + large result JSON

Target role:

- Workflow state + latest note reference

Suggested shape:

- `meeting_id`
- `status`
- `start_time`
- `end_time`
- `metadata`
- `result_preview`
- `result_object_path`
- `result_sha256`
- `result_byte_size`
- `error`

Bucket object example:

- `meetings/{meeting_id}/notes/latest.json`
- or `meetings/{meeting_id}/notes/latest.md`

Recommendation:

- Write both `json` and `md` only if both are truly used.
- Otherwise pick one canonical format and derive the other on demand later.

## Migration Strategy

### Phase 1. Add object-reference columns

Add nullable columns so the code can support hybrid reads:

- `full_transcripts.transcript_object_path`
- `full_transcripts.content_sha256`
- `full_transcripts.byte_size`
- `full_transcripts.transcript_preview`

- `transcript_versions.content_object_path`
- `transcript_versions.content_sha256`
- `transcript_versions.byte_size`

- `summary_processes.result_object_path`
- `summary_processes.result_sha256`
- `summary_processes.result_byte_size`
- `summary_processes.result_preview`

Do not remove old columns yet.

### Phase 2. Write new records to bucket first

Update write paths so new transcript and note bodies are uploaded to bucket and DB stores only references plus compact previews.

Important rule:

- New writes should be **bucket-first or bucket-plus-DB-reference**, not DB-body-first.

Fallback behavior:

- If bucket upload fails, decide whether to:
  - fail the operation, or
  - temporarily fall back to DB storage behind a feature flag

Recommendation:

- Use a short-lived fallback flag during rollout.
- Remove fallback once stable.

### Phase 3. Read with hybrid fallback

Update reads to:

1. Prefer bucket object path if present
2. Fall back to legacy DB column if object path is missing

This avoids breaking older meetings during migration.

### Phase 4. Backfill historical rows

Run one-time backfill jobs:

- export existing `full_transcripts.transcript_text` to bucket
- export existing `transcript_versions.content_json` to bucket
- export large `summary_processes.result` payloads to bucket

For each row:

1. Read DB payload
2. Serialize to canonical object format
3. Upload to bucket
4. Save object path + hash + byte size in DB
5. Mark migrated

Recommendation:

- Backfill in batches
- Log failures and continue
- Make the script idempotent

### Phase 5. Verify parity

Before deleting old DB bodies, verify:

- object exists
- hash matches source payload
- read path works in app
- no broken meeting details or notes UI

### Phase 6. Drop old bulky columns

Only after successful rollout and backfill:

- stop reading old columns
- remove or null out:
  - `full_transcripts.transcript_text`
  - `transcript_versions.content_json`
  - `summary_processes.result` if no longer needed inline

This should be a separate cleanup migration after a stable observation window.

## Canonical Object Formats

### Full transcript

Recommended:

- plain text for easy debugging, or
- JSON if you want metadata alongside body

Suggested JSON shape:

```json
{
  "meeting_id": "abc123",
  "text": "full transcript text",
  "updated_at": "2026-03-20T12:00:00Z",
  "source": "live"
}
```

### Transcript version

Recommended:

- JSON

Suggested shape:

```json
{
  "meeting_id": "abc123",
  "version_num": 3,
  "source": "diarized",
  "segments": [
    {
      "speaker": "Speaker 1",
      "text": "hello everyone",
      "start_time": 0.5,
      "end_time": 2.1
    }
  ]
}
```

### Notes

Recommended canonical format:

- JSON if the frontend/backend work with structured sections

Optional rendered companion:

- Markdown for export / debugging only if needed

Suggested JSON shape:

```json
{
  "meeting_id": "abc123",
  "template_name": "default",
  "generated_at": "2026-03-20T12:00:00Z",
  "content": {
    "sections": []
  }
}
```

## Rollout Risks

### 1. Broken reads for old meetings

Mitigation:

- hybrid read path until backfill is complete

### 2. Partial migration state

Mitigation:

- idempotent backfill
- store object path only after successful upload

### 3. Bucket upload failures during note/transcript generation

Mitigation:

- short-lived fallback flag
- explicit error logging

### 4. Search features accidentally depending on removed full blobs

Mitigation:

- verify all current transcript consumers
- keep `transcript_segments` intact
- switch search/retrieval to segment/index rows, not full body rows

## Recommended Order

1. Migrate `transcript_versions.content_json`
2. Migrate `full_transcripts.transcript_text`
3. Migrate large note bodies from `summary_processes.result`

Why this order:

- transcript snapshots are the clearest long-term blob risk
- full transcript body duplication is the next easiest win
- notes should move too, but their exact canonical format should be confirmed first

## Open Decisions

Before implementation, decide:

1. Should full transcript canonical storage be `txt` or `json`?
2. Should notes canonical storage be `json`, `md`, or both?
3. Do we want a temporary DB fallback if bucket upload fails?
4. Do we want previews stored in DB for fast meeting list / details rendering?
5. Should backfill null out old DB bodies immediately or only after a cooling period?

## Recommended Answer

My recommendation for this repo right now:

- full transcript: canonical `json`
- transcript versions: canonical `json`
- notes: canonical `json`
- DB stores preview + object path + hash + size
- keep hybrid read logic until all legacy rows are backfilled
- do not tackle notes versioning yet

## Success Criteria

The migration is successful when:

- new transcript bodies are no longer stored inline in Postgres
- new transcript version payloads are no longer stored inline in Postgres
- new generated note bodies are no longer stored inline in Postgres
- meeting details, transcript views, and summary views still work
- Postgres growth is driven mostly by structured rows, not large content blobs
