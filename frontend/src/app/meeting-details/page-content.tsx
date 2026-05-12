"use client";
import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { Summary, SummaryResponse } from '@/types';
import { useSidebar } from '@/components/Sidebar/SidebarProvider';
import Analytics from '@/lib/analytics';
import { TranscriptPanel } from '@/components/MeetingDetails/TranscriptPanel';
import { ShareNotesDialog } from '@/components/ShareNotesDialog';
import { SummaryPanel } from '@/components/MeetingDetails/SummaryPanel';
import { ChatInterface } from '@/components/MeetingDetails/ChatInterface';
import { Bot, MessageSquare } from 'lucide-react';
import { toast } from 'sonner';
import { authFetch } from '@/lib/api';
import { Key, AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

// Custom hooks
import { useMeetingData } from '@/hooks/meeting-details/useMeetingData';
import { useSummaryGeneration } from '@/hooks/meeting-details/useSummaryGeneration';
import { useModelConfiguration } from '@/hooks/meeting-details/useModelConfiguration';
import { useTemplates } from '@/hooks/meeting-details/useTemplates';
import { useCopyOperations } from '@/hooks/meeting-details/useCopyOperations';
import { useMeetingOperations } from '@/hooks/meeting-details/useMeetingOperations';
import { useDiarization } from '@/hooks/useDiarization';

import { useRouter } from 'next/navigation';
import { KeyManager } from '@/lib/crypto/key_manager';

export default function PageContent({
  meeting,
  summaryData,
  notesGenerationInfo,
  shouldAutoGenerate = false,
  onAutoGenerateComplete,
  onMeetingUpdated
}: {
  meeting: any;
  summaryData: Summary | null;
  notesGenerationInfo?: any;
  shouldAutoGenerate?: boolean;
  onAutoGenerateComplete?: () => void;
  onMeetingUpdated?: () => Promise<void>;
}) {
  const router = useRouter(); // Initialize router

  console.log('📄 PAGE CONTENT: Initializing with data:', {
    meetingId: meeting.id,
    summaryDataKeys: summaryData ? Object.keys(summaryData) : null,
    transcriptsCount: meeting.transcripts?.length,
  });

  const [isDecrypting, setIsDecrypting] = useState(false);
  const [decryptionError, setDecryptionError] = useState<string | null>(null);
  const [manualKeyInput, setManualKeyInput] = useState('');
  const [customPrivateKey, setCustomPrivateKey] = useState<CryptoKey | null>(null);

  const getFriendlyDecryptionMessage = (error: unknown) => {
    const rawMessage =
      error instanceof Error ? error.message : typeof error === 'string' ? error : 'Decryption failed';
    const normalized = rawMessage.toLowerCase();

    if (
      normalized.includes('operation-specific') ||
      normalized.includes('aes-gcm') ||
      normalized.includes('decrypt')
    ) {
      return 'Your local private key does not match the key used for this encrypted summary. Restore the correct key in Settings, then regenerate the notes.';
    }

    return rawMessage;
  };

  // State
  const [isRecording] = useState(false);
  const [summaryResponse] = useState<SummaryResponse | null>(null);
  const [shareDialogState, setShareDialogState] = useState<{ isOpen: boolean, meetingId: string }>({ isOpen: false, meetingId: "" });

  useEffect(() => {
    const handleShowShare = (e: any) => {
      setShareDialogState({ isOpen: true, meetingId: e.detail.meetingId });
    };
    window.addEventListener('show-share-dialog', handleShowShare);
    return () => window.removeEventListener('show-share-dialog', handleShowShare);
  }, []);
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [currentTranscriptVersion, setCurrentTranscriptVersion] = useState<number | undefined>(undefined);

  // Sidebar context
  const { serverAddress } = useSidebar();

  // Custom hooks
  const meetingData = useMeetingData({ meeting, summaryData, onMeetingUpdated });
  const modelConfig = useModelConfiguration({ serverAddress });
  const templates = useTemplates();

  const summaryGeneration = useSummaryGeneration({
    meeting,
    transcripts: meetingData.transcripts,
    modelConfig: modelConfig.modelConfig,
    isModelConfigLoading: modelConfig.isLoading,
    selectedTemplate: templates.selectedTemplate,
    onMeetingUpdated,
    updateMeetingTitle: meetingData.updateMeetingTitle,
    setAiSummary: meetingData.setAiSummary,
    initialNotesGenerationInfo: notesGenerationInfo,
  });
  const effectiveNotesGenerationInfo = summaryGeneration.notesGenerationInfo || notesGenerationInfo;

  const copyOperations = useCopyOperations({
    meeting,
    transcripts: meetingData.transcripts,
    meetingTitle: meetingData.meetingTitle,
    aiSummary: meetingData.aiSummary,
    blockNoteSummaryRef: meetingData.blockNoteSummaryRef,
  });

  const meetingOperations = useMeetingOperations({
    meeting,
    notesGenerationInfo: effectiveNotesGenerationInfo,
  });

  // Diarization
  const diarization = useDiarization(meeting.id);

  // Handle diarization errors
  useEffect(() => {
    if (diarization.error) {
      console.error('Diarization error:', diarization.error);

      // Check for specific "No audio" error
      if (diarization.error.includes('No audio recording directory found') || diarization.error.includes('No audio recording found')) {
        toast.error('Diarization Failed', {
          description: 'No audio recording found for this meeting. Diarization requires the original audio file.',
          duration: 5000
        });
      } else {
        toast.error('Diarization Failed', {
          description: diarization.error
        });
      }
    }
  }, [diarization.error]);

  // Track page view
  useEffect(() => {
    Analytics.trackPageView('meeting_details');
  }, []);

  // Track if initial generation has been triggered
  const [hasTriggeredInitialGeneration, setHasTriggeredInitialGeneration] = useState(false);

  // Auto-generate notes on page load when transcripts exist but no summary
  useEffect(() => {
    const transcriptsExist = meetingData.transcripts && meetingData.transcripts.length > 0;
    const noSummaryExists = !meetingData.aiSummary;
    const notProcessing = summaryGeneration.summaryStatus === 'idle' || summaryGeneration.summaryStatus === 'error';
    const modelReady = !modelConfig.isLoading && modelConfig.modelConfig.provider;

    if (transcriptsExist && noSummaryExists && notProcessing && modelReady && !hasTriggeredInitialGeneration) {
      console.log('🚀 Auto-generating notes on page load with template:', templates.selectedTemplate);
      setHasTriggeredInitialGeneration(true);
      // Slight delay to ensure everything is ready
      setTimeout(() => {
        summaryGeneration.handleGenerateSummary('');
      }, 500);
    }
  }, [
    meetingData.transcripts,
    meetingData.aiSummary,
    summaryGeneration.summaryStatus,
    modelConfig.isLoading,
    modelConfig.modelConfig.provider,
    templates.selectedTemplate,
    hasTriggeredInitialGeneration,
    summaryGeneration.handleGenerateSummary
  ]);

  // Auto-regenerate notes when template changes
  useEffect(() => {
    if (templates.templateChanged && meetingData.transcripts && meetingData.transcripts.length > 0) {
      console.log('🔄 Template changed, regenerating notes with:', templates.selectedTemplate);
      // Clear the current summary to show loading state
      meetingData.setAiSummary(null);
      // Trigger regeneration
      setTimeout(() => {
        summaryGeneration.handleGenerateSummary('');
        templates.acknowledgeTemplateChange();
      }, 100);
    }
  }, [
    templates.templateChanged,
    templates.selectedTemplate,
    templates.acknowledgeTemplateChange,
    meetingData.transcripts,
    meetingData.setAiSummary,
    summaryGeneration.handleGenerateSummary
  ]);

  // Refresh data when summary completes (detected by parent page polling)
  useEffect(() => {
    if (summaryGeneration.summaryStatus === 'completed') {
      console.log('✨ Summary completed, refreshing meeting data...');
      if (onMeetingUpdated) {
        onMeetingUpdated();
      }
    }
  }, [summaryGeneration.summaryStatus, onMeetingUpdated]);

  // E2EE Decryption Effect
  useEffect(() => {
    const attemptDecryption = async () => {
      const encryption = effectiveNotesGenerationInfo?.encryption;
      if (!encryption) return;

      const hasTranscript = !!encryption.transcript;
      const hasSummary = !!encryption.summary;
      if (!hasTranscript && !hasSummary) return;

      console.log('🔐 E2EE: Encrypted content detected, starting decryption...');
      setIsDecrypting(true);
      setDecryptionError(null);

      try {
        const keyPair = await KeyManager.getKeyPair();
        const activePrivateKey = customPrivateKey || keyPair?.privateKey;
        
        if (!activePrivateKey) {
          // No key → hide everything and redirect to settings for key restoration
          console.warn('🔐 E2EE: No private key found, redirecting to settings...');
          meetingData.setTranscripts([]);
          meetingData.setAiSummary(null);
          toast.error("Encryption key required", {
            description: "Redirecting to settings to restore your private key..."
          });
          router.push('/settings?tab=encryption');
          return;
        }

        // Helper to decrypt a given metadata entry and URL
        const decryptPayload = async (meta: any, url: string) => {
          const response = await authFetch(url);
          if (!response.ok) {
            if (response.status === 404) {
              console.warn(`🔐 E2EE: Encrypted artifact not found (404): ${url}, skipping`);
              return null; // Gracefully skip missing artifacts
            }
            throw new Error(`Failed to fetch encrypted artifact: ${url}`);
          }
          const encryptedData = new Uint8Array(await response.arrayBuffer());

          const ephemeralPubKey = Uint8Array.from(atob(meta.ephemeralPublicKey), c => c.charCodeAt(0));
          const kekNonce = Uint8Array.from(atob(meta.kekNonce), c => c.charCodeAt(0));
          const wrappedKey = Uint8Array.from(atob(meta.wrappedKey), c => c.charCodeAt(0));
          const nonce = Uint8Array.from(atob(meta.nonce), c => c.charCodeAt(0));

          const sessionKey = await KeyManager.decryptSessionKey(
            activePrivateKey, ephemeralPubKey, kekNonce, wrappedKey
          );
          const decryptedBuffer = await KeyManager.decryptDocumentAsync(
            sessionKey, nonce, encryptedData
          );
          return JSON.parse(new TextDecoder().decode(decryptedBuffer));
        };

        // 1. Decrypt Transcript if present and meeting transcripts are empty (purged)
        if (hasTranscript && (!meetingData.transcripts || meetingData.transcripts.length === 0)) {
          console.log('🔐 E2EE: Decrypting full transcript...');
          try {
            const decrypted = await decryptPayload(encryption.transcript, `/meetings/${meeting.id}/artifacts/transcript.enc.json`);
            if (decrypted) {
              meetingData.setTranscripts(decrypted);
            }
          } catch (err: any) {
            console.error('🔐 E2EE: Transcript decryption failed:', err);
            // If transcript decryption fails, it likely means a key mismatch
            const friendlyMessage = getFriendlyDecryptionMessage(err);
            setDecryptionError(friendlyMessage);
            throw new Error(friendlyMessage);
          }
        }

        // 2. Decrypt Summary if present
        if (hasSummary) {
          console.log('🔐 E2EE: Decrypting AI summary...');
          try {
            const decrypted = await decryptPayload(encryption.summary, `/meetings/${meeting.id}/artifacts/summary.enc.json`);
            // DocumentStorageService structure: { meeting_id, result: { ... } }
            if (decrypted && decrypted.result) {
              meetingData.setAiSummary(decrypted.result);
            }
          } catch (err: any) {
            console.error('🔐 E2EE: Summary decryption failed:', err);
            const friendlyMessage = getFriendlyDecryptionMessage(err);
            setDecryptionError(friendlyMessage);
            throw new Error(friendlyMessage);
          }
        }

        console.log('✅ E2EE: Decryption flow completed');
      } catch (err: any) {
        console.error('❌ E2EE Decryption Error:', err);
        setDecryptionError(err.message || "Decryption failed");
        toast.error("Decryption Failed", {
          description: err.message || "Could not decrypt meeting data."
        });
      } finally {
        setIsDecrypting(false);
      }
    };

    if (effectiveNotesGenerationInfo) {
      attemptDecryption();
    }
  }, [effectiveNotesGenerationInfo, meeting.id, customPrivateKey]);

  // AUTO-REFRESH TRANSCRIPT: When diarization completes, refresh meeting data to show speaker labels
  const [hasRefreshedForDiarization, setHasRefreshedForDiarization] = useState(false);
  useEffect(() => {
    if (diarization.status?.status === 'completed' && !hasRefreshedForDiarization) {
      // Only auto-refresh when viewing live transcript to avoid overriding a selected version.
      if (currentTranscriptVersion === undefined) {
        console.log('✅ Diarization completed, refreshing meeting data to show speaker labels...');
        setHasRefreshedForDiarization(true);
        if (onMeetingUpdated) {
          onMeetingUpdated();
        }
      }
    } else if (diarization.status?.status !== 'completed' && hasRefreshedForDiarization) {
      // Reset if status changes back (e.g. re-running diarization)
      setHasRefreshedForDiarization(false);
    }
  }, [diarization.status?.status, onMeetingUpdated, hasRefreshedForDiarization, currentTranscriptVersion]);

  // Convert speakers array to map for easier lookup
  const speakerMap = (diarization.speakers || []).reduce((acc, s) => {
    acc[s.label] = s.display_name;
    return acc;
  }, {} as Record<string, string>);

  const handleManualUnlock = async () => {
    if (!manualKeyInput.trim()) {
      toast.error('Please enter a private key');
      return;
    }
    
    try {
      // Import the custom private key string
      const pk = await KeyManager.importPrivateKey(manualKeyInput.trim());
      setCustomPrivateKey(pk); // This will trigger the useEffect to attempt decryption again
      toast.success('Attempting to decrypt with provided key...');
    } catch (err) {
      toast.error('Invalid Private Key format', {
        description: 'Ensure it is a valid base64/PEM encoded ECDH P-256 key.'
      });
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      className="flex flex-col h-screen bg-gray-50"
    >
      <div className="flex flex-1 overflow-hidden relative">
        {/* E2EE Decryption Overlay */}
        {decryptionError && (
          <div className="absolute inset-0 z-50 bg-white/95 flex flex-col items-center justify-center p-8 text-center backdrop-blur-sm">
            <AlertTriangle className="w-12 h-12 text-red-500 mb-4" />
            <h3 className="text-xl font-bold text-red-700 mb-2">Decryption Error</h3>
            <p className="text-gray-600 mb-6 max-w-md">{decryptionError}</p>
            
            <div className="w-full max-w-md bg-white p-6 rounded-lg shadow-sm border border-gray-200 mb-6 text-left">
              <h4 className="text-sm font-semibold text-gray-900 mb-2">Provide Private Key for this Meeting</h4>
              <p className="text-xs text-gray-500 mb-4">
                If this meeting was encrypted with a different key than your current one, paste the correct private key below to unlock it.
              </p>
              <Input
                type="password"
                placeholder="Paste base64 private key here..."
                value={manualKeyInput}
                onChange={(e) => setManualKeyInput(e.target.value)}
                className="mb-4 font-mono text-xs"
              />
              <Button onClick={handleManualUnlock} className="w-full bg-blue-600 hover:bg-blue-700">
                Unlock Meeting
              </Button>
            </div>

            <div className="flex gap-3">
              <Button onClick={() => window.location.reload()} variant="outline">Refresh Page</Button>
              <Button onClick={() => router.push('/settings?tab=encryption')} variant="secondary">
                <Key className="w-4 h-4 mr-2" />
                Go to Global Settings
              </Button>
            </div>
          </div>
        )}


        <TranscriptPanel
          transcripts={meetingData.transcripts}
          onCopyTranscript={copyOperations.handleCopyTranscript}
          onDownloadRecording={meetingOperations.handleDownloadRecording}
          isRecording={isRecording}
          currentVersion={currentTranscriptVersion}
          onCurrentVersionChange={setCurrentTranscriptVersion}
          onDiarize={diarization.triggerDiarization}
          onStopDiarize={diarization.stopDiarization}
          diarizationStatus={diarization.status?.status}
          isDiarizing={diarization.isDiarizing}
          diarizationProgress={diarization.progress}
          diarizationWaitEstimate={diarization.waitEstimateText}
          speakerMap={speakerMap}
          meetingId={meeting.id}
          onTranscriptsUpdate={meetingData.setTranscripts}
        />

        <SummaryPanel
          meeting={meeting}
          meetingTitle={meetingData.meetingTitle}
          onTitleChange={meetingData.handleTitleChange}
          isEditingTitle={meetingData.isEditingTitle}
          onStartEditTitle={() => meetingData.setIsEditingTitle(true)}
          onFinishEditTitle={() => meetingData.setIsEditingTitle(false)}
          isTitleDirty={meetingData.isTitleDirty}
          summaryRef={meetingData.blockNoteSummaryRef}
          isSaving={meetingData.isSaving}
          onSaveAll={meetingData.saveAllChanges}
          onCopySummary={copyOperations.handleCopySummary}
          aiSummary={meetingData.aiSummary}
          summaryStatus={summaryGeneration.summaryStatus}
          transcripts={meetingData.transcripts}
          modelConfig={modelConfig.modelConfig}
          setModelConfig={modelConfig.setModelConfig}
          onSaveModelConfig={modelConfig.handleSaveModelConfig}
          onGenerateSummary={summaryGeneration.handleGenerateSummary}
          summaryResponse={summaryResponse}
          onSaveSummary={meetingData.handleSaveSummary}
          onSummaryChange={meetingData.handleSummaryChange}
          onDirtyChange={meetingData.setIsSummaryDirty}
          summaryError={summaryGeneration.summaryError}
          onRegenerateSummary={summaryGeneration.handleRegenerateSummary}
          onRegenerateWithDiarized={summaryGeneration.handleRegenerateWithDiarized}
          notesGenerationInfo={summaryGeneration.notesGenerationInfo}
          getSummaryStatusMessage={summaryGeneration.getSummaryStatusMessage}
          availableTemplates={templates.availableTemplates}
          selectedTemplate={templates.selectedTemplate}
          onTemplateSelect={templates.handleTemplateSelection}
          isModelConfigLoading={modelConfig.isLoading}
          onDeleteMeeting={() => meetingOperations.handleDeleteMeeting(router)}
        />

      </div>

      <ShareNotesDialog
        isOpen={shareDialogState.isOpen}
        meetingId={shareDialogState.meetingId || meeting.id}
        onClose={() => setShareDialogState({ isOpen: false, meetingId: "" })}
      />

      {/* Chat Interface */}
      {isChatOpen && (
        <ChatInterface
          meetingId={meeting.id}
          onClose={() => setIsChatOpen(false)}
          currentTranscripts={meetingData.transcripts}
        />
      )}

      {/* Floating Chat Button */}
      {!isChatOpen && (
        <motion.button
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          whileHover={{ scale: 1.1 }}
          whileTap={{ scale: 0.9 }}
          onClick={() => setIsChatOpen(true)}
          className="fixed bottom-6 right-6 p-4 bg-blue-600 text-white rounded-full shadow-lg hover:bg-blue-700 transition-colors z-40 flex items-center gap-2"
        >
          <Bot className="w-6 h-6" />
          <span className="font-medium">Ask AI</span>
        </motion.button>
      )}
    </motion.div>
  );
}
