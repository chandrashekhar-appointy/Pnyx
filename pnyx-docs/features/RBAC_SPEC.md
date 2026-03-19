# Simplified RBAC Specification

## Overview
This document defines the simplified Role-Based Access Control (RBAC) model for Meeting Co-Pilot. The system is designed to be secure but lightweight, focusing on **Workspaces** and **Meetings** as the primary boundaries.

## RBAC Implementation Status (Jan 2026)

### ✅ Phase 1: Isolation & Security (Completed)
- **Data Model**: `meetings` table ownership, `workspaces` table.
- **Enforcement**: Backend allows meeting owners and explicitly permitted users from `meeting_permissions`.
- **Frontend**: All API calls are authenticated using `authFetch`.
- **Current Behavior**: Users see meetings they own plus meetings explicitly shared with them.

### ⏸️ Phase 2: Expanded Collaboration (Deferred)
- **Workspaces**: UI for creating and switching workspaces is still deferred.
- **Granular Roles**: Viewer/editor role differentiation is not yet enforced in the central RBAC policy.
- **Workspace Admin**: Workspace-scoped admin behavior described below is a future design target, not the current implementation.

## Core Concepts

### 1. Scopes
*   **Personal Scopes**: Meetings created outside of a workspace. Owned by a single user.
*   **Workspaces**: Shared environments for collaboration (e.g., "Engineering Team").
*   **Meetings**: The core resource protected by RBAC.

### 2. Roles

#### Workspace Roles
Applies to the Workspace entity itself.
*   **`workspace_admin`**:
    *   Manage workspace settings (rename, delete).
    *   Manage workspace members (invite, remove).
    *   **Super-power**: Can view and manage *all* meetings within the workspace.
*   **`workspace_member`**:
    *   Can create new meetings in the workspace.
    *   **Crucial Constraint**: Cannot see workspace meetings unless explicitly invited or created by them (or if they are the owner).

#### Meeting Roles
Applies to a specific Meeting entity.
*   **`meeting_owner`**:
    *   Full control.
    *   Manage invites (add/remove users).
    *   Delete meeting.
    *   Edit transcripts/notes.
    *   Use all AI features.
*   **`meeting_participant`**:
    *   View transcript and notes.
    *   Edit notes (collaborative).
    *   Use AI features (Ask AI, Generate Notes).
    *   *Cannot* manage invites or delete meeting.
*   **`meeting_viewer`**:
    *   Read-only access to transcript and notes.
    *   *Cannot* edit notes.
    *   *Cannot* use expensive AI features (optional restriction, to be decided).
    *   *Cannot* see other participants or invites.

## Permission Resolution Logic

The system determines access using a central Policy Check: `can(user_id, action, meeting_id)`

### Resolution Flow:
1.  **Check Meeting Existence**: valid `meeting_id`?
2.  **Check Ownership**: Is `user_id` == `meeting.owner_id`? -> **ALLOW ALL**.
3.  **Check Explicit Meeting Permission**:
    *   Does `meeting_permissions` table have an entry for `(meeting_id, user_id)`?
    *   If yes, allow access.
4.  **Default**: **DENY**.

### Implications
*   **Personal Meetings**: Only the `owner` and explicitly permitted users can access.
*   **Shared Meetings**: Access is granted by presence in `meeting_permissions`; role-specific behavior is still limited.
*   **Workspace Roles**: The role model below is aspirational documentation for a later phase and is not fully enforced by today's backend.

## Database Schema Changes

### 1. Workspaces
```sql
CREATE TABLE workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_id TEXT NOT NULL,     -- Creator of the workspace
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 2. Workspace Members
```sql
CREATE TABLE workspace_members (
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
    PRIMARY KEY (workspace_id, user_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
);
```

### 3. Meetings (Update)
```sql
ALTER TABLE meetings ADD COLUMN workspace_id TEXT REFERENCES workspaces(id);
ALTER TABLE meetings ADD COLUMN owner_id TEXT NOT NULL;
-- owner_id should be indexed for fast "My Meetings" queries
```

### 4. Meeting Permissions
```sql
CREATE TABLE meeting_permissions (
    meeting_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('participant', 'viewer')),
    PRIMARY KEY (meeting_id, user_id),
    FOREIGN KEY (meeting_id) REFERENCES meetings(id)
);
```

## AI & Feature Gating
| Action | Owner | WS Admin | Participant | Viewer |
| :--- | :---: | :---: | :---: | :---: |
| View Transcript | ✅ | ✅ | ✅ | ✅ |
| Play Audio | ✅ | ✅ | ✅ | ✅ |
| Edit Notes | ✅ | ✅ | ✅ | ❌ |
| Generate AI Notes | ✅ | ✅ | ✅ | ❌ |
| Chat with Meeting | ✅ | ✅ | ✅ | ❌ |
| Catch Me Up | ✅ | ✅ | ✅ | ❌ |
| Delete Meeting | ✅ | ✅ | ❌ | ❌ |
| Invite Users | ✅ | ✅ | ❌ | ❌ |
| Export | ✅ | ✅ | ✅ | ✅ |

## Non-Goals & Constraints
*   **No Global Super-Admin**: Platform admins cannot see content unless they are workspace admins or invited.
*   **No "Public" Workspaces**: All collaboration requires explicit membership + meeting access.
*   **Folders**: Folders are metadata tags only. They do not inherit or enforce permissions. A meeting in "Finance" folder is still only visible based on RBAC.
*   **No ABAC**: Complex policies (e.g. "view during business hours") are out of scope.
