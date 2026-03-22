# Meeting Co-Pilot — Product Requirements & Architecture Document

**Version**: 1.0
**Last Updated**: Dec 24, 2025
**Status**: Approved for Implementation

---

## 1. Executive Summary

### Problem

Meetings suffer from two critical failures:

**Loss of Momentum During Meetings**
- Discussions stall when quick questions can't be answered
- Participants zone out and lose context
- Status updates consume time meant for decisions
- Unclear what's been decided vs still being discussed
- Cognitive overload leads to disengagement

**Loss of Context After Meetings**
- Decisions evaporate, notes are scattered and incomplete
- No clear summary of what was actually decided
- No reliable follow-up process, actions get lost
- Same topics resurface in future meetings

This creates confusion, repeated discussions, and stalled progress.

### Solution

**Meeting Co-Pilot**: A web-based collaborative meeting assistant that enhances efficiency during meetings and preserves context after.

**Key Capabilities**

**During Meeting** (Enhance Momentum & Efficiency)
- Live transcript visible to all — everyone stays on the same page
- Real-time Q&A — instant answers without derailing discussion
- "Catch me up" — zoned-out participants recover without interrupting
- Live decision/action tracking — clarity on what's decided vs open
- Current topic display — keeps discussion focused
- Cross-meeting context — "What did we decide last time?" answered instantly

**After Meeting** (Preserve Context)
- Automatic structured summary with decisions and actions
- Action items with owners and deadlines
- Searchable meeting history
- Continuity recaps for follow-up meetings

### Value Proposition

| Before | After |
|--------|-------|
| "Wait, what did we decide last time?" | Instant AI answer from past meeting context |
| "Can someone recap what I missed?" | "Catch me up" button → private summary |
| "What's our current churn rate?" | Real-time lookup during meeting via AI |
| "Did we decide that or just discuss it?" | Live decisions panel shows decided vs open |
| 30 min status updates before real discussion | Pre-loaded context, jump to decisions |
| Scattered notes, lost action items | Structured, searchable, tracked actions |

### Approach

Fork and extend **Meetily** (open-source, 7k+ GitHub stars) to add multi-participant sessions and real-time collaborative features. Remove desktop-specific components for a pure web experience suitable for on-site meetings (95% of use cases).

### Target Users

- **Junior Contributors** — Stay engaged, catch up when lost
- **Mid-Level Contributors** — Collaborate efficiently, clear actions
- **Managers/Leaders** — Facilitate effectively, track decisions

---

## 🛑 Reality vs Original Vision (Jan 2026 Audit)

**The Pivot:**
The original vision was a "Google Docs for Meetings" — multi-user, real-time sync, collaborative editing.
**The Reality:** We have built a **"Super-Powered Recorder"** — a single-host tool that records, transcribes, and provides AI assistance.

**Why the change?**
1.  **Technical Complexity:** Real-time multi-user state sync (CRDTs/OT) was too high-risk for Initial Production Release.
2.  **Use Case Clarity:** 95% of users just want *better notes*, not another collaboration tool to manage during a meeting.
3.  **Performance:** Browser-based audio handling + multi-user streaming introduced unacceptable latency.

**Current Architecture:**
- **Single Host:** One user records via browser.
- **Cloud/Local AI:** Heavy lifting by Groq (Transcription) and Gemini/Ollama (Reasoning).
- **Post-Hoc Sharing:** Notes are shared *after* generation, not co-typed in real-time.

---

## 2. Goals & Non-Goals

### ✅ Goals (Status Audit)

| Goal | Status | Notes |
| :--- | :--- | :--- |
| **G1: Enable shared meeting context** | ⚠️ Changed | Context is shared *post-meeting* or via screen share, not multi-device sync. |
| **G2: Eliminate "corporate amnesia"** | ✅ Working | VectorDB + History search is implemented. |
| **G3: Support on-site meetings** | ✅ Working | Room mic capture works well. |
| **G4: Provide instant catch-up** | ✅ Working | "Catch Me Up" feature is implemented and functional. |
| **G5: Enable cross-meeting continuity** | ✅ Working | Logic is implemented, but the **Linking UX is rough** and needs attention. |
| **G6: Automate action tracking** | ✅ Working | AI extraction is reliable. |
| **G7: Answer questions in real-time** | ⚠️ Unstable | "Ask AI" chat is functional but requires attention for reliability. |
| **G8: Secure Access & RBAC** | ✅ Working | Basic auth and domain restriction implemented. |
| **G9: Dynamic Chat-Based Notes** | ✅ Working | Chat interface for notes is active. |
| **G10: Personal API Key Support** | ✅ Working | User settings for API keys implemented. |

### ❌ Non-Goals (What We Will NOT Do)

**NG1**: Video/audio conferencing
We are NOT building Zoom/Teams/Meet. We augment meetings, not host them.

**NG2**: Calendar integration
No integration with Google Calendar, Outlook, etc. Meetings are started manually.

**NG3**: System audio capture (online meetings)
We focus on room microphone for on-site meetings. Capturing Zoom/Teams audio requires a desktop app (out of scope for Initial Production Release).

**NG4**: Mobile app or mobile browser support
Desktop/laptop browser only. All participants use laptops.



**NG6**: Enterprise deployment
Single-instance deployment. No multi-tenant, no cloud scale.

**NG7**: Meeting scheduling
No scheduling, invites, or recurring meeting management.

**NG8**: Offline mode
Requires network connection for real-time sync between participants.

**NG9: Real-time collaborative multi-user editing**
While AI-assisted editing and manual host edits are supported, full "Google Docs" style simultaneous multi-user typing is not the focus of the initial version.

**NG10**: Custom AI model training
Use existing models (Whisper, Ollama, Claude). No fine-tuning or custom model development.

---

---

## 3. Functional Requirements: Fork-Based Approach

**Key**:
- ✅ **KEEP** – Already exists in Meetily; use as-is or with minor tweaks
- 🔧 **MODIFY** – Exists but needs significant changes for multi-user web
- ⭐ **NEW** – Does not exist; build from scratch

### FR1: Session Management

Summary: Session management is mostly new—Meetily is single-user; we need multi-user session handling.

| ID | Requirement | Status | Implementation Notes |
|----|-------------|--------|---------------------|
| FR1.1 | Single-user meeting can be started | ✅ KEEP | Meetily already has this via desktop app |
| FR1.2 | Generate unique session URL for sharing | ⭐ NEW | Need to build session ID generation + URL routing |
| FR1.3 | Participants can join via shared URL | ⭐ NEW | New join flow UI + backend session management |
| FR1.4 | Participants enter display name on join | ⭐ NEW | Simple join screen with name input |
| FR1.5 | Host can see list of joined participants | ⭐ NEW | Participant list component + WebSocket tracking |
| FR1.6 | Host can end the meeting session | 🔧 MODIFY | Meetily has stop recording; extend to broadcast end to all |
| FR1.7 | Session persists after end for access | ✅ KEEP | Meetily already stores meetings in PostgreSQL |

### FR2: Audio Capture & Transcription

Summary: Core transcription is solid—just swap Tauri mic access for browser API.

| ID | Requirement | Status | Implementation Notes |
|----|-------------|--------|---------------------|
| FR2.1 | Host grants microphone permission | 🔧 MODIFY | Replace Tauri mic access with browser getUserMedia() |
| FR2.2 | System captures room audio via microphone | 🔧 MODIFY | Keep Whisper pipeline, change input source from Tauri → browser |
| FR2.3 | Audio is streamed to backend in real-time | ✅ KEEP | Meetily already streams audio to backend |
| FR2.4 | System transcribes audio using Whisper | ✅ KEEP | Core Whisper integration already implemented |
| FR2.5 | Transcription appears within 2-3 seconds | ✅ KEEP | Meetily already achieves this latency |
| FR2.6 | Speaker diarization (Speaker 1, 2, etc.) | ✅ KEEP | Already implemented in Meetily |
| FR2.7 | Host can pause and resume recording | ✅ KEEP | Already exists in UI |
| FR2.8 | Host can label speakers with names | ✅ KEEP | Already implemented |

### FR3: Real-Time Sync (Multi-User)

Summary: The entire real-time sync layer is new—the biggest architectural addition.

| ID | Requirement | Status | Implementation Notes |
|----|-------------|--------|---------------------|
| FR3.1 | All participants see same live transcript | ⭐ NEW | Build WebSocket broadcasting system |
| FR3.2 | Transcript updates stream instantly to all | ⭐ NEW | Real-time event distribution architecture |
| FR3.3 | Extracted items sync to all participants | ⭐ NEW | Broadcast decisions/actions via WebSocket |
| FR3.4 | Participant join/leave is shown to all | ⭐ NEW | Session presence tracking + UI updates |

### FR4: AI Extraction

Summary: AI extraction is mostly done—leverage existing implementation.

| ID | Requirement | Status | Implementation Notes |
|----|-------------|--------|---------------------|
| FR4.1 | System identifies decisions from transcript | ✅ KEEP | Meetily already extracts decisions via LLM |
| FR4.2 | System extracts action items | ✅ KEEP | Already implemented |
| FR4.3 | Attempts to identify owner for each action | ✅ KEEP | Already tries to extract assignees |
| FR4.4 | Identifies current discussion topic | 🔧 MODIFY | Meetily has topic detection; may need real-time variant |
| FR4.5 | Extracted items update as meeting progresses | ✅ KEEP | Already works in real-time |
| FR4.6 | Host can manually add/edit/delete items | ✅ KEEP | UI already supports this |

### FR5: Catch Me Up

Summary: Partial summary logic exists—need participant-specific UI and private responses.

| ID | Requirement | Status | Implementation Notes |
|----|-------------|--------|---------------------|
| FR5.1 | Participant can request "Catch me up" | ⭐ NEW | New UI button + participant-specific request handling |
| FR5.2 | Participant selects time range (5/10/15 min) | ⭐ NEW | Time range selector UI |
| FR5.3 | System generates summary of selected time range | 🔧 MODIFY | Meetily summarizes full meeting; adapt to time-range |
| FR5.4 | Summary includes key points, decisions, actions | ✅ KEEP | Summarization prompts already exist |
| FR5.5 | Summary shown only to requesting participant | ⭐ NEW | Private message routing (not broadcast) |

### FR6: Cross-Meeting Context

Summary: VectorDB infrastructure exists—add cross-meeting queries and linking UI.

| ID | Requirement | Status | Implementation Notes |
|----|-------------|--------|---------------------|
| FR6.1 | Host can link new meeting to previous ones | ⭐ NEW | UI to select related meetings + DB schema for links |
| FR6.2 | System shows recap of linked meeting at start | ⭐ NEW | Pre-meeting recap generation |
| FR6.3 | Recap includes decisions, open actions, etc. | 🔧 MODIFY | Use existing extraction; format as recap |
| FR6.4 | Participants ask "What did we decide about X?" | 🔧 MODIFY | Extend existing Q&A to search past meetings |
| FR6.5 | System searches across all past meetings | 🔧 MODIFY | Meetily has VectorDB; expand scope to multi-meeting |

### FR7: Real-Time Q&A

Summary: Q&A foundation exists—add multi-meeting search and private responses.

| ID | Requirement | Status | Implementation Notes |
|----|-------------|--------|---------------------|
| FR7.1 | Participant can ask AI questions during meeting | 🔧 MODIFY | Meetily has Q&A; make participant-specific |
| FR7.2 | AI answers using current meeting context | ✅ KEEP | VectorDB already indexes current meeting |
| FR7.3 | AI answers using past meeting context | 🔧 MODIFY | Extend vector search to all meetings |
| FR7.4 | Q&A is private to asking participant | ⭐ NEW | Private WebSocket messaging (not broadcast) |

### FR8: Post-Meeting

Summary: Post-meeting is mostly done—just adapt for web sharing.

| ID | Requirement | Status | Implementation Notes |
|----|-------------|--------|---------------------|
| FR8.1 | System generates meeting summary when ended | ✅ KEEP | Meetily already does this |
| FR8.2 | Summary includes attendees, decisions, actions | ✅ KEEP | Already implemented |
| FR8.3 | Host can review and edit summary before sharing | ✅ KEEP | UI already supports editing |
| FR8.4 | Host can export summary as Markdown/PDF | ✅ KEEP | Export functionality already exists |
| FR8.5 | Host can copy shareable link to summary | 🔧 MODIFY | Adapt for web URLs (currently desktop file paths) |

### FR9: Meeting History

Summary: Meeting history is fully implemented—no changes needed.

| ID | Requirement | Status | Implementation Notes |
|----|-------------|--------|---------------------|
| FR9.1 | User can view list of past meetings | ✅ KEEP | Already implemented |
| FR9.2 | User can search past meetings by keyword | ✅ KEEP | VectorDB search already exists |
| FR9.3 | User can view full transcript of past meeting | ✅ KEEP | Already implemented |
| FR9.4 | User can view summary and actions | ✅ KEEP | Already implemented |

---

## 4. Non-Functional Requirements

### NFR1: Performance
- Transcription latency: < 3 seconds from speech to display
- Support 5-10 concurrent participants per session
- AI responses (catch-up, Q&A): < 10 seconds

### NFR2: Privacy & Reliability
- Use Enterprise-grade APIs (Claude/OpenAI) ensuring data is not used for training
- Real-time Cloud Sync: Audio/Transcript data must stream to the cloud instantly to prevent data loss on browser crash
- Keep local Ollama integration as an optional "Privacy Toggle" but not the default system state

### NFR3: Reliability
- Reconnect automatically if participant loses connection
- Transcript saved incrementally (no data loss on crash)
- Graceful degradation if AI services unavailable

### NFR4: Usability
- Join session in < 30 seconds (click URL, enter name)
- No installation required for participants
- Intuitive UI, minimal training needed

### NFR5: Compatibility
- **Browsers**: Chrome, Firefox, Safari, Edge (latest versions)
- **Devices**: Desktop/Laptop only
- **OS**: Windows, macOS, Linux

---

## 5. Architecture Decision: Web vs. Desktop

### Context

The original Meetily project is built as a Tauri-based desktop application. This section documents the rationale for transitioning to a web-based architecture for the Meeting Co-Pilot fork.

### Decision

✅ **We will build Meeting Co-Pilot as a Web-based application (Next.js + FastAPI) rather than a desktop application (Tauri).**

### 5.1. Core Capability Analysis

**Verdict**: Web covers all required capabilities; desktop adds only non-goal features.

| Capability | Desktop (Tauri) | Web-based | Analysis |
|------------|----------------|-----------|----------|
| Room Mic Capture | ✅ Full support via Tauri APIs | ✅ Full support via getUserMedia() | TIE - Both support primary use case |
| System Audio Capture | ✅ Can capture Zoom/Teams audio | ❌ Requires browser extension | Desktop Win - But explicitly a **Non-Goal (NG3)** |
| Multi-participant | ⚠️ Requires install + coordination | ✅ Native web, URL-based joining | **Web Win** - Core requirement (FR1) |
| Real-time Sync | ⚠️ Desktop → Server → Desktop | ✅ Standard WebSocket pattern | **Web Win** - Simpler implementation |
| Cross-platform | ⚠️ Separate OS builds required | ✅ Single codebase | **Web Win** - Lower maintenance |

### 5.2. User Experience (UX) & Friction

**Verdict**: Web provides a dramatically lower barrier to entry for participants.

| Aspect | Desktop (Tauri) | Web-based | Impact |
|--------|----------------|-----------|---------|
| Joining Flow | ❌ Download → Install → Launch | ✅ Click URL → Join | **CRITICAL** - Meets <30s join target |
| IT/Security | ❌ May block .exe/.dmg files | ✅ No installation required | Significant - Enterprise readiness |
| Updates | ❌ User must manually update | ✅ Instant via deployment | Significant - Faster iteration |
| Onboarding | ❌ High (10–15 min) | ✅ Low (<30 seconds) | **CRITICAL** - Direct impact on adoption |

### 5.3. Development & Maintenance

**Estimated time impact**: Desktop adds 30–40% to development timeline (3–4 weeks → 5–6 weeks).

| Aspect | Desktop (Tauri) | Web-based | Timeline Impact |
|--------|----------------|-----------|-----------------|
| Setup | ❌ Rust + Tauri + Node.js | ✅ Node.js only | -2 days |
| Learning Curve | ❌ Rust / Tauri APIs | ✅ Standard Web Stack | -3 to 5 days |
| Build/Deploy | ❌ Multi-OS builds + Code signing | ✅ Single cloud deployment | -2 days per release |
| Testing | ❌ Multiple OS versions | ✅ Browser-based testing | -3 days |

### 5.4. Risk Assessment

| Risk Category | Desktop (Tauri) | Web-based | Mitigation |
|---------------|----------------|-----------|------------|
| Technical Risk | 🔴 HIGH - Less familiar stack | 🟢 LOW - Standard web stack | Use Web to reduce risk |
| Timeline Risk | 🔴 HIGH - 40% longer dev time | 🟢 LOW - Rapid development | Use Web to meet Initial Production Release deadline |
| Adoption Risk | 🔴 HIGH - Install friction | 🟢 LOW - Zero-friction joining | Use Web to maximize trials |
| Maintenance | 🟡 MED - OS updates / versions | 🟢 LOW - Single version | Use Web to reduce support |

### 5.5. Summary of Pros and Cons

**Web-based Application (Next.js)**

**Pros**:
- ✅ Zero Installation: Essential for the <30 second "Catch Me Up" requirement
- ✅ Instant Updates: Deploy bug fixes to all users simultaneously
- ✅ Collaboration Native: WebSockets and URL-sharing are native to the browser environment
- ✅ IT-Friendly: No security hurdles for company users

**Cons**:
- ❌ No System Audio: Cannot record internal Zoom audio (mitigated: NG3)
- ❌ Internet Dependent: Requires connection for session sync (mitigated: NG8)

**Desktop Application (Tauri)**

**Pros**:
- ✅ System Audio: Can record internal sound without extensions
- ✅ Offline Mode: Can process data locally

**Cons**:
- ❌ High Friction: High drop-off rate due to installation requirements
- ❌ Slower Dev: Rust/Tauri overhead extends the Initial Production Release timeline significantly

---

## 6. User Flows

### 6.1 Host Starts Meeting

![Host Flow](./images/host-flow.png)

*See attached diagram for complete flow*

### 6.2. Participant Joins

![Participant Join Flow](./images/participant-join-flow.png)

*See attached diagram for complete flow*

### 6.3. Catch Me Up

![Catch Me Up Flow](./images/catch-me-up-flow.png)

**User Flow Summary**:
1. Participant zones out or joins late
2. Clicks "Catch Me Up" button
3. Selects time range (5/10/15/30 minutes)
4. AI processes transcript from selected window
5. Summary displayed privately to participant (not broadcast)
6. Participant returns to live meeting view with context restored

### 6.4 Cross-Meeting Context

![Cross-Meeting Context Flow](./images/cross-meeting-flow.png)

*See attached diagram for complete flow*

### 6.5 Real-Time Q&A

![Real-Time Q&A Flow](./images/qa-flow.png)

*See attached diagram for complete flow*

### 6.6 Post-Meeting Summary

![Post-Meeting Summary Flow](./images/post-meeting-flow.png)

*See attached diagram for complete flow*

---

## 7. Architecture Diagrams

### 7.1 System Context Diagram

![System Context](./images/system-context.png)

*C4 Level 1: Shows Meeting Co-Pilot system and external actors*

### 7.2 Container Diagram

![Container Diagram](./images/container-diagram.png)

*C4 Level 2: Shows main containers (Web App, API Server, Databases)*

### 7.3 Component Diagram - API Server

![Component Diagram](./images/component-diagram.png)

*C4 Level 3: Shows internal structure of API Server*

---

## 8. Solution Comparison & Justification

### 8.1 Open Source Alternatives Evaluated

**Comparison of Existing Solutions**

| Criteria | Meetily | Hyprnote | Scriberr | Build from Scratch |
|----------|---------|----------|----------|-------------------|
| GitHub Stars | 7,000+ | ~2,000 | ~3,000 | N/A |
| License | MIT | GPL-3.0 | MIT | N/A |
| Real-time Transcription | ✅ | ✅ | ❌ | Need to build |
| Speaker Diarization | ✅ | ✅ | ✅ | Need to build |
| AI Summarization | ✅ | ✅ | ✅ | Need to build |
| Action Extraction | ✅ | ✅ | ❌ | Need to build |
| Web-based | ❌ | ❌ | ✅ | Choice |
| Multi-user Sessions | ❌ | ❌ | ❌ | Need to build |
| VectorDB Integrated | ✅ | ❌ | ❌ | Need to build |
| Effort to Adapt | Medium | Medium–High | Medium | High |

**Decision**: ✅ **Fork Meetily**

**Justification**:
- Most complete feature set for our requirements
- MIT license allows modification and redistribution
- VectorDB already integrated (essential for cross-meeting context)
- Modern and extensible technology stack
- Approximately 60–70% of required core functionality already implemented

### 8.2 Tech Stack Decisions

| Component | Choice | Alternatives Considered | Reason for Choice |
|-----------|--------|------------------------|-------------------|
| Backend | FastAPI (Python) | Node.js, Go, Rust | Strong AI/ML ecosystem, rapid development |
| Frontend | Next.js (React) | Vite + React, SvelteKit | Already used in Meetily, production-proven |
| Database | PostgreSQL | PostgreSQL, MongoDB | Used for Production; PostgreSQL's hybrid nature (Relational + JSONB + Vectors) offers a superior unified architecture. |
| Vector Database | ChromaDB / LanceDB | Pinecone, pgvector | Embedded, no external server required |
| Real-time Communication | WebSocket | SSE, Polling | Bidirectional communication for Q&A |
| Transcription | Whisper.cpp | Cloud APIs | Local processing, privacy-first |
| LLM | Ollama + Claude | GPT, Gemini | Local inference + high-quality cloud fallback |

---

## 9. Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|-----------|------------|
| Poor room audio quality | High | Medium | Recommend use of a USB microphone, display real-time audio level indicator, and provide guidance for optimal mic positioning |
| Whisper transcription inaccuracy | High | Medium | Use larger Whisper models when available, allow manual transcript corrections, and display confidence indicators |
| AI hallucinations in summaries | Medium | Medium | Always show source transcript alongside summaries, allow host edits, and use structured prompts |
| Network issues during meeting | Medium | Low–Medium | Implement auto-reconnect, local audio buffering, and visible connection status |
| Local LLM (Ollama) performance too slow | Medium | Medium | Provide cloud LLM fallback, use smaller models for real-time features |
| Meetily codebase tightly coupled to Tauri | High | Low–Medium | Conduct a 2–3 day technical spike to evaluate decoupling effort, maintain fallback option to build from scratch |
| Speaker diarization inaccuracies | Low–Medium | Medium | Allow host to reassign speaker labels and provide manual speaker identification |
| Participant privacy concerns | Medium | Medium | Display clear recording indicators, require explicit consent on join, and emphasize local-first processing |

---

## 10. Implementation Plan

### Phase 0: Discovery & Setup
**Effort**: 2-3 days

- Clone and explore Meetily codebase
- Identify Tauri-specific code to remove
- Test Meetily backend independently
- Set up development environment
- Validate Whisper + Ollama work locally
- Decision: Confirm fork approach or pivot to scratch

**Deliverable**: Working local dev setup, go/no-go decision

### Phase 1: Core Web App
**Effort**: 4-5 days

- Remove Tauri shell from frontend
- Convert to standard Next.js web app
- Implement browser audio capture (getUserMedia)
- Stream audio to backend via WebSocket
- Display live transcript in UI
- Basic host view working

**Deliverable**: Host can record meeting in browser, see live transcript

### Phase 2: Multi-Participant Sessions
**Effort**: 3-4 days

- Implement session management (create, join, leave)
- Generate session URLs
- Build participant join flow (enter name, connect)
- WebSocket rooms for session broadcasting
- Sync transcript to all participants
- Participant list display

**Deliverable**: Multiple participants can join and see live transcript

### Phase 3: AI Features
**Effort**: 4-5 days

- Real-time decision/action extraction
- Display extracted items in sidebar
- "Catch me up" feature implementation
- Real-time Q&A with AI
- Current topic identification

**Deliverable**: AI features working during live meeting

### Phase 4: Cross-Meeting Context
**Effort**: 3-4 days

- Store meeting embeddings in VectorDB
- Implement meeting linking
- Build continuity recap view
- Cross-meeting search in Q&A
- Surface past decisions and open actions

**Deliverable**: New meetings show context from linked past meetings

### Phase 5: Post-Meeting & Polish
**Effort**: 3-4 days

- Meeting summary generation
- Export to Markdown/PDF
- Meeting history view
- Search past meetings
- UI polish
- Error handling and edge cases
- Basic testing

**Deliverable**: Complete Initial Production Release ready for demo

### Total Effort Estimate

| Phase | Effort |
|-------|--------|
| Phase 0: Discovery | 2-3 days |
| Phase 1: Core Web App | 4-5 days |
| Phase 2: Multi-Participant | 3-4 days |
| Phase 3: AI Features | 4-5 days |
| Phase 4: Cross-Meeting | 3-4 days |
| Phase 5: Polish | 3-4 days |
| **TOTAL** | **19-25 days (~3-4 weeks)** |
| **Demo-ready (Phase 0-2)** | **9-12 days (~2 weeks)** |

### Detailed Timeline with Dates

**Assumptions**:
- Working days: Monday to Friday (5 days/week)
- Start date: January 2, 2025 (Thursday)
- Buffer time included for blockers and testing
- Estimated completion: January 31, 2025

| Phase | Duration | Start Date | End Date | Working Days | Status | Key Deliverable |
|-------|----------|------------|----------|--------------|--------|-----------------|
| **Phase 0: Discovery & Setup** | 2-3 days | Dec 22, 2024 | Dec 24, 2024 | 3 days | ✅ **COMPLETE** | Dev environment ready, go/no-go decision confirmed |
| **Phase 1: Core Web App** | 5-7 days | Jan 2, 2025 | Jan 5, 2026 | 7 days | ✅ **COMPLETE** | Host can record meeting in browser with live transcript |
| **Phase 1.5: Groq Streaming** | 2 days | Jan 5, 2026 | Jan 5, 2026 | 2 days | ✅ **COMPLETE** | Real-time Groq Whisper streaming transcription |
| **Phase 2: Multi-Participant Sessions** | 3-4 days | - | - | - | ⏸️ **SKIPPED** | Deferred - not priority for single-user use case |
| **Phase 3: AI Features** | 4-5 days | Jan 6, 2026 | - | 5 days | ⚠️ **PARTIAL** | "Catch Me Up" is done; Q&A needs attention. |
| **Phase 4: Cross-Meeting Context** | 3-4 days | - | - | 5 days | ✅ **COMPLETE** | Logic is done; **Linking UX is rough** and needs attention. |
| **Phase 5: Post-Meeting & Polish** | 3-4 days | - | - | 5 days | 📋 Planned | Export, history, UI polish, production-ready |

**Total Duration**: 25 working days (5 weeks) including buffer
**Initial Production Release Demo-Ready**: January 24, 2025 (end of Phase 3)
**Production-Ready**: February 7, 2025 (end of Phase 5)

### Milestone Schedule

| Milestone | Target Date | Description | Success Criteria |
|-----------|-------------|-------------|------------------|
| **M0: Planning Complete** | ✅ Dec 24, 2024 | All documentation and implementation plans ready | PRD approved, PHASE_1_PLAN.md complete, architecture diagrams finalized |
| **M1: Web Audio Working** | ✅ Jan 5, 2026 | Single-user web recording functional | Browser can capture audio, stream to Whisper, display real-time transcript |
| **M1.5: Groq Streaming** | ✅ Jan 5, 2026 | Real-time streaming transcription | Continuous PCM streaming to Groq Whisper API with 1-2s latency |
| **M2: Multi-User Sessions** | ⏸️ SKIPPED | Collaborative sessions functional | Deferred - single-user AI features are priority |
| **M3: Initial Production Release Demo** | ⚠️ **PARTIAL** | Core AI features are partially stable | "Catch Me Up" is done; Q&A requires more work. |
| **M4: Full Feature Set** | ✅ **COMPLETE** | Cross-meeting context is implemented | Can link meetings and search across transcriptions. |
| **M5: Production Launch** | 🔜 **NEXT** | Polished and production-ready | The stabilization plan must be completed first. |

### Weekly Breakdown

**Week 1** (Jan 2,5, 2025) - *Phase 1 Start*
- Remove Tauri dependencies
- Implement browser audio capture
- Build WebSocket infrastructure

**Week 2** (Jan 6-9, 2025) - *Phase 1 Completion*
- Connect audio pipeline to Whisper
- Display live transcript in web UI
- Test end-to-end recording flow
- **Deliverable**: Working web-based single-user recording

**Week 3** (Jan 12-16, 2025) - *Phase 2: Multi-User*
- Session management (create, join, leave)
- WebSocket rooms for broadcasting
- Participant presence tracking
- **Deliverable**: Multiple users seeing same transcript

**Week 4** (Jan 19-23, 2025) - *Phase 3: AI Features*
- "Catch Me Up" with time-range selection
- Real-time Q&A (private to participant)
- Decision/Action extraction
- **Deliverable**: ⭐ **Initial Production Release Demo-Ready**

**Week 5** (Jan 26-30, 2025) - *Phase 4: Context*
- ChromaDB integration
- Meeting linking UI
- Cross-meeting search
- **Deliverable**: Full feature set complete

**Week 6** (Feb 2-6, 2025) - *Phase 5: Polish*
- Export to PDF/Markdown
- Meeting history and search
- Error handling and edge cases
- **Deliverable**: Production-ready for office deployment

### Risk Buffer & Contingency

**Built-in Buffers**:
- Each phase has 1-2 days buffer (e.g., "4-5 days" estimated as 5 working days)
- Weekend breaks between major phases for testing/discovery
- Total timeline: 25 days vs optimistic 19 days = 6 days buffer (32%)

**Contingency Plan**:
- If Phase 1 takes longer: Can reduce Phase 5 scope (polish is flexible)
- If audio format issues: Switch to cloud transcription API temporarily
- If WebSocket issues: Can use polling as interim solution

**Critical Path**:
Phase 1 → Phase 2 → Phase 3 are **sequential dependencies**
Phase 4 and Phase 5 can be **deprioritized** if timeline pressure increases

---

## 11. References

### Background

This PRD is based on detailed research identifying 29 meeting problems, of which 10 are addressable via software.

📄 **Full Research Document**:
[Meeting Problems Research](https://docs.google.com/document/d/16cJ47zE0ZgSHLRcDU3-8NJsqSk2heqMPBplKZEJmmGw/edit?tab=t.0)

**Key sections in the research document**:
- Problem Statement
- User Personas
- Success Metrics
- Scope Boundaries

### Open Source Projects Evaluated

- **Meetily**: https://github.com/Zackriya-Solutions/meeting-minutes
- **Hyprnote**: https://github.com/fastrepl/hyprnote
- **Scriberr**: https://github.com/rishikanthc/Scriberr

---

**Document Status**: Approved
**Next Review**: After Phase 1 Completion
**Owned By**: Meeting Co-Pilot Team
