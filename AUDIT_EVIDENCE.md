# Audit Evidence Code Snippets

This file contains the code snippets mapped directly to the findings in the comprehensive audit report.

## Critical Findings

### C1. Production database credentials are committed

**`backend/docker-compose.yml` (Lines 38-42)**
```yaml
      - PYTHONPATH=/app
      - DATABASE_PATH=/app/data/meeting_minutes.db
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL:-redis://redis:6379/0}
      - OLLAMA_HOST=${OLLAMA_HOST:-http://host.docker.internal:11434}
```

**`backend/Dockerfile.app` (Lines 36-40)**
```text
ENV PYTHONUNBUFFERED=1
ENV DATABASE_PATH=/app/data/meeting_minutes.db
ENV DATABASE_URL=postgresql://neondb_owner:npg_3JYK7ySezjrT@ep-morning-truth-ahrz730e-pooler.c-3.us-east-1.aws.neon.tech/neondb?sslmode=require

# Expose the port the app runs on
```

**`backend/app/vector_store.py` (Lines 66-69)**
```python
    # Use the same DATABASE_URL as the main app
    default_url = "postgresql://neondb_owner:npg_3JYK7ySezjrT@ep-morning-truth-ahrz730e-pooler.c-3.us-east-1.aws.neon.tech/neondb?sslmode=require"
    db_url = os.getenv('DATABASE_URL', default_url)
    return await asyncpg.connect(db_url)
```

**`backend/cleanup_legacy_tables.py` (Lines 1-5)**
```python
import psycopg2
import os

NEON_URL = os.getenv("DATABASE_URL", "postgresql://neondb_owner:npg_3JYK7ySezjrT@ep-morning-truth-ahrz730e-pooler.c-3.us-east-1.aws.neon.tech/neondb?sslmode=require")

```

**`backend/setup_vector_table.py` (Lines 1-6)**
```python
import psycopg2
import os

# Use the same logic as migrate_to_neon.py to get the URL
NEON_URL = os.getenv("DATABASE_URL", "postgresql://neondb_owner:npg_3JYK7ySezjrT@ep-morning-truth-ahrz730e-pooler.c-3.us-east-1.aws.neon.tech/neondb?sslmode=require")

```

### C2. Google JWT audience verification disabled

**`backend/app/core/security.py` (Lines 69-88)**
```python
        # Note: audience check is crucial
        # DEBUG: Temporarily disable strict audience check to debug mismatch
        payload = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience=None,  # GOOGLE_CLIENT_ID,
            options={
                "verify_at_hash": False,
                "verify_aud": False,  # Explicitly disable audience verification
            },
        )

        token_aud = payload.get("aud")
        # print(f"DEBUG AUTH: Token aud: '{token_aud}'", flush=True)
        # print(f"DEBUG AUTH: Server GOOGLE_CLIENT_ID: '{GOOGLE_CLIENT_ID}'", flush=True)

        if str(token_aud) != str(GOOGLE_CLIENT_ID):
            print("DEBUG AUTH: Audience Mismatch! Continuing for debug...", flush=True)
            # raise HTTPException(status_code=401, detail=f"Audience mismatch: {token_aud} vs {GOOGLE_CLIENT_ID}")
```

### C3. /admin/reindex-all is publicly callable

**`backend/app/api/routers/admin.py` (Lines 15-19)**
```python
    from schemas.credits import AdminCreditOverrideRequest, AdminSetUnlimitedRequest, CreditBalanceResponse
    from schemas.user import User

router = APIRouter()
logger = logging.getLogger(__name__)
```

### C4. WebSocket auth token sent in query string

**`frontend/src/lib/audio-streaming/AudioStreamClient.ts` (Lines 237-246)**
```typescript
         const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 5000);
         console.warn(`[AudioStream] Connection failed. Retrying in ${delay}ms (Attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})`);
         
         await new Promise(r => setTimeout(r, delay));
         return this.connectWithRetry();
       } else {
         console.error('[AudioStream] Max retries reached. Giving up.');
         this.callbacks.onError?.(new Error('Connection lost. Please refresh.'));
         throw error;
       }
```

**`backend/app/api/routers/audio.py` (Lines 738-750)**
```python
        finally:
            session_cleanup_tasks.pop(session_id, None)

    session_cleanup_tasks[session_id] = asyncio.create_task(_cleanup_after_grace())


@router.websocket("/ws/streaming-audio")
async def websocket_streaming_audio(
    websocket: WebSocket,
    session_id: Optional[str] = None,
    meeting_id: Optional[str] = None,
    auth_token: Optional[str] = None,
):
```

## High Findings

### H1. Analytics admin endpoint SQL injection sink

**`backend/app/api/routers/analytics.py` (Lines 30-65)**
```python
async def track_event(request: TrackEventRequest, req: Request):
    """Ingest analytics events from frontend."""
    # Try to extract user email if logged in
    user_email = request.user_id
    try:
        # Since this endpoint can be called anonymously (before login),
        # we don't enforce token presence, but we try to decode it if exists.
        auth_header = req.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            current_user = await get_current_user(
                auth_header
            )  # Actually get_current_user needs Depends injected HTTPAuthorizationCredentials.
            # We will just rely on the frontend passing the user_id for now for simplicity in public endpoints
            pass
    except Exception:
        pass

    query = """
    INSERT INTO analytics_events (session_id, user_id, event_name, properties, timestamp)
    VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP)
    """

    try:
        async with db._get_connection() as conn:
            await conn.execute(
                query,
                request.session_id,
                user_email,
                request.event_name,
                json.dumps(request.properties),
            )
    except Exception as e:
        logger.error(f"Failed to insert analytics event: {e}")
        # Return success anyway so frontend doesn't crash on analytics failure

    return {"status": "success"}
```

**`backend/app/api/routers/analytics.py` (Lines 85-120)**
```python
            base_where = "user_id NOT LIKE 'localhost%'"
            if user_filter == "exclude_admin":
                base_where += " AND user_id != 'gagan@appointy.com'"
            elif user_filter and user_filter != "all":
                # if user_filter is a specific email
                base_where += f" AND user_id = '{user_filter}'"

            # Top-level KPIs
            total_events = await conn.fetchval(
                f"SELECT COUNT(*) FROM analytics_events WHERE {base_where}"
            )
            unique_users = await conn.fetchval(
                f"SELECT COUNT(DISTINCT user_id) FROM analytics_events WHERE user_id IS NOT NULL AND {base_where}"
            )

            # Breakdown by feature
            feature_breakdown_rows = await conn.fetch(f"""
                SELECT event_name, COUNT(*) as count 
                FROM analytics_events 
                WHERE {base_where}
                GROUP BY event_name 
                ORDER BY count DESC
                LIMIT 15
            """)
            feature_breakdown = [
                {"name": row["event_name"], "value": row["count"]}
                for row in feature_breakdown_rows
            ]

            # Template popularity (for notes_generated OR notes_template_switched)
            template_popularity_rows = await conn.fetch(f"""
                SELECT properties->>'template_name' as template_name, COUNT(*) as count
                FROM analytics_events
                WHERE event_name IN ('notes_generated', 'notes_template_switched') 
                  AND properties->>'template_name' IS NOT NULL
                  AND {base_where}
```

### H2. Encryption fallback silently converts stored secrets

**`backend/app/core/encryption.py` (Lines 7-37)**
```python
MASTER_KEY = os.getenv("MASTER_KEY")

if not MASTER_KEY:
    # Fallback to a fixed key if not provided (NOT recommended for production)
    # In this app, we should ensure it's provided.
    # raise ValueError("MASTER_KEY not found in environment variables")
    pass  # Allow for now if not set, but handle in functions

# Initialize Fernet lazily or handle error if key missing
try:
    fernet = Fernet(MASTER_KEY.encode()) if MASTER_KEY else None
except Exception:
    fernet = None


def encrypt_key(plain_text: str) -> str:
    """Encrypt a plain text API key."""
    if not plain_text or not fernet:
        return ""
    return fernet.encrypt(plain_text.encode()).decode()


def decrypt_key(encrypted_text: str) -> str:
    """Decrypt an encrypted API key."""
    if not encrypted_text or not fernet:
        return ""
    try:
        return fernet.decrypt(encrypted_text.encode()).decode()
    except Exception:
        # If decryption fails (e.g. key changed), return empty or handle error
        return ""
```

**`backend/app/db/manager.py` (Lines 300-390)**
```python
                    UPDATE full_transcripts
                    SET meeting_name = $1
                    WHERE meeting_id = $2
                """,
                    meeting_name,
                    meeting_id,
                )

    async def get_transcript_data(self, meeting_id: str):
        """Get transcript/summary process data for a meeting"""
        async with self._get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT meeting_id, status, result, error, start_time, end_time, metadata
                FROM summary_processes 
                WHERE meeting_id = $1
                ORDER BY start_time DESC
                LIMIT 1
            """,
                meeting_id,
            )

            if row:
                # Convert Record to dict
                data = dict(row)
                # Handle JSONB fields if they are strings (asyncpg might return dict directly if jsonb)
                if isinstance(data.get("result"), str):
                    try:
                        data["result"] = json.loads(data["result"])
                    except:
                        pass
                if isinstance(data.get("metadata"), str):
                    try:
                        data["metadata"] = json.loads(data["metadata"])
                    except:
                        pass
                return data
            return None

    async def save_meeting(
        self,
        meeting_id: str,
        title: str,
        folder_path: str = None,
        owner_id: str = None,
        workspace_id: str = None,
    ):
        """Save or update a meeting"""
        try:
            async with self._get_connection() as conn:
                # Check existence
                exists = await conn.fetchval(
                    "SELECT id FROM meetings WHERE id = $1", meeting_id
                )

                if not exists:
                    now = datetime.utcnow()
                    await conn.execute(
                        """
                        INSERT INTO meetings (id, title, created_at, updated_at, folder_path, owner_id, workspace_id)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                        meeting_id,
                        title,
                        now,
                        now,
                        folder_path,
                        owner_id,
                        workspace_id,
                    )
                    logger.info(
                        f"Saved meeting {meeting_id} (Owner: {owner_id}, WS: {workspace_id})"
                    )
                else:
                    # Optional: We could update title here if we wanted
                    pass
                return True
        except Exception as e:
            logger.error(f"Error saving meeting: {str(e)}")
            raise

    async def save_meeting_transcript(
        self,
        meeting_id: str,
        transcript: str,
        timestamp: str,
        summary: str = "",
        action_items: str = "",
        key_points: str = "",
        audio_start_time: float = None,
        audio_end_time: float = None,
```

### H3. Sharing flow contradictory

**`backend/app/api/routers/transcripts.py` (Lines 1145-1159)**
```python
                                    continue

                                # Check if section title already exists
                                existing_section = next(
                                    (
                                        s
                                        for s in final_result[key]["sections"]
                                        if s["title"] == new_section["title"]
                                    ),
                                    None,
                                )

                                if existing_section:
                                    # Merge blocks
                                    existing_section["blocks"].extend(
```

**`backend/app/api/routers/sharing.py` (Lines 116-127)**
```python
@router.get("/view/{share_token}")
async def view_shared_note_by_token(share_token: str):
    """Public endpoint for email link access (redirects to app with auth check)."""
    share_record = await db_manager.get_shared_note_by_token(share_token)
    if not share_record:
        raise HTTPException(status_code=404, detail="Invalid or expired share link")
        
    app_base_url = os.environ.get("APP_BASE_URL", "http://localhost:3118")
    meeting_id = share_record["meeting_id"]
    
    # Redirect to the frontend shared route
    return RedirectResponse(url=f"{app_base_url}/meeting-details?id={meeting_id}&shared=true&token={share_token}")
```

**`frontend/src/app/meeting-details/page.tsx` (Lines 102-145)**
```typescript
      const isShared = searchParams.get('shared') === 'true';
      const url = isShared ? `/api/sharing/${meetingId}` : `/get-meeting/${meetingId}`;
      const response = await authFetch(url);
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      const data = await response.json();
      
      if (isShared) {
        // Shared response format: { meeting: {...}, summary: {...}, transcripts: [...] }
        if (data.meeting) {
          data.meeting.transcripts = data.transcripts || [];
          setMeetingDetails(data.meeting);
          setCurrentMeeting({ id: data.meeting.id, title: data.meeting.title });
        }
        
        // Also trigger the "viewed" endpoint in the background
        authFetch(`/api/sharing/${meetingId}/viewed`, { method: 'PATCH' })
          .then(() => {
            if (refetchSharedNotes) refetchSharedNotes();
          })
          .catch(err => console.error("Failed to mark shared note viewed", err));
          
      } else {
        // Standard response format
        console.log('Meeting details:', data);
        setMeetingDetails(data);
        setCurrentMeeting({ id: data.id, title: data.title });
      }
    } catch (error) {
      console.error('Error fetching meeting details:', error);
      setError("Failed to load meeting details");
    }
  }, [meetingId, setCurrentMeeting, serverAddress, searchParams]);

    const fetchMeetingSummary = useCallback(async () => {
    if (!meetingId || meetingId === 'intro-call' || !serverAddress) return;
    try {
      const isShared = searchParams.get('shared') === 'true';
      const url = isShared ? `/api/sharing/${meetingId}` : `/get-summary/${meetingId}`;
      const response = await authFetch(url);
      
```

### H4. API contracts drifted

**`frontend/src/components/Sidebar/SidebarProvider.tsx` (Lines 205-210)**
```typescript
  const searchTranscripts = async (query: string) => {
    if (!query.trim()) {
      setSearchResults([]);
      return;
    }

```

**`backend/app/api/routers/chat.py` (Lines 376-390)**
```python
@router.post("/search-context")
async def search_context_endpoint(request: SearchContextRequest):
    """
    Search across past meetings for relevant context.
    Returns matching chunks with source citations.
    """
    try:
        # Fallback to empty results for now as VectorDB logic is not fully migrated
        results = []
        return {
            "status": "success",
            "query": request.query,
            "results": results,
            "total_indexed": 0,
        }
```

**`frontend/src/components/CalendarConnectPrompt.tsx` (Lines 80-88)**
```typescript
  const handleConnect = async () => {
    try {
      const response = await authFetch('/api/calendar/connect?request_write_scope=false', {
        method: 'POST',
      });
      if (!response.ok) throw new Error('Failed to get auth URL');
      const data = await response.json();
      if (data.auth_url) {
        window.location.href = data.auth_url;
```

**`backend/app/api/routers/calendar.py` (Lines 79-88)**
```python
@router.post("/google/connect")
async def start_google_calendar_connect(
    request: CalendarConnectRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        return await oauth_service.build_google_authorization_url(
            user_email=current_user.email,
            request_write_scope=request.request_write_scope,
        )
```

### H5. Auth and RBAC docs overstate what code enforces

**`pnyx-docs/features/AUTH_AND_RBAC.md` (Lines 74-79)**
```markdown
## Security Measures

1.  **Domain Restriction**: Hardcoded check in NextAuth callback rejects non-`@appointy.com` emails.
2.  **Stateless Validation**: Backend verifies JWT signature using Google's public keys (JWKS) on every request. No database session lookups required for validity check (though RBAC requires DB).
3.  **Audience Check**: Backend ensures token was issued specifically for this `GOOGLE_CLIENT_ID`.

```

**`pnyx-docs/features/RBAC_SPEC.md` (Lines 28-70)**
```markdown
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
3.  **Check Workspace Admin**:
    *   Is meeting in a valid `workspace_id`?
    *   Is user `workspace_admin` of that workspace? -> **ALLOW ALL**.
4.  **Check Explicit Meeting Invitation**:
    *   Does `meeting_permissions` table have an entry for `(meeting_id, user_id)`?
    *   If yes, resolve based on assigned role (`participant` or `viewer`).
5.  **Default**: **DENY**.
```

**`backend/app/core/rbac.py` (Lines 18-65)**
```python
    async def can(self, user: User, action: str, meeting_id: str) -> bool:
        """
        Central Policy Check: Can `user` perform `action` on `meeting_id`?

        Default policy: Only meeting owner (or explicitly permitted user) has access.
        """
        if not user or not user.email:
            return False

        # Allow AI interaction with the current recording (not yet saved in DB)
        if meeting_id == "current-recording" and action == "ai_interact":
            return True

        try:
            async with self.db._get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT owner_id FROM meetings WHERE id = $1", meeting_id
                )
                if row and row.get("owner_id") == user.email:
                    return True

                # Optional: check meeting_permissions table if it exists
                try:
                    perm = await conn.fetchrow(
                        """
                        SELECT 1
                        FROM meeting_permissions
                        WHERE meeting_id = $1 AND user_id = $2
                        LIMIT 1
                        """,
                        meeting_id,
                        user.email,
                    )
                    if perm:
                        return True
                except Exception as e:
                    # Table may not exist; keep private by default
                    logger.debug(
                        f"RBAC: meeting_permissions check skipped: {e}", exc_info=True
                    )
        except Exception as e:
            logger.error(f"RBAC: Error checking permissions: {e}", exc_info=True)
            return False

        logger.info(
            f"RBAC Deny: {user.email} cannot {action} meeting {meeting_id}"
        )
        return False
```

### H6. Hardcoded org-specific policy

**`frontend/src/lib/auth.ts` (Lines 48-80)**
```typescript
 * - Google OAuth with @appointy.com domain restriction
 * - JWT session strategy for backend API calls
 * - Automatic token refresh rotation
 */
export const authOptions: NextAuthOptions = {
  providers: [
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID!,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
      authorization: {
        params: {
          prompt: "consent",
          access_type: "offline",
          response_type: "code",
          scope: "openid email profile https://www.googleapis.com/auth/calendar.readonly https://www.googleapis.com/auth/calendar.events",
        },
      },
    }),
  ],
  
  callbacks: {
    // Domain restriction - only allow @appointy.com
    async signIn({ user }) {
      const allowedDomains = ['appointy.com'];
      const email = user.email || '';
      const domain = email.split('@')[1];
      
      if (!allowedDomains.includes(domain)) {
        console.log(`[Auth] Rejected login from: ${email}`);
        return false;
      }
      
      console.log(`[Auth] Successful login: ${email}`);
```

**`backend/app/api/deps.py` (Lines 52-58)**
```python
    # Domain restriction check
    # Temporary bypass for testing
    if not email.endswith("@appointy.com"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access restricted to @appointy.com users (found {email})",
        )
```

**`backend/app/api/routers/audio.py` (Lines 676-684)**
```python


async def _authenticate_websocket(auth_token: Optional[str]) -> Optional[User]:
    if not auth_token:
        return None
    payload = await verify_google_token(auth_token)
    email = payload.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="Token missing email")
```

**`backend/app/api/routers/analytics.py` (Lines 72-76)**
```python
    """Fetch dashboard metrics, restricted to admin."""
    # Security: Only admin can see the dashboard
    if not user or getattr(user, "email", "") != "gagan@appointy.com":
        raise HTTPException(status_code=403, detail="Forbidden: Admin access only")

```

**`frontend/src/app/dashboard/page.tsx` (Lines 29-34)**
```typescript
    if (status === 'authenticated') {
      if (session?.user?.email !== 'gagan@appointy.com') {
        router.push('/'); // Redirect non-admins to home
        return;
      }
      fetchMetrics();
```

**`frontend/src/components/Sidebar/index.tsx` (Lines 43-45)**
```typescript
  const { data: session } = useSession();
  const isAdmin = session?.user?.email === 'gagan@appointy.com';

```

### H7. DB access creates new connection per request

**`backend/app/db/manager.py` (Lines 42-76)**
```python
    @asynccontextmanager
    async def _get_connection(self):
        """Get a new database connection from the pool"""
        # In a real prod app, you'd want a global pool created on startup
        # For now, creating a connection per request is okay for low traffic,
        # but we should move to a pool pattern in main.py startup event later.

        conn = None
        max_retries = 3
        retry_delay = 1
        last_error = None

        for attempt in range(max_retries):
            try:
                conn = await asyncpg.connect(self.db_url)
                break
            except (OSError, asyncpg.PostgresError) as e:
                last_error = e
                logger.warning(
                    f"Database connection attempt {attempt + 1}/{max_retries} failed: {e}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (2**attempt))

        if conn is None:
            logger.error(f"Failed to connect to database after {max_retries} attempts")
            if last_error:
                raise last_error
            else:
                raise ConnectionError("Could not connect to database")

        try:
            yield conn
        finally:
            await conn.close()
```

### H8. CI uses unpinned third-party review action

**`.github/workflows/ai-review.yml` (Lines 21-24)**
```yaml
      - name: Run AI Reviewer
        uses: tegveer-work/ai-code-reviewer@main 
        with:
          gemini_api_key: ${{ secrets.GEMINI_API_KEY }}
```

## Medium Findings

### M1. Main app metadata, docs, and runtime ports are inconsistent

**`backend/app/main.py` (Lines 50-53)**
```python
        credits,
        payments,
    )

```

**`backend/app/main.py` (Lines 129-132)**
```python

@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "1.0.0"}
```

### M2. Calendar OAuth defaults wrong

**`backend/app/services/calendar/google_oauth.py` (Lines 36-39)**
```python

    def _get_frontend_settings_url(self) -> str:
        return os.getenv("CALENDAR_OAUTH_FRONTEND_SETTINGS_URL", "http://localhost:3000/settings")

```

### M3. Analytics product partly stubbed

**`frontend/src/lib/analytics.ts` (Lines 90-186)**
```typescript
  static async trackDailyActiveUser(): Promise<void> {}
  static async trackUserFirstLaunch(): Promise<void> {}
  
  static async isSessionActive(): Promise<boolean> {
    return true;
  }

  static async getPersistentUserId(): Promise<string> {
    let userId = typeof sessionStorage !== 'undefined' ? sessionStorage.getItem('meeting_copilot_user_id') : null;
    if (!userId) {
      userId = `user_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      if (typeof sessionStorage !== 'undefined') sessionStorage.setItem('meeting_copilot_user_id', userId);
    }
    return userId;
  }

  static async checkAndTrackFirstLaunch(): Promise<void> {}
  static async checkAndTrackDailyUsage(): Promise<void> {}

  static getCurrentUserId(): string | null {
    return this.currentUserId;
  }

  static async getPlatform(): Promise<string> {
    return 'Web';
  }

  static async getOSVersion(): Promise<string> {
    return 'Web';
  }

  static async getDeviceInfo(): Promise<DeviceInfo> {
    return {
      platform: 'Web',
      os_version: 'Web',
      architecture: 'unknown'
    };
  }

  static async calculateDaysSince(dateKey: string): Promise<number | null> {
    return 0;
  }

  static async updateMeetingCount(): Promise<void> {}
  static async getMeetingsCountToday(): Promise<number> { return 0; }
  static async hasUsedFeatureBefore(featureName: string): Promise<boolean> { return false; }
  static async markFeatureUsed(featureName: string): Promise<void> {}

  static async trackSessionStarted(sessionId: string): Promise<void> {}
  static async trackSessionEnded(sessionId: string): Promise<void> {}
  
  static async trackMeetingCompleted(meetingId: string, metrics: any): Promise<void> {
    this.track('meeting_completed', { meeting_id: meetingId, ...metrics });
  }

  static async trackFeatureUsedEnhanced(featureName: string, properties?: Record<string, any>): Promise<void> {
    this.track('feature_used', { feature: featureName, ...properties });
  }

  static async trackCopy(copyType: 'transcript' | 'summary', properties?: Record<string, any>): Promise<void> {
    this.track('content_copied', { type: copyType, ...properties });
  }

  static async trackMeetingStarted(meetingId: string, meetingTitle: string): Promise<void> {
    this.track('meeting_started', { meeting_id: meetingId, title_length: meetingTitle.length });
  }
  static async trackRecordingStarted(meetingId: string): Promise<void> {
    this.track('recording_started', { meeting_id: meetingId });
  }
  static async trackRecordingStopped(meetingId: string, durationSeconds?: number): Promise<void> {
    this.track('recording_stopped', { meeting_id: meetingId, duration: durationSeconds });
  }
  static async trackMeetingDeleted(meetingId: string): Promise<void> {
    this.track('meeting_deleted', { meeting_id: meetingId });
  }
  static async trackSettingsChanged(settingType: string, newValue: string): Promise<void> {
    this.track('settings_changed', { setting_type: settingType, new_value: newValue });
  }
  static async trackFeatureUsed(featureName: string): Promise<void> {
    this.track('feature_used', { feature: featureName });
  }
  
  static async trackPageView(pageName: string): Promise<void> {
    this.track('page_view', { page: pageName });
  }
  static async trackButtonClick(buttonName: string, location?: string): Promise<void> {
    this.track('button_click', { button: buttonName, location });
  }
  static async trackError(errorType: string, errorMessage: string): Promise<void> {
    this.track('error_occurred', { error_type: errorType, message: errorMessage });
  }
  static async trackAppStarted(): Promise<void> {
    this.track('app_started');
  }
  static async cleanup(): Promise<void> {}
  static reset(): void {}
  static async waitForInitialization(timeout: number = 5000): Promise<boolean> { return true; }
```

**`frontend/src/components/AnalyticsProvider.tsx` (Lines 46-125)**
```typescript
    const initAnalytics = async () => {
      // Load preference from localStorage
      const storedOptIn = localStorage.getItem('analyticsOptedIn');
      // Default to true if not set
      let analyticsOptedIn = true;
      if (storedOptIn !== null) {
        analyticsOptedIn = storedOptIn === 'true';
      } else {
        localStorage.setItem('analyticsOptedIn', 'true');
      }

      setIsAnalyticsOptedIn(analyticsOptedIn);

      if (analyticsOptedIn) {
        initAnalytics2();
      }
    }

    const initAnalytics2 = async () => {
      // Mark as initialized to prevent duplicates
      initialized.current = true;

      // Get persistent user ID or use session email if available
      const userId = (session?.user?.email) || await Analytics.getPersistentUserId();

      // Initialize analytics
      await Analytics.init(userId);

      // Get device info
      const deviceInfo = await Analytics.getDeviceInfo();

      // Store platform info if needed (skipping implementation details for local store)

      // Identify user
      await Analytics.identify(userId, {
        app_version: '0.1.1',
        platform: deviceInfo.platform,
        os_version: deviceInfo.os_version,
        architecture: deviceInfo.architecture,
        first_seen: new Date().toISOString(),
        user_agent: navigator.userAgent,
      });

      // Start analytics session
      const sessionId = await Analytics.startSession(userId);
      if (sessionId) {
        await Analytics.trackSessionStarted(sessionId);
      }

      // Check and track first launch
      await Analytics.checkAndTrackFirstLaunch();

      // Track app started
      await Analytics.trackAppStarted();

      // Check and track daily usage
      await Analytics.checkAndTrackDailyUsage();

      // Set up cleanup on page unload
      const handleBeforeUnload = async () => {
        if (sessionId) {
          await Analytics.trackSessionEnded(sessionId);
        }
        await Analytics.cleanup();
      };

      window.addEventListener('beforeunload', handleBeforeUnload);

      // Cleanup function
      return () => {
        window.removeEventListener('beforeunload', handleBeforeUnload);
        if (sessionId) {
          Analytics.trackSessionEnded(sessionId);
        }
        Analytics.cleanup();
      };
    };

    initAnalytics().catch(console.error);
  }, [session?.user?.email]); // Re-run if email loads but keep inner initialized.current check so we don't spam
```

### M4. Dead or legacy frontend scaffolding

**`frontend/package.json` (Lines 3-7)**
```json
    "version": "0.1.1",
    "private": true,
    "main": "electron/main.js",
    "scripts": {
        "dev": "next dev -p 3118",
```

**`frontend/src/lib/parakeet.ts` (Lines 1-29)**
```typescript
// This file is deprecated in the web version.
// Parakeet/NeMo models are local inference engines not supported in the web app.

export class ParakeetAPI {
  static async init(): Promise<void> {}
  static async getAvailableModels(): Promise<any[]> { return []; }
  static async loadModel(modelName: string): Promise<void> {}
  static async getCurrentModel(): Promise<string | null> { return null; }
  static async isModelLoaded(): Promise<boolean> { return false; }
  static async transcribeAudio(audioData: number[]): Promise<string> { return ""; }
  static async getModelsDirectory(): Promise<string> { return ""; }
  static async downloadModel(modelName: string): Promise<void> {}
  static async cancelDownload(modelName: string): Promise<void> {}
  static async deleteCorruptedModel(modelName: string): Promise<string> { return ""; }
  static async hasAvailableModels(): Promise<boolean> { return false; }
  static async validateModelReady(): Promise<string> { return ""; }
  static async openModelsFolder(): Promise<void> {}
}

export const PARAKEET_MODEL_CONFIGS = {};
export const MODEL_DISPLAY_CONFIG = {};

export function getModelDisplayName(name: string): string { return name; }
export function getModelDisplayInfo(name: string): any { return null; }
export function isQuantizedModel(name: string): boolean { return false; }
export function getModelPerformanceBadge(quantization: any): any { return { label: '', color: '' }; }
export function getStatusColor(status: any): string { return ''; }
export function formatFileSize(size: number): string { return ''; }
export function getModelIcon(accuracy: any): string { return ''; }
```

### M5. Test suite health overstated

**`backend/tests/unit/test_ai_host_participant.py` (Lines 1-6)**
```python
import pytest

from app.services import ai_participant as aip
from app.schemas.ai_participant import HostEventType, HostSuggestion


```

**`backend/app/schemas/ai_participant.py` (Lines 1-85)**
```python
from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


class GuardrailReason(str, Enum):
    AGENDA_DEVIATION = "agenda_deviation"
    NO_DECISION = "no_decision"
    UNRESOLVED_QUESTION = "unresolved_question"
    MISSING_CONTEXT_OR_REPEAT = "missing_context_or_repeat"


class GuardrailLLMOutput(BaseModel):
    intervention_required: bool = False
    reason: Optional[GuardrailReason] = None
    insight: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class GuardrailAlert(BaseModel):
    id: str
    reason: GuardrailReason
    insight: str
    confidence: float = Field(ge=0.0, le=1.0)
    timestamp: str


class HostRoleMode(str, Enum):
    FACILITATOR = "facilitator"
    ADVISOR = "advisor"
    CHAIRPERSON = "chairperson"


class HostSuggestion(BaseModel):
    id: str
    event_type: str
    title: str
    content: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    timestamp: str
    status: str = "suggested"
    source_excerpt: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class HostInterventionCard(BaseModel):
    id: str
    event_type: str
    headline: str
    body: str
    priority: str = "medium"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    timestamp: str
    linked_suggestion_id: Optional[str] = None


class MeetingHostState(BaseModel):
    meeting_id: str
    meeting_summary: Optional[str] = None
    agenda_progress: float = Field(default=0.0, ge=0.0, le=1.0)
    current_topic: Optional[str] = None
    unresolved_items: List[str] = Field(default_factory=list)
    suggested_items: List[HostSuggestion] = Field(default_factory=list)
    pinned_items: List[HostSuggestion] = Field(default_factory=list)
    dismissed_item_ids: List[str] = Field(default_factory=list)
    intervention_history: List[HostInterventionCard] = Field(default_factory=list)
    last_response_outcomes: List[str] = Field(default_factory=list)
    counters: Dict[str, int] = Field(default_factory=dict)
    updated_at: Optional[str] = None


class HostPolicyConfig(BaseModel):
    role_mode: HostRoleMode = HostRoleMode.FACILITATOR
    intervention_channel: str = "in_app_cards"
    min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    suggestion_cooldown_seconds: int = 60
    intervention_cooldown_seconds: int = 120
    max_suggestions_buffer: int = 30
    max_intervention_history: int = 30
    max_pinned_items: int = 100
    allow_interruptions: bool = False
    event_threshold_overrides: Dict[str, float] = Field(default_factory=dict)
    forbidden_actions: List[str] = Field(default_factory=list)
    escalation_rules: Dict[str, str] = Field(default_factory=dict)
    source: str = "system"
```

**`frontend/playwright.config.ts` (Lines 3-20)**
```typescript
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://localhost:3118",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
```

### M6. Backend dependency pins Python version issues

**`backend/requirements.txt` (Lines 29-32)**
```text
ten-vad
asyncpg==0.29.0
psycopg2-binary==2.9.9
google-cloud-storage>=2.14.0
```

### M7. Linting not in clean CI-ready state

**`frontend/package.json` (Lines 9-13)**
```json
        "export": "next export",
        "start": "next start -p 3118",
        "lint": "next lint",
        "test:e2e": "playwright test",
        "test:e2e:ui": "playwright test --ui"
```

**`frontend/eslint.config.mjs` (Lines 1-16)**
```javascript
import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
];

export default eslintConfig;
```

### M8. Search product presented as implemented while empty

**`backend/app/api/routers/chat.py` (Lines 380-391)**
```python
    Returns matching chunks with source citations.
    """
    try:
        # Fallback to empty results for now as VectorDB logic is not fully migrated
        results = []
        return {
            "status": "success",
            "query": request.query,
            "results": results,
            "total_indexed": 0,
        }

```

