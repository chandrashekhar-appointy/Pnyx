# AGENTS.md

This file provides guidance to Codex when working with code in this repository.

## ⚠️ **IMPORTANT: Project Direction Change**

**This project is transitioning from Meetily (Tauri desktop app) to Meeting Co-Pilot (web-based collaborative meeting assistant).**

**Current Status**: **Phase 7 Completed** - Context-Aware Chatbot (Jan 30, 2026)

---

## Real-Time Streaming Transcription Architecture (Current)

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│   Browser (Next.js Frontend)                                             │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  RecordingControls.tsx                                            │   │
│  │  └── AudioStreamClient (WebSocket + AudioWorklet)                 │   │
│  │       ├── getUserMedia() → 48kHz audio                            │   │
│  │       ├── AudioWorklet → downsample to 16kHz PCM                  │   │
│  │       └── WebSocket binary streaming (continuous)                 │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│       ↑ JSON (partial/final transcripts)  ↓ Binary PCM audio            │
└───────┼───────────────────────────────────┼─────────────────────────────┘
        │ WebSocket: /ws/streaming-audio    │
        ↓                                   ↓
┌─────────────────────────────────────────────────────────────────────────┐
│   Backend (FastAPI + Python)                                             │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  StreamingTranscriptionManager                                    │   │
│  │  ├── SimpleVAD (Voice Activity Detection, threshold=0.08)        │   │
│  │  ├── RollingAudioBuffer (6s window, 5s slide = 1s overlap)       │   │
│  │  └── Smart deduplication (_remove_overlap algorithm)             │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│       │                                                                  │
│       ↓                                                                  │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  GroqTranscriptionClient                                          │   │
│  │  ├── Groq API (whisper-large-v3 model)                            │   │
│  │  ├── Auto language detection (Hindi/English/Hinglish)            │   │
│  │  └── No prompts (pure transcription to avoid prompt leakage)     │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│       │                                                                  │
│       ↓                                                                  │
│  ┌─────────┐  ┌──────────┐                                              │
│  │Postgres │  │ Partial/ │                                              │
│  │ Storage │  │ Final    │ → WebSocket JSON response to browser         │
│  └─────────┘  └──────────┘                                              │
└─────────────────────────────────────────────────────────────────────────┘
```

### Key Components

#### Frontend (`frontend/src/lib/audio-streaming/`)

| File | Purpose |
|------|---------|
| `AudioStreamClient.ts` | Main client - manages WebSocket + AudioWorklet |
| `audio-processor.worklet.js` | AudioWorklet - downsamples 48kHz → 16kHz with anti-aliasing |

**Audio Pipeline**:
1. `getUserMedia()` captures microphone at 48kHz
2. AudioWorklet runs in separate thread for real-time processing
3. Downsamples to 16kHz mono PCM with anti-aliasing filter
4. Streams binary chunks via WebSocket (continuous, not chunked files)

#### Backend (`backend/app/`)

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app, WebSocket endpoint `/ws/streaming-audio` |
| `streaming_transcription.py` | Orchestrates VAD → Buffer → Groq → Partial/Final |
| `groq_client.py` | Groq API integration for Whisper Large v3 |
| `vad.py` | SimpleVAD - amplitude-based voice activity detection |
| `rolling_buffer.py` | Sliding window buffer (6s window, 5s slide) |

### Transcription Flow

```
1. Browser captures audio (48kHz)
       ↓
2. AudioWorklet downsamples (48kHz → 16kHz, anti-aliasing)
       ↓
3. WebSocket streams binary PCM to backend
       ↓
4. SimpleVAD checks if speech (threshold=0.08)
       ↓ (if speech)
5. RollingAudioBuffer accumulates (6s window, 5s slide)
       ↓ (when slide interval reached)
6. GroqTranscriptionClient sends to Groq API
       ↓
7. Smart deduplication removes overlapping words
       ↓
8. Partial/Final logic determines transcript state
       ↓
9. WebSocket sends JSON to browser
       ↓
10. UI displays transcript (partial=gray, final=black)
```

### Key Technical Decisions

1. **Groq Whisper over Local Whisper**:
   - Groq API provides faster processing (~1-2s latency)
   - whisper-large-v3 model for best Hinglish support
   - No GPU required on server

2. **6s Window, 5s Slide (1s Overlap)**:
   - More context for complete sentences
   - 1s overlap catches boundary words
   - Smart deduplication prevents repeated text

3. **No Prompts in Transcription**:
   - Prompts were leaking into transcription output
   - Pure transcription mode with auto language detection
   - Output is in original script (Devanagari for Hindi)

4. **SimpleVAD over Silero**:
   - Amplitude-based (fast, no ML overhead)
   - Threshold 0.08 (not too sensitive)
   - Can upgrade to Silero later if needed

### Smart Deduplication Algorithm

```python
def _remove_overlap(self, new_text: str) -> str:
    """
    Remove overlapping text from new transcript.

    Example:
        last_final_text = "Hello how are you"
        new_text = "are you doing today"
        return = "doing today"  (removed "are you" overlap)
    """
    # Get last 10 words from final transcript
    # Check if new text starts with any of those words
    # Remove overlapping prefix
```

---

## Project Overview

**Meeting Co-Pilot** is a web-based collaborative meeting assistant forked from Meetily. It enhances meetings through:
- **Real-time multi-participant collaboration** (web-based, no installation)
- **Live transcript** visible to all participants
- **AI-powered features** (catch-up, Q&A, decision tracking)
- **Cross-meeting context** (link related meetings, surface past decisions)

### Key Difference from Meetily
| Aspect | Meetily (Original) | Meeting Co-Pilot (Fork) |
|--------|-------------------|------------------------|
| Architecture | Tauri desktop app | Web-based (Next.js + FastAPI) |
| Users | Single user | Multi-participant sessions |
| Audio | Desktop APIs | Browser getUserMedia() |
| Use Case | Privacy-first local | Collaborative on-site meetings |

## Product Requirements Document (PRD)

**Full PRD Location**: `/docs/PRD.md` (if you've added it) or shared externally

**Key Goals**:
1. **G1**: Enable shared meeting context (all see same transcript)
2. **G2**: Eliminate "corporate amnesia" (searchable history)
3. **G3**: Support on-site meetings (room mic + laptops)
4. **G4**: Instant catch-up (AI summaries for zoned-out participants)
5. **G5**: Cross-meeting continuity (link related meetings)
6. **G6**: Automate action tracking
7. **G7**: Real-time Q&A during meetings

**Explicitly NOT Building**:
- Video/audio conferencing (not Zoom/Teams)
- System audio capture (online meetings)
- Mobile apps
- Complex auth/permissions
- Enterprise multi-tenant

## Architecture: Web vs Desktop

**Decision**: Web-based (removing Tauri)

**Rationale**:
- ✅ 95% of meetings are on-site (room mic sufficient)
- ✅ Multi-participant = URL sharing (no install)
- ✅ Faster development (no Rust/Tauri complexity)
- ✅ Lower barrier to entry (<30s join time)
- ❌ Cannot capture system audio (Zoom/Teams) - acceptable trade-off

## Current Technology Stack

### Backend (✅ Complete - Real-Time Streaming)
- **Framework**: FastAPI (Python 3.11+)
- **Database**: PostgreSQL (asyncpg)
- **Transcription**: Groq Whisper Large v3 API (cloud, low latency)
- **Audio Processing**: SimpleVAD + RollingAudioBuffer
- **LLM**: pydantic-ai (Codex, OpenAI, Groq, Ollama)
- **WebSocket**: Real-time bidirectional audio/transcript streaming

### Frontend (✅ Complete - Web Audio)
- **Framework**: Next.js 14 + React 18 (pure web, no Tauri)
- **Audio Capture**: Browser getUserMedia() + AudioWorklet
- **Streaming**: WebSocket binary streaming to backend
- **State**: React hooks + context for recording state

## Essential Development Commands

### Backend Development (✅ Currently Working)

**Location**: `/backend`

```bash
# Docker (Recommended - Currently Running)
docker ps                           # Check running containers
./run-docker.sh logs --service app  # View backend logs

# Manual (if not using Docker)
./build_whisper.sh small            # Build Whisper with model
./clean_start_backend.sh            # Start FastAPI server (port 5167)
```

**Service Endpoints**:
- **Backend API**: http://localhost:5167
- **API Docs**: http://localhost:5167/docs
- **Whisper Server**: http://localhost:8178

### Frontend Development (🔧 In Transition)

**Location**: `/frontend`

**Current (Tauri - Being Removed)**:
```bash
pnpm run tauri:dev  # ❌ Don't use - requires Rust/Cargo
```

**Temporary (Web Dev Server)**:
```bash
cd frontend
pnpm install
pnpm run dev        # ✅ Use this - runs Next.js at http://localhost:3118
```

**Known Issues**:
- You'll see Tauri errors in browser console (expected - being removed)
- Audio recording won't work (needs browser API implementation)
- UI will load but some features are non-functional

## Implementation Plan (Revised Jan 30, 2026)

### Phase 6: Import Recording ✅ (COMPLETED)
- [x] `POST /upload-meeting-recording` endpoint
- [x] FFmpeg file conversion & validation
- [x] Background processing pipeline (Transcribe -> Diarize -> Summarize)
- [x] Frontend Import UI & Progress tracking

### Phase 7: Context-Aware Chatbot ✅ (COMPLETED)
**Goal**: Intelligent RAG system with Web Search capabilities.
- [x] **Context Router**: Logic to route queries to Live, History, or Web.
- [x] **Web Search**: Integration with Tavily/Google for external fact-checking.
- [x] **Debate Detection**: Auto-trigger search when participants disagree.
- [x] **Hybrid Retrieval**: Combine internal vector search with web results.

### Phase 8: Polish & Production 🚀 (IN PROGRESS)
- [ ] **Cloud Storage**: Migrate local uploads to GCP Buckets.
- [ ] **Production Deployment**: Docker/Cloud Run setup.
- [ ] **UX Polish**: Linking visualization, Confidence UI, Export features.
- [ ] **Security**: Finalize RBAC and API security.

## Architecture Diagrams

### Current (Meetily - Desktop)
```
┌─────────────────────────────────────────┐
│   Frontend (Tauri Desktop App)          │
│  ┌──────────┐  ┌────────────────────┐  │
│  │ Next.js  │←→│  Rust (Audio/IPC)  │  │
│  └──────────┘  └────────────────────┘  │
│       ↑ Tauri Events                    │
└───────┼─────────────────────────────────┘
        │ HTTP
        ↓
┌───────────────────────────────────────┐
│   Backend (FastAPI)                   │
│  ┌─────────┐  ┌──────────┐           │
│  │ Postgres│  │ Whisper  │           │
│  └─────────┘  └──────────┘           │
└───────────────────────────────────────┘
```

### Target (Meeting Co-Pilot - Web)
```
┌─────────────────────────────────────────┐
│   Frontend (Pure Web - Next.js)         │
│  ┌──────────┐  ┌────────────────────┐  │
│  │   UI     │  │  Browser Audio API │  │
│  └──────────┘  └────────────────────┘  │
│       ↑ WebSocket (Real-time Sync)      │
└───────┼─────────────────────────────────┘
        ↓
┌───────────────────────────────────────┐
│   Backend (FastAPI)                   │
│  ┌─────────┐  ┌──────────┐  ┌──────┐│
│  │Postgres │  │ Whisper  │  │Vector││
│  │         │  │          │  │  DB  ││
│  └─────────┘  └──────────┘  └──────┘│
└───────────────────────────────────────┘
```

## Migration Strategy: What to Keep vs Remove

### ✅ KEEP (60-70% of Meetily)
- **Backend**: Entire FastAPI app
  - Meeting CRUD operations
  - Whisper integration
  - LLM summarization
  - VectorDB for embeddings
- **Frontend UI**: Most React components
  - Meeting list
  - Transcript display
  - Summary view
  - Settings

### 🔧 MODIFY (Significant Changes)
- **Audio Capture**: Tauri APIs → Browser getUserMedia()
- **Real-time Communication**: Single-user → Multi-user WebSocket
- **State Management**: Add session/participant tracking
- **Q&A**: Single-user → Private per-participant

### ✅ REMOVED (Cleanup Complete)
- **All Rust Code**: `frontend/src-tauri/` - DELETED
- **Tauri Dependencies**: Removed from package.json
- **Batch Processing**: Old `/ws/audio` endpoint removed from backend
- **Test Pages**: `test-audio/` and `test-streaming/` removed
- **Old Audio Library**: `frontend/src/lib/audio-web/` removed

## Key Files Reference

### Backend (✅ Complete)
- `backend/app/main.py` - FastAPI app, WebSocket `/ws/streaming-audio`
- `backend/app/streaming_transcription.py` - StreamingTranscriptionManager
- `backend/app/groq_client.py` - Groq Whisper API client
- `backend/app/vad.py` - Voice Activity Detection
- `backend/app/rolling_buffer.py` - Sliding window audio buffer
- `backend/app/db.py` - PostgreSQL database operations
- `backend/app/summarization.py` - LLM summarization

### Frontend (✅ Complete - Pure Web)
- `frontend/src/app/page.tsx` - Main recording interface
- `frontend/src/components/RecordingControls.tsx` - Streaming audio recording
- `frontend/src/lib/audio-streaming/AudioStreamClient.ts` - WebSocket + AudioWorklet client
- `frontend/public/audio-processor.worklet.js` - Real-time audio downsampling

## Common Development Tasks (During Migration)

### Identifying Tauri Code to Remove
```bash
# Search for Tauri imports
cd frontend
grep -r "@tauri-apps/api" src/

# Search for invoke calls
grep -r "invoke(" src/

# Search for listen calls
grep -r "listen(" src/
```

### Testing Backend Independently
```bash
# Backend should already be running (Docker)
curl http://localhost:5167/get-meetings
curl http://localhost:5167/docs  # Swagger UI
```

### Browser Audio Capture (To Implement)
```typescript
// Replace Tauri audio with:
const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
const mediaRecorder = new MediaRecorder(stream);
// Stream to backend via WebSocket
```

## Important Constraints & Decisions

1. **On-Site Meetings Only**: 95% use case, room microphone sufficient
2. **No System Audio**: Cannot capture Zoom/Teams (desktop app required)
3. **Web Browser Only**: Desktop/laptop browsers, no mobile
4. **Single-Instance Deployment**: Single-instance deployment for the current rollout.
5. **Session-Based Access**: Session-based access for the current release.

## Repository Conventions

- **Logging Format**: Backend uses detailed formatting with filename:line:function
- **Error Handling**: Backend uses Python exceptions, frontend uses try-catch
- **Git Branches**:
  - `main`: Stable releases
  - `feature/web-migration`: Current work (if created)
  - `feature/*`: New features
  - `fix/*`: Bug fixes

## Testing & Debugging

### Backend (Already Running)
```bash
# View logs
docker logs meeting-copilot-backend -f

# Test endpoints
curl http://localhost:5167/get-meetings
```

### Frontend (During Migration)
```bash
cd frontend
pnpm run dev
# Open http://localhost:3118
# Check browser console for errors
```

**Expected Errors (Temporary)**:
- `window.__TAURI_INTERNALS__ is undefined` - Normal, removing Tauri
- CORS errors for Ollama - Normal, will fix with proper WebSocket

## Phase 0 Discovery Checklist ✅ COMPLETED

**Audio System**:
- [x] Understand how Tauri captures microphone
- [x] Identify VAD (Voice Activity Detection) logic
- [x] Check if Whisper integration is Tauri-dependent

**Real-Time Features**:
- [x] How does live transcript update work?
- [x] Is there any WebSocket code already?
- [x] How are decisions/actions extracted?

**Database & Backend**:
- [x] Backend is independent (confirmed running)
- [x] Check VectorDB integration
- [x] Understand meeting storage schema

**Frontend State**:
- [x] How is recording state managed?
- [x] What React contexts exist?
- [x] Which components are Tauri-dependent?

### Phase 0 Findings Summary

**Backend Architecture** ✅:
- **FastAPI**: Fully functional on port 5167, WebSocket + HTTP endpoints
- **Database**: PostgreSQL with complete schema (meetings, transcripts, summary_processes, settings)
- **LLM Integration**: Working with pydantic-ai (Codex, OpenAI, Groq, Ollama)
- **Transcription**: Groq Whisper Large v3 API (cloud, ~1-2s latency)
- **Audio Flow**: Browser PCM → WebSocket → VAD → Buffer → Groq API → Transcript

**Backend Status** (Updated Phase 1.5):
1. ✅ **WebSocket Support**: `/ws/streaming-audio` for real-time transcription
2. ✅ **Real-Time Streaming**: Continuous PCM → Groq Whisper API
3. ⏳ **No Multi-User Sessions**: Skipped (Phase 2 deferred)
4. ⏳ **No VectorDB**: Phase 4 requirement

**Frontend Status** ✅:
- **Pure Web**: No Tauri, no Rust - just Next.js + React
- **Audio Capture**: Browser getUserMedia() + AudioWorklet
- **Streaming**: WebSocket binary streaming to backend
- **Transcription Flow**: Browser → AudioWorklet → WebSocket → Groq → UI

## Day 4: Web Audio Integration ✅ COMPLETED (Jan 2, 2026)

**Completion Date**: Jan 2, 2026
**Status**: ✅ **FULLY FUNCTIONAL - Production Ready (after Tauri removal)**

### What Was Built

**Browser Audio Capture** (`frontend/src/lib/audio-web/`):
- ✅ WebAudioCapture class - MediaRecorder API integration
- ✅ AudioWebSocketClient class - Real-time binary streaming
- ✅ Microphone permission handling and device enumeration
- ✅ Audio level visualization using AudioContext
- ✅ Complete WebM file generation (stop/restart mechanism, 10s chunks)

**Backend Audio Processing** (`backend/app/main.py`):
- ✅ WebSocket endpoint `/ws/audio` for real-time audio streaming
- ✅ `convert_webm_to_wav()` - ffmpeg conversion (WebM/Opus → WAV/PCM)
- ✅ `transcribe_with_whisper()` - HTTP integration with Whisper server
- ✅ Session management with UUID-based session IDs
- ✅ Automatic cleanup of temporary audio files

**Whisper Configuration**:
- ✅ Upgraded to `ggml-small.bin` model (466MB, multilingual)
- ✅ Auto language detection for Hindi/English code-switching
- ✅ Stereo audio format (16kHz, required for diarization)
- ✅ 10-second chunk size (optimal for mixed-language context)

**Docker & Infrastructure**:
- ✅ Static ffmpeg binary installation (fast, 40MB vs 727MB apt-get)
- ✅ Docker networking fix (`host.docker.internal` for Whisper connection)
- ✅ Added dependencies: aiohttp, aiofiles, websockets

**Test Interface** (`frontend/src/app/test-audio/`):
- ✅ Complete test UI with recording controls
- ✅ Live transcript display with auto-scroll
- ✅ Real-time audio level visualization
- ✅ Connection status and debug logs

### Key Technical Solutions

1. **Complete WebM Files**: Stop/restart MediaRecorder every 10s instead of timeslice (ffmpeg requires complete EBML headers)
2. **Audio Format**: Browser outputs WebM/Opus → ffmpeg converts to WAV/PCM stereo 16kHz → Whisper processes
3. **Docker Networking**: Backend in container connects to host Whisper via `host.docker.internal:8178`
4. **Multilingual**: Auto language detection handles Hindi/English code-switching, no translation applied
5. **Real-Time**: ~2-3 second latency from speech to transcript in browser

### Testing

Access test interface: `http://localhost:3118/test-audio`

End-to-end pipeline verified:
- Browser mic → MediaRecorder → WebSocket → Backend → ffmpeg → Whisper → Transcript → UI

### Day 4 Accomplishments ✅

1. **✅ Web Audio Recording**
   - Browser MediaRecorder capturing audio
   - WebSocket streaming to backend (10s chunks)
   - Real-time transcription working
   - Transcripts display on screen

2. **✅ Meeting Storage**
   - Meetings save to PostgreSQL via HTTP API
   - Meeting list loads in sidebar
   - Meeting details page works
   - Transcripts persist correctly

3. **✅ Dual-Mode Support**
   - Feature flag (`USE_WEB_AUDIO = true`)
   - Web audio uses HTTP APIs
   - Tauri code still present (for fallback)
   - Can switch between modes easily

4. **✅ Critical Fixes**
   - Recording state management (web vs Tauri)
   - Unique transcript IDs (no more duplicates)
   - Sidebar fetches via HTTP
   - Meeting details page uses HTTP

### Files Modified (Day 4)
- ✅ `frontend/src/app/page.tsx` - Web audio state
- ✅ `frontend/src/components/RecordingControlsWeb.tsx` - NEW component
- ✅ `frontend/src/components/Sidebar/SidebarProvider.tsx` - HTTP API
- ✅ `frontend/src/app/meeting-details/page.tsx` - HTTP API
- ✅ `frontend/src/lib/audio-web/config.ts` - Feature flag

### Testing Results
- ✅ Recording works (start/stop)
- ✅ Transcripts appear on screen (2-3s latency)
- ✅ Meetings save to database
- ✅ Meeting list persists after refresh
- ✅ Meeting details page opens correctly
- ✅ No critical errors

---

## Phase 1.5: Real-Time Groq Streaming ✅ COMPLETED (Jan 5, 2026)

**Completion Date**: Jan 5, 2026
**Status**: ✅ **FULLY INTEGRATED - Streaming transcription on main page**

### What Changed from Day 4

Day 4 used **batch processing** (10s WebM chunks → ffmpeg → local Whisper). Phase 1.5 switched to **real-time streaming** (continuous PCM → Groq Whisper API).

| Aspect | Day 4 (Batch) | Phase 1.5 (Streaming) |
|--------|---------------|----------------------|
| Audio Format | WebM/Opus (10s chunks) | Raw PCM (continuous) |
| Transcription | Local Whisper server | Groq Whisper API |
| Latency | 2-3 seconds | 1-2 seconds |
| WebSocket | `/ws/audio` | `/ws/streaming-audio` |
| Frontend | MediaRecorder + stop/restart | AudioWorklet + continuous |

### New Components Built

**Backend** (`backend/app/`):
- ✅ `streaming_transcription.py` - StreamingTranscriptionManager class
- ✅ `groq_client.py` - GroqTranscriptionClient for Whisper API
- ✅ `vad.py` - SimpleVAD (amplitude-based voice detection)
- ✅ `rolling_buffer.py` - RollingAudioBuffer (6s window, 5s slide)
- ✅ New WebSocket endpoint `/ws/streaming-audio`

**Frontend** (`frontend/src/lib/audio-streaming/`):
- ✅ `AudioStreamClient.ts` - Manages WebSocket + AudioWorklet
- ✅ `audio-processor.worklet.js` - Real-time 48kHz → 16kHz downsampling

### Integration with Main Page

Updated `RecordingControls.tsx` to use streaming:
- Replaced `WebAudioCapture` + `AudioWebSocketClient` with `AudioStreamClient`
- Changed WebSocket from `/ws/audio` to `/ws/streaming-audio`
- Uses `onPartial` and `onFinal` callbacks for live transcript updates

### Key Technical Decisions

1. **Groq API instead of local Whisper**:
   - Faster (~1-2s latency vs 2-3s)
   - `whisper-large-v3` model (best for Hinglish)
   - No GPU required on server

2. **No prompts in transcription**:
   - Prompts were leaking into output
   - Pure transcription with auto language detection
   - Output in original script (Devanagari for Hindi)

3. **6s window, 5s slide (1s overlap)**:
   - More context for complete sentences
   - Smart deduplication removes repeated words

4. **AudioWorklet for downsampling**:
   - Runs in separate thread (no main thread blocking)
   - Anti-aliasing filter for quality
   - Continuous streaming (no file boundaries)

### Files Modified (Phase 1.5)
- ✅ `backend/app/main.py` - Added `/ws/streaming-audio` endpoint
- ✅ `backend/app/streaming_transcription.py` - NEW orchestrator
- ✅ `backend/app/groq_client.py` - NEW Groq API client
- ✅ `backend/app/vad.py` - NEW voice activity detection
- ✅ `backend/app/rolling_buffer.py` - NEW sliding window buffer
- ✅ `frontend/src/components/RecordingControls.tsx` - Switched to streaming
- ✅ `frontend/src/lib/audio-streaming/AudioStreamClient.ts` - NEW streaming client
- ✅ `frontend/public/audio-processor.worklet.js` - NEW AudioWorklet

### Testing

Access main page: `http://localhost:3118`

End-to-end pipeline:
- Browser mic → AudioWorklet → WebSocket → VAD → Buffer → Groq API → Dedup → UI

---

## Phase 2: Multi-Participant Sessions ⏸️ SKIPPED

**Status**: ⏸️ **DEFERRED** - Not a priority for current use case

Phase 2 (session URLs, participant joining, WebSocket rooms) is being skipped for now. The current focus is on single-user AI features that provide immediate value.

**What's Skipped**:
- FR1.2-FR1.5: Session URLs, participant joining, participant list
- FR3.1-FR3.4: Multi-user real-time sync

**Rationale**: Single-user transcription with AI features provides sufficient value for initial deployment. Multi-user can be added later if needed.

---

## 🚀 Next: Phase 3 - AI Features

**Status**: 📋 **PLANNED** - Ready to implement
**Estimated Effort**: 4-5 days

### Phase 3 Goals (from PRD)

Build AI-powered features that enhance meeting productivity:

1. **"Catch Me Up"** - Participants who zone out can get a summary of what they missed
2. **Real-Time Q&A** - Ask AI questions during meeting using transcript context
3. **Decision/Action Extraction** - Automatically identify decisions and action items
4. **Current Topic Display** - Show what's being discussed right now

### Feature Breakdown

#### 3.1 Catch Me Up (FR5)

| ID | Requirement | Implementation |
|----|-------------|----------------|
| FR5.1 | Participant can request "Catch me up" | Add button in UI |
| FR5.2 | Select time range (5/10/15 min) | Time range selector component |
| FR5.3 | Generate summary of selected range | Use existing LLM summarization |
| FR5.4 | Include key points, decisions, actions | Reuse summary prompts |
| FR5.5 | Summary shown privately | Display in modal/sidebar |

**Technical Approach**:
- Add "Catch Me Up" button to meeting UI
- Filter transcripts by time range (last N minutes)
- Call existing `/process-transcript` endpoint with filtered text
- Display summary in a modal or sidebar panel

#### 3.2 Real-Time Q&A (FR7)

| ID | Requirement | Implementation |
|----|-------------|----------------|
| FR7.1 | Ask AI questions during meeting | Chat input in sidebar |
| FR7.2 | AI answers using current meeting context | Pass transcript to LLM |
| FR7.3 | AI answers using past meeting context | VectorDB search (Phase 4) |
| FR7.4 | Q&A is private to asking participant | Single-user, no broadcast needed |

**Technical Approach**:
- Add Q&A input field to meeting UI
- Create new endpoint `/ask-question`
- Pass current transcript + question to LLM
- Stream response back to UI

#### 3.3 Decision/Action Extraction (FR4)

| ID | Requirement | Implementation |
|----|-------------|----------------|
| FR4.1 | Identify decisions from transcript | ✅ Already exists |
| FR4.2 | Extract action items | ✅ Already exists |
| FR4.4 | Current discussion topic | Real-time topic extraction |
| FR4.5 | Items update as meeting progresses | Periodic re-extraction during recording |

**Technical Approach**:
- Existing summarization already extracts decisions/actions
- Add periodic extraction during recording (every 2-3 minutes)
- Display in sidebar panel
- Add "Current Topic" component

### Implementation Tasks

1. **UI Components**
   - [ ] "Catch Me Up" button + time selector modal
   - [ ] Q&A chat input in sidebar
   - [ ] Decisions/Actions panel in sidebar
   - [ ] Current Topic display

2. **Backend Endpoints**
   - [ ] `POST /catch-me-up` - Generate time-range summary
   - [ ] `POST /ask-question` - Q&A with transcript context
   - [ ] `POST /extract-items` - Real-time decision/action extraction

3. **Integration**
   - [ ] Wire up UI to new endpoints
   - [ ] Add periodic extraction during recording
   - [ ] Handle loading states and errors

### Success Criteria for Phase 3

- [ ] "Catch Me Up" generates summary for selected time range
- [ ] Q&A answers questions using current transcript
- [ ] Decisions and actions display in sidebar during recording
- [ ] Current topic updates as discussion progresses

---

**Full implementation details**: `/DAY4_COMPLETE.md`

**This file auto-updates as we progress through phases. Last updated: Phase 1.5 Complete - Jan 5, 2026**
