"use client";

import { Button } from '@/components/ui/button';
import { ButtonGroup } from '@/components/ui/button-group';
import { Copy, Download, Video, Loader2 } from 'lucide-react';
import Analytics from '@/lib/analytics';

interface TranscriptButtonGroupProps {
  transcriptCount: number;
  onCopyTranscript: () => void;
  onDownloadRecording: () => Promise<void>;
  isRecording?: boolean;
  // Online (Recall bot) meetings expose a video recording instead of audio.
  hasVideo?: boolean;
  isPreparingVideo?: boolean;
  onDownloadVideo?: () => void;
}

export function TranscriptButtonGroup({
  transcriptCount,
  onCopyTranscript,
  onDownloadRecording,
  hasVideo,
  isPreparingVideo,
  onDownloadVideo,
}: TranscriptButtonGroupProps) {
  return (
    <div className="flex items-start justify-end w-full gap-3">
      <div className="flex flex-col items-end gap-1">
        <ButtonGroup>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              Analytics.trackButtonClick('copy_transcript', 'meeting_details');
              onCopyTranscript();
            }}
            disabled={transcriptCount === 0}
            title={transcriptCount === 0 ? 'No transcript available' : 'Copy Transcript'}
          >
            <Copy className="h-4 w-4 sm:mr-2" />
            <span className="hidden sm:inline">Copy</span>
          </Button>

          {hasVideo ? (
            <Button
              size="sm"
              variant="outline"
              className="xl:px-4"
              disabled={isPreparingVideo}
              onClick={() => {
                Analytics.trackButtonClick('download_video', 'meeting_details');
                onDownloadVideo?.();
              }}
              title="Download Video Recording"
            >
              {isPreparingVideo ? (
                <Loader2 className="h-4 w-4 sm:mr-2 animate-spin" />
              ) : (
                <Video className="h-4 w-4 sm:mr-2" />
              )}
              <span className="hidden sm:inline">Download Video</span>
            </Button>
          ) : (
            <Button
              size="sm"
              variant="outline"
              className="xl:px-4"
              onClick={() => {
                Analytics.trackButtonClick('download_recording', 'meeting_details');
                onDownloadRecording();
              }}
              title="Download Audio File"
            >
              <Download className="h-4 w-4 sm:mr-2" />
              <span className="hidden sm:inline">Download Recording</span>
            </Button>
          )}
        </ButtonGroup>
      </div>
    </div>
  );
}
