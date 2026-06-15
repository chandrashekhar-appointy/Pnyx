"use client";

import { Transcript } from '@/types';
import { TranscriptView } from '@/components/TranscriptView';
import { TranscriptButtonGroup } from './TranscriptButtonGroup';
// import { AudioPlayer } from './AudioPlayer'; // disabled — playback broken
import { useState, useEffect } from 'react';
import { authFetch } from '@/lib/api';
import { toast } from 'sonner';

interface TranscriptPanelProps {
  transcripts: Transcript[];
  onCopyTranscript: () => void;
  onDownloadRecording: () => Promise<void>;
  isRecording: boolean;
  meetingId?: string;
  onTranscriptsUpdate?: (transcripts: Transcript[]) => void;
  className?: string;
}

export function TranscriptPanel({
  transcripts,
  onCopyTranscript,
  onDownloadRecording,
  isRecording,
  meetingId,
  className,
}: TranscriptPanelProps) {
  // Online (Recall bot) meetings have a video recording instead of local audio.
  const [hasVideo, setHasVideo] = useState(false);
  const [isPreparingVideo, setIsPreparingVideo] = useState(false);

  useEffect(() => {
    if (!meetingId) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await authFetch(`/api/meetings/${meetingId}/bot-recording-url`);
        if (!cancelled && res.ok) {
          const data = await res.json();
          if (data?.video_url) setHasVideo(true);
        }
      } catch {
        /* not a bot meeting / no recording — keep audio download */
      }
    })();
    return () => { cancelled = true; };
  }, [meetingId]);

  const handleDownloadVideo = async () => {
    if (!meetingId) return;
    setIsPreparingVideo(true);
    try {
      // Fetch a fresh presigned URL on click (they expire).
      const res = await authFetch(`/api/meetings/${meetingId}/bot-recording-url`);
      if (!res.ok) throw new Error('Recording not available yet');
      const data = await res.json();
      const url = data?.video_url || data?.audio_url;
      if (!url) throw new Error('Recording not ready yet');
      window.open(url, '_blank', 'noopener,noreferrer');
    } catch (e: any) {
      toast.error('Could not get video', { description: e?.message || 'Try again in a moment.' });
    } finally {
      setIsPreparingVideo(false);
    }
  };

  return (
    <div className={`${className ?? 'hidden md:flex'} md:w-1/4 lg:w-1/3 min-w-0 border-r border-gray-200 bg-white flex-col relative shrink-0`}>
      {/* AudioPlayer disabled — playback broken, re-enable once fixed */}
      {/* {meetingId && <AudioPlayer meetingId={meetingId} />} */}
      <div className="p-4 border-b border-gray-200">
        <TranscriptButtonGroup
          transcriptCount={transcripts?.length || 0}
          onCopyTranscript={onCopyTranscript}
          onDownloadRecording={onDownloadRecording}
          isRecording={isRecording}
          hasVideo={hasVideo}
          isPreparingVideo={isPreparingVideo}
          onDownloadVideo={handleDownloadVideo}
        />
      </div>

      <div className="flex-1 overflow-y-auto pb-4 relative">
        <TranscriptView
          transcripts={transcripts}
          isRecording={isRecording}
        />
      </div>
    </div>
  );
}
