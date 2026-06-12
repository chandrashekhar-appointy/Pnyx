/**
 * Pnyx popup — shows today's calendar events + recent meetings.
 * Communicates with background.js via chrome.storage for event states.
 */

const PNYX_ORIGIN = 'https://frontend-dev-350906.bifrost.saastack.site';
const CALENDAR_API = 'https://www.googleapis.com/calendar/v3';

// ─── View helpers ─────────────────────────────────────────────────────────────

function show(id)  { document.getElementById(id).classList.remove('hidden'); }
function hide(id)  { document.getElementById(id).classList.add('hidden'); }

function formatTime(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit', hour12: true });
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

// ─── Auth ─────────────────────────────────────────────────────────────────────

function getToken(interactive) {
  return new Promise((resolve, reject) => {
    chrome.identity.getAuthToken({ interactive }, (token) => {
      if (chrome.runtime.lastError || !token) reject(chrome.runtime.lastError);
      else resolve(token);
    });
  });
}

// ─── Calendar events ──────────────────────────────────────────────────────────

async function loadCalendarEvents(token) {
  const now = new Date();
  // Show events from 1 hour ago through end of day (so in-progress meetings show)
  const timeMin = new Date(now.getTime() - 60 * 60 * 1000).toISOString();
  const timeMax = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59).toISOString();

  const params = new URLSearchParams({
    timeMin, timeMax, singleEvents: 'true', orderBy: 'startTime',
  });

  const resp = await fetch(`${CALENDAR_API}/calendars/primary/events?${params}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) return [];
  const data = await resp.json();

  return (data.items || []).filter(e => {
    if (!e.start?.dateTime) return false;
    if (e.transparency === 'transparent') return false;
    const self = (e.attendees || []).find(a => a.self);
    if (self?.responseStatus === 'declined') return false;
    return true;
  });
}

// ─── Pnyx recent meetings ─────────────────────────────────────────────────────

async function loadRecentMeetings() {
  try {
    const resp = await fetch(`${PNYX_ORIGIN}/api/meetings?limit=5`, {
      credentials: 'include',
    });
    if (!resp.ok) return [];
    const data = await resp.json();
    return Array.isArray(data) ? data : (data.meetings || []);
  } catch {
    return [];
  }
}

// ─── Render calendar events ───────────────────────────────────────────────────

function renderEvents(events, eventStates) {
  const container = document.getElementById('eventsList');
  container.innerHTML = '';

  if (!events.length) {
    container.innerHTML = '<div class="no-events">No meetings today</div>';
    return;
  }

  const now = Date.now();

  events.forEach(event => {
    const startMs = new Date(event.start.dateTime).getTime();
    const endMs   = new Date(event.end.dateTime).getTime();
    const online  = isOnline(event);
    const state   = eventStates[event.id]?.state;
    const title   = event.summary || 'Meeting';
    const time    = formatTime(event.start.dateTime);

    const isLive  = now >= startMs && now <= endMs;
    const isStarted = state === 'STARTED';
    const isDone  = state === 'NOTES_READY' || now > endMs;

    const row = document.createElement('div');
    row.className = 'event-row';

    // Dot
    const dot = document.createElement('div');
    dot.className = `event-dot${isLive ? ' live' : isDone ? ' done' : ''}`;

    // Info
    const info = document.createElement('div');
    info.className = 'event-info';

    const timeEl = document.createElement('div');
    timeEl.className = 'event-time';
    timeEl.textContent = time;

    const titleEl = document.createElement('div');
    titleEl.className = 'event-title';
    titleEl.textContent = title;

    const metaEl = document.createElement('div');
    metaEl.className = 'event-meta';
    if (online) {
      metaEl.textContent = 'Online meeting';
    } else if (event.location) {
      metaEl.textContent = event.location;
    } else {
      metaEl.textContent = 'In-room';
    }

    info.appendChild(timeEl);
    info.appendChild(titleEl);
    info.appendChild(metaEl);

    // Action
    const actions = document.createElement('div');
    actions.className = 'event-actions';

    if (state === 'NOTES_READY') {
      const badge = document.createElement('span');
      badge.className = 'badge-done';
      badge.textContent = 'Notes ready';
      badge.style.cursor = 'pointer';
      badge.addEventListener('click', () => {
        const meetingId = eventStates[event.id]?.meetingId;
        const url = meetingId
          ? `${PNYX_ORIGIN}/meeting-details?id=${meetingId}`
          : PNYX_ORIGIN;
        chrome.tabs.create({ url });
        window.close();
      });
      actions.appendChild(badge);
    } else if (isStarted) {
      const badge = document.createElement('span');
      badge.className = 'badge-done';
      badge.textContent = '● Recording';
      actions.appendChild(badge);
    } else if (online) {
      const badge = document.createElement('span');
      badge.className = 'badge-bot';
      badge.textContent = 'Bot';
      actions.appendChild(badge);
    } else if (!isDone) {
      const btn = document.createElement('button');
      btn.className = 'btn-start';
      btn.textContent = isLive ? '🎙 Start' : '🎙 Record';
      btn.addEventListener('click', () => {
        const params = new URLSearchParams({
          autoStart: 'true',
          source: 'extension',
          meetingTitle: title,
          calendar_event_id: event.id,
        });
        chrome.tabs.create({ url: `${PNYX_ORIGIN}/?${params}` });
        window.close();
      });
      actions.appendChild(btn);
    }

    row.appendChild(dot);
    row.appendChild(info);
    row.appendChild(actions);
    container.appendChild(row);
  });
}

// ─── Render recent Pnyx meetings ──────────────────────────────────────────────

function renderRecentMeetings(meetings) {
  const container = document.getElementById('recentList');
  container.innerHTML = '';

  if (!meetings.length) {
    container.innerHTML = '<div class="no-events">No recent meetings</div>';
    return;
  }

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
  try {
    await getToken(true);
    init();
  } catch {
    // User cancelled
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

  const [events, recentMeetings, { eventStates = {} }] = await Promise.all([
    loadCalendarEvents(token),
    loadRecentMeetings(),
    chrome.storage.local.get('eventStates'),
  ]);

  hide('loadingView');
  show('mainView');

  renderEvents(events, eventStates);
  renderRecentMeetings(recentMeetings);
}

init();
