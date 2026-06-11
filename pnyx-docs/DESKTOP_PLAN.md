# Pnyx — Web + Desktop (Tauri 2.x) Implementation Plan

**Created**: June 2026  
**Status**: Phase 0 in progress  
**Owner**: Engineering

---

## Before You Start: 3 Things the Plan Verified from the Code

1. **WebSocket reconnect is already solid** — 12 attempts, exponential backoff in `AudioStreamClient.ts`. Do not touch it.
2. **Recall bot reliability code mostly exists** — `automatic_leave`, `reconcile_stuck_bots`, `_finalize_bot` are all present in `backend/app/services/recall/`. The gaps are a signature bypass bug, a multi-worker race condition, and zero tests.
3. **Static export is impossible** — NextAuth server routes + `middleware.ts` mean Tauri must load the remote hosted URL, not a bundled static build. `output: 'export'` must **never** be added to `next.config.js`.

---

## Why Desktop?

Pnyx should work as both a web app (stays as-is) and a native desktop app (Mac → Windows → Linux) using Tauri 2.x.

| Advantage | Detail |
|---|---|
| System audio capture | Record Zoom/Meet/Teams natively — no Recall.ai bot ($0.50–1.00/hr saved) |
| Bluetooth audio | Handled at OS level; browser `getUserMedia` has device-switching issues |
| Screen capture | Screenshot every 60s → slides, docs, whiteboard in AI notes context |
| Menubar icon | One-click recording (Granola-style), near-zero activation friction |
| System notifications | "Notes ready" as OS notification, not email |

The web app stays deployable and fully functional. Tauri wraps the same Next.js frontend.

---

## Architecture Decision: Remote URL (Locked for All Phases)

Tauri loads the **remote hosted Next.js URL**, not a static export and not a bundled Node sidecar.

**Why**: NextAuth server routes + `middleware.ts` + `next.config.js output: "standalone"` make static export impossible. The remote-URL approach keeps Google OAuth and all auth flows identical to the browser.

**Implication**: `output: 'export'` must NEVER be added to `next.config.js`. If offline support becomes required in a future version, switch to the `.next/standalone` sidecar — the existing standalone build already supports this as a fallback.

---

## Cost Impact Summary

| Phase | Change | Saving |
|---|---|---|
| 0.1–0.2 | Switch AI participant + notes to gpt-4o-mini | ~$3.80/meeting (~$380/100 meetings) |
| Phase 6 | No Recall bot on desktop | $0.65–1.26/meeting saved |
| Combined | Desktop user, optimised config | ~$1.50/meeting vs ~$6 today |

---

## What NOT to Do (Common Mistakes)

1. Never add `output: 'export'` to `next.config.js` — breaks NextAuth
2. Never port the WebSocket streaming logic to Rust — reuse `AudioStreamClient` exactly as-is
3. Never use Tauri v1 config keys (`tauri.bundle`, `allowlist`) — they fail silently in v2
4. Never start Tauri work before Phase 0.6 — `page.tsx` must be decomposed before adding the `isDesktop()` branch

---

## Phase 0: Reliability + Cost Hardening

**Goal**: Cut AI costs, close Recall reliability gaps, decompose `page.tsx`, and lock the Python 3.11 dev workflow — all before any Tauri work.  
**Duration**: 9 days  
**Prerequisites**: None. Start immediately.

---

### 0.1 Switch AI participant model default to gpt-4o-mini

- **File**: `backend/app/services/ai_participant.py`
- **What**:
  - Line 88: change `"openai": "gpt-5.4"` → `"openai": "gpt-4o-mini"` inside `DEFAULT_PROVIDER_MODELS`
  - Line 514: change the hardcoded Agent placeholder `"openai:gpt-5.4"` → `"openai:gpt-4o-mini"` (it is runtime-overridden but the placeholder should not name a costly model)
  - Do NOT change `_call_llm_json` / `chat.completions.create` — gpt-4o-mini accepts the existing `temperature=0.1` + `response_format={"type":"json_object"}` call unchanged
- **Why**: AI participant runs every 25s per meeting; gpt-5.4 ≈ $4/hr, gpt-4o-mini is ~15–30× cheaper
- **Test**: Run a meeting locally with no `AI_PARTICIPANT_MODEL` env var; confirm logs show `last_model_used=gpt-4o-mini`. Existing live smoke tests pass.

---

### 0.2 Remove gpt-5.4 defaults from notes/chat

- **Files**:
  - `backend/app/api/routers/chat.py` (lines 231, 412)
  - `backend/app/api/routers/settings.py` (line 105)
  - `backend/app/services/chat/service.py` (lines 175, 182)
- **What**: Replace every `os.getenv("NOTES_SUMMARY_MODEL", "gpt-5.4")` default with `"gpt-4o-mini"`. Keep the env var name so production can override to `gpt-4o` if notes quality regresses.
- **Why**: gpt-5.4 default for notes is the second-largest LLM cost line after AI participant
- **Test**: `grep -rn '"gpt-5.4"' backend/app/` returns zero results. Notes still generate for a sample meeting.

---

### 0.3 Fix webhook signature bypass in production

- **File**: `backend/app/services/recall/manager.py` (lines 272–276, `verify_signature` function)
- **What**: When `self.webhook_secret` is empty:
  - If `ENVIRONMENT != "development"` → return `False` and log an error
  - Keep `return True` only when `ENVIRONMENT == "development"`
- **Why**: Today an unset secret silently accepts all webhooks — a forged-transcript injection vector in production
- **Test**: Unit test — `verify_signature(b"x", "")` with `ENVIRONMENT="production"` returns `False`; with `ENVIRONMENT="development"` returns `True`

---

### 0.4 Make bot reconciler single-flight across workers

- **File**: `backend/app/services/recall/bot_reconciler.py` (`_run` loop, lines 66–89)
- **What**: Before calling `reconcile_stuck_bots`, acquire a short-lived Redis lock:
  ```python
  SET recall:reconciler:lock <worker_id> NX EX <interval_seconds + 30>
  ```
  Using the existing `RecallManager.redis` connection. Skip the cycle if the lock is held. Release/let-expire after the cycle completes.
- **Why**: The reconciler is an in-process asyncio loop started per process. N gunicorn/uvicorn workers race on the same stuck bots, causing duplicate `remove_bot` calls.
- **Test**: Start two backend processes locally; confirm only one logs `[BotReconciler]` reconcile activity per interval (the other logs "lock held, skipping").

---

### 0.5 Add Recall finalization tests

- **File**: new `backend/tests/test_recall_manager.py`
- **What**: Tests with a mocked `RecallClient` and fake DB covering:
  - (a) `_finalize_bot(status="completed")` with empty transcript schedules notes and does **not** delete the meeting
  - (b) `_finalize_bot(status="fatal")` with no segments deletes the meeting
  - (c) `reconcile_stuck_bots` finalizes terminal bots, force-leaves stuck-joining bots, and handles 404 from Recall API
- **Why**: This is the highest-risk reliability code and currently has zero test coverage; regressions here cause "bots sit for 24 hours" incidents
- **Test**: `pytest backend/tests/test_recall_manager.py` green. Wire into CI in `.github/workflows/pre-deploy.yml` under the hermetic integration tests gate.

---

### 0.6 Decompose `page.tsx` — behavior-preserving extraction only

- **Current state**: `frontend/src/app/page.tsx` is 4,250 lines with ~90 `useState` hooks
- **Files created**:
  - `frontend/src/app/_home/types.ts`
  - `frontend/src/hooks/useAIHostState.ts`
  - `frontend/src/hooks/useGuardrailAlerts.ts`
  - `frontend/src/hooks/useMeetingContextInputs.ts`
  - `frontend/src/app/_home/dialogs/HostStyleStartDialog.tsx`
  - `frontend/src/app/_home/dialogs/ReauthModal.tsx`
- **What**: Pure extraction in 5 commits, each gated by Playwright e2e. No logic changes in any commit.
  1. Extract all TypeScript interfaces (lines 64–183: `ModelConfig`, `StreamingHealthPayload`, `AIGuardrailAlert`, `AIHostSuggestion`, etc.) → `_home/types.ts`; import them back in `page.tsx`
  2. Extract AI host suggestion queue, interventions, pinned items, state-delta, past-insights state + their setters/effects → `useAIHostState.ts`
  3. Extract `activeGuardrailAlert`, `guardrailAlertHistory`, `showGuardrailHistory` state → `useGuardrailAlerts.ts`
  4. Extract `meetingGoalInput`, `meetingAgendaInput`, `meetingParticipantsInput`, `contextAppliedStatus` → `useMeetingContextInputs.ts`
  5. Extract the two dialog JSX blocks (host-style start dialog ~lines 4178–4212, reauth modal ~lines 4220–4247) → props-driven components in `_home/dialogs/`
- **Rule**: If a Playwright test breaks, the extraction changed behavior. Revert and fix before continuing.
- **Why**: 4,250-line monolith is high-regression-risk and blocks the Phase 2 `isDesktop()` branch addition
- **Test**: `pnpm lint` clean and full Playwright e2e suite green after every single commit.

---

### 0.7 Document Python 3.11 venv setup

- **File**: new `backend/README.dev.md`
- **What**:
  ```bash
  python3.11 -m venv venv
  source venv/bin/activate
  pip install -r requirements.txt -r requirements-dev.txt
  ```
  Include explicit warning: Python 3.9 must not be used (asyncio/typing differences vs 3.11 prod). Add one-line check: `python --version` must show `3.11.x` before running the server.
- **Why**: 3.9 local / 3.11 production mismatch causes "works locally, breaks in prod" defects
- **Test**: A new engineer following the doc lands on Python 3.11.13 and `uvicorn app.main:app` starts without errors.

---

### Phase 0 Success Criteria

- `grep -rn '"gpt-5.4"' backend/app/` → zero hits; AI participant logs `gpt-4o-mini`
- `pytest backend/tests/test_recall_manager.py` green and running in CI
- Webhook `verify_signature` rejects unsigned webhooks in production when secret is unset
- `page.tsx` reduced by types + ≥3 hooks + 2 dialogs; full Playwright e2e suite green throughout

---

## Phase 1: Tauri 2.x Scaffolding

**Goal**: Add a Tauri 2.x desktop shell that loads the existing hosted Next.js app. Web build stays byte-for-byte unchanged.  
**Duration**: 6 days  
**Prerequisites**: Phase 0.6 complete (smaller `page.tsx` lowers risk of desktop-detection edit in Phase 2). Rust toolchain + Xcode CLT installed on dev machine.

---

### 1.1 De-risking spike (scratch branch — do this before 1.2)

- **What**: Build a throwaway Tauri 2.x app pointing at your dev URL with one Rust command `ping() → "pong"` and a capability granting that URL IPC access. Confirm:
  - (a) `invoke('ping')` resolves from the remote-loaded page
  - (b) Google OAuth redirect completes inside the Tauri webview
- **Why**: If either fails, the entire desktop approach must pivot before Phase 1.2 writes any permanent code
- **Test**: Console in Tauri devtools logs `pong`; Google sign-in succeeds inside the Tauri window

---

### 1.2 Create `src-tauri/` Rust crate

- **File**: new `frontend/src-tauri/` with `Cargo.toml`, `src/main.rs`, `src/lib.rs`, `build.rs`, `icons/`
- **What**: Run `cargo tauri init` inside `frontend/`. Set Cargo dependency:
  ```toml
  tauri = { version = "2", features = [] }
  ```
  `main.rs` calls `tauri::Builder::default().run(...)`. Register the `ping` command from the spike.
- **Test**: `cargo build` inside `src-tauri/` succeeds with no errors.

---

### 1.3 Write `tauri.conf.json`

- **File**: `frontend/src-tauri/tauri.conf.json`
- **What** (verified Tauri 2.x keys — do not substitute v1 keys):
  ```json
  {
    "productName": "Pnyx",
    "version": "0.1.0",
    "identifier": "com.appointy.pnyx",
    "build": {
      "frontendDist": "https://YOUR-PROD-DOMAIN.com",
      "devUrl": "https://YOUR-DEV-DOMAIN.com",
      "beforeDevCommand": "",
      "beforeBuildCommand": ""
    },
    "app": {
      "windows": [
        { "label": "main", "title": "Pnyx", "width": 1280, "height": 800 }
      ],
      "security": { "capabilities": ["remote-main"] }
    },
    "bundle": {
      "active": true,
      "targets": ["dmg", "app"],
      "icon": ["icons/icon.icns"]
    }
  }
  ```
  Replace `YOUR-PROD-DOMAIN.com` and `YOUR-DEV-DOMAIN.com` with actual domains. The production build uses a `tauri.conf.prod.json` overlay to swap the URL.
- **Test**: `cargo tauri dev` opens a window showing the live Pnyx web app.

---

### 1.4 Capability file for remote IPC

- **File**: new `frontend/src-tauri/capabilities/remote-main.json`
- **What**:
  ```json
  {
    "$schema": "../gen/schemas/desktop-schema.json",
    "identifier": "remote-main",
    "windows": ["main"],
    "remote": {
      "urls": ["https://YOUR-PROD-DOMAIN.com", "https://YOUR-DEV-DOMAIN.com"]
    },
    "permissions": ["core:default"]
  }
  ```
  Without `remote.urls`, a remote-loaded page cannot call any Tauri command — this is what the spike validated. Custom-command and plugin permissions are appended to `permissions` as each later phase adds them.
- **Test**: From the running Tauri window devtools, `window.__TAURI_INTERNALS__.invoke('ping')` resolves `"pong"`.

---

### 1.5 Add desktop npm scripts, keep web unchanged

- **File**: `frontend/package.json` (scripts only), `frontend/.gitignore`
- **What**:
  - Add to scripts: `"tauri:dev": "tauri dev"`, `"tauri:build": "tauri build"`
  - Add to devDependencies: `@tauri-apps/cli`
  - Add to dependencies: `@tauri-apps/api`
  - Add to `.gitignore`: `src-tauri/target/`, `src-tauri/gen/`
  - Do NOT change `dev`, `build`, or `start` scripts
  - Do NOT add `output: 'export'` to `next.config.js`
- **Test**: `pnpm build` (web) output is unchanged — diff to `next.config.js` is zero. `pnpm tauri:dev` launches the desktop window.

---

### Phase 1 Success Criteria

- `pnpm tauri:dev` opens a native window with the full Pnyx web app, signed in via Google
- A custom Rust command is invocable from the remote-loaded frontend (`invoke('ping')` → `"pong"`)
- `pnpm build` web output and the CI deployment pipeline are unchanged

---

## Phase 2: System Audio Capture — macOS

**Goal**: On macOS desktop, capture system audio + microphone natively. No Recall bot needed. Same backend WebSocket pipeline.  
**Duration**: 10 days  
**Prerequisites**: Phase 1 complete. macOS 13+ required for ScreenCaptureKit system-audio support.

---

### Architecture Decision (Locked)

Rust captures audio and emits `AudioFrame { timestamp_secs: f64, pcm: Vec<i16> }` to JS over a Tauri Channel. JS feeds those frames into `AudioStreamClient` exactly where the AudioWorklet output is consumed today (`AudioStreamClient.ts` lines 166–202).

**All 785 lines of reconnect/framing/backpressure/queue logic in `AudioStreamClient.ts` are reused unchanged.** Desktop only replaces the *source* of PCM frames — never the streaming or reliability layer.

Two streams kept separate then summed:
- **System audio** (remote meeting participants) via ScreenCaptureKit
- **Local mic** (local speaker) via cpal default input

Both resampled to 16 kHz mono Int16, summed in Rust. Timestamp source-of-truth moves from `AudioContext.currentTime` to a Rust monotonic capture clock.

---

### 2.1 Rust audio capture command

- **File**: new `frontend/src-tauri/src/audio_capture.rs`; registered in `src/lib.rs`
- **Cargo deps to add**:
  ```toml
  cpal = "0.15"
  screencapturekit = "0.1"  # macOS system audio
  ```
- **What**:
  ```rust
  #[tauri::command]
  async fn start_system_capture(channel: tauri::ipc::Channel<AudioFrame>) { ... }

  #[tauri::command]
  fn stop_system_capture() { ... }

  #[derive(serde::Serialize, Clone)]
  struct AudioFrame { timestamp_secs: f64, pcm: Vec<i16> }
  ```
  Opens the SCK system-audio stream + cpal default-input mic. Resamples both to 16 kHz mono Int16. Sums them per ~3s-equivalent chunk. Emits `AudioFrame` via the channel. Gate entire SCK path behind `#[cfg(target_os = "macos")]`.
- **Test**: A Rust integration test plays a YouTube video; confirms non-silent Int16 frames at 16 kHz in the channel output.

---

### 2.2 macOS permissions (Screen Recording TCC)

- **Files**: `frontend/src-tauri/tauri.conf.json` macOS entitlements section, `frontend/src-tauri/Info.plist`
- **What**: Add usage description strings:
  - `NSMicrophoneUsageDescription`
  - Screen Recording usage string (ScreenCaptureKit triggers macOS Screen Recording TCC prompt on first call)
- **Test**: First launch shows the macOS Screen Recording prompt; after granting, `start_system_capture` produces non-silent audio.

---

### 2.3 Frontend desktop detection + capture seam

- **File**: new `frontend/src/lib/platform.ts`
  ```typescript
  export const isDesktop = (): boolean =>
    typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
  ```

- **File**: `frontend/src/lib/audio-streaming/AudioStreamClient.ts` (`start()` method, lines 109–216)
- **What**: Add a branch at the start of `start()`:
  ```typescript
  if (isDesktop()) {
    // Skip getUserMedia / AudioContext / worklet (lines 134-206)
    // Instead:
    const channel = new Channel<AudioFrame>();
    channel.onmessage = (frame) => {
      // Build same [8-byte float64 timestamp][Int16 PCM] buffer the worklet produces
      // Mirror lines 176-185, using frame.timestamp_secs instead of AudioContext.currentTime
      // Then call the existing send/queue path (lines 187-201) unchanged
    };
    await invoke('start_system_capture', { channel });
    return; // existing WebSocket/reconnect/queue code runs from here unchanged
  }
  // Web path: getUserMedia / AudioContext / worklet — UNCHANGED
  ```
  On stop, call `invoke('stop_system_capture')` in the desktop branch before the existing cleanup.
- **Why**: Minimal seam. Backend WS contract is byte-identical. Entire reliability layer is reused.
- **Test**: In the Tauri window, start a recording while a Meet call plays; backend receives frames and transcripts appear — identical UI to web. In a browser, `isDesktop()` is `false` and `getUserMedia` path runs unchanged.

---

### 2.4 Add capture commands to capability

- **File**: `frontend/src-tauri/capabilities/remote-main.json`
- **What**: Append to `permissions`:
  ```json
  "allow-start-system-capture",
  "allow-stop-system-capture"
  ```
- **Test**: `invoke('start_system_capture', ...)` from the remote UI resolves without "command not allowed" error.

---

### Phase 2 Success Criteria

- Mac desktop records a Zoom/Meet/Teams call end-to-end with no Recall bot
- Transcript quality in the UI is equal to or better than bot-based transcription
- Web browser path is unaffected (`isDesktop() === false`)
- Remote participants and local speaker both appear in the transcript without duplication

---

## Phase 3: System Audio Capture — Windows

**Goal**: Same native capture interface on Windows via WASAPI loopback. No frontend code changes.  
**Duration**: 7 days  
**Prerequisites**: Phase 2 complete. The `AudioFrame` channel contract and frontend branch are reused as-is.

---

### 3.1 WASAPI loopback capture

- **File**: `frontend/src-tauri/src/audio_capture.rs`
- **What**: Under `#[cfg(target_os = "windows")]`, implement `start_system_capture` using `cpal`'s WASAPI loopback host (capture the default render endpoint as an input device) for system audio, plus the cpal default input for mic. Resample and sum to 16 kHz mono Int16. Emit the identical `AudioFrame` struct over the same channel. The macOS SCK implementation stays behind its own `#[cfg(target_os = "macos")]`.
- **Test**: On Windows, a Teams/Meet call streams to the backend and transcribes. Frontend code is byte-identical to the Mac path.

---

### 3.2 Windows bundle target

- **File**: `frontend/src-tauri/tauri.conf.json`
- **What**: Add `"nsis"` to `bundle.targets`
- **Test**: `cargo tauri build` on Windows produces a working `.exe` installer.

---

### Phase 3 Success Criteria

- Windows desktop build transcribes an online meeting with no bot
- The `AudioFrame` contract and `AudioStreamClient` desktop branch are shared across Mac and Windows with only Rust `#[cfg]` differences — no frontend divergence

---

## Phase 4: Menubar / System Tray + Notifications

**Goal**: One-click recording from a tray/menubar icon with idle/recording/processing states and native OS notifications.  
**Duration**: 8 days  
**Prerequisites**: Phase 2 (so the tray "Start" drives real capture).

---

### 4.1 Tray icon + menu

- **Files**: `frontend/src-tauri/Cargo.toml`, new `frontend/src-tauri/src/tray.rs`
- **What**:
  - Add `features = ["tray-icon"]` to the `tauri` dep in `Cargo.toml`
  - In `setup`, build the tray:
    ```rust
    TrayIconBuilder::new()
      .icon(app.default_window_icon().unwrap().clone())
      .menu(&menu)
      .on_menu_event(|app, event| { ... })
      .on_tray_icon_event(|tray, event| { ... })
      .build(app)?;
    ```
  - Menu items: **Start/Stop Recording**, **Show Window**, **Quit**
  - Left-click on the icon toggles recording

---

### 4.2 Tray ↔ recording bridge

- **Files**: `frontend/src-tauri/src/tray.rs`, new `frontend/src/lib/desktopBridge.ts`, `frontend/src/components/RecordingControls.tsx`
- **What**:
  - Tray "Start Recording" emits Tauri event `tray://toggle-recording`
  - `desktopBridge.ts`: when `isDesktop()`, listen for this event and call the same `handleStartRecording`/stop path the in-app button uses — no duplicated recording logic
  - When recording state changes in the UI, call `invoke('set_tray_state', { state })` to sync the tray
- **Rule**: There is exactly one recording entry point. Tray and in-app button both call it.
- **Test**: Clicking tray "Start" begins a recording identical to clicking the in-app button. Both stay visually in sync.

---

### 4.3 Tray icon states

- **Files**: `frontend/src-tauri/src/tray.rs`, `frontend/src-tauri/icons/` (add `idle.png`, `recording.png`, `processing.png`)
- **What**:
  ```rust
  #[tauri::command]
  fn set_tray_state(state: &str, tray: tauri::State<TrayHandle>) {
    match state {
      "recording" => tray.set_icon(recording_icon),
      "processing" => tray.set_icon(processing_icon),
      _ => tray.set_icon(idle_icon),
    }
  }
  ```
  Frontend calls `invoke('set_tray_state', { state: 'recording' })` on start, `'processing'` on stop, `'idle'` when notes are ready.
- **Test**: Icon visibly changes across all three states during a full record → stop → notes-ready cycle.

---

### 4.4 Native notifications

- **Files**: `frontend/src-tauri/Cargo.toml`, `frontend/src-tauri/capabilities/remote-main.json`, `frontend/src/lib/recordingNotification.ts`
- **What**:
  - Add `tauri-plugin-notification` to `Cargo.toml`
  - Add `"notification:default"` to `permissions` in the capability file
  - In `recordingNotification.ts`, branch on `isDesktop()`: use the Tauri notification plugin for "Recording started" and "Notes ready for your meeting" — web path uses the existing mechanism unchanged
- **Test**: On desktop, starting a recording and completing notes each raise a native OS notification. Web path is unchanged.

---

### Phase 4 Success Criteria

- Menubar/tray icon starts and stops a real recording with one click
- Tray icon cycles: idle (gray) → recording (red) → processing (amber) → idle (gray)
- OS notifications fire on desktop; web notifications are unaffected

---

## Phase 5: Screen Capture Context

**Goal**: Periodic screenshots during desktop recording feed visual context into AI notes. Deleted after notes are generated.  
**Duration**: 8 days  
**Prerequisites**: Phase 2 (capture lifecycle). Phase 0.2 (gpt-4o-mini is vision-capable for multimodal context).

---

### 5.1 Rust periodic screenshot command

- **File**: new `frontend/src-tauri/src/screenshot.rs`, `Cargo.toml`
- **Cargo dep**: `xcap = "0.0.9"` (cross-platform screenshot)
- **What**:
  ```rust
  #[tauri::command]
  fn start_screen_capture(meeting_id: String, interval_secs: u64) { ... }

  #[tauri::command]
  fn stop_screen_capture() { ... }
  ```
  Spawns a task that grabs the primary display every `interval_secs` (default 60), JPEG-encodes at max 1280px width, and uploads directly to the backend `POST /meetings/{meeting_id}/screenshots`. Start/stop triggered alongside `start_system_capture` / `stop_system_capture`.
- **Test**: During a desktop recording, one JPEG per 60s appears in the backend storage.

---

### 5.2 Backend screenshot upload endpoint

- **File**: new `backend/app/api/routers/screenshots.py` (or append to `audio.py`)
- **What**:
  ```python
  POST /meetings/{meeting_id}/screenshots
  # Body: JPEG bytes + capture_timestamp header
  # Auth: same token validation as the audio WS endpoint
  # Storage: temp meeting-scoped path, keyed by meeting_id + timestamp
  ```
- **Test**: Uploaded screenshot is retrievable for the meeting; unauthenticated request returns 401.

---

### 5.3 Include screenshots in notes context, then delete

- **File**: `backend/app/tasks/generate_notes.py` (`_generate_meeting_notes_async`, lines 51–156)
- **What**:
  1. After resolving the transcript (line ~69), load all screenshots for the meeting
  2. Pass them as image parts in the multimodal notes prompt alongside the transcript text
  3. After notes are successfully stored: delete all screenshots for the meeting
  4. On failure: retain screenshots for the retry (task already retries on empty transcript)
- **Test**: A recording with a visible slide produces notes that reference the slide content. Screenshots are absent from storage after notes complete.

---

### 5.4 User toggle

- **File**: `frontend/src/components/PreferenceSettings` (existing component), `frontend/src/lib/platform.ts`
- **What**: Add "Capture screen for better notes" toggle, visible only when `isDesktop()`. Default ON. Only call `start_screen_capture` when this toggle is enabled.
- **Why**: Screenshots are sensitive — users must be able to opt out
- **Test**: Toggling off stops screenshots from being taken. Toggling on resumes them next recording. Web UI is unaffected.

---

### Phase 5 Success Criteria

- Desktop notes for slide-heavy meetings reference slide content visible on screen
- Screenshots are deleted after notes generation completes
- User toggle in preferences disables the feature
- Web UI is unaffected

---

## Phase 6: Drop Recall.ai for Desktop Users

**Goal**: Desktop users record online meetings with $0 Recall cost. Web users keep the bot path.  
**Duration**: 4 days  
**Prerequisites**: Phases 2 and 3 (native capture proven on Mac and Windows).

---

### 6.1 Hide bot invite panel on desktop

- **Files**: `frontend/src/components/BotInvitePanel.tsx`, `frontend/src/app/page.tsx` (where `BotInvitePanel` renders)
- **What**: When `isDesktop()`, hide/disable the bot invite panel and show a "Record this meeting" CTA that calls `start_system_capture`. Web path unchanged.
- **Test**: Desktop window shows native record CTA and no bot invite panel. Web shows the bot invite panel as today.

---

### 6.2 Guard bot spawn endpoint from desktop clients

- **File**: `backend/app/api/routers/bot.py`
- **What**: `desktopBridge.ts` adds `X-Pnyx-Client: desktop` header to all requests when `isDesktop()`. Backend checks: if this header is present, return 400 "Use native capture on desktop." This prevents accidental bot spawns from a future code path.
- **Test**: A request to `spawn_bot` with `X-Pnyx-Client: desktop` returns 400. Without the header it succeeds as before.

---

### Phase 6 Success Criteria

- Desktop user records a Zoom/Meet/Teams call at $0 Recall.ai cost using native capture
- Web users retain the full Recall bot experience unchanged
- `spawn_bot` is unreachable from desktop client code

---

## Phase 7: Distribution

**Goal**: Signed, notarized, auto-updating Mac and Windows builds produced by CI on tagged releases.  
**Duration**: 10 days  
**Prerequisites**: Phases 1–6. Apple Developer account + Windows EV certificate procured.

---

### 7.1 macOS code signing + notarization

- **Files**: `frontend/src-tauri/tauri.conf.json` (macOS signing identity), CI secrets
- **What**: Configure the signing identity and hardened-runtime entitlements (include the microphone and screen-recording entitlements from Phase 2.2). Run `xcrun notarytool submit` + stapling on the `.dmg`/`.app` as part of the CI build.
- **Requirements**: Apple Developer account with a Developer ID Application certificate
- **Test**: Download the `.dmg` on a clean Mac; opens without Gatekeeper override; `spctl -a -v Pnyx.app` passes.

---

### 7.2 Windows signing + installer

- **Files**: `frontend/src-tauri/tauri.conf.json` (Windows certificate + timestamp server config)
- **What**: Sign the NSIS installer with the EV certificate; configure a timestamp server (e.g. `http://timestamp.digicert.com`).
- **Requirements**: Extended Validation (EV) code-signing certificate
- **Test**: Installer runs on a clean Windows VM without a SmartScreen warning.

---

### 7.3 Auto-updater

- **Files**: `frontend/src-tauri/Cargo.toml`, `frontend/src-tauri/capabilities/remote-main.json`, update manifest endpoint
- **What**:
  - Add `tauri-plugin-updater` to `Cargo.toml`
  - Add `"updater:default"` to `permissions` in the capability file
  - Generate an updater keypair (`cargo tauri signer generate`)
  - Host the update manifest JSON at a stable URL (GitHub Releases or a CDN)
  - App checks for updates on launch
- **Test**: Bump version, publish manifest. An installed older version self-updates on next launch.

---

### 7.4 CI Tauri build job

- **File**: new `.github/workflows/desktop-build.yml`
- **What**:
  ```yaml
  name: Desktop Build
  on:
    push:
      tags: ['v*']
  jobs:
    build:
      strategy:
        matrix:
          os: [macos-latest, windows-latest]
      runs-on: ${{ matrix.os }}
      steps:
        - uses: actions/checkout@v4
        - uses: dtolnay/rust-toolchain@stable
        - uses: pnpm/action-setup@v4
        - uses: actions/setup-node@v4
          with: { node-version: '20' }
        - run: pnpm install --frozen-lockfile
          working-directory: frontend
        - run: pnpm tauri:build
          working-directory: frontend
          env:
            TAURI_SIGNING_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}
            APPLE_CERTIFICATE: ${{ secrets.APPLE_CERTIFICATE }}      # macOS only
            WINDOWS_CERTIFICATE: ${{ secrets.WINDOWS_CERTIFICATE }}  # Windows only
        - uses: actions/upload-artifact@v4
          with:
            name: pnyx-desktop-${{ matrix.os }}
            path: frontend/src-tauri/target/release/bundle/
  ```
  Do not couple this to the existing web deploy pipeline (`pre-deploy.yml`).
- **Test**: Pushing a `v0.1.0` tag produces signed Mac `.dmg` + Windows `.exe` as CI artifacts.

---

### 7.5 Linux (deferred — lowest priority)

- **Files**: `tauri.conf.json` (add `"appimage"`, `"deb"` to targets), CI matrix add `ubuntu-latest`
- **What**: Add `#[cfg(target_os = "linux")]` PulseAudio/ALSA monitor-source capture path in `audio_capture.rs` using the cpal PulseAudio backend (captures the monitor source = system audio). Same `AudioFrame` contract.
- **Test**: AppImage runs on a clean Ubuntu 22.04 VM and records via the PulseAudio monitor source.

---

### Phase 7 Success Criteria

- Tagged release produces a notarized macOS `.dmg` and signed Windows `.exe` from CI
- Installed apps detect and apply updates automatically
- Linux AppImage builds and captures audio (when delivered)

---

## Summary Timeline

| Phase | Name | Duration | Cumulative |
|---|---|---|---|
| 0 | Reliability + Cost | 9 days | Week 2 |
| 1 | Tauri Scaffolding | 6 days | Week 3 |
| 2 | macOS System Audio | 10 days | Week 5 |
| 3 | Windows System Audio | 7 days | Week 7 |
| 4 | Menubar + Notifications | 8 days | Week 8–9 |
| 5 | Screen Capture Context | 8 days | Week 10–11 |
| 6 | Drop Recall Bot (Desktop) | 4 days | Week 11 |
| 7 | Distribution | 10 days | Week 13–14 |

**Total: ~13–14 weeks to a fully signed, auto-updating, distribution-ready desktop app with system audio and no Recall bot dependency.**

Phase 0 alone delivers ~$3.80/meeting in savings and can start immediately. Phases 0–1 can run in parallel if there are two engineers.

---

## Critical Files Reference

| File | Role |
|---|---|
| `frontend/src/lib/audio-streaming/AudioStreamClient.ts` | The streaming pipeline — reuse, never rewrite |
| `frontend/src/app/page.tsx` | Main recording page — decompose in Phase 0.6 before touching |
| `frontend/src/lib/platform.ts` | New in Phase 2.3 — the desktop detection seam |
| `frontend/src/lib/desktopBridge.ts` | New in Phase 4.2 — tray ↔ UI event bridge |
| `frontend/src-tauri/capabilities/remote-main.json` | Grows with each phase as permissions are added |
| `backend/app/services/ai_participant.py` | Phase 0.1 target — model default |
| `backend/app/services/recall/manager.py` | Phase 0.3–0.4 target — signature + reconciler |
| `backend/app/tasks/generate_notes.py` | Phase 5.3 target — screenshot context injection |
