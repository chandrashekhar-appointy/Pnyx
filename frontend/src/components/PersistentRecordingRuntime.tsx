'use client';

import { useEffect } from 'react';
import { RecordingControls } from '@/components/RecordingControls';
import { usePersistentRecordingSession } from '@/lib/recordingSessionStore';

export function PersistentRecordingRuntime() {
  const {
    isRecording,
    isPaused,
    currentSessionId,
    resumeStartSignal,
    recordingElapsedSeconds,
    setRecordingElapsedSeconds,
  } = usePersistentRecordingSession();

  useEffect(() => {
    if (!isRecording || isPaused) return;

    const intervalId = setInterval(() => {
      setRecordingElapsedSeconds((prev) => prev + 1);
    }, 1000);

    return () => clearInterval(intervalId);
  }, [isRecording, isPaused, setRecordingElapsedSeconds]);

  if (!isRecording && !currentSessionId) return null;

  return (
    <div className="hidden" aria-hidden="true">
      <RecordingControls
        renderUI={false}
        isRecording={isRecording}
        barHeights={['4px', '4px', '4px']}
        onRecordingStop={() => {}}
        onRecordingStart={() => {}}
        onTranscriptReceived={() => {}}
        onTranscriptionError={() => {}}
        isRecordingDisabled={false}
        isParentProcessing={false}
        initialSessionId={currentSessionId}
        startSignal={resumeStartSignal}
        onPauseChange={() => {}}
      />
    </div>
  );
}
