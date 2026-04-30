# End-to-End Automated Testing Master Plan

## Goal
Establish a zero-human-intervention, fully automated E2E testing pipeline that validates every critical path from user login and audio capture to real-time transcription, async processing, and file downloads. This plan is structured so your agent can execute it systematically.

---

## 1. Test Infrastructure & Mocking Setup

Before writing functional tests, the agent must set up the test environment to avoid hitting real paid APIs (Groq, ElevenLabs, OpenAI) or cloud storage during routine CI runs, while still testing the exact logic.

### 1.1 Backend Test Environment (Pytest + Asyncio)
- **Database:** Spin up an ephemeral PostgreSQL container (e.g., via `testcontainers` or Docker Compose) for tests. Run all DB migrations before the test suite.
- **Redis & Celery:** Spin up a local Redis container. Configure Celery to run in `task_always_eager=True` mode for synchronous testing, or run a real test worker.
- **API Mocks (`pytest-httpx` or `respx`):** 
  - Mock Groq/ElevenLabs transcription endpoints to return fixed JSON transcripts.
  - Mock OpenAI/Gemini to return structured meeting summaries.
  - Mock Google OAuth token verification.
- **Storage:** Use `STORAGE_TYPE=local` pointing to a temporary test directory (`pytest` `tmp_path`), ensuring no GCP calls are made during basic CI.

### 1.2 Frontend Test Environment (Playwright)
- **Auth State:** Pre-populate NextAuth session cookies to bypass the Google Login UI for most tests.
- **Microphone Mocking:** Launch Chromium with `--use-fake-ui-for-media-stream` and `--use-file-for-fake-audio-capture=test_audio.wav` to simulate real microphone input without a human speaking.
- **API Interception:** Intercept network requests if testing UI states without a real backend (though true E2E will hit the test backend).

---

## 2. Backend Integration & System Tests

The agent must implement the following test suites in `backend/tests/integration/`:

### 2.1 WebSocket Audio Streaming Pipeline (`test_ws_streaming.py`)
- **Connect & Authenticate:** Establish a WebSocket connection to `/ws/streaming-audio` using a valid mock JWT.
- **Stream PCM Audio:** Send binary 16kHz PCM chunks simulating a 30-second speech.
- **Verify VAD & Buffer:** Assert that the backend's Voice Activity Detection detects speech and buffers it.
- **Verify Transcription:** Assert that the backend calls the (mocked) Groq/ElevenLabs API and sends back `partial` and `final` JSON transcripts over the WebSocket.
- **Disconnection & Cleanup:** Close the socket and verify the `RecordingSession` transitions to `finalizing`, triggers the Celery chunk merge task, and ultimately reaches `completed`.

### 2.2 Reconnect & Resilience (`test_reconnection.py`)
- Drop the WebSocket mid-stream and reconnect within `STREAMING_RESUME_GRACE_SECONDS`. Verify the session resumes without creating duplicate DB entries.
- Trigger backpressure (send audio faster than processing) and ensure the server gracefully handles or drops chunks without crashing.

### 2.3 Notes Generation & RAG (`test_notes_and_chat.py`)
- **Generation:** Trigger `POST /generate-detailed-notes` for a completed meeting. Verify the mocked LLM is called and the `summary_processes` table updates correctly.
- **Chat:** Send a message to the AI Participant (`POST /chat-meeting`). Verify the RAG pipeline retrieves the correct transcript context and returns a valid response.

### 2.4 File Management & Downloads (`test_storage.py`)
- Verify `GET /meetings/{meeting_id}/recording-url` generates a valid signed URL (or artifact endpoint URL for encrypted files).
- Assert the HTTP response correctly denies access if RBAC fails or the meeting belongs to another user.

### 2.5 Scheduled Tasks (`test_celery_beat.py`)
- Directly invoke the `weekly_credit_reset` Celery task.
- Assert that user credits are reset to `10000` and `credit_ledger` entries are created.

---

## 3. Frontend End-to-End Tests (Playwright)

The agent must create `frontend/tests/e2e/` with the following critical user journeys:

### 3.1 The "Happy Path" Meeting (`meeting_flow.spec.ts`)
1. **Login:** User accesses `/`. (Uses pre-injected auth cookie).
2. **Start Meeting:** Clicks "Start Recording". 
3. **Capture:** Fake audio file is fed into the browser. Verify the "Live Transcript" UI displays text (sent back via WebSocket).
4. **End Meeting:** User clicks "Stop Recording". 
5. **Summarize:** UI navigates to meeting details. Verify the "Generating Summary" loader appears and eventually shows the AI notes.
6. **Download:** User clicks "Download Audio". Verify the browser successfully downloads the `.wav` file.

### 3.2 Calendar Auto-Start Flow (`autostart.spec.ts`)
1. User navigates directly to `/?autoStart=true&source=calendar_email&meetingTitle=Sync`.
2. Verify NextAuth preserves the query parameters through the login redirect.
3. Verify that upon successful page load, the browser requests microphone permissions and *automatically* begins recording without a manual click.

### 3.3 UI Interactions & Sharing (`sharing_and_chat.spec.ts`)
1. **Share Notes:** Open a meeting, click Share, generate a token link.
2. **View Shared:** Open the generated link in an Incognito context (unauthenticated). Verify the notes render but edit controls are hidden.
3. **AI Chat:** Open the chat sidebar, type "What did we decide?", verify the AI response bubble appears.

---

## 4. Execution & CI/CD Strategy

To ensure zero human intervention, the agent must set up a GitHub Actions workflow (`.github/workflows/e2e.yml`) that runs on every pull request and push to `main`:

### The CI Pipeline Steps:
1. **Linting & Type Checking:** `ruff check`, `mypy`, `tsc --noEmit`.
2. **Infrastructure Spin-up:** Use `docker-compose -f docker-compose.test.yml up -d` to launch PostgreSQL and Redis.
3. **Backend Unit & Integration Tests:** Run `pytest backend/tests/ -v --cov`.
4. **Build Next.js:** Ensure the frontend compiles successfully (`npm run build`).
5. **Start Full Stack:** Launch the FastAPI backend and Next.js frontend in the background.
6. **Run Playwright:** Execute `npx playwright test`. Playwright will launch the headless browsers (with fake media streams), interact with the UI, and hit the live test backend.
7. **Artifact Upload:** If tests fail, upload Playwright video recordings and trace files to GitHub artifacts for easy debugging.

---

## Next Steps for Your Agent

When you instruct your agent to implement this, tell it to proceed in these exact phases:

1. **Phase 1: Setup Mocks & Fixtures** (Create Pytest fixtures for DB, Redis, and `respx` mocks for external APIs).
2. **Phase 2: Backend WebSocket & Async Tests** (Implement `test_ws_streaming.py` and Celery task tests).
3. **Phase 3: Playwright Configuration** (Setup `playwright.config.ts` with `--use-fake-ui-for-media-stream`).
4. **Phase 4: Frontend E2E Scripts** (Write the Playwright specs covering the Happy Path and Auto-start flows).
5. **Phase 5: CI Pipeline** (Create the `.github/workflows/e2e.yml` file tying it all together).
