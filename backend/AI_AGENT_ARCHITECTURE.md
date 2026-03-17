# Meeting Co-Pilot: AI Agent Architecture

This document explains the "Observer-Tool" architecture that powers the AI Participant in Meeting Co-Pilot.

## Overview

The AI in this system is designed as an **Observer Agent**. Unlike traditional scripts that follow a fixed loop, this agent "watches" the meeting transcript and decides which actions (tools) to trigger based on the conversation's context.

### The Unified Observer Agent
The core component is the `AIParticipantEngine`, which uses a `pydantic-ai` Agent. This agent performs a single reasoning pass but can take multiple discrete actions.

---

## Tool-Based Logic (The "How It Works")

Instead of outputting a generic JSON block, the Agent now uses specific tools for different "Meeting Needs":

### 1. Decisions (`add_decision`)
- **When**: Triggered only when participants explicitly agree on a choice or commitment.
- **Why**: Prevents the AI from guessing. It must see consensus.
- **Deduplication**: The tool checks the current `pinned_items` and `suggested_items`. If the decision already exists, it returns `SKIP` to the agent.

### 2. Open Discussions (`add_discussion`)
- **When**: Triggered when there is a conflict, a complex question, or a topic that needs a concluding choice.
- **Why**: Keeps track of "unresolved" items that the team shouldn't forget before ending the call.

### 3. AI Insights & Guardrails (`add_insight`)
- **When**: Triggered for "Meta" observations. This is the **Guardrail** system.
- **Auto-Pinning**: Insights are now **Auto-Pinned** (shared directly). They appear in the AI Insights panel without requiring manual confirmation.
- **Guardrail Examples**:
    - **Agenda Drift**: "The team is talking about the Christmas party instead of the API Roadmap."
    - **Inactivity/Participation**: "Rahul has been quiet during this budget discussion."
    - **Tone/Style**: Ensuring the host stays neutral.
- **Why**: Protects the meeting's productivity by alerting the room to "invisible" patterns.

### 4. Participant Actions (`add_action_item`)
- **When**: Triggered when a specific task is assigned to an individual or a follow-up is needed.
- **Auto-Pinning**: These are now **Auto-Pinned**. They appear directly in the **Participant Actions** tab to ensure accountability without manual friction.
- **Why**: Distinguishes between a group "Decision" and an individual "Action Item."
- **Data captured**: `owner`, `task`, and `due_date`. These are sent as `follow_up_needed` events.

### 5. Meeting Summary (`update_summary`)
- **When**: Periodically called to update the cumulative summary.
- **Formatting**: The agent is now strictly instructed to use **Rich Markdown** (`###` headers, `**bolding**`, `-` lists) to ensure the UI is structured and readable.
- **Rendering**: The frontend is calibrated to detect the `meeting_summary` field and parse it as Markdown using the BlockNote editor.

---

## AI Insights: Deep Dive

AI Insights (handled by the `add_insight` tool) work by mapping transcript patterns to **"Behavioral Skills"**.

1.  **Skill Profiles**: Profiles like `Facilitator` or `Advisor` (defined in `.md` files) tell the agent *what kind* of insights to look for.
2.  **Normalization**: The `insight_type` is normalized to internal event types (e.g., `participation_gap`, `risk_signal`).
3.  **Intervention Flow**: 
    - The insight is first recorded as a **Suggestion**.
    - If it's important enough (high confidence/priority), it is promoted to an **Intervention Card**.
    - This is what flashes on the user's screen or shows up in the "AI Insights" panel.

---

## State Recovery & Meeting Resume

To ensure professional-grade reliability, the system implements a robust state hydration mechanism:

1.  **Backend Hydration**: When an `AIParticipantEngine` is initialized for a session, it attempts to load its previous `ai_host_state` (Summary, Decisions, Action Items) from the database metadata.
2.  **WebSocket Sync**: As soon as a client connects to the meeting, the backend pushes an `ai_host_state_delta` containing the restored state.
3.  **Frontend Synthesis**: The React app merges this delta into its local state, ensuring that the Side Panel is fully populated even after a meeting pause or page refresh.
4.  **Continuous Persistence**: Manual user actions (pinning/dismissing) and automatic tool calls (summary updates) trigger immediate database updates.
- **Zero Redundancy**: Internal checks prevent the agent from saying the same thing twice.
- **Accuracy**: By using tools, the agent "confirms" its action with the system state first.
- **Specialization**: We can add new tools (e.g., `send_to_jira`) without changing the core agent's logic.
