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
// Backend base URL. Local dev = http://localhost:5167. Must also be listed in
// manifest host_permissions. '' = backend features off (graceful no-op).
const BACKEND_ORIGIN = 'http://localhost:5167';

function backendEnabled() {
  return typeof BACKEND_ORIGIN === 'string' && BACKEND_ORIGIN.length > 0;
}

// ─── Meeting-URL matching (calendar event ↔ active bot session) ───────────────

function normalizeMeetingUrl(url) {
  if (!url) return '';
  return String(url)
    .toLowerCase()
    .replace(/^https?:\/\//, '')
    .replace(/^www\./, '')
    .split('?')[0]
    .replace(/\/+$/, '');
}

function eventMeetingUrl(event) {
  if (event.hangoutLink) return event.hangoutLink;
  const ep = (event.conferenceData?.entryPoints || []).find(e => e.uri);
  if (ep) return ep.uri;
  const text = `${event.location || ''} ${event.description || ''}`;
  const m = text.match(/https?:\/\/[^\s<>"]+/);
  return m ? m[0] : '';
}

function urlsMatch(a, b) {
  const na = normalizeMeetingUrl(a);
  const nb = normalizeMeetingUrl(b);
  if (!na || !nb) return false;
  return na === nb || na.includes(nb) || nb.includes(na);
}

/**
 * Classify an online event against active bot sessions:
 *   'recording' → bot is in the call → suppress the manual Start
 *   'pending'   → bot dispatched (requesting/joining) but not in yet → fallback OK
 *   'absent'    → no bot for this meeting → fallback OK
 */
function classifyBot(event, botSessions) {
  const url = eventMeetingUrl(event);
  const match = (botSessions || []).find(s => urlsMatch(url, s.meeting_url));
  if (!match) return 'absent';
  return match.status === 'recording' ? 'recording' : 'pending';
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

function renderEvents(events, eventStates, botSessions) {
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

    // For online meetings, ask the backend whether the Pnyx bot is actually in
    // the call. Only 'recording' counts as "the bot has it handled".
    const botClass = online ? classifyBot(event, botSessions) : 'absent';

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
    if (!online) {
      metaEl.textContent = event.location || 'In-room';
    } else if (botClass === 'recording') {
      metaEl.textContent = 'Online · Pnyx bot is recording';
    } else if (botClass === 'pending') {
      metaEl.textContent = 'Online · bot is joining…';
    } else {
      metaEl.textContent = 'Online · start here only if the bot didn’t join';
    }

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
    } else if (online && botClass === 'recording') {
      // Bot is genuinely in the call — no manual Start, no false "Bot" claim.
      const badge = document.createElement('span');
      badge.className = 'badge-bot';
      badge.textContent = '● Bot';
      actions.appendChild(badge);
    } else if (!isDone) {
      // In-room, or online where the bot is absent/pending → manual Start.
      // Online uses a muted style since the bot is the primary path.
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

// ─── Backend calls (authenticated with the Google access token) ───────────────

async function backendFetch(path, token) {
  const resp = await fetch(`${BACKEND_ORIGIN}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`backend ${resp.status}`);
  return resp.json();
}

async function loadActiveBotSessions(token) {
  if (!backendEnabled()) return [];
  try {
    const data = await backendFetch('/api/meetings/active-bot-sessions', token);
    return Array.isArray(data) ? data : [];
  } catch (e) {
    console.warn('[Pnyx] active-bot-sessions failed:', e?.message);
    return []; // graceful: fall back to the manual-fallback Start button
  }
}

async function loadRecentMeetings(token) {
  if (!backendEnabled()) return null;
  try {
    const data = await backendFetch('/get-meetings', token);
    return Array.isArray(data) ? data : null;
  } catch (e) {
    console.warn('[Pnyx] get-meetings failed:', e?.message);
    return null;
  }
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
    // /get-meetings returns {id, title} only — no date. Show it when present.
    date.textContent = (m.created_at || m.start_time)
      ? formatRelative(m.created_at || m.start_time)
      : '';

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

  const hasOnline = events.some(isOnline);
  const [{ eventStates = {} }, botSessions, recentMeetings] = await Promise.all([
    chrome.storage.local.get('eventStates'),
    hasOnline ? loadActiveBotSessions(token) : Promise.resolve([]),
    loadRecentMeetings(token),
  ]);

  hide('loadingView');
  show('mainView');

  renderEvents(events, eventStates, botSessions);
  renderRecentMeetings(recentMeetings);

  // Trigger a background re-check so states reflect the latest the moment the
  // user opens the popup.
  chrome.runtime.sendMessage({ type: 'runCheck' });
}

init();
