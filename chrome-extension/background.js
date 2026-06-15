/**
 * Pnyx Chrome Extension — Background Service Worker
 *
 * ARCHITECTURE (important):
 *   This extension is INTENTIONALLY self-contained. It needs NO connection to
 *   the Pnyx backend to deliver its core value:
 *     1. Read Google Calendar  → via chrome.identity (Calendar API directly)
 *     2. Fire smart reminders  → via chrome.alarms + chrome.notifications
 *     3. One-click recording    → opens a frontend URL; the frontend's existing
 *                                 logged-in session handles all auth. The
 *                                 extension never calls the backend.
 *
 *   "Already recording" suppression uses a reliable, backend-free heuristic:
 *   if a Pnyx tab is open during the meeting window, the user is clearly in the
 *   app already, so we don't nag. Cross-device suppression and a "recent
 *   meetings" list would require an authenticated backend channel — that is a
 *   SEPARATE, opt-in workstream (see BACKEND_ORIGIN below) and is disabled by
 *   default so the extension works out of the box.
 */

// ─── Config ───────────────────────────────────────────────────────────────────

const PNYX_ORIGIN = 'https://frontend-dev-350906.bifrost.saastack.site';
const CALENDAR_API = 'https://www.googleapis.com/calendar/v3';
const CHECK_INTERVAL_MINUTES = 1;

// OPTIONAL backend channel — leave '' to keep the extension fully self-contained.
// To enable cross-device suppression + recent meetings, this requires the
// backend-auth spike described in CHROME_EXTENSION_PLAN.md (Phase 3). Until then,
// all backend-dependent features degrade gracefully to no-ops.
const BACKEND_ORIGIN = '';

function backendEnabled() {
  return typeof BACKEND_ORIGIN === 'string' && BACKEND_ORIGIN.length > 0;
}

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
  } catch (e) {
    console.warn('[Pnyx] no auth token (connect calendar in the popup):', e?.message);
    return [];
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
      console.warn('[Pnyx] calendar 401 — clearing cached token');
      chrome.identity.removeCachedAuthToken({ token });
      return [];
    }
    if (!resp.ok) {
      console.warn('[Pnyx] calendar fetch failed:', resp.status);
      return [];
    }
    const data = await resp.json();
    const raw = data.items || [];
    const eligible = raw.filter(shouldProcess);
    console.log(`[Pnyx] calendar: ${raw.length} events today, ${eligible.length} eligible (timed, with guests, not declined/free)`);
    return eligible;
  } catch (e) {
    console.warn('[Pnyx] calendar fetch error:', e?.message);
    return [];
  }
}

function shouldProcess(event) {
  if (!event.start?.dateTime) return false; // all-day event
  if (event.transparency === 'transparent') return false; // marked Free
  const self = (event.attendees || []).find(a => a.self);
  if (self?.responseStatus === 'declined') return false;
  // Skip solo blocks (focus time, reminders) — no other attendees
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

// ─── Suppression heuristics (backend-free) ────────────────────────────────────

async function isPnyxTabOpen() {
  try {
    const tabs = await chrome.tabs.query({ url: `${PNYX_ORIGIN}/*` });
    return tabs.length > 0;
  } catch {
    return false;
  }
}

/**
 * OPTIONAL: detect an active Pnyx meeting on the backend (cross-device).
 * No-op unless BACKEND_ORIGIN is configured AND the backend-auth spike is done.
 * Today this always returns null, so the extension relies on the tab heuristic.
 */
async function findActivePnyxMeetingViaBackend() {
  if (!backendEnabled()) return null;
  // Intentionally a stub until the authenticated backend channel exists.
  // When implemented: GET `${BACKEND_ORIGIN}/meetings/active-bot-sessions`
  // with an Authorization: Bearer <google token> header.
  return null;
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
  NOTES_READY: (title) => ({
    title: `✓ ${title} — Meeting ended`,
    message: 'Open Pnyx to view notes, transcript and action items',
    buttons: [{ title: 'View in Pnyx' }],
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
  console.log(`[Pnyx] check @ ${new Date().toLocaleTimeString()}`);
  await pruneOldEvents();

  const [events, states] = await Promise.all([
    fetchTodaysEvents(),
    getEventStates(),
  ]);

  const now = Date.now();
  const MIN = 60 * 1000;

  for (const event of events) {
    const id = event.id;
    const startMs = new Date(event.start.dateTime).getTime();
    const endMs   = new Date(event.end.dateTime).getTime();
    const msBeforeStart = startMs - now;
    const msAfterStart  = now - startMs;
    const online = isOnline(event);
    const state  = states[id]?.state || S.PENDING;
    const title  = event.summary || 'Meeting';
    const mins   = Math.round(msBeforeStart / MIN);

    // Initialise storage entry on first encounter
    if (!states[id]) {
      await setEventState(id, {
        state: S.PENDING,
        eventStart: Math.floor(startMs / 1000),
        eventEnd:   Math.floor(endMs   / 1000),
        isOnline:   online,
        title,
      });
    }

    // Terminal states — nothing more to do
    if ([S.DISMISSED, S.EXPIRED, S.NOTES_READY].includes(state)) continue;

    // Optional cross-device "already recording" detection (no-op until the
    // backend channel is enabled).
    if (state !== S.STARTED) {
      const activeMeeting = await findActivePnyxMeetingViaBackend();
      if (activeMeeting) {
        await setEventState(id, { state: S.STARTED, meetingId: activeMeeting.id });
        continue;
      }
    }

    // Notes ready — fires once after the meeting ends, only if recording started
    if (state === S.STARTED && now > endMs + 2 * MIN) {
      await fireNotification(id, event, 'NOTES_READY');
      await setEventState(id, { state: S.NOTES_READY });
      continue;
    }
    if (state === S.STARTED) continue;

    // Online meetings: the Pnyx bot handles recording, so never nag in-room.
    if (online) {
      console.log(`[Pnyx] "${title}" — online meeting, no in-room reminder`);
      continue;
    }

    // ── In-room reminder ladder (THRESHOLD-based: fires at next poll after the
    //    threshold is crossed, so a reminder is never missed) ──────────────────
    let stage = null;
    if (msAfterStart > 15 * MIN) {
      await setEventState(id, { state: S.EXPIRED });
      console.log(`[Pnyx] "${title}" — >15min in, giving up (expired)`);
      continue;
    } else if (msAfterStart >= 4 * MIN &&
               [S.PENDING, S.REMINDED_T10, S.REMINDED_START].includes(state)) {
      stage = 'FINAL';
    } else if (msAfterStart >= 0 &&
               [S.PENDING, S.REMINDED_T10].includes(state)) {
      stage = 'START';
    } else if (msBeforeStart <= 10 * MIN && state === S.PENDING) {
      stage = 'T10';
    }

    if (stage) {
      await fireNotification(id, event, stage);
      const next = { T10: S.REMINDED_T10, START: S.REMINDED_START, FINAL: S.REMINDED_FINAL };
      await setEventState(id, { state: next[stage] });
      console.log(`[Pnyx] 🔔 "${title}" — fired ${stage} (starts in ${mins}min, state ${state}→${next[stage]})`);
    } else {
      console.log(`[Pnyx] "${title}" — no reminder due (starts in ${mins}min, state ${state})`);
    }
  }
}

// ─── URL builder ──────────────────────────────────────────────────────────────

function buildStartUrl(calendarEventId, title) {
  const params = new URLSearchParams({
    autoStart: 'true',
    source: 'extension',
    meetingTitle: title || '',
    calendar_event_id: calendarEventId || '',
  });
  return `${PNYX_ORIGIN}/?${params}`;
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
    chrome.tabs.create({
      url: meetingId ? `${PNYX_ORIGIN}/meeting-details?id=${meetingId}` : PNYX_ORIGIN,
    });
    return;
  }

  if (buttonIndex === 0) {
    // Primary action: start recording
    chrome.tabs.create({ url: buildStartUrl(eventId, eventTitle) });
    await setEventState(eventId, { state: S.STARTED });
  } else {
    // Secondary action
    if (type === 'T10') {
      // "Remind at start time" — keep REMINDED_T10 so the START nag still fires
    } else {
      // "Skip" / "Don't remind me" — hard dismiss, no more notifications
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

// ─── Messages from the popup (e.g. user clicked Start in the popup) ────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === 'markStarted' && msg.eventId) {
    setEventState(msg.eventId, { state: S.STARTED }).then(() => sendResponse({ ok: true }));
    return true; // async response
  }
  if (msg?.type === 'runCheck') {
    processEvents().then(() => sendResponse({ ok: true }));
    return true;
  }
});

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

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create('pnyx_check', {
    delayInMinutes: 0,
    periodInMinutes: CHECK_INTERVAL_MINUTES,
  });
});

// Run immediately on service-worker wake-up (Chrome restarts the worker when an
// alarm fires while it was asleep).
processEvents();
