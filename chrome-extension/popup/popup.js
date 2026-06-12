/**
 * Pnyx popup — shows today's Google Calendar events with one-click Start.
 *
 * The popup is self-contained: it reads Google Calendar directly and opens
 * frontend URLs to start recording. It does NOT call the Pnyx backend.
 *
 * "Recent meetings" requires an authenticated backend channel and is therefore
 * hidden unless BACKEND_ORIGIN is configured (see background.js / the plan doc).
 */

const PNYX_ORIGIN = 'https://frontend-dev-350906.bifrost.saastack.site';
const CALENDAR_API = 'https://www.googleapis.com/calendar/v3';
const BACKEND_ORIGIN = ''; // keep in sync with background.js; '' = backend features off

function backendEnabled() {
  return typeof BACKEND_ORIGIN === 'string' && BACKEND_ORIGIN.length > 0;
}

// ─── View helpers ─────────────────────────────────────────────────────────────

function show(id) { document.getElementById(id).classList.remove('hidden'); }
function hide(id) { document.getElementById(id).classList.add('hidden'); }

function formatTime(iso) {
  return new Date(iso).toLocaleTimeString('en-IN', {
    hour: 'numeric', minute: '2-digit', hour12: true,
  });
}

function formatRelative(iso) {
  const d = new Date(iso);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const day   = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diff  = Math.round((today - day) / 86400000);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Yesterday';
  if (diff < 7)  return d.toLocaleDateString('en-IN', { weekday: 'short' });
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

function isOnline(event) {
  if (event.conferenceData) return true;
  const text = `${event.description || ''} ${event.location || ''}`.toLowerCase();
  return (
    text.includes('zoom.us') ||
    text.includes('meet.google.com') ||
    text.includes('teams.microsoft.com') ||
    text.includes('webex.com')
  );
}

// ─── Auth (Google Calendar only) ──────────────────────────────────────────────

function getToken(interactive) {
  return new Promise((resolve, reject) => {
    chrome.identity.getAuthToken({ interactive }, (token) => {
      if (chrome.runtime.lastError || !token) reject(chrome.runtime.lastError);
      else resolve(token);
    });
  });
}

async function loadCalendarEvents(token) {
  const now = new Date();
  // Whole of today (local) through end of day, so past + upcoming meetings show.
  const timeMin = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0).toISOString();
  const timeMax = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59).toISOString();

  const params = new URLSearchParams({
    timeMin, timeMax, singleEvents: 'true', orderBy: 'startTime',
  });

  const resp = await fetch(`${CALENDAR_API}/calendars/primary/events?${params}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (resp.status === 401) {
    chrome.identity.removeCachedAuthToken({ token });
    throw new Error('token expired');
  }
  if (!resp.ok) {
    // Surface the real API failure (403 = Calendar API not enabled / scope not
    // granted; etc.) instead of silently showing "no meetings".
    let detail = '';
    try { detail = (await resp.json())?.error?.message || ''; } catch { /* ignore */ }
    const err = new Error(`Calendar API ${resp.status}${detail ? `: ${detail}` : ''}`);
    err.apiStatus = resp.status;
    throw err;
  }
  const data = await resp.json();

  const raw = data.items || [];
  const filtered = raw.filter(e => {
    if (!e.start?.dateTime) return false;               // all-day event
    if (e.transparency === 'transparent') return false; // marked Free
    const self = (e.attendees || []).find(a => a.self);
    if (self?.responseStatus === 'declined') return false;
    // Only real meetings: must have at least one OTHER attendee. This drops
    // solo placeholders like "Lunch", focus blocks, and personal reminders.
    const others = (e.attendees || []).filter(a => !a.self);
    if (others.length === 0) return false;
    return true;
  });
  // Attach diagnostics so the UI can distinguish "API returned nothing" from
  // "everything got filtered out (all-day / declined / free)".
  filtered.rawCount = raw.length;
  return filtered;
}

// ─── Render calendar events ───────────────────────────────────────────────────

function renderEvents(events, eventStates) {
  const container = document.getElementById('eventsList');
  container.innerHTML = '';

  if (!events.length) {
    const raw = events.rawCount || 0;
    const msg = raw > 0
      ? `${raw} event${raw > 1 ? 's' : ''} today, but all are all-day, declined, or marked Free`
      : 'No timed meetings on your primary calendar today';
    container.innerHTML = `<div class="no-events">${msg}</div>`;
    return;
  }

  const now = Date.now();

  events.forEach(event => {
    const startMs = new Date(event.start.dateTime).getTime();
    const endMs   = new Date(event.end.dateTime).getTime();
    const online  = isOnline(event);
    const state   = eventStates[event.id]?.state;
    const title   = event.summary || 'Meeting';

    const isLive    = now >= startMs && now <= endMs;
    const isStarted = state === 'STARTED';
    const isDone    = state === 'NOTES_READY' || now > endMs;

    const row = document.createElement('div');
    row.className = 'event-row';

    const dot = document.createElement('div');
    dot.className = `event-dot${isLive ? ' live' : isDone ? ' done' : ''}`;

    const info = document.createElement('div');
    info.className = 'event-info';

    const timeEl = document.createElement('div');
    timeEl.className = 'event-time';
    timeEl.textContent = formatTime(event.start.dateTime);

    const titleEl = document.createElement('div');
    titleEl.className = 'event-title';
    titleEl.textContent = title;

    const metaEl = document.createElement('div');
    metaEl.className = 'event-meta';
    metaEl.textContent = online
      ? 'Online · record here only if the bot didn’t join'
      : (event.location || 'In-room');

    info.appendChild(timeEl);
    info.appendChild(titleEl);
    info.appendChild(metaEl);

    const actions = document.createElement('div');
    actions.className = 'event-actions';

    if (isStarted) {
      const badge = document.createElement('span');
      badge.className = 'badge-done';
      badge.textContent = '● Recording';
      actions.appendChild(badge);
    } else if (!isDone) {
      // Both in-room and online get a Start button. For online meetings this is
      // a manual fallback — the bot normally records, but the extension can't
      // verify the bot was accepted (needs the backend channel, Phase 4), so we
      // never falsely claim a bot is present.
      const btn = document.createElement('button');
      btn.className = online ? 'btn-start btn-start-muted' : 'btn-start';
      btn.textContent = isLive ? '🎙 Start' : '🎙 Record';
      btn.addEventListener('click', () => startRecording(event.id, title));
      actions.appendChild(btn);
    }

    row.appendChild(dot);
    row.appendChild(info);
    row.appendChild(actions);
    container.appendChild(row);
  });
}

function startRecording(eventId, title) {
  const params = new URLSearchParams({
    autoStart: 'true',
    source: 'extension',
    meetingTitle: title,
    calendar_event_id: eventId,
  });
  // Tell the background worker this event is now being recorded so it stops nagging.
  chrome.runtime.sendMessage({ type: 'markStarted', eventId });
  chrome.tabs.create({ url: `${PNYX_ORIGIN}/?${params}` });
  window.close();
}

// ─── Recent meetings (optional, backend-gated) ────────────────────────────────

async function loadRecentMeetings() {
  // Requires an authenticated backend channel; disabled by default.
  if (!backendEnabled()) return null;
  // Stub until the backend-auth spike is done (see CHROME_EXTENSION_PLAN.md).
  return null;
}

function renderRecentMeetings(meetings) {
  const section = document.getElementById('recentSection');
  const divider = document.getElementById('recentDivider');

  // Hide the whole section gracefully when there's no backend data.
  if (!meetings || !meetings.length) {
    section.classList.add('hidden');
    if (divider) divider.classList.add('hidden');
    return;
  }

  const container = document.getElementById('recentList');
  container.innerHTML = '';
  meetings.slice(0, 4).forEach(m => {
    const row = document.createElement('div');
    row.className = 'recent-row';
    row.title = 'Open meeting notes';
    row.addEventListener('click', () => {
      chrome.tabs.create({ url: `${PNYX_ORIGIN}/meeting-details?id=${m.id}` });
      window.close();
    });

    const title = document.createElement('div');
    title.className = 'recent-title';
    title.textContent = m.title || m.meeting_title || 'Untitled meeting';

    const date = document.createElement('div');
    date.className = 'recent-date';
    date.textContent = formatRelative(m.created_at || m.start_time);

    const arrow = document.createElement('div');
    arrow.className = 'recent-arrow';
    arrow.textContent = '→';

    row.appendChild(title);
    row.appendChild(date);
    row.appendChild(arrow);
    container.appendChild(row);
  });
}

// ─── Init ─────────────────────────────────────────────────────────────────────

document.getElementById('openApp').addEventListener('click', (e) => {
  e.preventDefault();
  chrome.tabs.create({ url: PNYX_ORIGIN });
  window.close();
});

document.getElementById('connectBtn').addEventListener('click', async () => {
  const errEl = document.getElementById('connectError');
  if (errEl) { errEl.textContent = ''; errEl.classList.add('hidden'); }
  try {
    await getToken(true);
    init();
  } catch (e) {
    // Surface the real reason instead of failing silently. The most common
    // cause is a bad oauth2.client_id in manifest.json or an Extension ID that
    // doesn't match the Chrome App OAuth client in Google Cloud Console.
    const msg = (chrome.runtime.lastError && chrome.runtime.lastError.message)
      || (e && e.message) || 'Could not connect. Check the OAuth client setup.';
    console.error('[Pnyx] getAuthToken failed:', msg);
    if (errEl) { errEl.textContent = msg; errEl.classList.remove('hidden'); }
  }
});

async function init() {
  hide('connectView');
  hide('mainView');
  show('loadingView');

  let token;
  try {
    token = await getToken(false);
  } catch {
    hide('loadingView');
    show('connectView');
    return;
  }

  let events = [];
  try {
    events = await loadCalendarEvents(token);
  } catch (e) {
    if (e?.message === 'token expired') {
      // Stale token — bounce back to the connect screen.
      hide('loadingView');
      show('connectView');
      return;
    }
    // A real API error (403 Calendar API disabled, scope not granted, etc.).
    // Show it on the connect screen so it's actionable instead of "no meetings".
    hide('loadingView');
    show('connectView');
    const errEl = document.getElementById('connectError');
    if (errEl) {
      errEl.textContent = e?.message || 'Could not load calendar';
      errEl.classList.remove('hidden');
    }
    console.error('[Pnyx] loadCalendarEvents failed:', e);
    return;
  }

  const [{ eventStates = {} }, recentMeetings] = await Promise.all([
    chrome.storage.local.get('eventStates'),
    loadRecentMeetings(),
  ]);

  hide('loadingView');
  show('mainView');

  renderEvents(events, eventStates);
  renderRecentMeetings(recentMeetings);

  // Trigger a background re-check so states reflect the latest the moment the
  // user opens the popup.
  chrome.runtime.sendMessage({ type: 'runCheck' });
}

init();
