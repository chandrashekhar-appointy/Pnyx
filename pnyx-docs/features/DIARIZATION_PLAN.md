# Diarization & Transcription Architecture

**Status:** Live / Implemented  
**Last Updated:** January 28, 2026

This document outlines the implemented architecture for handling real-time transcription, parallel audio recording, and post-meeting speaker diarization in Pnyx.

---

## 1. High-Level Workflow

The system operates in two distinct phases to ensure low latency during the meeting and high accuracy after the meeting.

### Phase A: During Meeting (Real-Time)
**Goal:** Instant feedback, live transcripts, low latency.

1.  **Browser Audio:** Captured via `AudioStreamClient` (AudioWorklet).
2.  **WebSocket Stream:** 16kHz PCM audio sent to backend.
3.  **Parallel Processing:**
    *   **Path A (Transcription):** Audio → VAD → Rolling Buffer → Groq (Whisper Large v3) → Live Transcript UI.
    *   **Path B (Recording):** Audio → Chunk Buffer → Disk Storage (`.pcm` files).

### Phase B: Post-Meeting (Async Processing)
**Goal:** Speaker attribution (Who said what?), 100% word accuracy.

1.  **Trigger:** User clicks "Diarize" or auto-triggered after recording.
2.  **Merge:** Raw PCM chunks combined into a single WAV file.
3.  **Gold Standard Analysis:**
    *   **Transcription:** Full audio re-transcribed with Whisper (Groq) for maximum accuracy.
    *   **Diarization:** Audio processed by Deepgram/AssemblyAI to identify speaker turns.
4.  **Alignment:** Whisper text is mapped to Deepgram speaker labels.
5.  **Update:** Live transcripts in database are **replaced** with high-fidelity, speaker-labeled segments.

---

## 2. Component Architecture

### A. Live Transcription Engine (`streaming_transcription.py`)

Handles the real-time text generation during the meeting.

*   **VAD (Voice Activity Detection):**
    *   **Strategy:** TenVAD (C++) → SileroVAD (ML) → SimpleVAD (Energy).
    *   **Purpose:** Only sends audio to LLM when speech is detected, saving cost and bandwidth.
*   **Rolling Buffer:**
    *   Maintains a sliding window of audio (12s window, 1.5s overlap).
    *   Ensures context is preserved between chunks.
*   **Smart Triggers:**
    *   Finalizes text (commits to DB) upon:
        *   **Silence:** > 1.2s silence.
        *   **Punctuation:** Complete sentence + 3s delay.
        *   **Timeout:** Max buffer fill (12s).
        *   **Stability:** Text unchanged for 4+ consecutive updates.
*   **Deduplication:**
    *   Uses **Sentence Hashing** and **N-gram Overlap** to prevent repeating phrases.
    *   Fuzzy matching removes overlapping words at chunk boundaries.

### B. Parallel Audio Recorder (`audio_recorder.py`)

Captures the raw "source of truth" for later processing.

*   **Non-Blocking:** Runs in a separate asyncio task to never delay transcription.
*   **Chunk Storage:**
    *   Saves audio in 30-second raw PCM chunks.
    *   **Path:** `./data/recordings/{meeting_id}/chunk_00001.pcm`
    *   **Crash Resilience:** If server crashes, only the last ~30s chunk is lost.
*   **Session Linking:**
    *   Initially records to a temporary `session_id`.
    *   Renames folder to `meeting_id` upon successful save (`/save-transcript`).

### C. Diarization Service (`diarization.py`)

The core of the "Gold Standard" post-processing strategy.

*   **Provider:** Pluggable (Deepgram Nova-2 default, AssemblyAI supported).
*   **Hybrid Alignment Strategy:**
    *   **Step 1 (The Words):** `groq.transcribe_full_audio()` gets the perfect text.
    *   **Step 2 (The Speakers):** `deepgram.listen()` gets speaker timestamps (e.g., "Speaker 0: 0.5s - 4.2s").
    *   **Step 3 (Alignment):**
        *   Matches Whisper text segments to Speaker time ranges.
        *   **Overlap Check:** Assigns speaker based on max time overlap.
        *   **Fallback:** Nearest-neighbor distance if no direct overlap.
*   **Outcome:** We get the *text quality* of Whisper Large v3 with the *speaker separation* of Deepgram.

---

## 3. Data Flow Diagram

```mermaid
graph TD
    User[User Microphone] -->|WebSocket PCM| WS[WebSocket Handler]
    
    subgraph "Real-Time Path"
        WS -->|Audio Copy 1| VAD[VAD Engine]
        VAD -->|Speech| Buffer[Rolling Buffer]
        Buffer -->|Window| Groq[Groq API (Whisper)]
        Groq -->|Partial/Final| UI[Frontend UI]
        Groq -->|Final| DB[(PostgreSQL)]
    end
    
    subgraph "Recording Path"
        WS -->|Audio Copy 2| Recorder[AudioRecorder]
        Recorder -->|30s Chunks| Disk[Disk Storage]
        Disk --> chunk1.pcm
        Disk --> chunk2.pcm
    end
    
    subgraph "Post-Meeting (Diarization)"
        Job[Diarization Job] -->|Read| Disk
        Job -->|Merge| WAV[Merged WAV]
        
        WAV -->|Full Audio| Deepgram[Deepgram API]
        WAV -->|Full Audio| Whisper[Groq API]
        
        Deepgram -->|Speaker Segments| Aligner[Alignment Engine]
        Whisper -->|High-Fi Text| Aligner
        
        Aligner -->|Final Transcripts| DB
    end
```

---

## 4. Database Schema (`db.py` / `schema.sql`)

### `meetings`
*   `id`: UUID
*   `diarization_status`: 'pending' | 'processing' | 'completed' | 'failed'
*   `audio_recorded`: Boolean

### `transcript_segments`
*   `meeting_id`: FK
*   `transcript`: Text content
*   `speaker`: Label ("Speaker 0", "Speaker 1") or Name ("Alice")
*   `timestamp`: Display time `[MM:SS]` (IST/UTC aware)
*   `audio_start_time`: Offset in seconds (for playback sync)
*   `audio_end_time`: Offset in seconds

### `meeting_speakers` (Mapping)
*   `diarization_label`: "Speaker 0"
*   `display_name`: "Alice Smith" (User editable)

---

## 5. Changes from Original Plan

| Feature | Original Plan | Implemented Architecture |
| :--- | :--- | :--- |
| **Transcription Source** | Deepgram Real-time | **Groq (Whisper Large v3)** for lower latency & higher accuracy. |
| **Diarization Timing** | Post-Meeting | **Post-Meeting (Confirmed).** Real-time was deemed too expensive/complex for Initial Production Release. |
| **Diarization Logic** | Native Provider Text | **Hybrid "Gold Standard":** Uses Whisper for text + Deepgram for speakers. |
| **Audio Storage** | Single File | **Chunked PCM:** Better reliability against crashes. |
| **VAD** | WebRTC VAD | **TenVAD / SileroVAD:** Significantly higher accuracy. |
| **Deduplication** | Simple String Match | **Smart Hash + N-Gram:** Handles fuzzy overlaps and near-duplicates. |

---

## 6. Future Improvements

*   **Voice Fingerprinting:** Enroll users ("This is always Alice") to auto-label across meetings.
*   **Stereo Diarization:** If hardware supports it, separate channels for local vs remote audio.
*   **Optimistic UI:** Show "Speaker A/B" placeholders during live meetings if low-latency diarization becomes viable.