'use client';

import { useSyncExternalStore } from 'react';
import type { Transcript } from '@/types';
import type { AudioStreamClient } from '@/lib/audio-streaming/AudioStreamClient';

type Updater<T> = T | ((prev: T) => T);

interface RecordingSessionState {
  meetingTitle: string;
  transcripts: Transcript[];
  partialTranscript: string;
  isRecording: boolean;
  isPaused: boolean;
  currentSessionId: string | null;
  pendingRecoveryId: string | null;
  resumeStartSignal: number;
  recordingElapsedSeconds: number;
  audioTimelineOffsetSeconds: number;
}

type Listener = () => void;

let state: RecordingSessionState = {
  meetingTitle: '+ New Call',
  transcripts: [],
  partialTranscript: '',
  isRecording: false,
  isPaused: false,
  currentSessionId: null,
  pendingRecoveryId: null,
  resumeStartSignal: 0,
  recordingElapsedSeconds: 0,
  audioTimelineOffsetSeconds: 0,
};

let recordingClient: AudioStreamClient | null = null;
const listeners = new Set<Listener>();

function emitChange() {
  listeners.forEach((listener) => listener());
}

function applyValue<T>(current: T, next: Updater<T>): T {
  return typeof next === 'function' ? (next as (prev: T) => T)(current) : next;
}

function updateState<K extends keyof RecordingSessionState>(
  key: K,
  value: Updater<RecordingSessionState[K]>
) {
  const nextValue = applyValue(state[key], value);
  if (Object.is(nextValue, state[key])) return;
  state = {
    ...state,
    [key]: nextValue,
  };
  emitChange();
}

function sortTranscripts(items: Transcript[]): Transcript[] {
  return [...items].sort((a, b) => {
    if (a.audio_start_time !== undefined && b.audio_start_time !== undefined) {
      if (Math.abs(a.audio_start_time - b.audio_start_time) < 0.1) {
        return (a.sequence_id || 0) - (b.sequence_id || 0);
      }
      return a.audio_start_time - b.audio_start_time;
    }
    return (a.sequence_id || 0) - (b.sequence_id || 0);
  });
}

export function setPartialTranscript(text: string) {
  updateState('partialTranscript', text);
}

export function appendStreamingTranscript(update: {
  text: string;
  timestamp: string;
  sequence_id?: number;
  audio_start_time?: number;
  audio_end_time?: number;
  duration?: number;
  source?: 'live' | 'diarized';
  speaker?: string;
  speaker_confidence?: number;
  stability_score?: number;
  stability_class?: 'stable' | 'volatile';
  segment_finalize_latency_seconds?: number;
  boundary_score?: number;
}) {
  const offset = state.audioTimelineOffsetSeconds;
  const adjustedAudioStart =
    typeof update.audio_start_time === 'number'
      ? update.audio_start_time + offset
      : undefined;
  const adjustedAudioEnd =
    typeof update.audio_end_time === 'number'
      ? update.audio_end_time + offset
      : undefined;

  const nextTranscript: Transcript = {
    id: update.sequence_id ? update.sequence_id.toString() : Date.now().toString(),
    text: update.text,
    timestamp: update.timestamp,
    sequence_id: update.sequence_id || 0,
    audio_start_time: adjustedAudioStart,
    audio_end_time: adjustedAudioEnd,
    duration: update.duration,
    source: update.source || 'live',
    speaker: update.speaker,
    speaker_confidence: update.speaker_confidence,
    stability_score: update.stability_score,
    stability_class: update.stability_class || 'stable',
    segment_finalize_latency_seconds: update.segment_finalize_latency_seconds,
    boundary_score: update.boundary_score,
  };

  const exists = state.transcripts.some(
    (transcript) =>
      transcript.text === update.text && transcript.timestamp === update.timestamp
  );

  if (exists) return;

  state = {
    ...state,
    partialTranscript: '',
    transcripts: sortTranscripts([...state.transcripts, nextTranscript]),
  };
  emitChange();
}

export function resetResumeStartSignal() {
  updateState('resumeStartSignal', 0);
}

export function usePersistentRecordingSession() {
  const snapshot = useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => state,
    () => state
  );

  return {
    ...snapshot,
    setMeetingTitle: (value: Updater<string>) => updateState('meetingTitle', value),
    setTranscripts: (value: Updater<Transcript[]>) => updateState('transcripts', value),
    setPartialTranscript: (value: Updater<string>) => updateState('partialTranscript', value),
    setIsRecording: (value: Updater<boolean>) => updateState('isRecording', value),
    setIsPaused: (value: Updater<boolean>) => updateState('isPaused', value),
    setCurrentSessionId: (value: Updater<string | null>) => updateState('currentSessionId', value),
    setPendingRecoveryId: (value: Updater<string | null>) => updateState('pendingRecoveryId', value),
    setResumeStartSignal: (value: Updater<number>) => updateState('resumeStartSignal', value),
    setRecordingElapsedSeconds: (value: Updater<number>) => updateState('recordingElapsedSeconds', value),
    setAudioTimelineOffsetSeconds: (value: Updater<number>) => updateState('audioTimelineOffsetSeconds', value),
    resetResumeStartSignal: () => updateState('resumeStartSignal', 0),
  };
}

export function getRecordingSessionSnapshot(): RecordingSessionState {
  return state;
}

export function getPersistentRecordingClient(): AudioStreamClient | null {
  return recordingClient;
}

export function setPersistentRecordingClient(client: AudioStreamClient | null) {
  recordingClient = client;
}
