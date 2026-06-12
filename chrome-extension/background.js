/**
 * Pnyx Chrome Extension — Background Service Worker
 *
 * Responsibilities:
 *  1. Poll Google Calendar every 10 min for today's events
 *  2. Run the notification state machine per event
 *  3. Fire smart notifications (max 3 per meeting, never after Skip)
 *  4. Suppress notifications when Pnyx is already recording
 *  5. Handle all notification button clicks
 */

const PNYX_ORIGIN = 'https://frontend-dev-350906.bifrost.saastack.site';
const CALENDAR_API = 'https://www.googleapis.com/calendar/v3';
const CHECK_INTERVAL_MINUTES = 10;

// ─── State constants ──────────────────────────────────────────────────────────

const S = {
  PENDING:        'PENDING',
  REMINDED_T10:   'REMINDED_T10',
  REMINDED_START: 'REMINDED_START',
  REMINDED_FINAL: 'REMINDED_FINAL',
  STARTED:        'STARTED',
  DISMISSED:      'DISMISSED',
  EXPIRED:        'EXPIRED',
  NOTES_READY:    'NOTES_READY',
};

// ─── Storage helpers ──────────────────────────────────────────────────────────

async function getEventStates() {
  const { eventStates = {} } = await chrome.storage.local.get('eventStates');
  return eventStates;
}

async function setEventState(eventId, patch) {
  const states = await getEventStates();
  states[eventId] = { ...(states[eventId] || {}), ...patch };
  await chrome.storage.local.set({ eventStates: states });
}

async function getNotifMap() {
  const { notifMap = {} } = await chrome.storage.local.get('notifMap');
  return notifMap;
}

async function setNotifMap(map) {
  await chrome.storage.local.set({ notifMap: map });
}

// Remove events older than 24 h to prevent unbounded storage growth
async function pruneOldEvents() {
  const states = await getEventStates();
  const cutoff = Math.floor((Date.now() - 86400 * 1000) / 1000);
  const pruned = {};
  for (const [id, data] of Object.entries(states)) {
    if ((data.eventEnd || 0) > cutoff) pruned[id] = data;
  }
  await chrome.storage.local.set({ eventStates: pruned });
}

// ─── Google Calendar ──────────────────────────────────────────────────────────

function getAuthToken(interactive) {
  return new Promise((resolve, reject) => {
    chrome.identity.getAuthToken({ interactive }, (token) => {
      if (chrome.runtime.lastError || !token) {
        reject(chrome.runtime.lastError || new Error('No token'));
      } else {
        resolve(token);
      }
    });
  });
}

async function fetchTodaysEvents() {
  let token;
  try {
    token = await getAuthToken(false);
  } catch {
    return []; // Not authenticated yet — popup will prompt
  }

  const now = new Date();
  const timeMin = new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString();
  const timeMax = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59).toISOString();

  const params = new URLSearchParams({
    timeMin,
    timeMax,
    singleEvents: 'true',
    orderBy: 'startTime',
  });

  try {
    const resp = await fetch(`${CALENDAR_API}/calendars/primary/events?${params}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (resp.status === 401) {
      // Token expired — clear it so next call triggers interactive flow
      chrome.identity.removeCachedAuthToken({ token });
      return [];
    }
    if (!resp.ok) return [];
    const data = await resp.json();
    return (data.items || []).filter(shouldProcess);
  } catch {
    return [];
  }
}

function shouldProcess(event) {
  if (!event.start?.dateTime) return false; // all-day
  if (event.transparency === 'transparent') return false; // Free/busy = free
  const self = (event.attendees || []).find(a => a.self);
  if (self?.responseStatus === 'declined') return false;
  // Skip solo blocks (no other attendees)
  const others = (event.attendees || []).filter(a => !a.self);
  if (others.length === 0) return false;
  return true;
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

// ─── Pnyx activity detection ──────────────────────────────────────────────────

async function findActivePnyxMeeting(eventStartMs) {
  try {
    const resp = await fetch(`${PNYX_ORIGIN}/api/meetings?active=true&limit=5`, {
      credentials: 'include',
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    const meetings = Array.isArray(data) ? data : (data.meetings || []);
    const lo = eventStartMs - 15 * 60 * 1000;
    const hi = eventStartMs + 15 * 60 * 1000;
    return meetings.find(m => {
      const t = new Date(m.created_at || m.start_time).getTime();
      return t >= lo && t <= hi;
    }) || null;
  } catch {
    return null;
  }
}

async function isPnyxTabOpen() {
  try {
    const tabs = await chrome.tabs.query({ url: `${PNYX_ORIGIN}/*` });
    return tabs.length > 0;
  } catch {
    return false;
  }
}

// ─── Notification helpers ─────────────────────────────────────────────────────

const NOTIF_CONFIGS = {
  T10: (title, location) => ({
    title: `${title} · 10 minutes`,
    message: `In-room meeting${location ? ` · ${location}` : ''}`,
    buttons: [{ title: '🎙 Start Recording' }, { title: 'Remind at start time' }],
    requireInteraction: true,
  }),
  START: (title) => ({
    title: `${title} just started`,
    message: 'Start recording now',
    buttons: [{ title: '🎙 Start Now' }, { title: 'Skip this meeting' }],
    requireInteraction: true,
  }),
  FINAL: (title) => ({
    title: `${title} · 5 min in`,
    message: 'Last reminder to start recording',
    buttons: [{ title: 'Start now' }, { title: "Don't remind me" }],
    requireInteraction: false,
  }),
  BOT_MISSING: (title) => ({
    title: `${title} · Online meeting`,
    message: 'Pnyx bot is not in this meeting',
    buttons: [{ title: 'Add Pnyx Bot' }, { title: 'Skip' }],
    requireInteraction: false,
  }),
  NOTES_READY: (title) => ({
    title: `✓ ${title} — Notes ready`,
    message: 'AI notes, transcript and action items waiting',
    buttons: [{ title: 'View Notes' }],
    requireInteraction: false,
  }),
};

async function fireNotification(eventId, event, type) {
  const title = event.summary || 'Meeting';
  const location = event.location || '';
  const cfg = NOTIF_CONFIGS[type](title, location);
  const notifId = `pnyx_${eventId}_${type}_${Date.now()}`;

  await chrome.notifications.create(notifId, {
    type: 'basic',
    iconUrl: chrome.runtime.getURL('icons/48.png'),
    ...cfg,
    priority: type === 'NOTES_READY' ? 0 : 2,
  });

  const map = await getNotifMap();
  map[notifId] = { eventId, type, eventTitle: title };
  await setNotifMap(map);
}

// ─── Main event processing loop ───────────────────────────────────────────────

async function processEvents() {
  await pruneOldEvents();

  const [events, states] = await Promise.all([
    fetchTodaysEvents(),
    getEventStates(),
  ]);

  const now = Date.now();

  for (const event of events) {
    const id = event.id;
    const startMs = new Date(event.start.dateTime).getTime();
    const endMs   = new Date(event.end.dateTime).getTime();
    const msBeforeStart = startMs - now;
    const msAfterStart  = now - startMs;
    const online = isOnline(event);
    const state  = states[id]?.state || S.PENDING;

    // Initialise storage entry on first encounter
    if (!states[id]) {
      await setEventState(id, {
        state: S.PENDING,
        eventStart: Math.floor(startMs / 1000),
        eventEnd:   Math.floor(endMs   / 1000),
        isOnline:   online,
        title:      event.summary || 'Meeting',
      });
    }

    // Terminal states — nothing more to do
    if ([S.DISMISSED, S.EXPIRED, S.NOTES_READY].includes(state)) continue;

    // Detect if Pnyx already recording for this event
    if (state !== S.STARTED) {
      const activeMeeting = await findActivePnyxMeeting(startMs);
      if (activeMeeting) {
        await setEventState(id, { state: S.STARTED, meetingId: activeMeeting.id });
        continue;
      }
    }

    // Notes ready — fires once after meeting ends (only if recording was started)
    if (state === S.STARTED && now > endMs + 2 * 60 * 1000) {
      await fireNotification(id, event, 'NOTES_READY');
      await setEventState(id, { state: S.NOTES_READY });
      continue;
    }

    if (state === S.STARTED) continue;

    // Online meetings: suppress unless bot check reveals it's missing
    // Phase 3 will add the Recall bot API check here
    if (online) continue;

    // ── In-room notification ladder ──────────────────────────────────────────

    // T-10: between 11 min and 9 min before start
    if (
      msBeforeStart <= 11 * 60 * 1000 &&
      msBeforeStart >   9 * 60 * 1000 &&
      state === S.PENDING
    ) {
      if (!(await isPnyxTabOpen())) {
        await fireNotification(id, event, 'T10');
        await setEventState(id, { state: S.REMINDED_T10 });
      }
      continue;
    }

    // T+0: within first 2 min of start
    if (
      msAfterStart >= 0 &&
      msAfterStart <= 2 * 60 * 1000 &&
      [S.PENDING, S.REMINDED_T10].includes(state)
    ) {
      await fireNotification(id, event, 'START');
      await setEventState(id, { state: S.REMINDED_START });
      continue;
    }

    // T+5: between 4 and 6 min after start (last chance)
    if (
      msAfterStart >= 4 * 60 * 1000 &&
      msAfterStart <= 6 * 60 * 1000 &&
      [S.PENDING, S.REMINDED_T10, S.REMINDED_START].includes(state)
    ) {
      await fireNotification(id, event, 'FINAL');
      await setEventState(id, { state: S.REMINDED_FINAL });
      continue;
    }

    // Give up silently after T+10
    if (msAfterStart > 10 * 60 * 1000) {
      await setEventState(id, { state: S.EXPIRED });
    }
  }
}

// ─── Notification click handlers ──────────────────────────────────────────────

chrome.notifications.onButtonClicked.addListener(async (notifId, buttonIndex) => {
  chrome.notifications.clear(notifId);
  const map = await getNotifMap();
  const notif = map[notifId];
  if (!notif) return;

  const { eventId, type, eventTitle } = notif;

  if (type === 'NOTES_READY') {
    const states = await getEventStates();
    const meetingId = states[eventId]?.meetingId;
    const url = meetingId
      ? `${PNYX_ORIGIN}/meeting-details?id=${meetingId}`
      : PNYX_ORIGIN;
    chrome.tabs.create({ url });
    return;
  }

  if (buttonIndex === 0) {
    // Primary action: start recording (or add bot)
    if (type === 'BOT_MISSING') {
      chrome.tabs.create({ url: PNYX_ORIGIN });
    } else {
      const url = buildStartUrl(eventId, eventTitle);
      chrome.tabs.create({ url });
    }
    await setEventState(eventId, { state: S.STARTED });
  } else {
    // Secondary action
    if (type === 'T10') {
      // "Remind at start time" — state stays REMINDED_T10, will fire START
    } else {
      // "Skip" / "Don't remind me" — hard dismiss
      await setEventState(eventId, { state: S.DISMISSED });
    }
  }
});

// Clicking the notification body (not a button) = start recording
chrome.notifications.onClicked.addListener(async (notifId) => {
  chrome.notifications.clear(notifId);
  const map = await getNotifMap();
  const notif = map[notifId];
  if (!notif) return;

  const { eventId, type, eventTitle } = notif;

  if (type === 'NOTES_READY') {
    const states = await getEventStates();
    const meetingId = states[eventId]?.meetingId;
    chrome.tabs.create({
      url: meetingId ? `${PNYX_ORIGIN}/meeting-details?id=${meetingId}` : PNYX_ORIGIN,
    });
  } else {
    chrome.tabs.create({ url: buildStartUrl(eventId, eventTitle) });
    await setEventState(eventId, { state: S.STARTED });
  }
});

function buildStartUrl(calendarEventId, title) {
  const params = new URLSearchParams({
    autoStart: 'true',
    source: 'extension',
    meetingTitle: title || '',
    calendar_event_id: calendarEventId || '',
  });
  return `${PNYX_ORIGIN}/?${params}`;
}

// ─── Alarm + lifecycle ────────────────────────────────────────────────────────

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === 'pnyx_check') await processEvents();
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create('pnyx_check', {
    delayInMinutes: 0,
    periodInMinutes: CHECK_INTERVAL_MINUTES,
  });
});

// Also run on service worker wake-up (covers cases where alarm fires
// while the worker was sleeping — Chrome restarts it automatically)
processEvents();
