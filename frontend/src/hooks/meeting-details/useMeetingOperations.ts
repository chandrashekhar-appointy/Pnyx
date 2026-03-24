import { useCallback } from 'react';
import { toast } from 'sonner';
import { useSidebar } from '@/components/Sidebar/SidebarProvider';
import { apiUrl } from '@/lib/config';
import { authFetch } from '@/lib/api';
import Analytics from '@/lib/analytics';
import { KeyManager } from '@/lib/crypto/key_manager';

interface UseMeetingOperationsProps {
  meeting: any;
  notesGenerationInfo?: any;
}

export function useMeetingOperations({
  meeting,
  notesGenerationInfo,
}: UseMeetingOperationsProps) {
  const { serverAddress, meetings, setCurrentMeeting, setMeetings, refetchMeetings } = useSidebar();
  const baseUrl = serverAddress || apiUrl;

  // Download recording
  const handleDownloadRecording = useCallback(async () => {
    try {
      const response = await authFetch(`/meetings/${meeting.id}/recording-url`);
      if (!response.ok) {
        if (response.status === 409) {
          throw new Error('Recording is still being finalized');
        }
        throw new Error('No recording available');
      }
      
      const data = await response.json();

      // E2EE: Handle encrypted audio download
      if (data.encrypted) {
        const encryption = notesGenerationInfo?.encryption;
        if (!encryption?.audio) {
          toast.error('Audio encryption metadata not found');
          return;
        }

        const keyPair = await KeyManager.getKeyPair();
        if (!keyPair?.privateKey) {
          toast.error('Encryption key required', {
            description: 'Go to Settings → Encryption to restore your private key.'
          });
          return;
        }

        toast.info('Decrypting audio...', { duration: 5000 });

        const artifactResp = await authFetch(data.artifact_url);
        if (!artifactResp.ok) {
          throw new Error('Failed to fetch encrypted audio');
        }
        const encryptedData = new Uint8Array(await artifactResp.arrayBuffer());

        const meta = encryption.audio;
        const ephemeralPubKey = Uint8Array.from(atob(meta.ephemeralPublicKey), c => c.charCodeAt(0));
        const kekNonce = Uint8Array.from(atob(meta.kekNonce), c => c.charCodeAt(0));
        const wrappedKey = Uint8Array.from(atob(meta.wrappedKey), c => c.charCodeAt(0));
        const nonce = Uint8Array.from(atob(meta.nonce), c => c.charCodeAt(0));

        const sessionKey = await KeyManager.decryptSessionKey(
          keyPair.privateKey, ephemeralPubKey, kekNonce, wrappedKey
        );
        const decryptedBuffer = await KeyManager.decryptDocument(
          sessionKey, nonce, encryptedData
        );

        // Trigger download of decrypted audio
        const blob = new Blob([decryptedBuffer], { type: 'audio/wav' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `recording-${meeting.id}.wav`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);

        await Analytics.trackRecordingDownloaded('enc.wav', {
          meeting_id: meeting.id,
          encrypted: true,
        });

        toast.success('Recording downloaded and decrypted');
        return;
      }

      if (data.url) {
        await Analytics.trackRecordingDownloaded(data.format || 'wav', {
          meeting_id: meeting.id,
          filename: data.filename,
        });
        // Trigger download
        const link = document.createElement('a');
        link.href = data.url;
        const ext = data.format || 'wav';
        link.download = data.filename || `recording-${meeting.id}.${ext}`;
        link.rel = 'noopener noreferrer';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
      }
    } catch (error) {
      console.error('Failed to download recording:', error);
      toast.error('Failed to download recording');
    }
  }, [meeting.id, notesGenerationInfo]);

  // Delete meeting
  const handleDeleteMeeting = useCallback(async (router: any) => {
    try {
      if (!confirm('Are you sure you want to delete this meeting? This action cannot be undone.')) {
        return;
      }

      const response = await authFetch('/delete-meeting', {
        method: 'POST',
        // headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ meeting_id: meeting.id }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || 'Failed to delete meeting');
      }

      await Analytics.trackMeetingDeleted(meeting.id);
      setMeetings(meetings.filter((item) => item.id !== meeting.id));
      setCurrentMeeting({ id: 'intro-call', title: '+ New Call' });
      await refetchMeetings();
      toast.success('Meeting deleted successfully');
      router.replace('/');
      router.refresh();
      if (typeof window !== 'undefined') {
        window.location.replace('/');
      }
      
    } catch (error) {
      console.error('Failed to delete meeting:', error);
      toast.error('Failed to delete meeting', { 
        description: error instanceof Error ? error.message : 'Unknown error'
      });
    }
  }, [meeting.id, baseUrl, meetings, setCurrentMeeting, setMeetings, refetchMeetings]);

  return {
    handleDownloadRecording,
    handleDeleteMeeting,
  };
}
