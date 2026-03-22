# System Architecture

Meeting Co-Pilot is a web-based collaborative meeting assistant. It combines a FastAPI backend with a Next.js frontend, connected via WebSocket for real-time audio streaming and transcription.

## High-Level Architecture Diagram

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
│  │ Postgres│  │ Partial/ │                                              │
│  │ Storage │  │ Final    │ → WebSocket JSON response to browser         │
│  └─────────┘  └──────────┘                                              │
└─────────────────────────────────────────────────────────────────────────┘
```

## Component Details

### Frontend (Next.js)

* Provides the user interface for managing meetings, displaying transcriptions, and configuring the application.
* Uses browser `getUserMedia()` API to capture microphone audio.
* AudioWorklet processes audio in a separate thread for real-time downsampling (48kHz → 16kHz).
* Communicates with backend via WebSocket for real-time audio streaming and transcript updates.

**Key Files**:
- `frontend/src/components/RecordingControls.tsx` - Main recording UI
- `frontend/src/lib/audio-streaming/AudioStreamClient.ts` - WebSocket + AudioWorklet client
- `frontend/public/audio-processor.worklet.js` - Real-time audio processing

### Backend (FastAPI + Python)

* **FastAPI Server**: HTTP endpoints for meeting CRUD, WebSocket for real-time audio.
* **StreamingTranscriptionManager**: Orchestrates VAD → Buffer → Transcription → Response.
* **SimpleVAD**: Amplitude-based voice activity detection (threshold 0.08).
* **RollingAudioBuffer**: 6-second window with 5-second slide (1s overlap).
* **GroqTranscriptionClient**: Sends audio to Groq Whisper API (whisper-large-v3).
* **Database**: PostgreSQL for meeting metadata, transcripts, and summaries.
* **LLM Integration**: pydantic-ai for Claude, OpenAI, Groq, and Ollama.

**Key Files**:
- `backend/app/main.py` - FastAPI app, WebSocket endpoints
- `backend/app/streaming_transcription.py` - Transcription orchestrator
- `backend/app/groq_client.py` - Groq Whisper API client
- `backend/app/vad.py` - Voice activity detection
- `backend/app/rolling_buffer.py` - Sliding window audio buffer
- `backend/app/db.py` - Database operations

## Real-Time Transcription Flow

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

## Key Technical Decisions

### Groq Whisper API over Local Whisper
- Groq API provides faster processing (~1-2s latency)
- `whisper-large-v3` model for best Hindi/English (Hinglish) support
- No GPU required on server

### 6s Window, 5s Slide (1s Overlap)
- More context for complete sentences
- 1s overlap catches boundary words
- Smart deduplication prevents repeated text

### No Prompts in Transcription
- Prompts were leaking into transcription output
- Pure transcription mode with auto language detection
- Output is in original script (Devanagari for Hindi)

### AudioWorklet for Browser Processing
- Runs in separate thread (no main thread blocking)
- Anti-aliasing filter for quality downsampling
- Continuous streaming (no file boundaries like MediaRecorder)

## Smart Deduplication Algorithm

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

## WebSocket Message Format

### Client → Server
- Binary: Raw 16kHz mono PCM audio samples (Int16)

### Server → Client
```json
{
  "type": "partial" | "final",
  "text": "transcribed text",
  "confidence": 0.95,
  "is_stable": true,
  "reason": "silence_detected" | "buffer_full" | "manual"
}
```

## Technology Stack

### Backend
- **Framework**: FastAPI (Python 3.11+)
- **Database**: PostgreSQL (asyncpg)
- **Transcription**: Groq Whisper Large v3 API
- **Audio Processing**: SimpleVAD + RollingAudioBuffer
- **LLM**: pydantic-ai (Claude, OpenAI, Groq, Ollama)

### Frontend
- **Framework**: Next.js 14 + React 18
- **Audio Capture**: Browser getUserMedia() + AudioWorklet
- **Streaming**: WebSocket binary streaming
- **State**: React hooks + context

## Endpoints

### HTTP Endpoints
- `GET /get-meetings` - List all meetings
- `POST /create-meeting` - Create new meeting
- `GET /get-meeting/{id}` - Get meeting details
- `DELETE /delete-meeting/{id}` - Delete meeting
- `GET /docs` - Swagger API documentation

### WebSocket Endpoints
- `/ws/streaming-audio` - Real-time audio streaming (current)
- `/ws/audio` - Legacy batch audio (deprecated)
