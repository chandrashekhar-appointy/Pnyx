'use client';

import { useState, useEffect, useContext, useCallback, useMemo, useRef, Suspense } from 'react';
import { AnimatePresence, LayoutGroup, motion } from 'framer-motion';
import { Transcript, TranscriptUpdate, Summary, SummaryResponse } from '@/types';
import { EditableTitle } from '@/components/EditableTitle';
import { TranscriptView } from '@/components/TranscriptView';
import { RecordingControls } from '@/components/RecordingControls';
import { BotInvitePanel } from '@/components/BotInvitePanel';
import { AISummary } from '@/components/AISummary';
import { DeviceSelection, SelectedDevices } from '@/components/DeviceSelection';
import { EncryptionCTABar } from '@/components/EncryptionCTABar';
import { useSidebar } from '@/components/Sidebar/SidebarProvider';
import { TranscriptSettings, TranscriptModelProps } from '@/components/TranscriptSettings';
import { LanguageSelection } from '@/components/LanguageSelection';
// import { PermissionWarning } from '@/components/PermissionWarning';
import { PreferenceSettings } from '@/components/PreferenceSettings';
import { useNavigation } from '@/hooks/useNavigation';
import { useRouter, useSearchParams } from 'next/navigation';
import type { CurrentMeeting } from '@/components/Sidebar/SidebarProvider';
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import Analytics from '@/lib/analytics';
import { showRecordingNotification } from '@/lib/recordingNotification';
import { Button } from '@/components/ui/button';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  AlertCircle,
  Bot,
  Calendar,
  CheckCircle2,
  Copy,
  ChevronLeftCircle,
  ChevronRightCircle,
  GlobeIcon,
  HelpCircle,
  History as HistoryIcon,
  MessageCircle,
  Settings,
  Sparkles,
  X,
  Zap,
} from 'lucide-react';
import { ChatInterface } from '@/components/MeetingDetails/ChatInterface';
import { MicrophoneIcon } from '@heroicons/react/24/outline';
import { toast } from 'sonner';
import { authFetch, AuthError } from '@/lib/api';
import { recoveryService, PendingMeetingData } from '@/lib/transcriptRecovery';
import { SetupRequirements } from '@/components/SetupRequirements';
import { CalendarMeetingPicker, CalendarEvent, formatCalendarEventTimeIST } from '@/components/CalendarMeetingPicker';
import { AudioStreamClient } from '@/lib/audio-streaming/AudioStreamClient';
import { 
  getPersistentRecordingClient, 
  usePersistentRecordingSession, 
  resetResumeStartSignal,
  setPartialTranscript,
  appendStreamingTranscript 
} from '@/lib/recordingSessionStore';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';



interface ModelConfig {
  provider: 'ollama' | 'groq' | 'claude' | 'openrouter' | 'gemini' | 'openai';
  model: string;
  whisperModel: string;
}

type SummaryStatus = 'idle' | 'processing' | 'summarizing' | 'regenerating' | 'completed' | 'error';

interface OllamaModel {
  name: string;
  id: string;
  size: string;
  modified: string;
}

interface StreamingHealthPayload {
  session_id: string;
  active_connections: number;
  runtime?: {
    reconnect_storm_detected?: boolean;
    recent_resume_events_count?: number;
    dropped_audio_chunks?: number;
    queue_depth?: number;
    max_audio_queue_depth?: number;
    backpressure_close_triggered?: boolean;
    last_warning?: string | null;
    alert_counts?: Record<string, number>;
    alert_history?: Array<{
      type: string;
      severity: string;
      message: string;
      timestamp: string;
    }>;
  };
  manager_stats?: {
    stable_segments?: number;
    volatile_segments?: number;
    semantic_drift_events?: number;
    correction_events?: number;
    alignment_merges?: number;
    alignment_fallbacks?: number;
  };
}

interface AIGuardrailAlert {
  id: string;
  reason: 'agenda_deviation' | 'no_decision' | 'unresolved_question' | 'missing_context_or_repeat';
  insight: string;
  confidence: number;
  timestamp: string;
  updated_at?: string;
}

interface AIHostSuggestion {
  id: string;
  event_type: string;
  title: string;
  content: string;
  confidence: number;
  timestamp: string;
  status?: string;
  source_excerpt?: string;
  metadata?: Record<string, unknown>;
}

interface AIHostIntervention {
  id: string;
  event_type: string;
  headline: string;
  body: string;
  priority: 'low' | 'medium' | 'high';
  confidence: number;
  timestamp: string;
  linked_suggestion_id?: string;
}

interface ManualMeetingContext {
  calendar_event_id?: string;
  goal: string;
  agenda_text: string;
  participants: string[];
}

interface AIHostStyleItem {
  id: string;
  name: string;
  source: 'system' | 'user';
  read_only: boolean;
  is_default: boolean;
  is_active: boolean;
  skill_markdown: string;
}

interface AIHostStylesPayload {
  styles: AIHostStyleItem[];
  default_style_id: string;
}

interface BehaviorCategory {
  id: string;
  icon: string;
  label: string;
  display_hint: string;
  confidence_threshold: number;
}

interface BehaviorSpecPayload {
  name: string;
  summary_visibility: string;
  suppressed_categories: string[];
  output_categories: Array<{
    id: string;
    icon: string;
    label: string;
    description: string;
    display_hint: string;
    priority_default: string;
  }>;
}

const CORE_EVENT_TYPES = new Set(['decision_candidate', 'open_discussion']);

const extractRoleModeFromMarkdown = (markdown: string): string | null => {
  const match = markdown.match(/role_mode\s*:\s*([a-zA-Z_]+)/i);
  if (!match?.[1]) return null;
  return match[1].replace(/_/g, ' ').trim();
};

const toTitleCase = (value: string): string =>
  value
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(' ');

export default function Home() {
  return (
    <Suspense fallback={null}>
      <HomeContent />
    </Suspense>
  );
}

function HomeContent() {
  const {
    meetingTitle,
    setMeetingTitle,
    transcripts,
    setTranscripts,
    isRecording,
    setIsRecording,
    isPaused,
    setIsPaused,
    pendingRecoveryId,
    setPendingRecoveryId,
    currentSessionId,
    setCurrentSessionId,
    resumeStartSignal,
    setResumeStartSignal,
    recordingElapsedSeconds,
    setRecordingElapsedSeconds,
    audioTimelineOffsetSeconds,
    setAudioTimelineOffsetSeconds,
    partialTranscript,
    setPartialTranscript,
  } = usePersistentRecordingSession();

  const [showSummary, setShowSummary] = useState(false);
  const [summaryStatus, setSummaryStatus] = useState<SummaryStatus>('idle');
  const [barHeights, setBarHeights] = useState(['58%', '76%', '58%']);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [customPrompt, setCustomPrompt] = useState('');
  const [aiSummary, setAiSummary] = useState<Summary | null>({
    key_points: { title: "Key Points", blocks: [] },
    action_items: { title: "Action Items", blocks: [] },
    decisions: { title: "Decisions", blocks: [] },
    main_topics: { title: "Main Topics", blocks: [] }
  });
  const [summaryResponse, setSummaryResponse] = useState<SummaryResponse | null>(null);
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [modelConfig, setModelConfig] = useState<ModelConfig>({
    provider: 'gemini',
    model: 'gemini-2.5-flash',
    whisperModel: 'large-v3'
  });
  const [transcriptModelConfig, setTranscriptModelConfig] = useState<TranscriptModelProps>({
    provider: 'parakeet',
    model: 'parakeet-tdt-0.6b-v3-int8',
    apiKey: null
  });
  const [originalTranscript, setOriginalTranscript] = useState<string>('');
  const [models, setModels] = useState<OllamaModel[]>([]);
  const [error, setError] = useState<string>('');
  const [showModelSettings, setShowModelSettings] = useState(false);
  const [showErrorAlert, setShowErrorAlert] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const [showChunkDropWarning, setShowChunkDropWarning] = useState(false);
  const [chunkDropMessage, setChunkDropMessage] = useState('');
  const [isSavingTranscript, setIsSavingTranscript] = useState(false);
  const [isRecordingDisabled, setIsRecordingDisabled] = useState(false);
  const [selectedDevices, setSelectedDevices] = useState<SelectedDevices>({
    micDevice: null,
    systemDevice: null
  });
  const [showDeviceSettings, setShowDeviceSettings] = useState(false);
  const [showModelSelector, setShowModelSelector] = useState(false);
  const [modelSelectorMessage, setModelSelectorMessage] = useState('');
  const [showLanguageSettings, setShowLanguageSettings] = useState(false);
  const [selectedLanguage, setSelectedLanguage] = useState('auto-translate');
  const [isProcessingTranscript, setIsProcessingTranscript] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const [showConfidenceIndicator, setShowConfidenceIndicator] = useState<boolean>(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('showConfidenceIndicator');
      return saved !== null ? saved === 'true' : true;
    }
    return true;
  });

  // State for web audio recording
  // State for web audio recording
  const [isChatOpen, setIsChatOpen] = useState(false);

  // Recovery State
  const [showReauthModal, setShowReauthModal] = useState(false);
  const [isRestoredSession, setIsRestoredSession] = useState(false);
  const [streamingHealth, setStreamingHealth] = useState<StreamingHealthPayload | null>(null);
  const [activeGuardrailAlert, setActiveGuardrailAlert] = useState<AIGuardrailAlert | null>(null);
  const [guardrailAlertHistory, setGuardrailAlertHistory] = useState<AIGuardrailAlert[]>([]);
  const [showGuardrailHistory, setShowGuardrailHistory] = useState(false);
  const [activeHostIntervention, setActiveHostIntervention] = useState<AIHostIntervention | null>(null);
  const [hostSuggestionQueue, setHostSuggestionQueue] = useState<AIHostSuggestion[]>([]);
  const [hostStateDelta, setHostStateDelta] = useState<Record<string, unknown> | null>(null);
  const [pinnedHostSuggestions, setPinnedHostSuggestions] = useState<AIHostSuggestion[]>([]);
  const [pastInsights, setPastInsights] = useState<{type: 'discussion' | 'action' | 'decision'; id?: string; text: string; title?: string; eventType?: string; timestamp: number}[]>([]);
  const insightTimestampsRef = useRef<Map<string, number>>(new Map());
  const [isTranscriptPanelCollapsed, setIsTranscriptPanelCollapsed] = useState(false);
  const hostClientRef = useRef<AudioStreamClient | null>(null);
  const [meetingGoalInput, setMeetingGoalInput] = useState('');
  const [meetingAgendaInput, setMeetingAgendaInput] = useState('');
  const [meetingParticipantsInput, setMeetingParticipantsInput] = useState('');
  const [contextAppliedStatus, setContextAppliedStatus] = useState<'idle' | 'applying' | 'applied' | 'failed'>('idle');
  const [contextApplySignal, setContextApplySignal] = useState(0);
  const [hostStyles, setHostStyles] = useState<AIHostStyleItem[]>([]);
  const [defaultHostStyleId, setDefaultHostStyleId] = useState<string>('system:facilitator');
  const [selectedHostStyleId, setSelectedHostStyleId] = useState<string>('__default__');
  const [askHostStyleBeforeStart, setAskHostStyleBeforeStart] = useState<boolean>(false);
  const [showHostStyleStartDialog, setShowHostStyleStartDialog] = useState<boolean>(false);
  const [pendingStartStyleId, setPendingStartStyleId] = useState<string>('__default__');
  const [showGuardrailContextDialog, setShowGuardrailContextDialog] = useState(false);
  const [showCalendarPicker, setShowCalendarPicker] = useState(false);
  const [selectedCalendarEvent, setSelectedCalendarEvent] = useState<CalendarEvent | null>(null);
  const pendingStartResolverRef = useRef<((allow: boolean) => void) | null>(null);
  const lastGuardrailSignatureRef = useRef<string>('');
  const lastGuardrailAtRef = useRef<number>(0);
  const [behaviorSpec, setBehaviorSpec] = useState<BehaviorSpecPayload | null>(null);
  const [insightsPanelWidth, setInsightsPanelWidth] = useState<number>(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('insights_panel_width');
      return saved ? Math.max(320, Math.min(Number(saved), 800)) : 380;
    }
    return 380;
  });
  const insightsResizeRef = useRef<{ startX: number; startWidth: number } | null>(null);

  useEffect(() => {
    const checkRecoveries = async () => {
      const hasLivePersistentSession =
        isRecording ||
        Boolean(currentSessionId) ||
        Boolean(getPersistentRecordingClient()?.getSessionId());

      if (hasLivePersistentSession) {
        return;
      }

      if (typeof window !== 'undefined') {
        const launchParams = new URLSearchParams(window.location.search);
        if (launchParams.get('autoStart') === 'true') {
          return;
        }
      }

      const pending = await recoveryService.getAllPendingTranscripts();
      if (pending.length > 0) {
        const latest = pending[0];
        const recoveredId = latest.sessionId || latest.meetingId;

        if (currentSessionId && recoveredId === currentSessionId) {
          return;
        }

        const ageMinutes = (Date.now() - latest.timestamp) / 1000 / 60;
        Analytics.trackRecordingRecoveryDetected({
          recovery_id: recoveredId,
          pending_count: pending.length,
          age_minutes: Math.round(ageMinutes),
          auto_restore_candidate: ageMinutes < 5,
        });

        // If backup is fresh (< 5 mins), auto-restore seamlessly
        if (ageMinutes < 5) {
          console.log('[Recovery] Found fresh backup (< 5 mins), auto-restoring...');
          setMeetingTitle(latest.title);
          setTranscripts(latest.transcripts);
          setPendingRecoveryId(recoveredId);
          setIsRestoredSession(true);

          // Don't set 'recovery' ID to avoid blocking recording controls
          // Just treat it as loaded state ready to append
          setCurrentSessionId(recoveredId);

          toast.success('Session Restored', {
            description: 'Your meeting context has been automatically restored.'
          });
          Analytics.trackRecordingRecoveryRestored({
            recovery_id: recoveredId,
            source: 'auto',
            transcript_count: latest.transcripts.length,
          });
        } else {
          // Old backup: Prompt user
          toast('Unsaved Transcripts Found', {
            description: `Found ${pending.length} unsaved meetings. Click to recover.`,
            action: {
              label: 'Recover',
              onClick: () => handleRecoverTranscripts(latest)
            },
            duration: 10000,
          });
        }
      }
    };
    checkRecoveries();
  }, [currentSessionId, isRecording, setCurrentSessionId, setMeetingTitle, setPendingRecoveryId, setTranscripts]);

  useEffect(() => {
    const loadHostStyles = async () => {
      try {
        const res = await authFetch('/api/user/ai-host-styles', {
          method: 'GET',
          preventLogout: true,
        });
        if (!res.ok) return;
        const data = (await res.json()) as AIHostStylesPayload;
        setHostStyles(data.styles || []);
        setDefaultHostStyleId(data.default_style_id || 'system:facilitator');
      } catch {
        // Keep UI quiet if endpoint not available.
      }
    };
    void loadHostStyles();
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const loadPreference = () => {
      setAskHostStyleBeforeStart(localStorage.getItem('ai_host_ask_before_meeting') === 'true');
    };
    loadPreference();
    const onStorage = (event: StorageEvent) => {
      if (event.key === 'ai_host_ask_before_meeting') {
        loadPreference();
      }
    };
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener('storage', onStorage);
    };
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (window.innerWidth < 1024) {
      setIsTranscriptPanelCollapsed(true);
    }
  }, []);

  useEffect(() => {
    return () => {
      if (pendingStartResolverRef.current) {
        pendingStartResolverRef.current(false);
        pendingStartResolverRef.current = null;
      }
    };
  }, []);

  const handleRecoverTranscripts = async (data: PendingMeetingData) => {
    setMeetingTitle(data.title);
    setTranscripts(data.transcripts);
    setCurrentMeeting({ id: 'recovery', title: data.title });
    const recoveredId = data.sessionId || data.meetingId;
    setPendingRecoveryId(recoveredId);
    setCurrentSessionId(recoveredId);
    Analytics.trackRecordingRecoveryRestored({
      recovery_id: recoveredId,
      source: 'manual',
      transcript_count: data.transcripts.length,
    });
    toast.success('Restored unsaved meeting', { description: 'Please try saving again.' });
  };

  // Catch Me Up feature state
  const [isCatchUpOpen, setIsCatchUpOpen] = useState(false);
  const [catchUpSummary, setCatchUpSummary] = useState('');
  const [isCatchUpLoading, setIsCatchUpLoading] = useState(false);
  const [showCatchUpMenu, setShowCatchUpMenu] = useState(false);
  const [catchUpMinutes, setCatchUpMinutes] = useState<number | null>(null); // null = all, number = last N minutes
  const [customMinutesInput, setCustomMinutesInput] = useState('');


  // Permission check skipped as browser handles it

  const { 
    currentMeeting, 
    setCurrentMeeting, 
    setMeetings, 
    meetings, 
    isMeetingActive, 
    setIsMeetingActive, 
    setIsRecording: setSidebarIsRecording, 
    serverAddress, 
    isCollapsed: sidebarCollapsed, 
    refetchMeetings, 
    activeBotMeetingId,
    setActiveBotMeetingId
  } = useSidebar();
  const handleNavigation = useNavigation('', ''); // Initialize with empty values
  const router = useRouter();
  const searchParams = useSearchParams();
  const botMeetingId = searchParams.get('botMeeting') || activeBotMeetingId;

  // Clear active bot meeting if we are on the home page but no botMeeting param is present.
  // This ensures that navigating back to "New Call" from a bot session correctly
  // restores the local recording "Start Pnyx" button.
  useEffect(() => {
    if (!searchParams.get('botMeeting') && activeBotMeetingId) {
      console.log('[Home] Clearing activeBotMeetingId as no botMeeting param is present');
      setActiveBotMeetingId(null);
    }
  }, [searchParams, activeBotMeetingId, setActiveBotMeetingId]);

  // Track Bot WS Connection
  const [botWsConnected, setBotWsConnected] = useState(false);
  const botWsRef = useRef<WebSocket | null>(null);

  // Auto-connect to bot websocket if botMeetingId is present
  useEffect(() => {
    if (!botMeetingId || !serverAddress) return;

    // Disconnect existing if any
    if (botWsRef.current) {
      botWsRef.current.close();
      botWsRef.current = null;
    }

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // Using the same backend host as serverAddress
    const host = new URL(serverAddress).host;
    const wsUrl = `${wsProtocol}//${host}/ws/bot-meeting/${botMeetingId}`;

    const token = localStorage.getItem('token');
    const protocols = token ? ['access_token', token] : [];

    const ws = new WebSocket(wsUrl, protocols);
    botWsRef.current = ws;

    let pingInterval: NodeJS.Timeout;

    ws.onopen = () => {
      console.log('Bot WS Connected for meeting:', botMeetingId);
      setBotWsConnected(true);
      setCurrentSessionId(botMeetingId); // use it as session ID to keep UI unified
      setMeetingTitle('Live Bot Session'); 
      setIsMeetingActive(true);

      // Heartbeat
      pingInterval = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, 30000);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'transcript') {
          // It's a transcript update
          const update = data.data;
          
          if (update.is_final) {
            setTranscripts((prev) => {
               // duplicate check
               if (prev.some(t => t.id === update.id)) return prev;
               return [...prev, update].sort((a, b) => a.sequence_id - b.sequence_id);
            });
            setPartialTranscript('');
          } else {
             setPartialTranscript(update.text);
          }
        } 
        else if (data.type === 'bot_status') {
           if (data.status === 'completed' || data.status === 'fatal') {
              console.log('Bot session ended:', data.status);
              toast.info(`Bot session ended: ${data.status}`);
              setIsMeetingActive(false);
              // auto-redirect to notes if completed
              if (data.status === 'completed') {
                 router.push(`/meeting-details?id=${botMeetingId}`);
              } else {
                 router.push('/');
              }
           }
        }
        else if (data.type === 'ai_host_suggestion') {
          handleHostSuggestion(data.data);
        }
        else if (data.type === 'ai_host_intervention') {
          handleHostIntervention(data.data);
        }
        else if (data.type === 'ai_host_state_delta') {
          handleHostStateDelta(data.data);
        }
        else if (data.type === 'behavior_spec_sync') {
          setBehaviorSpec(data.payload || data.data || null);
        }
      } catch (err) {
        console.error('Bot WS parse error', err);
      }
    };

    ws.onclose = () => {
      console.log('Bot WS Closed');
      setBotWsConnected(false);
      clearInterval(pingInterval);
    };

    ws.onerror = (err) => {
      console.error('Bot WS Error:', err);
      toast.error('Bot live connection error');
    };

    return () => {
      clearInterval(pingInterval);
      if (botWsRef.current) {
         botWsRef.current.close();
         botWsRef.current = null;
      }
    };
  }, [botMeetingId, serverAddress]);

  // Ref for final buffer flush functionality
  const finalFlushRef = useRef<(() => void) | null>(null);

  // Ref to avoid stale closure issues with transcripts
  const transcriptsRef = useRef<Transcript[]>(transcripts);
  const meetingTitleRef = useRef<string>(meetingTitle);
  const currentSessionIdRef = useRef<string | null>(currentSessionId);
  const pendingRecoveryIdRef = useRef<string | null>(pendingRecoveryId);
  const trackedMeetingStartRef = useRef<string | null>(null);
  const audioTimelineOffsetRef = useRef<number>(audioTimelineOffsetSeconds);

  const isUserAtBottomRef = useRef<boolean>(true);

  // Ref for the transcript scrollable container
  const transcriptContainerRef = useRef<HTMLDivElement>(null);

  // Keep ref updated with current transcripts
  useEffect(() => {
    transcriptsRef.current = transcripts;
  }, [transcripts]);
  useEffect(() => {
    meetingTitleRef.current = meetingTitle;
  }, [meetingTitle]);
  useEffect(() => {
    currentSessionIdRef.current = currentSessionId;
  }, [currentSessionId]);
  useEffect(() => {
    pendingRecoveryIdRef.current = pendingRecoveryId;
  }, [pendingRecoveryId]);
  useEffect(() => {
    audioTimelineOffsetRef.current = audioTimelineOffsetSeconds;
  }, [audioTimelineOffsetSeconds]);

  // Streaming diagnostics polling (Phase 4 hardening visibility)
  useEffect(() => {
    if (!isRecording || !currentSessionId) {
      setStreamingHealth(null);
      return;
    }

    let cancelled = false;
    let interval: ReturnType<typeof setInterval> | null = null;

    const fetchStreamingHealth = async () => {
      try {
        const res = await authFetch(`/sessions/${currentSessionId}/streaming-health`, {
          method: 'GET',
          preventLogout: true
        });
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) setStreamingHealth(data as StreamingHealthPayload);
      } catch {
        // Keep UI quiet if endpoint temporarily unavailable
      }
    };

    void fetchStreamingHealth();
    interval = setInterval(fetchStreamingHealth, 10000);

    return () => {
      cancelled = true;
      if (interval) clearInterval(interval);
    };
  }, [isRecording, currentSessionId]);

  const saveRecoverySnapshot = useCallback(async () => {
    const latestTranscripts = transcriptsRef.current;
    if (!latestTranscripts || latestTranscripts.length === 0) return;

    const recoveryId = currentSessionIdRef.current || pendingRecoveryIdRef.current || `recovery-${Date.now()}`;
    const defaultTemplate = localStorage.getItem('selectedTemplate') || 'standard_meeting';

    await recoveryService.savePendingTranscript({
      meetingId: recoveryId,
      title: meetingTitleRef.current || 'Untitled Meeting',
      transcripts: latestTranscripts,
      timestamp: Date.now(),
      templateId: defaultTemplate,
      sessionId: currentSessionIdRef.current
    });

    if (!pendingRecoveryIdRef.current) {
      pendingRecoveryIdRef.current = recoveryId;
      setPendingRecoveryId(recoveryId);
    }
  }, []);

  // Auto-save effect (stable timer; does not reset on each transcript append)
  useEffect(() => {
    if (!isRecording) return;

    const intervalId = setInterval(async () => {
      console.log('[AutoSave] Saving recovery backup...');
      try {
        await saveRecoverySnapshot();
      } catch (err) {
        console.warn('[AutoSave] Failed to save backup:', err);
      }
    }, 30000);

    return () => clearInterval(intervalId);
  }, [isRecording, saveRecoverySnapshot]);

  // Save shortly after transcript updates while recording.
  useEffect(() => {
    if (!isRecording || transcripts.length === 0) return;

    const timeoutId = setTimeout(() => {
      saveRecoverySnapshot().catch((err) => {
        console.warn('[AutoSave] Failed to save transcript-change snapshot:', err);
      });
    }, 1500);

    return () => clearTimeout(timeoutId);
  }, [isRecording, transcripts.length, saveRecoverySnapshot]);

  // Last-chance snapshot before reload/tab close.
  useEffect(() => {
    if (!isRecording) return;

    const handleBeforeUnload = () => {
      saveRecoverySnapshot().catch(() => { });
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    window.addEventListener('pagehide', handleBeforeUnload);

    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload);
      window.removeEventListener('pagehide', handleBeforeUnload);
    };
  }, [isRecording, saveRecoverySnapshot]);

  const parseTranscriptTimestampMs = useCallback((timestamp?: string): number | null => {
    if (!timestamp) return null;

    // Handle HH:MM:SS local-style strings
    if (/^\d{2}:\d{2}:\d{2}$/.test(timestamp)) {
      const [hours, minutes, seconds] = timestamp.split(':').map(Number);
      const now = new Date();
      const parsed = new Date(
        now.getFullYear(),
        now.getMonth(),
        now.getDate(),
        hours,
        minutes,
        seconds,
        0
      );
      return parsed.getTime();
    }

    const parsed = new Date(timestamp).getTime();
    return Number.isNaN(parsed) ? null : parsed;
  }, []);

  const getTranscriptElapsedSeconds = useCallback((items: Transcript[]): number => {
    if (!items.length) return 0;

    const timestampMs = items
      .map((t) => parseTranscriptTimestampMs(t.timestamp))
      .filter((t): t is number => t !== null)
      .sort((a, b) => a - b);

    if (timestampMs.length >= 2) {
      const elapsed = Math.floor((timestampMs[timestampMs.length - 1] - timestampMs[0]) / 1000);
      if (elapsed > 0) return elapsed;
    }

    const maxAudioEnd = items.reduce((max, t) => {
      const end = t.audio_end_time ?? t.audio_start_time ?? 0;
      return end > max ? end : max;
    }, 0);

    return Math.floor(maxAudioEnd);
  }, [parseTranscriptTimestampMs]);

  // Keep elapsed duration in sync while recording, including silent intervals.
  useEffect(() => {
    if (!isRecording || isPaused) return;

    const intervalId = setInterval(() => {
      setRecordingElapsedSeconds((prev) => prev + 1);
    }, 1000);

    return () => clearInterval(intervalId);
  }, [isRecording, isPaused]);

  // Keep a reasonable baseline when loading recovered transcripts before resuming.
  useEffect(() => {
    if (isRecording) return;
    const inferred = getTranscriptElapsedSeconds(transcripts);
    setRecordingElapsedSeconds((prev) => (inferred > prev ? inferred : prev));
  }, [transcripts, isRecording, getTranscriptElapsedSeconds]);

  // Smart auto-scroll: Track user scroll position
  useEffect(() => {
    const handleScroll = () => {
      const container = transcriptContainerRef.current;
      if (!container) return;

      const { scrollTop, scrollHeight, clientHeight } = container;
      const isAtBottom = scrollTop + clientHeight >= scrollHeight - 10; // 10px tolerance
      isUserAtBottomRef.current = isAtBottom;
    };

    const container = transcriptContainerRef.current;
    if (container) {
      container.addEventListener('scroll', handleScroll);
      return () => container.removeEventListener('scroll', handleScroll);
    }
  }, []);

  // Auto-scroll when transcripts change (only if user is at bottom)
  useEffect(() => {
    // Only auto-scroll if user was at the bottom before new content
    if (isUserAtBottomRef.current && transcriptContainerRef.current) {
      // Wait for Framer Motion animation to complete (150ms) before scrolling
      // This ensures scrollHeight includes the full rendered height of the new transcript
      const scrollTimeout = setTimeout(() => {
        const container = transcriptContainerRef.current;
        if (container) {
          container.scrollTo({
            top: container.scrollHeight,
            behavior: 'smooth'
          });
        }
      }, 150); // Match Framer Motion transition duration

      return () => clearTimeout(scrollTimeout);
    }
  }, [transcripts]);

  const modelOptions: Record<ModelConfig['provider'], string[]> = {
    ollama: models.map(model => model.name),
    claude: ['claude-opus-4-1-20250805'],
    groq: ['llama-3.3-70b-versatile'],
    openrouter: [
      'anthropic/claude-opus-4.1',
      'openai/gpt-5.4',
      'google/gemini-2.5-flash',
    ],
    gemini: ['gemini-2.5-flash'],
    openai: ['gpt-5.4', 'gpt-5', 'gpt-5-mini'],
  };

  useEffect(() => {
    if (models.length > 0 && modelConfig.provider === 'ollama') {
      setModelConfig(prev => ({
        ...prev,
        model: models[0].name
      }));
    }
  }, [models]);

  const whisperModels = [
    'tiny',
    'tiny.en',
    'tiny-q5_1',
    'tiny.en-q5_1',
    'tiny-q8_0',
    'base',
    'base.en',
    'base-q5_1',
    'base.en-q5_1',
    'base-q8_0',
    'small',
    'small.en',
    'small.en-tdrz',
    'small-q5_1',
    'small.en-q5_1',
    'small-q8_0',
    'medium',
    'medium.en',
    'medium-q5_0',
    'medium.en-q5_0',
    'medium-q8_0',
    'large-v1',
    'large-v2',
    'large-v2-q5_0',
    'large-v2-q8_0',
    'large-v3',
    'large-v3-q5_0',
    'large-v3-turbo',
    'large-v3-turbo-q5_0',
    'large-v3-turbo-q8_0'
  ];

  useEffect(() => {
    // Track page view
    Analytics.trackPageView('home');
  }, []);

  // Load saved transcript configuration on mount
  useEffect(() => {
    const loadTranscriptConfig = async () => {
      try {
        const savedConfig = localStorage.getItem('transcript_config');
        if (savedConfig) {
          const config = JSON.parse(savedConfig);
          console.log('Loaded saved transcript config from localStorage:', config);
          setTranscriptModelConfig(config);
        }
      } catch (error) {
        console.error('Failed to load transcript config:', error);
      }
    };
    loadTranscriptConfig();
  }, []);

  useEffect(() => {
    setCurrentMeeting({ id: 'intro-call', title: meetingTitle });

  }, [meetingTitle, setCurrentMeeting]);





  useEffect(() => {
    if (isRecording) {
      const interval = setInterval(() => {
        setBarHeights(prev => {
          const newHeights = [...prev];
          newHeights[0] = Math.random() * 20 + 10 + 'px';
          newHeights[1] = Math.random() * 20 + 10 + 'px';
          newHeights[2] = Math.random() * 20 + 10 + 'px';
          return newHeights;
        });
      }, 300);

      return () => clearInterval(interval);
    }
  }, [isRecording]);

  // Update sidebar recording state
  useEffect(() => {
    setSidebarIsRecording(isRecording);
  }, [isRecording, setSidebarIsRecording]);

  // Handle receiving transcript updates from RecordingControls
  const handleTranscriptReceived = useCallback((newTranscript: TranscriptUpdate) => {
    // Deduplicate by sequence_id
    setTranscripts(prev => {
      // Check if we already have this transcript
      if (prev.some(t => t.sequence_id === newTranscript.sequence_id)) {
        return prev;
      }

      const transcriptData: Transcript = {
        id: `${Date.now()}-${prev.length}`,
        text: newTranscript.text,
        timestamp: newTranscript.timestamp,
        sequence_id: newTranscript.sequence_id,
        is_partial: newTranscript.is_partial,
        // Optional fields
        chunk_start_time: newTranscript.chunk_start_time,
        confidence: newTranscript.confidence,
        audio_start_time: newTranscript.audio_start_time,
        audio_end_time: newTranscript.audio_end_time,
        duration: newTranscript.duration,
      };

      return [...prev, transcriptData].sort((a, b) => (a.sequence_id || 0) - (b.sequence_id || 0));
    });

    // Auto-scroll logic is handled by the existing useEffect on [transcripts]
  }, []);

  const getGuardrailReasonLabel = (reason: AIGuardrailAlert['reason']) => {
    switch (reason) {
      case 'agenda_deviation':
        return 'Agenda';
      case 'no_decision':
        return 'Decision';
      case 'unresolved_question':
        return 'Question';
      case 'missing_context_or_repeat':
        return 'Context';
      default:
        return 'Guardrail';
    }
  };

  const getHostEventLabel = (eventType: AIHostSuggestion['event_type']) => {
    switch (eventType) {
      case 'decision_candidate':
        return 'Decision';
      case 'open_discussion':
        return 'Open Discussion';
      default:
        return eventType
          .split('_')
          .filter(Boolean)
          .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
          .join(' ');
    }
  };

  const getHostEventIcon = (eventType: AIHostSuggestion['event_type']) => {
    switch (eventType) {
      case 'decision_candidate':
        return <CheckCircle2 className="h-4 w-4 text-emerald-600" />;
      case 'open_discussion':
        return <HelpCircle className="h-4 w-4 text-sky-600" />;
      default:
        return <Sparkles className="h-4 w-4 text-indigo-600" />;
    }
  };

  const getHostEventBadgeClasses = (eventType: AIHostSuggestion['event_type']) => {
    switch (eventType) {
      case 'decision_candidate':
        return 'border-amber-300 text-amber-700 bg-amber-50';
      case 'open_discussion':
        return 'border-sky-300 text-sky-700 bg-sky-50';
      default:
        return 'border-indigo-300 text-indigo-700 bg-indigo-50';
    }
  };

  const getGuardrailBadgeClasses = (reason: AIGuardrailAlert['reason']) => {
    switch (reason) {
      case 'agenda_deviation':
        return 'border-blue-300 text-blue-700 bg-blue-50';
      case 'no_decision':
        return 'border-amber-300 text-amber-700 bg-amber-50';
      case 'unresolved_question':
        return 'border-red-300 text-red-700 bg-red-50';
      case 'missing_context_or_repeat':
        return 'border-emerald-300 text-emerald-700 bg-emerald-50';
      default:
        return 'border-gray-300 text-gray-700 bg-gray-50';
    }
  };

  const formatGuardrailTime = (raw?: string) => {
    if (!raw) return '--:--';
    const trimmed = raw.trim();
    const hasTimezone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(trimmed);
    const normalized = hasTimezone ? trimmed : `${trimmed}Z`;
    const dt = new Date(normalized);
    if (Number.isNaN(dt.getTime())) return '--:--';
    return dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  const handleGuardrailAlert = useCallback((alert: AIGuardrailAlert) => {
    const insightKey = `${alert.reason}:${(alert.insight || '').trim().toLowerCase()}`;
    const nowMs = Date.now();
    if (
      insightKey === lastGuardrailSignatureRef.current &&
      nowMs - lastGuardrailAtRef.current < 180000
    ) {
      return;
    }
    lastGuardrailSignatureRef.current = insightKey;
    lastGuardrailAtRef.current = nowMs;

    setActiveGuardrailAlert(alert);
    setGuardrailAlertHistory((prev) => {
      const next = [alert, ...prev.filter((item) => item.id !== alert.id && item.insight !== alert.insight)];
      return next.slice(0, 3);
    });
  }, []);

  const handleHostSuggestion = useCallback((suggestion: AIHostSuggestion) => {
    Analytics.trackAIParticipantInteraction('suggestion_received', {
      suggestion_id: suggestion.id,
      event_type: suggestion.event_type,
      confidence: suggestion.confidence,
    });
    setHostSuggestionQueue((prev) => {
      const deduped = prev.filter((item) => item.id !== suggestion.id);
      return [suggestion, ...deduped].slice(0, 8);
    });
  }, []);

  const handleHostIntervention = useCallback((intervention: AIHostIntervention) => {
    setActiveHostIntervention(intervention);
    if (intervention.linked_suggestion_id) {
      setHostSuggestionQueue((prev) =>
        prev.filter((item) => item.id !== intervention.linked_suggestion_id)
      );
    }
  }, []);

  const handleHostStateDelta = useCallback((state: Record<string, unknown>) => {
    setHostStateDelta(state);
  }, []);

  const handleHostActionAck = useCallback((payload: { action: string; applied: boolean }) => {
    if (!payload?.applied) return;
    if (payload.action === 'pin' || payload.action === 'dismiss') {
      toast.success(`Host suggestion ${payload.action}ned`);
    }
  }, []);

  const pinHostSuggestion = useCallback((suggestionId: string) => {
    Analytics.trackAIParticipantInteraction('suggestion_pinned', {
      suggestion_id: suggestionId,
    });
    setHostSuggestionQueue((prev) => {
      let matched: AIHostSuggestion | null = null;
      const next = prev.filter((item) => {
        if (item.id === suggestionId) {
          matched = item;
          return false;
        }
        return true;
      });
      if (matched) {
        setPinnedHostSuggestions((existing) => {
          const deduped = existing.filter((item) => item.id !== matched!.id);
          return [{ ...matched!, status: 'pinned' }, ...deduped].slice(0, 12);
        });
      }
      return next;
    });
    hostClientRef.current?.pinHostSuggestion(suggestionId);
  }, []);

  const dismissHostSuggestion = useCallback((suggestionId: string) => {
    Analytics.trackAIParticipantInteraction('suggestion_dismissed', {
      suggestion_id: suggestionId,
    });
    setHostSuggestionQueue((prev) => prev.filter((item) => item.id !== suggestionId));
    hostClientRef.current?.dismissHostSuggestion(suggestionId);
  }, []);

  const handleHostClientReady = useCallback((client: AudioStreamClient | null) => {
    hostClientRef.current = client;
  }, []);

  const activeHostStyles = useMemo(
    () => hostStyles.filter((style) => style.is_active),
    [hostStyles]
  );

  const effectiveHostStyleId = useMemo(
    () => (selectedHostStyleId === '__default__' ? defaultHostStyleId : selectedHostStyleId),
    [selectedHostStyleId, defaultHostStyleId]
  );

  const effectiveHostStyle = useMemo(
    () => hostStyles.find((style) => style.id === effectiveHostStyleId) || null,
    [hostStyles, effectiveHostStyleId]
  );

  const effectiveHostStyleMarkdown = useMemo(
    () => effectiveHostStyle?.skill_markdown || '',
    [effectiveHostStyle]
  );

  const effectiveHostModeLabel = useMemo(() => {
    const roleFromMarkdown = extractRoleModeFromMarkdown(effectiveHostStyleMarkdown);
    if (roleFromMarkdown) return toTitleCase(roleFromMarkdown);
    if (effectiveHostStyle?.id?.startsWith('system:')) {
      return toTitleCase(effectiveHostStyle.id.split(':')[1] || 'facilitator');
    }
    if (effectiveHostStyle?.name) return effectiveHostStyle.name;
    return 'Facilitator';
  }, [effectiveHostStyle, effectiveHostStyleMarkdown]);

  const hostStateSuggestedItems = useMemo(() => {
    const raw = (hostStateDelta as Record<string, unknown> | null)?.suggested_items;
    if (!Array.isArray(raw)) return [] as AIHostSuggestion[];
    return raw.filter((item): item is AIHostSuggestion => {
      if (!item || typeof item !== 'object') return false;
      const suggestion = item as Partial<AIHostSuggestion>;
      return (
        typeof suggestion.id === 'string' &&
        typeof suggestion.title === 'string' &&
        typeof suggestion.content === 'string'
      );
    });
  }, [hostStateDelta]);

  useEffect(() => {
    if (!hostStateSuggestedItems.length) return;
    setHostSuggestionQueue((prev) => {
      const merged = new Map<string, AIHostSuggestion>();
      prev.forEach((item) => merged.set(item.id, item));
      hostStateSuggestedItems.forEach((item) => {
        const isPinned = pinnedHostSuggestions.some(p => p.id === item.id);
        if (!isPinned) {
          merged.set(item.id, { ...item, status: 'suggested' });
        }
      });
      return Array.from(merged.values()).slice(0, 30);
    });
  }, [hostStateSuggestedItems, pinnedHostSuggestions]);

  const hostStatePinnedItems = useMemo(() => {
    const raw = (hostStateDelta as Record<string, unknown> | null)?.pinned_items;
    if (!Array.isArray(raw)) return [] as AIHostSuggestion[];
    return raw.filter((item): item is AIHostSuggestion => {
      if (!item || typeof item !== 'object') return false;
      const suggestion = item as Partial<AIHostSuggestion>;
      return (
        typeof suggestion.id === 'string' &&
        typeof suggestion.title === 'string' &&
        typeof suggestion.content === 'string'
      );
    });
  }, [hostStateDelta]);

  useEffect(() => {
    if (!hostStatePinnedItems.length) return;
    setPinnedHostSuggestions((prev) => {
      const merged = new Map<string, AIHostSuggestion>();
      prev.forEach((item) => merged.set(item.id, item));
      hostStatePinnedItems.forEach((item) => merged.set(item.id, { ...item, status: 'pinned' }));
      return Array.from(merged.values()).slice(0, 12);
    });
  }, [hostStatePinnedItems]);

  const unresolvedDiscussionItems = useMemo(() => {
    const raw = (hostStateDelta as Record<string, unknown> | null)?.unresolved_items;
    if (!Array.isArray(raw)) return [] as string[];
    return raw
      .map((item) => String(item || '').trim())
      .filter((item) => item.length > 0)
      .slice(0, 8);
  }, [hostStateDelta]);

  const hostCurrentTopic = useMemo(() => {
    const topicRaw = (hostStateDelta as Record<string, unknown> | null)?.current_topic;
    if (typeof topicRaw === 'string' && topicRaw.trim()) return topicRaw.trim();
    if (activeHostIntervention?.headline) return activeHostIntervention.headline;
    if (meetingGoalInput.trim()) return meetingGoalInput.trim();
    return 'General discussion';
  }, [hostStateDelta, activeHostIntervention, meetingGoalInput]);

  const decisionStatusLabel = useMemo(
    () => ((pinnedHostSuggestions.length > 0 || hostStatePinnedItems.length > 0) ? 'Decision Made' : 'In Progress'),
    [pinnedHostSuggestions.length, hostStatePinnedItems.length]
  );

  const openDiscussionCount = useMemo(() => {
    const derived = hostSuggestionQueue.filter(
      (item) => item.event_type === 'open_discussion'
    ).length;
    return Math.max(unresolvedDiscussionItems.length, derived);
  }, [hostSuggestionQueue, unresolvedDiscussionItems.length]);

  const proposedActionCount = useMemo(() => {
    return hostSuggestionQueue.filter((item) => !CORE_EVENT_TYPES.has(item.event_type)).length;
  }, [hostSuggestionQueue]);

  const pendingDecisions = useMemo(
    () => hostSuggestionQueue.filter((item) => item.event_type === 'decision_candidate'),
    [hostSuggestionQueue]
  );

  const decisionsForPanel = useMemo(() => {
    const merged = new Map<string, AIHostSuggestion>();
    // Only include core decision types in the Decisions panel
    [...hostStatePinnedItems, ...pinnedHostSuggestions].forEach((item) => {
      if (item.event_type === 'decision_candidate') {
        merged.set(item.id, { ...item, status: 'pinned' });
      }
    });
    return Array.from(merged.values()).slice(0, 8);
  }, [hostStatePinnedItems, pinnedHostSuggestions]);

  const discussionsForPanel = useMemo(() => {
    const fromSuggestions = hostSuggestionQueue
      .filter((item) => item.event_type === 'open_discussion')
      .map((item) => item.content.trim())
      .filter((item) => item.length > 0);
    
    // Also include pinned discussions
    const fromPinned = [...hostStatePinnedItems, ...pinnedHostSuggestions]
      .filter((item) => item.event_type === 'open_discussion')
      .map((item) => item.content.trim());

    const merged = Array.from(new Set([...unresolvedDiscussionItems, ...fromPinned]));

    // Include active host intervention if it's an open discussion
    if (activeHostIntervention && activeHostIntervention.event_type === 'open_discussion') {
      const bodyText = activeHostIntervention.body.trim();
      if (bodyText && !merged.includes(bodyText)) merged.push(bodyText);
    }

    fromSuggestions.forEach((item) => {
      if (!merged.includes(item)) merged.push(item);
    });
    return merged.slice(0, 8);
  }, [hostSuggestionQueue, unresolvedDiscussionItems, activeHostIntervention, hostStatePinnedItems, pinnedHostSuggestions]);

  const participantActionsForPanel = useMemo(() => {
    const merged = new Map<string, AIHostSuggestion>();
    
    const GUARDRAIL_TYPES = new Set(['agenda_deviation', 'no_decision', 'unresolved_question', 'missing_context_or_repeat', 'guardrail', 'agenda_drift', 'off_topic']);
    
    // Include both suggested and pinned non-core items, explicitly filtering out guardrails
    hostSuggestionQueue.forEach((item) => {
      if (!CORE_EVENT_TYPES.has(item.event_type) && !GUARDRAIL_TYPES.has(item.event_type)) {
        merged.set(item.id, { ...item, status: 'suggested' });
      }
    });
    
    [...hostStatePinnedItems, ...pinnedHostSuggestions].forEach((item) => {
      if (!CORE_EVENT_TYPES.has(item.event_type) && !GUARDRAIL_TYPES.has(item.event_type)) {
        merged.set(item.id, { ...item, status: 'pinned' });
      }
    });

    return Array.from(merged.values()).slice(0, 8);
  }, [hostSuggestionQueue, hostStatePinnedItems, pinnedHostSuggestions]);

  // ── Auto-expire: move discussions, decisons & insights to Past Insights after 90s ──
  const INSIGHT_EXPIRE_MS = 90_000;

  useEffect(() => {
    const ts = insightTimestampsRef.current;
    const now = Date.now();

    // Register new discussions
    discussionsForPanel.forEach((text) => {
      const key = `disc::${text}`;
      if (!ts.has(key)) ts.set(key, now);
    });
    // Register new suggested or pinned actions/insights
    participantActionsForPanel.forEach((item) => {
      const key = `action::${item.id}`;
      if (!ts.has(key)) ts.set(key, now);
    });
    // Register new pinned decisions
    decisionsForPanel.forEach((item) => {
      const key = `decision::${item.id}`;
      if (!ts.has(key)) ts.set(key, now);
    });

    const interval = setInterval(() => {
      const currentTime = Date.now();
      const newlyExpired: typeof pastInsights = [];

      // Check discussions
      discussionsForPanel.forEach((text) => {
        const key = `disc::${text}`;
        const created = ts.get(key);
        if (created && currentTime - created > INSIGHT_EXPIRE_MS) {
          newlyExpired.push({ type: 'discussion', text, timestamp: created });
          ts.delete(key);
        }
      });

      // Check actions/insights (suggested and pinned)
      participantActionsForPanel.forEach((item) => {
        const key = `action::${item.id}`;
        const created = ts.get(key);
        if (created && currentTime - created > INSIGHT_EXPIRE_MS) {
          newlyExpired.push({ type: 'action', id: item.id, text: item.content, title: item.title, eventType: item.event_type, timestamp: created });
          ts.delete(key);
          if (item.status === 'suggested') {
            setHostSuggestionQueue((prev) => prev.filter((s) => s.id !== item.id));
          } else {
            setPinnedHostSuggestions((prev) => prev.filter((s) => s.id !== item.id));
          }
        }
      });

      // Check decisions
      decisionsForPanel.forEach((item) => {
        const key = `decision::${item.id}`;
        const created = ts.get(key);
        if (created && currentTime - created > INSIGHT_EXPIRE_MS) {
          newlyExpired.push({ type: 'decision', id: item.id, text: item.content, title: item.title, eventType: item.event_type, timestamp: created });
          ts.delete(key);
          setPinnedHostSuggestions((prev) => prev.filter((s) => s.id !== item.id));
        }
      });

      if (newlyExpired.length > 0) {
        setPastInsights((prev) => {
          const existing = new Set(prev.map(d => `${d.type}::${d.id || d.text}`));
          const unique = newlyExpired.filter(d => !existing.has(`${d.type}::${d.id || d.text}`));
          return [...prev, ...unique].slice(-50);
        });
      }
    }, 10_000);

    return () => clearInterval(interval);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [discussionsForPanel, participantActionsForPanel, decisionsForPanel]);

  // Filter out expired items from the active panels
  const expiredDiscTexts = useMemo(() => new Set(pastInsights.filter(i => i.type === 'discussion').map(i => i.text)), [pastInsights]);
  const activeDiscussions = useMemo(() => discussionsForPanel.filter(t => !expiredDiscTexts.has(t)), [discussionsForPanel, expiredDiscTexts]);

  const expiredDecisionIds = useMemo(() => new Set(pastInsights.filter(i => i.type === 'decision').map(i => i.id)), [pastInsights]);
  const activeDecisions = useMemo(() => decisionsForPanel.filter(i => !expiredDecisionIds.has(i.id)), [decisionsForPanel, expiredDecisionIds]);

  const expiredInsightIds = useMemo(() => new Set(pastInsights.filter(i => i.type === 'action').map(i => i.id)), [pastInsights]);
  const activePinnedInsights = useMemo(() => participantActionsForPanel.filter(i => i.status === 'pinned' && !expiredInsightIds.has(i.id)), [participantActionsForPanel, expiredInsightIds]);

  const totalPastInsightCount = useMemo(() => {
    let count = guardrailAlertHistory.length + pastInsights.length;
    // Include summary if it's real AI content
    const backendSummary = (hostStateDelta as Record<string, unknown> | null)?.meeting_summary as string | undefined;
    if (backendSummary?.trim()) count++;
    return count;
  }, [guardrailAlertHistory.length, pastInsights.length, hostStateDelta]);

  const isInsightIntervention = activeHostIntervention &&
    !CORE_EVENT_TYPES.has(activeHostIntervention.event_type) ? activeHostIntervention : null;

  const liveMeetingSummary = useMemo(() => {
    const backendSummary = (hostStateDelta as Record<string, unknown> | null)?.meeting_summary as string | undefined;
    if (backendSummary?.trim()) return backendSummary.trim();
    if (isInsightIntervention && typeof isInsightIntervention !== 'boolean' && isInsightIntervention.body?.trim()) return isInsightIntervention.body.trim();
    if (participantActionsForPanel[0]?.content?.trim()) return participantActionsForPanel[0].content.trim();
    if (meetingGoalInput.trim()) return `Meeting focus: ${meetingGoalInput.trim()}.`;
    if (transcripts.length > 0) {
      return `Meeting is active with ${transcripts.length} transcript segments captured so far.`;
    }
    return 'Meeting intelligence will appear here as discussion progresses.';
  }, [hostStateDelta, isInsightIntervention, participantActionsForPanel, meetingGoalInput, transcripts.length]);

  const parsedMeetingParticipants = useMemo(
    () =>
      meetingParticipantsInput
        .split(',')
        .map((item) => item.trim())
        .filter((item, index, arr) => item.length > 0 && arr.indexOf(item) === index)
        .slice(0, 30),
    [meetingParticipantsInput]
  );

  const manualMeetingContext: ManualMeetingContext = useMemo(
    () => ({
      calendar_event_id: selectedCalendarEvent?.event_id,
      goal: meetingGoalInput.trim(),
      agenda_text: meetingAgendaInput.trim(),
      participants: parsedMeetingParticipants,
    }),
    [meetingGoalInput, meetingAgendaInput, parsedMeetingParticipants, selectedCalendarEvent]
  );

  useEffect(() => {
    setContextAppliedStatus('idle');
  }, [meetingGoalInput, meetingAgendaInput, meetingParticipantsInput, selectedCalendarEvent]);

  const hasManualContextValues = Boolean(
    manualMeetingContext.calendar_event_id ||
    manualMeetingContext.goal ||
    manualMeetingContext.agenda_text ||
    (manualMeetingContext.participants && manualMeetingContext.participants.length > 0)
  );

  const applyManualContext = useCallback(() => {
    if (!hasManualContextValues) {
      setContextAppliedStatus('failed');
      return;
    }
    if (!isRecording) {
      toast.info('Context will be applied when recording starts.');
      return;
    }
    setContextAppliedStatus('applying');
    setContextApplySignal((v) => v + 1);
  }, [hasManualContextValues, isRecording]);

  const handleInlineHostModeChange = useCallback((nextStyleId: string) => {
    setSelectedHostStyleId(nextStyleId);
    const nextStyleMarkdown =
      hostStyles.find((style) => style.id === (nextStyleId === '__default__' ? defaultHostStyleId : nextStyleId))
        ?.skill_markdown || '';
    if (isRecording && nextStyleMarkdown.trim()) {
      hostClientRef.current?.applyHostSkillOverride(nextStyleMarkdown);
    }
    if (isRecording) {
      setTimeout(() => {
        toast.success('AI Participant style updated for this meeting');
      }, 0);
    }
  }, [defaultHostStyleId, hostStyles, isRecording]);

  const handleBeforeStartRecording = useCallback((): boolean | Promise<boolean> => {
    if (!askHostStyleBeforeStart) {
      if (selectedHostStyleId !== '__default__') {
        setSelectedHostStyleId('__default__');
      }
      return true;
    }
    if (isRecording) return true;
    if (showHostStyleStartDialog) return false;

    const initialStyle = selectedHostStyleId === '__default__' ? '__default__' : selectedHostStyleId;
    setPendingStartStyleId(initialStyle);
    setShowHostStyleStartDialog(true);

    return new Promise<boolean>((resolve) => {
      pendingStartResolverRef.current = resolve;
    });
  }, [askHostStyleBeforeStart, isRecording, selectedHostStyleId, showHostStyleStartDialog]);

  const cancelStartStyleDialog = useCallback(() => {
    setShowHostStyleStartDialog(false);
    if (pendingStartResolverRef.current) {
      pendingStartResolverRef.current(false);
      pendingStartResolverRef.current = null;
    }
  }, []);

  const confirmStartStyleDialog = useCallback(() => {
    setSelectedHostStyleId(pendingStartStyleId);
    setShowHostStyleStartDialog(false);
    if (pendingStartResolverRef.current) {
      pendingStartResolverRef.current(true);
      pendingStartResolverRef.current = null;
    }
  }, [pendingStartStyleId]);

  // Sync transcript history and meeting name from backend on reload
  // This fixes the issue where reloading during active recording causes state desync








  /*
  useEffect(() => {
    const loadModels = async () => {
      try {
        const response = await fetch('http://localhost:11434/api/tags', {
          method: 'GET',
          headers: {
            'Content-Type': 'application/json',
          },
        });

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        const modelList = data.models.map((model: any) => ({
          name: model.name,
          id: model.model,
          size: formatSize(model.size),
          modified: model.modified_at
        }));
        setModels(modelList);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load Ollama models');
        console.error('Error loading models:', err);
      }
    };

    loadModels();
  }, []);
  */

  /*
  const formatSize = (size: number): string => {
    if (size < 1024) {
      return `${size} B`;
    } else if (size < 1024 * 1024) {
      return `${(size / 1024).toFixed(1)} KB`;
    } else if (size < 1024 * 1024 * 1024) {
      return `${(size / (1024 * 1024)).toFixed(1)} MB`;
    } else {
      return `${(size / (1024 * 1024 * 1024)).toFixed(1)} GB`;
    }
  };
  */

  const handleRecordingStart = async () => {
    try {
      console.log('handleRecordingStart called - setting up meeting title and state');

      const now = new Date();
      const day = String(now.getDate()).padStart(2, '0');
      const month = String(now.getMonth() + 1).padStart(2, '0');
      const year = String(now.getFullYear()).slice(-2);
      const hours = String(now.getHours()).padStart(2, '0');
      const minutes = String(now.getMinutes()).padStart(2, '0');
      const seconds = String(now.getSeconds()).padStart(2, '0');
      const randomTitle = `Meeting ${day}_${month}_${year}_${hours}_${minutes}_${seconds}`;

      // Only set new title if we are NOT recovering/resuming
      // If pendingRecoveryId exists, we want to keep the restored title
      // If currentSessionId exists, we are resuming an active session
      if (!pendingRecoveryId && !currentSessionId && meetingTitle === '+ New Call') {
        setMeetingTitle(randomTitle);
      }

      // Update state
      console.log('Setting recording state to true');
      setActiveGuardrailAlert(null);
      setGuardrailAlertHistory([]);
      setShowGuardrailHistory(false);
      setActiveHostIntervention(null);
      setHostSuggestionQueue([]);
      setHostStateDelta(null);
      setPinnedHostSuggestions([]);
      lastGuardrailSignatureRef.current = '';
      lastGuardrailAtRef.current = 0;
      setIsRecording(true);
      setIsPaused(false);

      const inferredElapsed = Math.max(
        recordingElapsedSeconds,
        getTranscriptElapsedSeconds(transcripts)
      );

      // If this is a restored session or we have existing transcripts, we are RESUMING
      // We must ensure new transcripts don't collide with old ones
      if (transcripts.length > 0) {
        // Ensure earliest resumed transcript chunks use the resumed timeline.
        audioTimelineOffsetRef.current = inferredElapsed;
        setRecordingElapsedSeconds(inferredElapsed);
        setAudioTimelineOffsetSeconds(inferredElapsed);
        if (currentSessionId && !pendingRecoveryId) {
          console.log('[Recording] Resuming existing session with ID:', currentSessionId);
          setIsRestoredSession(true); // Keep strictly true to indicate continuation
        } else {
          console.log('[Recording] Starting new session. Archiving previous transcripts...');
          // Invalidate sequence IDs of existing transcripts to prevent collision with new stream (which starts at 0)
          setTranscripts(prev => prev.map(t => ({
            ...t,
            sequence_id: -1 // Mark as "historical" so handleTranscriptReceived won't dedup against new 0
          })));
          setIsRestoredSession(false);
        }
      } else {
        setTranscripts([]); // Clear previous transcripts only if starting fresh
        setRecordingElapsedSeconds(0);
        setAudioTimelineOffsetSeconds(0);
      }

      setIsMeetingActive(true);

      // Show recording notification if enabled
      await showRecordingNotification();
    } catch (error) {
      console.error('Failed to start recording:', error);
      alert('Failed to start recording. Check console for details.');
      setIsRecording(false);
      Analytics.trackButtonClick('start_recording_error', 'home_page');
    }
  };

  // Check for autoStartRecording flag and start recording automatically
  // Uses a ref guard to ensure this only fires ONCE on mount, not on re-renders.
  const hasCheckedAutoStartRef = useRef(false);
  useEffect(() => {
    if (hasCheckedAutoStartRef.current) return;

    const checkAutoStartRecording = async () => {
      if (typeof window !== 'undefined') {
        const shouldAutoStart = sessionStorage.getItem('autoStartRecording');
        const urlParams = new URLSearchParams(window.location.search);
        const shouldAutoStartFromUrl = urlParams.get('autoStart') === 'true';

        // Only proceed if there's an explicit auto-start flag
        if (!shouldAutoStart && !shouldAutoStartFromUrl) {
          hasCheckedAutoStartRef.current = true;
          return;
        }

        if ((shouldAutoStart === 'true' || shouldAutoStartFromUrl) && !isRecording && !isMeetingActive) {
          hasCheckedAutoStartRef.current = true;
          console.log('Auto-starting recording from navigation/new-tab launch...');
          sessionStorage.removeItem('autoStartRecording'); // Clear the flag
          if (shouldAutoStartFromUrl) {
            const urlMeetingTitle = urlParams.get('meetingTitle');
            if (urlMeetingTitle) {
              setMeetingTitle(urlMeetingTitle);
            }
            urlParams.delete('autoStart');
            urlParams.delete('source');
            urlParams.delete('meetingTitle');
            const nextQuery = urlParams.toString();
            window.history.replaceState(
              {},
              '',
              `${window.location.pathname}${nextQuery ? `?${nextQuery}` : ''}`
            );
          }
          setResumeStartSignal((v) => v + 1);
        }
      }
    };

    checkAutoStartRecording();
  }, [isRecording, isMeetingActive]);


  // Stop recording and save audio


  const handleWebAudioRecordingStop = async () => {
    console.log('💾 [Web Audio] Saving meeting to database...');
    setSummaryStatus('processing');
    setIsSavingTranscript(true);

    try {
      const freshTranscripts = transcriptsRef.current;

      if (freshTranscripts.length === 0) {
        console.warn('No transcripts to save');
        toast.error('No transcripts to save', {
          description: 'Recording was too short or no speech was detected.'
        });
        return;
      }

      console.log(`💾 Saving ${freshTranscripts.length} transcripts via HTTP API...`);

      // Get default template from localStorage or use 'standard_meeting'
      const defaultTemplate = localStorage.getItem('selectedTemplate') || 'standard_meeting';

      // Call backend API directly - backend will automatically generate notes based on template
      const response = await authFetch('/save-transcript', {
        method: 'POST',
        // headers: { 'Content-Type': 'application/json' }, // authFetch handles Content-Type
        body: JSON.stringify({
          meeting_title: meetingTitle || 'Web Audio Meeting',
          transcripts: freshTranscripts.map((t, index) => ({
            id: t.id || `transcript-${Date.now()}-${Math.random().toString(36).substring(2, 9)}-${index}`,
            text: t.text,
            timestamp: t.timestamp,
            audio_start_time: t.audio_start_time || 0,
            audio_end_time: t.audio_end_time || 0,
            duration: t.duration || 0,
          })),
          folder_path: null,
          template_id: defaultTemplate,
          meeting_id: currentSessionIdRef.current || pendingRecoveryIdRef.current || undefined,
          session_id: currentSessionIdRef.current,
        }),
        preventLogout: true
      });

      if (response.ok) {
        // Clear session ID after successful save
        setCurrentSessionId(null);
      }

      if (!response.ok) {
        throw new Error(`Failed to save meeting: ${response.statusText}`);
      }

      const data = await response.json();
      const meetingId = data.meeting_id;

      console.log('✅ [Web Audio] Meeting saved with ID:', meetingId);
      await Analytics.trackMeetingSaved(meetingId, {
        transcript_count: freshTranscripts.length,
        template_id: defaultTemplate,
        title_length: (meetingTitle || 'Web Audio Meeting').length,
        recovered_session: Boolean(pendingRecoveryIdRef.current || pendingRecoveryId),
      });
      await Analytics.trackMeetingCompleted(meetingId, {
        transcript_count: freshTranscripts.length,
        template_id: defaultTemplate,
      });

      // Store transcript for LLM post-processing
      const fullTranscript = freshTranscripts.map(t => t.text).join('\n');
      setOriginalTranscript(fullTranscript);

      // Cleanup pending recovery if exists
      const recoveryToDelete = pendingRecoveryIdRef.current || pendingRecoveryId;
      if (recoveryToDelete) {
        await recoveryService.deletePendingTranscript(recoveryToDelete);
        setPendingRecoveryId(null);
        pendingRecoveryIdRef.current = null;
      }

      // Update UI
      await refetchMeetings();
      setCurrentMeeting({
        id: meetingId,
        title: meetingTitle || 'Web Audio Meeting'
      });

      // Reset persistent live-session state after a successful save so
      // returning to the home page starts from a clean slate.
      setPartialTranscript('');
      setTranscripts([]);
      setMeetingTitle('+ New Call');
      setRecordingElapsedSeconds(0);
      setAudioTimelineOffsetSeconds(0);
      setIsPaused(false);

      toast.success('Recording saved! Generating meeting notes...', {
        description: `Notes are being generated automatically based on ${defaultTemplate} template.`,
        duration: 5000,
      });

      // Notes are now generated automatically by the backend
      // Navigate to meeting details page after a short delay
      setTimeout(() => {
        router.push(`/meeting-details?id=${meetingId}`);
        Analytics.trackPageView('meeting_details');
      }, 1500);

    } catch (error) {
      console.error('❌ [Web Audio] Failed to save meeting:', error);

      if (error instanceof AuthError) {
        await Analytics.trackMeetingSaveFailed({
          reason: 'auth_error',
          title_length: (meetingTitle || 'Untitled Meeting').length,
          transcript_count: transcriptsRef.current.length,
        });
        // Save to recovery
        const freshTranscripts = transcriptsRef.current;
        const recoveryId = currentSessionIdRef.current || pendingRecoveryIdRef.current || `recovery-${Date.now()}`;
        const defaultTemplate = localStorage.getItem('selectedTemplate') || 'standard_meeting';

        await recoveryService.savePendingTranscript({
          meetingId: recoveryId,
          title: meetingTitle || 'Untitled Meeting',
          transcripts: freshTranscripts,
          timestamp: Date.now(),
          templateId: defaultTemplate,
          sessionId: currentSessionIdRef.current
        });

        setPendingRecoveryId(recoveryId);
        pendingRecoveryIdRef.current = recoveryId;
        setShowReauthModal(true);
        toast.error('Session Expired', {
          description: 'Your session expired while saving. Please log in again to save your meeting.',
          duration: 10000
        });
      } else {
        await Analytics.trackMeetingSaveFailed({
          reason: error instanceof Error ? error.message : 'unknown_error',
          title_length: (meetingTitle || 'Untitled Meeting').length,
          transcript_count: transcriptsRef.current.length,
        });
        toast.error('Failed to save recording', {
          description: error instanceof Error ? error.message : 'Unknown error'
        });
      }
    } finally {
      setSummaryStatus('idle');
      setIsSavingTranscript(false);
      setIsProcessingTranscript(false);
      setIsRecordingDisabled(false);
      setIsStopping(false);
      setIsMeetingActive(false);
      setSidebarIsRecording(false);
    }
  };

  const handleRecordingStop = async (success: boolean = true) => {
    // Immediately update UI state to reflect that recording has stopped
    setIsRecording(false);
    setIsPaused(false);
    setIsRecordingDisabled(false);
    setIsMeetingActive(false);
    setSidebarIsRecording(false);

    if (success) {
      await handleWebAudioRecordingStop();
    }
  };

  const handleTranscriptUpdate = (update: any) => {
    console.log('🎯 handleTranscriptUpdate called with:', {
      sequence_id: update.sequence_id,
      text: update.text.substring(0, 50) + '...',
      timestamp: update.timestamp,
      is_partial: update.is_partial
    });

    const adjustedAudioStart =
      typeof update.audio_start_time === 'number'
        ? update.audio_start_time + audioTimelineOffsetRef.current
        : undefined;
    const adjustedAudioEnd =
      typeof update.audio_end_time === 'number'
        ? update.audio_end_time + audioTimelineOffsetRef.current
        : undefined;

    const newTranscript = {
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
      boundary_score: update.boundary_score
    };

    setTranscripts(prev => {
      console.log('📊 Current transcripts count before update:', prev.length);

      // Check if this transcript already exists
      const exists = prev.some(
        t => t.text === update.text && t.timestamp === update.timestamp
      );
      if (exists) {
        console.log('🚫 Duplicate transcript detected, skipping:', update.text.substring(0, 30) + '...');
        return prev;
      }

      // Add new transcript and sort by audio_start_time to fix ordering
      const updated = [...prev, newTranscript];
      const sorted = updated.sort((a, b) => {
        // Primary sort: audio timestamp (if available)
        if (a.audio_start_time !== undefined && b.audio_start_time !== undefined) {
          // If timestamps are essentially equal (within 100ms), rely on sequence ID
          if (Math.abs(a.audio_start_time - b.audio_start_time) < 0.1) {
            return (a.sequence_id || 0) - (b.sequence_id || 0);
          }
          return a.audio_start_time - b.audio_start_time;
        }
        // Fallback: sequence ID (arrival time)
        return (a.sequence_id || 0) - (b.sequence_id || 0);
      });

      console.log('✅ Added new transcript. New count:', sorted.length);
      console.log('📝 Latest transcript:', {
        id: newTranscript.id,
        text: newTranscript.text.substring(0, 30) + '...',
        sequence_id: newTranscript.sequence_id
      });

      return sorted;
    });
  };

  const generateAISummary = useCallback(async (prompt: string = '') => {
    setSummaryStatus('processing');
    setSummaryError(null);

    try {
      const fullTranscript = transcripts.map(t => t.text).join('\n');
      if (!fullTranscript.trim()) {
        throw new Error('No transcript text available. Please add some text first.');
      }

      // Store the original transcript for regeneration
      setOriginalTranscript(fullTranscript);

      console.log('Generating summary for transcript length:', fullTranscript.length);

      // Process transcript
      console.log('Processing transcript...');

      const processResponse = await authFetch('/process-transcript', {
        method: 'POST',
        // headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: fullTranscript,
          meeting_id: currentMeeting?.id || 'new-meeting', // Ensure meeting_id is passed if available
          model: modelConfig.provider,
          modelName: modelConfig.model,
          chunkSize: 40000,
          overlap: 1000,
          customPrompt: prompt,
        })
      });

      if (!processResponse.ok) throw new Error('Failed to start processing');

      const result = await processResponse.json();
      const process_id = result.process_id;
      console.log('Process ID:', process_id);


      // Poll for summary status
      const pollInterval = setInterval(async () => {
        try {

          const summaryResponse = await authFetch(`/get-summary/${process_id}`);
          const result = await summaryResponse.json();
          console.log('Summary status:', result);

          if (result.status === 'error') {
            setSummaryError(result.error || 'Unknown error');
            setSummaryStatus('error');
            clearInterval(pollInterval);
            return;
          }

          if (result.status === 'completed' && result.data) {
            clearInterval(pollInterval);

            // Remove MeetingName from data before formatting
            const { MeetingName, ...summaryData } = result.data;

            // Update meeting title if available
            if (MeetingName) {
              setMeetingTitle(MeetingName);
            }

            // Format the summary data with consistent styling
            const formattedSummary = Object.entries(summaryData).reduce((acc: Summary, [key, section]: [string, any]) => {
              acc[key] = {
                title: section.title,
                blocks: section.blocks.map((block: any) => ({
                  ...block,
                  // type: 'bullet',
                  color: 'default',
                  content: block.content.trim() // Remove trailing newlines
                }))
              };
              return acc;
            }, {} as Summary);

            setAiSummary(formattedSummary);
            setSummaryStatus('completed');
          }
        } catch (error) {
          console.error('Failed to get summary status:', error);
          if (error instanceof Error) {
            setSummaryError(`Failed to get summary status: ${error.message}`);
          } else {
            setSummaryError('Failed to get summary status: Unknown error');
          }
          setSummaryStatus('error');
          clearInterval(pollInterval);
        }
      }, 3000); // Poll every 3 seconds

      // Cleanup interval on component unmount
      return () => clearInterval(pollInterval);

    } catch (error) {
      console.error('Failed to generate summary:', error);
      if (error instanceof Error) {
        setSummaryError(`Failed to generate summary: ${error.message}`);
      } else {
        setSummaryError('Failed to generate summary: Unknown error');
      }
      setSummaryStatus('error');
    }
  }, [transcripts, modelConfig, serverAddress]);

  const handleSummary = useCallback((summary: any) => {
    setAiSummary(summary);
  }, []);

  const handleSummaryChange = (newSummary: Summary) => {
    console.log('Summary changed:', newSummary);
    setAiSummary(newSummary);
  };

  const handleTitleChange = (newTitle: string) => {
    setMeetingTitle(newTitle);
    setCurrentMeeting({ id: 'intro-call', title: newTitle });
  };

  const syncMeetingTitleToBackend = useCallback(async (title: string) => {
    const candidateMeetingId =
      (currentMeeting?.id && !['intro-call', 'recovery'].includes(currentMeeting.id) ? currentMeeting.id : null) ||
      currentSessionId;

    if (!candidateMeetingId || !title.trim()) return;

    try {
      await authFetch('/save-meeting-title', {
        method: 'POST',
        body: JSON.stringify({ meeting_id: candidateMeetingId, title: title.trim() }),
      });
    } catch (error) {
      console.error('Failed to sync meeting title with backend:', error);
    }
  }, [currentMeeting?.id, currentSessionId]);

  const handleCalendarMeetingSelect = useCallback(async (event: CalendarEvent) => {
    setSelectedCalendarEvent(event);
    setMeetingTitle(event.meeting_title);
    setCurrentMeeting({ id: currentMeeting?.id || 'intro-call', title: event.meeting_title });
    setShowCalendarPicker(false);
    await Analytics.trackCalendarMeetingLinked({
      calendar_event_id: event.event_id,
      attendee_count: event.attendees?.length || 0,
      has_meeting_link: Boolean(event.meeting_link),
      source: 'meeting_start_picker',
    });
    await syncMeetingTitleToBackend(event.meeting_title);
    toast.success(`Connected to ${event.meeting_title}`);
  }, [currentMeeting?.id, syncMeetingTitleToBackend]);

  const handleClearCalendarMeeting = useCallback(() => {
    if (selectedCalendarEvent) {
      Analytics.trackCalendarMeetingUnlinked({
        calendar_event_id: selectedCalendarEvent.event_id,
        source: 'meeting_start_picker',
      });
    }
    setSelectedCalendarEvent(null);
    toast.info('Calendar meeting disconnected');
  }, [selectedCalendarEvent]);

  const getSummaryStatusMessage = (status: SummaryStatus) => {
    switch (status) {
      case 'idle':
        return 'Ready to generate summary';
      case 'processing':
        return isRecording ? 'Processing transcript...' : 'Finalizing transcription...';
      case 'summarizing':
        return 'Generating AI summary...';
      case 'regenerating':
        return 'Regenerating AI summary...';
      case 'completed':
        return 'Summary generated successfully!';
      case 'error':
        return summaryError || 'An error occurred';
      default:
        return '';
    }
  };

  const handleDownloadTranscript = async () => {
    try {
      // Create transcript object with metadata
      const transcriptData = {
        title: meetingTitle,
        timestamp: new Date().toISOString(),
        transcripts: transcripts
      };

      // Generate filename
      const sanitizedTitle = meetingTitle.replace(/[^a-zA-Z0-9]/g, '_');
      const filename = `${sanitizedTitle}_transcript.json`;

      // Create blob and download link
      const blob = new Blob([JSON.stringify(transcriptData, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      console.log('Transcript downloaded successfully');
    } catch (error) {
      console.error('Failed to download transcript:', error);
      alert('Failed to download transcript. Please try again.');
    }
  };

  const handleUploadTranscript = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      const text = await file.text();
      const data = JSON.parse(text);

      // Validate the uploaded file structure
      if (!data.transcripts || !Array.isArray(data.transcripts)) {
        throw new Error('Invalid transcript file format');
      }

      // Update state with uploaded data
      setMeetingTitle(data.title || 'Uploaded Transcript');
      setTranscripts(data.transcripts);

      // Generate summary for the uploaded transcript
      handleSummary(data.transcripts);
    } catch (error) {
      console.error('Error uploading transcript:', error);
      alert('Failed to upload transcript. Please make sure the file format is correct.');
    }
  };

  const handleRegenerateSummary = useCallback(async () => {
    if (!originalTranscript.trim()) {
      console.error('No original transcript available for regeneration');
      return;
    }

    setSummaryStatus('regenerating');
    setSummaryError(null);

    try {
      console.log('Regenerating summary with original transcript...');

      // Process transcript
      console.log('Processing transcript...');

      const processResponse = await authFetch('/process-transcript', {
        method: 'POST',
        // headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: originalTranscript,
          meeting_id: currentMeeting?.id, // Important for security and ownership
          model: modelConfig.provider,
          modelName: modelConfig.model,
          chunkSize: 40000,
          overlap: 1000,
        })
      });

      if (!processResponse.ok) throw new Error('Failed to start processing');

      const result = await processResponse.json();
      const process_id = result.process_id;
      console.log('Process ID:', process_id);

      // Poll for summary status
      const pollInterval = setInterval(async () => {
        try {

          const summaryResponse = await authFetch(`/get-summary/${process_id}`);
          const result = await summaryResponse.json();
          console.log('Summary status:', result);

          if (result.status === 'error') {
            setSummaryError(result.error || 'Unknown error');
            setSummaryStatus('error');
            clearInterval(pollInterval);
            return;
          }

          if (result.status === 'completed' && result.data) {
            clearInterval(pollInterval);

            // Remove MeetingName from data before formatting
            const { MeetingName, ...summaryData } = result.data;

            // Update meeting title if available
            if (MeetingName) {
              setMeetingTitle(MeetingName);
            }

            // Format the summary data with consistent styling
            const formattedSummary = Object.entries(summaryData).reduce((acc: Summary, [key, section]: [string, any]) => {
              acc[key] = {
                title: section.title,
                blocks: section.blocks.map((block: any) => ({
                  ...block,
                  // type: 'bullet',
                  color: 'default',
                  content: block.content.trim()
                }))
              };
              return acc;
            }, {} as Summary);

            setAiSummary(formattedSummary);
            setSummaryStatus('completed');
          } else if (result.status === 'error') {
            clearInterval(pollInterval);
            throw new Error(result.error || 'Failed to generate summary');
          }
        } catch (error) {
          clearInterval(pollInterval);
          console.error('Failed to get summary status:', error);
          if (error instanceof Error) {
            setSummaryError(error.message);
          } else {
            setSummaryError('An unexpected error occurred');
          }
          setSummaryStatus('error');
          setAiSummary(null);
        }
      }, 1000);

      return () => clearInterval(pollInterval);
    } catch (error) {
      console.error('Failed to regenerate summary:', error);
      if (error instanceof Error) {
        setSummaryError(error.message);
      } else {
        setSummaryError('An unexpected error occurred');
      }
      setSummaryStatus('error');
      setAiSummary(null);
    }
  }, [originalTranscript, modelConfig, serverAddress]);

  const handleCopyTranscript = useCallback(async () => {
    // Format timestamps as recording-relative [MM:SS] instead of wall-clock time
    const formatTime = (seconds: number | undefined): string => {
      if (seconds === undefined) return '[00:00]';
      const totalSecs = Math.floor(seconds);
      const mins = Math.floor(totalSecs / 60);
      const secs = totalSecs % 60;
      return `[${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}]`;
    };

    const fullTranscript = transcripts
      .map((t) => {
        const speaker = (t.speaker || '').trim();
        const speakerPrefix = speaker ? `${speaker}: ` : '';
        return `${formatTime(t.audio_start_time)} ${speakerPrefix}${t.text}`;
      })
      .join('\n');
    try {
      await navigator.clipboard.writeText(fullTranscript);
      toast.success("Transcript copied to clipboard");
    } catch (err) {
      console.error('Failed to copy transcript:', err);
      toast.error('Failed to copy transcript to clipboard');
    }
  }, [transcripts]);

  // Handle Catch Me Up - get quick summary of meeting so far
  // Get meeting duration in minutes for time range validation
  const getMeetingDurationMinutes = useCallback(() => {
    const elapsedSeconds = Math.max(
      recordingElapsedSeconds,
      getTranscriptElapsedSeconds(transcripts)
    );
    return Math.ceil(elapsedSeconds / 60);
  }, [transcripts, recordingElapsedSeconds, getTranscriptElapsedSeconds]);

  const handleCatchUp = useCallback(async (minutes: number | null = null) => {
    if (!transcripts.length && minutes === null) {
      toast.error("No transcript yet to catch up on");
      return;
    }

    setShowCatchUpMenu(false);
    setIsCatchUpOpen(true);
    setIsCatchUpLoading(true);
    setCatchUpSummary('');
    setCatchUpMinutes(minutes);

    try {
      // Filter transcripts by time range if specified
      let filteredTranscripts = transcripts;
      const nowMs = Date.now();
      const windowStartMs = minutes !== null ? nowMs - (minutes * 60 * 1000) : null;

      if (minutes !== null && windowStartMs !== null && transcripts.length > 0) {
        filteredTranscripts = transcripts.filter((t) => {
          const ts = parseTranscriptTimestampMs(t.timestamp);
          return ts !== null && ts >= windowStartMs;
        });
      }

      // Safety limit: only take last 1000 transcripts to avoid payload too large errors
      if (filteredTranscripts.length > 1000) {
        console.warn('[CatchUp] Limiting to last 1000 transcripts to avoid payload issues');
        filteredTranscripts = filteredTranscripts.slice(-1000);
      }

      const transcriptEntries = filteredTranscripts
        .filter((t) => (t.stability_class || 'stable') === 'stable')
        .map((t) => ({
          text: t.text,
          timestamp: t.timestamp,
          audio_start_time: t.audio_start_time,
          stability_score: t.stability_score,
          stability_class: t.stability_class || 'stable',
        }));

      // Optimization: Limit the number of transcripts sent to prevent payload size issues
      // 1000 transcripts is roughly 1.5 - 2 hours of meeting data
      const MAX_CATCHUP_TRANSCRIPTS = 1000;
      const limitedTranscriptEntries = transcriptEntries.length > MAX_CATCHUP_TRANSCRIPTS
        ? transcriptEntries.slice(-MAX_CATCHUP_TRANSCRIPTS)
        : transcriptEntries;

      if (transcriptEntries.length > MAX_CATCHUP_TRANSCRIPTS) {
        console.warn(`[CatchUp] Truncating ${transcriptEntries.length} transcripts to latest ${MAX_CATCHUP_TRANSCRIPTS} to prevent payload size issues.`);
      }

      const timeLabel = minutes ? `last ${minutes} minutes` : 'entire meeting';
      console.log(`[CatchUp] Summarizing ${timeLabel}: ${limitedTranscriptEntries.length} transcripts`);
      await Analytics.trackCatchUpRequested(minutes, {
        transcript_count: limitedTranscriptEntries.length,
        meeting_elapsed_seconds: Math.max(
          recordingElapsedSeconds,
          getTranscriptElapsedSeconds(transcripts)
        ),
      });

      // The catch-up endpoint currently only supports gemini and groq
      const supportedProviders = ['gemini', 'groq'];
      let provider = modelConfig?.provider || 'gemini';
      let modelName = modelConfig?.model || 'gemini-2.5-flash';

      if (!supportedProviders.includes(provider)) {
        console.warn(`[CatchUp] Unsupported provider "${provider}" selected. Falling back to Gemini.`);
        provider = 'gemini';
        modelName = 'gemini-2.5-flash';
      }

      const response = await authFetch('/catch-up', {
        method: 'POST',
        body: JSON.stringify({
          transcripts: limitedTranscriptEntries,
          model: provider,
          model_name: modelName,
          window_minutes: minutes,
          window_start_iso: windowStartMs ? new Date(windowStartMs).toISOString() : null,
          window_end_iso: new Date(nowMs).toISOString(),
          meeting_elapsed_seconds: Math.max(
            recordingElapsedSeconds,
            getTranscriptElapsedSeconds(transcripts)
          )
        })
      });

      if (!response.ok) {
        throw new Error(`Failed to get catch-up: ${response.statusText}`);
      }

      const contentType = response.headers.get('content-type') || '';
      if (contentType.includes('application/json')) {
        const data = await response.json();
        setCatchUpSummary(data.summary || data.error || 'No catch-up available for this window.');
        return;
      }

      // Stream the response
      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');

      const decoder = new TextDecoder();
      let summary = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        summary += chunk;
        setCatchUpSummary(summary);
      }
    } catch (error) {
      console.error('Catch-up error:', error);
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      toast.error('Catch Up Failed', {
        description: `Could not generate summary: ${errorMessage}. Please try again.`
      });
      setCatchUpSummary('Error getting catch-up summary. Please try again.');
    } finally {
      setIsCatchUpLoading(false);
    }
  }, [transcripts, parseTranscriptTimestampMs, modelConfig, recordingElapsedSeconds, getTranscriptElapsedSeconds]);


  const handleGenerateSummary = useCallback(async () => {
    if (!transcripts.length) {
      console.log('No transcripts available for summary');
      return;
    }

    try {
      await generateAISummary(customPrompt);
    } catch (error) {
      console.error('Failed to generate summary:', error);
      if (error instanceof Error) {
        setSummaryError(error.message);
      } else {
        setSummaryError('Failed to generate summary: Unknown error');
      }
    }
  }, [transcripts, generateAISummary]);

  // Handle transcript configuration save
  const handleSaveTranscriptConfig = async (config: TranscriptModelProps) => {
    try {
      console.log('[HomePage] Saving transcript config to localStorage:', config);
      localStorage.setItem('transcript_config', JSON.stringify(config));
      console.log('[HomePage] ✅ Successfully saved transcript config');
    } catch (error) {
      console.error('[HomePage] ❌ Failed to save transcript config:', error);
    }
  };

  // Handle confidence indicator toggle
  const handleConfidenceToggle = (checked: boolean) => {
    setShowConfidenceIndicator(checked);
    if (typeof window !== 'undefined') {
      localStorage.setItem('showConfidenceIndicator', checked.toString());
    }
    // Trigger a custom event to notify other components
    window.dispatchEvent(new CustomEvent('confidenceIndicatorChanged', { detail: checked }));
  };



  const isSummaryLoading = summaryStatus === 'processing' || summaryStatus === 'summarizing' || summaryStatus === 'regenerating';

  const isProcessingStop = summaryStatus === 'processing' || isProcessingTranscript
  useEffect(() => {
    // Honor saved model settings from backend (including OpenRouter)
    const fetchModelConfig = async () => {
      try {
        const response = await authFetch('/get-model-config');
        if (response.ok) {
          const data = await response.json();
          if (data && data.provider) {
            setModelConfig(prev => ({
              ...prev,
              provider: data.provider,
              model: data.model || prev.model,
              whisperModel: data.whisperModel || prev.whisperModel,
            }));
          }
        }
      } catch (error) {
        console.error('Failed to fetch saved model config in page.tsx:', error);
      }
    };
    if (serverAddress) fetchModelConfig();
  }, [serverAddress]);

  // Load device preferences on startup
  useEffect(() => {
    const loadDevicePreferences = async () => {
      try {
        const savedDevices = localStorage.getItem('device_preferences');
        if (savedDevices) {
          const prefs = JSON.parse(savedDevices);
          if (prefs && (prefs.micDevice || prefs.systemDevice)) {
            setSelectedDevices(prefs);
            console.log('Loaded device preferences from localStorage:', prefs);
          }
        }
      } catch (error) {
        console.log('No device preferences found or failed to load:', error);
      }
    };
    loadDevicePreferences();
  }, []);

  // Load language preference on startup
  useEffect(() => {
    const loadLanguagePreference = async () => {
      try {
        const savedLanguage = localStorage.getItem('language_preference');
        if (savedLanguage) {
          setSelectedLanguage(savedLanguage);
          console.log('Loaded language preference:', savedLanguage);
        } else {
          setSelectedLanguage('auto-translate');
        }
      } catch (error) {
        console.log('No language preference found or failed to load, using default (auto-translate):', error);
        setSelectedLanguage('auto-translate');
      }
    };
    loadLanguagePreference();
  }, []);

  const handleDiscardRecovery = async () => {
    if (confirm('Are you sure you want to discard this recovered meeting? This cannot be undone.')) {
      const recoveryToDelete = pendingRecoveryIdRef.current || pendingRecoveryId;
      if (recoveryToDelete) {
        await recoveryService.deletePendingTranscript(recoveryToDelete);
        setPendingRecoveryId(null);
        pendingRecoveryIdRef.current = null;
      }
      setTranscripts([]);
      setMeetingTitle('+ New Call');
      setCurrentMeeting(null);
      toast.info('Recovered meeting discarded');
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      className="flex flex-col h-screen dot-grid-bg"
    >
      <EncryptionCTABar isRecording={isRecording} />
      {showErrorAlert && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <Alert className="max-w-md mx-4 border-red-200 bg-white shadow-xl">
            <AlertTitle className="text-red-800">Recording Stopped</AlertTitle>
            <AlertDescription className="text-red-700">
              {errorMessage}
              <button
                onClick={() => setShowErrorAlert(false)}
                className="ml-2 text-red-600 hover:text-red-800 underline"
              >
                Dismiss
              </button>
            </AlertDescription>
          </Alert>
        </div>
      )}
      {showChunkDropWarning && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <Alert className="max-w-lg mx-4 border-yellow-200 bg-white shadow-xl">
            <AlertTitle className="text-yellow-800">Transcription Performance Warning</AlertTitle>
            <AlertDescription className="text-yellow-700">
              {chunkDropMessage}
              <button
                onClick={() => setShowChunkDropWarning(false)}
                className="ml-2 text-yellow-600 hover:text-yellow-800 underline"
              >
                Dismiss
              </button>
            </AlertDescription>
          </Alert>
        </div>
      )}
      <div className="flex flex-1 overflow-hidden">
        {/* Left side - Transcript */}
        <div className="flex-1 border-r border-gray-200/60 bg-transparent flex flex-col overflow-y-auto">
          {/* Title area - Sticky header */}
          <div className={`sticky top-0 z-10 border-gray-200/60 transition-all ${
            (isMeetingActive || isRecording || transcripts.length > 0) ? 'glass-surface p-4 border-b' : 'p-0 h-0 overflow-hidden'
          }`}>
            <div className="flex flex-col space-y-3">
              {/* <SetupRequirements /> */}
              <div className="flex  flex-col space-y-2">
                {(isMeetingActive || isRecording || transcripts.length > 0) && (
                  <div className="w-full flex flex-col gap-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <h1 className="text-xl font-semibold text-gray-900 truncate max-w-[420px]" title={meetingTitle}>
                          {meetingTitle}
                        </h1>
                        {isRecording && (
                          <span className="inline-flex items-center rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-xs font-medium text-red-700">
                            Live
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => setShowGuardrailContextDialog(true)}
                          className="inline-flex items-center gap-1.5 rounded-md border border-emerald-200 bg-emerald-50 px-2.5 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100 transition-colors shadow-sm"
                          title="Meeting context"
                        >
                          <Settings className="h-3.5 w-3.5" />
                          Meeting Context
                        </button>
                        <button
                          type="button"
                          onClick={() => setShowCalendarPicker(true)}
                          className="inline-flex items-center gap-1.5 rounded-md border border-blue-200 bg-blue-50 px-2.5 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100 transition-colors shadow-sm"
                          title="Pick Calendar Meeting"
                        >
                          <Calendar className="h-3.5 w-3.5" />
                          Calendar Event
                        </button>
                        <span className="text-xs font-medium text-gray-500">Mode</span>
                        <select
                          value={selectedHostStyleId}
                          onChange={(e) => handleInlineHostModeChange(e.target.value)}
                          className="rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 focus:border-gray-400 focus:outline-none"
                          title={`Current AI Participant style: ${effectiveHostModeLabel}`}
                        >
                          <option value="__default__">
                            Default ({toTitleCase(defaultHostStyleId.replace(/^.*:/, '') || 'facilitator')})
                          </option>
                          {activeHostStyles.map((style) => (
                            <option key={style.id} value={style.id}>
                              {style.name} ({style.source})
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>
                    {/* {selectedCalendarEvent && (
                      <div className="flex items-center justify-between gap-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2 text-sm font-medium text-blue-800">
                            <CheckCircle2 className="h-4 w-4" />
                            <span className="truncate">Connected to meeting: {selectedCalendarEvent.meeting_title}</span>
                          </div>
                          <p className="mt-1 text-xs text-blue-700">
                            {formatCalendarEventTimeIST(selectedCalendarEvent.start_time, selectedCalendarEvent.end_time)}
                          </p>
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          <button
                            type="button"
                            onClick={() => setShowCalendarPicker(true)}
                            className="rounded-md border border-blue-300 bg-white px-2.5 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100 transition-colors"
                          >
                            Change
                          </button>
                          <button
                            type="button"
                            onClick={handleClearCalendarMeeting}
                            className="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-100 transition-colors"
                          >
                            Clear
                          </button>
                        </div>
                      </div>
                    )} */}
                  </div>
                )}
                <div className="flex justify-center items-center space-x-2">
                  {/* {showSummary && !isRecording && (
                    <>
                      <button
                        onClick={handleGenerateSummary}
                        disabled={summaryStatus === 'processing'}
                        className={`px-3 py-2 border rounded-md transition-all duration-200 inline-flex items-center gap-2 shadow-sm ${
                          summaryStatus === 'processing'
                            ? 'bg-yellow-50 border-yellow-200 text-yellow-700'
                            : transcripts.length === 0
                            ? 'bg-gray-50 border-gray-200 text-gray-400 cursor-not-allowed'
                            : 'bg-green-50 border-green-200 text-green-700 hover:bg-green-100 hover:border-green-300 active:bg-green-200'
                        }`}
                        title={
                          summaryStatus === 'processing'
                            ? 'Generating summary...'
                            : transcripts.length === 0
                            ? 'No transcript available'
                            : 'Generate AI Summary'
                        }
                      >
                        {summaryStatus === 'processing' ? (
                          <>
                            <svg className="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                            </svg>
                            <span className="text-sm">Processing...</span>
                          </>
                        ) : (
                          <>
                            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
                            </svg>
                            <span className="text-sm">Generate Note</span>
                          </>
                        )}
                      </button>
                      <button
                        onClick={() => setShowModelSettings(true)}
                        className="px-3 py-2 border rounded-md transition-all duration-200 inline-flex items-center gap-2 shadow-sm bg-gray-50 border-gray-200 text-gray-700 hover:bg-gray-100 hover:border-gray-300 active:bg-gray-200"
                        title="Model Settings"
                      >
                        <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                          <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                        </svg>
                      </button>
                    </>
                  )} */}
                </div>

                {/* {showSummary && !isRecording && (
                  <>
                    <button
                      onClick={handleGenerateSummary}
                      disabled={summaryStatus === 'processing'}
                      className={`px-3 py-2 border rounded-md transition-all duration-200 inline-flex items-center gap-2 shadow-sm ${
                        summaryStatus === 'processing'
                          ? 'bg-yellow-50 border-yellow-200 text-yellow-700'
                          : transcripts.length === 0
                          ? 'bg-gray-50 border-gray-200 text-gray-400 cursor-not-allowed'
                          : 'bg-green-50 border-green-200 text-green-700 hover:bg-green-100 hover:border-green-300 active:bg-green-200'
                      }`}
                      title={
                        summaryStatus === 'processing'
                          ? 'Generating summary...'
                          : transcripts.length === 0
                          ? 'No transcript available'
                          : 'Generate AI Summary'
                      }
                    >
                      {summaryStatus === 'processing' ? (
                        <>
                          <svg className="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                          </svg>
                          <span className="text-sm">Processing...</span>
                        </>
                      ) : (
                        <>
                          <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
                          </svg>
                          <span className="text-sm">Generate Note</span>
                        </>
                      )}
                    </button>
                    <button
                      onClick={() => setShowModelSettings(true)}
                      className="px-3 py-2 border rounded-md transition-all duration-200 inline-flex items-center gap-2 shadow-sm bg-gray-50 border-gray-200 text-gray-700 hover:bg-gray-100 hover:border-gray-300 active:bg-gray-200"
                      title="Model Settings"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                      </svg>
                    </button>
                  </>
                )} */}
              </div>
            </div>
          </div>

          {/* Permission Warning (only for Tauri mode) */}

          <div className="pb-28">
            <div className="mx-auto w-full max-w-[1200px] px-4 lg:px-6 space-y-4">
              {!isRecording && transcripts.length === 0 && !pendingRecoveryId && !isProcessingStop && !isSavingTranscript && (
                <div className="flex min-h-[480px] items-center justify-center">
                  <div className="w-full max-w-2xl rounded-3xl border border-slate-200/60 bg-white/60 backdrop-blur-sm px-10 py-14 text-center shadow-sm">
                    <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-red-50 text-red-500 shadow-sm">
                      <Sparkles className="h-8 w-8" />
                    </div>
                    <h2 className="mt-8 text-3xl font-semibold tracking-tight text-slate-900">Pnyx is ready when you are</h2>
                    <p className="mx-auto mt-4 max-w-xl text-sm leading-6 text-slate-600">
                      Start Pnyx to capture live transcript, decisions, action items, and meeting notes in real time.
                    </p>
                    <div className="mt-8 flex flex-wrap items-center justify-center gap-4 text-xs font-medium text-slate-500">
                      <span className="rounded-full border border-slate-200 bg-white px-3 py-1.5">Live transcript</span>
                      <span className="rounded-full border border-slate-200 bg-white px-3 py-1.5">AI meeting notes</span>
                      <span className="rounded-full border border-slate-200 bg-white px-3 py-1.5">Action items</span>
                    </div>
                  </div>
                </div>
              )}
              {isRecording && (
                <>
                  {/* ───── Dynamic Insight Chips ───── */}
                  <div className="flex flex-col gap-8 items-center pt-4">

                    {/* Floating Insight Chips - only appear when data surfaces */}
                    <AnimatePresence mode="popLayout">
                      {/* Active Guardrail Alert Chip */}
                      {activeGuardrailAlert && (
                        <motion.div
                          key={`guardrail-${activeGuardrailAlert.id}`}
                          initial={{ opacity: 0, y: 20, scale: 0.95 }}
                          animate={{ opacity: 1, y: 0, scale: 1 }}
                          exit={{ opacity: 0, y: -10, scale: 0.95 }}
                          transition={{ duration: 0.3 }}
                          className="insight-chip w-full max-w-xl px-5 py-4"
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="flex-1">
                              <div className="flex items-center gap-2 mb-2">
                                <AlertCircle className="h-4 w-4 text-rose-500" />
                                <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold ${getGuardrailBadgeClasses(activeGuardrailAlert.reason)}`}>
                                  {getGuardrailReasonLabel(activeGuardrailAlert.reason)}
                                </span>
                                <span className="text-[11px] text-gray-400">
                                  {formatGuardrailTime(activeGuardrailAlert.updated_at || activeGuardrailAlert.timestamp)}
                                </span>
                              </div>
                              <p className="text-sm text-gray-800 leading-relaxed">{activeGuardrailAlert.insight}</p>
                            </div>
                            <span className="rounded-full bg-rose-100 px-2 py-1 text-[11px] font-semibold text-rose-700 shrink-0">
                              {Math.round(activeGuardrailAlert.confidence * 100)}%
                            </span>
                          </div>
                        </motion.div>
                      )}

                      {/* Pending Decision Chips */}
                      {pendingDecisions.map((item) => (
                        <motion.div
                          key={`decision-${item.id}`}
                          layout
                          layoutId={item.id}
                          initial={{ opacity: 0, y: 20, scale: 0.95 }}
                          animate={{ opacity: 1, y: 0, scale: 1 }}
                          exit={{ opacity: 0, y: -10, scale: 0.95 }}
                          transition={{ duration: 0.3 }}
                          className="insight-chip w-full max-w-xl px-5 py-4"
                        >
                          <div className="flex items-start gap-3">
                            <div className="mt-0.5">{getHostEventIcon(item.event_type)}</div>
                            <div className="flex-1">
                              <div className="flex items-center justify-between gap-2">
                                <p className="text-sm font-medium text-gray-900">{item.title}</p>
                                <span className="text-[11px] text-gray-400">
                                  {Math.round(item.confidence * 100)}%
                                </span>
                              </div>
                              <p className="mt-1 text-xs text-gray-600">{item.content}</p>
                              <div className="mt-3 flex items-center gap-2">
                                <button
                                  type="button"
                                  onClick={() => pinHostSuggestion(item.id)}
                                  className="rounded-full border border-emerald-300 bg-emerald-50 px-3 py-1 text-[11px] font-semibold text-emerald-800 transition-colors hover:bg-emerald-100"
                                >
                                  Pin Decision
                                </button>
                                <button
                                  type="button"
                                  onClick={() => dismissHostSuggestion(item.id)}
                                  className="rounded-full border border-gray-200 bg-white px-3 py-1 text-[11px] font-medium text-gray-600 transition-colors hover:bg-gray-50"
                                >
                                  Dismiss
                                </button>
                              </div>
                            </div>
                          </div>
                        </motion.div>
                      ))}

                      {/* Participant Action Chips (non-core insights) */}
                      {participantActionsForPanel.filter(item => item.status === 'suggested').slice(0, 2).map((item) => (
                        <motion.div
                          key={`action-${item.id}`}
                          layout
                          layoutId={item.id}
                          initial={{ opacity: 0, y: 20, scale: 0.95 }}
                          animate={{ opacity: 1, y: 0, scale: 1 }}
                          exit={{ opacity: 0, y: -10, scale: 0.95 }}
                          transition={{ duration: 0.3 }}
                          className="insight-chip w-full max-w-xl px-5 py-4"
                        >
                          <div className="flex items-start gap-3">
                            <div className="mt-0.5">{getHostEventIcon(item.event_type)}</div>
                            <div className="flex-1">
                              <div className="flex items-center justify-between gap-2">
                                <p className="text-sm font-medium text-gray-900">{item.title}</p>
                                <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${getHostEventBadgeClasses(item.event_type)}`}>
                                  {getHostEventLabel(item.event_type)}
                                </span>
                              </div>
                              <p className="mt-1 text-xs text-gray-600">{item.content}</p>
                              <div className="mt-3 flex items-center gap-2">
                                <button
                                  type="button"
                                  onClick={() => pinHostSuggestion(item.id)}
                                  className="rounded-full border border-indigo-300 bg-indigo-50 px-3 py-1 text-[11px] font-semibold text-indigo-800 transition-colors hover:bg-indigo-100"
                                >
                                  Pin
                                </button>
                                <button
                                  type="button"
                                  onClick={() => dismissHostSuggestion(item.id)}
                                  className="rounded-full border border-gray-200 bg-white px-3 py-1 text-[11px] font-medium text-gray-600 transition-colors hover:bg-gray-50"
                                >
                                  Dismiss
                                </button>
                              </div>
                            </div>
                          </div>
                        </motion.div>
                      ))}
                    </AnimatePresence>

                    {/* ───── Always-Visible Pinned Decisions ───── */}
                    {activeDecisions.length > 0 && (
                      <div className="w-full max-w-xl">
                        <h4 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-emerald-700 mb-3">
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          Pinned Decisions ({activeDecisions.length})
                        </h4>
                        <div className="space-y-2">
                          {activeDecisions.map((item) => (
                            <motion.div
                              key={`pinned-dec-${item.id}`}
                              initial={{ opacity: 0, y: 10 }}
                              animate={{ opacity: 1, y: 0 }}
                              className="rounded-xl border border-emerald-200 bg-emerald-50/50 px-4 py-3"
                            >
                              <p className="text-sm font-medium text-emerald-900">{item.title}</p>
                              <p className="mt-1 text-xs text-emerald-800">{item.content}</p>
                            </motion.div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* ───── Always-Visible Pinned Insights ───── */}
                    {activePinnedInsights.length > 0 && (
                      <div className="w-full max-w-xl">
                        <h4 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-indigo-700 mb-3">
                          <Sparkles className="h-3.5 w-3.5" />
                          AI Participant Insights ({activePinnedInsights.length})
                        </h4>
                        <div className="space-y-2">
                          {activePinnedInsights.map((item) => (
                            <motion.div
                              key={`pinned-act-${item.id}`}
                              initial={{ opacity: 0, y: 10 }}
                              animate={{ opacity: 1, y: 0 }}
                              className="rounded-xl border border-indigo-200 bg-indigo-50/50 px-4 py-3"
                            >
                              <p className="text-sm font-medium text-gray-900">{item.title}</p>
                              <p className="mt-1 text-xs text-gray-700">{item.content}</p>
                            </motion.div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* ───── Open Discussions (visible when active) ───── */}
                    {activeDiscussions.length > 0 && (
                      <div className="w-full max-w-xl">
                        <h4 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-blue-700 mb-3">
                          <MessageCircle className="h-3.5 w-3.5" />
                          Open Discussions ({activeDiscussions.length})
                        </h4>
                        <div className="space-y-2">
                          {activeDiscussions.map((item, index) => (
                            <motion.div
                              key={`disc-${index}`}
                              initial={{ opacity: 0, y: 10 }}
                              animate={{ opacity: 1, y: 0 }}
                              className="rounded-xl border border-blue-200 bg-blue-50/50 px-4 py-3 text-sm text-blue-900"
                            >
                              {item}
                            </motion.div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Empty state — everything is calm */}
                    {!activeGuardrailAlert && pendingDecisions.length === 0 && participantActionsForPanel.filter(i => i.status === 'suggested').length === 0 && activeDecisions.length === 0 && activePinnedInsights.length === 0 && activeDiscussions.length === 0 && (
                      <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        className="flex flex-col items-center gap-3 py-16 text-center"
                      >
                        <div className="h-12 w-12 rounded-2xl bg-slate-100 flex items-center justify-center">
                          <Sparkles className="h-6 w-6 text-slate-400" />
                        </div>
                        <p className="text-sm text-slate-500">Pnyx is listening — insights will surface here when relevant.</p>
                      </motion.div>
                    )}

                    {/* Live Summary removed from center panel per user feedback (moved to Past Insights panel) */}

                    {/* Past Insights trigger */}
                    {totalPastInsightCount > 0 && (
                      <button
                        type="button"
                        onClick={() => setShowGuardrailHistory(true)}
                        className="text-xs font-medium text-slate-500 hover:text-slate-700 transition-colors flex items-center gap-1.5 mt-2"
                      >
                        <HistoryIcon className="h-3.5 w-3.5" />
                        View Past Insights ({totalPastInsightCount})
                      </button>
                    )}

                  </div>

                  {/* ───── Past Insights Drawer (slide-over) ───── */}
                  <AnimatePresence>
                    {showGuardrailHistory && (
                      <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="fixed inset-0 bg-black/20 z-40"
                        onClick={() => setShowGuardrailHistory(false)}
                      />
                    )}
                  </AnimatePresence>
                  <AnimatePresence>
                    {showGuardrailHistory && (
                      <motion.aside
                        initial={{ x: '100%', opacity: 0 }}
                        animate={{ x: 0, opacity: 1 }}
                        exit={{ x: '100%', opacity: 0 }}
                        transition={{ duration: 0.3, ease: [0.4, 0, 0.2, 1] }}
                        style={{ width: insightsPanelWidth }}
                        className="fixed right-0 top-0 bottom-0 max-w-[90vw] z-50 glass-surface border-l border-gray-200/60 flex flex-col"
                      >
                        {/* Resize handle */}
                        <div
                          className="absolute left-0 top-0 bottom-0 w-1.5 cursor-col-resize hover:bg-indigo-400/40 active:bg-indigo-500/50 transition-colors z-10"
                          onMouseDown={(e) => {
                            e.preventDefault();
                            insightsResizeRef.current = { startX: e.clientX, startWidth: insightsPanelWidth };
                            const onMouseMove = (ev: MouseEvent) => {
                              if (!insightsResizeRef.current) return;
                              const delta = insightsResizeRef.current.startX - ev.clientX;
                              const newWidth = Math.max(320, Math.min(insightsResizeRef.current.startWidth + delta, 800));
                              setInsightsPanelWidth(newWidth);
                            };
                            const onMouseUp = () => {
                              document.removeEventListener('mousemove', onMouseMove);
                              document.removeEventListener('mouseup', onMouseUp);
                              if (typeof window !== 'undefined') {
                                localStorage.setItem('insights_panel_width', String(insightsPanelWidth));
                              }
                              insightsResizeRef.current = null;
                            };
                            document.addEventListener('mousemove', onMouseMove);
                            document.addEventListener('mouseup', onMouseUp);
                          }}
                        />
                        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200/60">
                          <h3 className="text-base font-semibold text-gray-900">Past Insights</h3>
                          <button
                            type="button"
                            onClick={() => setShowGuardrailHistory(false)}
                            className="rounded-full p-1 hover:bg-gray-100 transition-colors"
                          >
                            <X className="h-5 w-5 text-gray-500" />
                          </button>
                        </div>
                        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-6">
                          {/* Guardrail History */}
                          {guardrailAlertHistory.length > 0 && (
                            <div>
                              <h4 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-rose-700 mb-3">
                                <AlertCircle className="h-3.5 w-3.5" />
                                Guardrails ({guardrailAlertHistory.length})
                              </h4>
                              <div className="space-y-2">
                                {guardrailAlertHistory.map((item) => (
                                  <div key={item.id} className="rounded-xl border border-rose-200 bg-white px-3 py-2.5">
                                    <div className="flex items-center justify-between gap-2 mb-1">
                                      <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${getGuardrailBadgeClasses(item.reason)}`}>
                                        {getGuardrailReasonLabel(item.reason)}
                                      </span>
                                      <span className="text-[11px] text-gray-400">
                                        {formatGuardrailTime(item.updated_at || item.timestamp)}
                                      </span>
                                    </div>
                                    <p className="text-xs text-gray-700 leading-relaxed">{item.insight}</p>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* Past Decisions */}
                          {pastInsights.filter(i => i.type === 'decision').length > 0 && (
                            <div>
                              <h4 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-emerald-700 mb-3">
                                <CheckCircle2 className="h-3.5 w-3.5" />
                                Past Decisions ({pastInsights.filter(i => i.type === 'decision').length})
                              </h4>
                              <div className="space-y-2">
                                {pastInsights.filter(i => i.type === 'decision').map((item, idx) => (
                                  <div key={`past-dec-${idx}`} className="rounded-xl border border-emerald-200 bg-white px-3 py-2.5">
                                    <p className="text-[11px] font-semibold text-emerald-900 mb-1">{item.title}</p>
                                    <p className="text-xs text-gray-700 leading-relaxed">{item.text}</p>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* Past Discussions */}
                          {pastInsights.filter(i => i.type === 'discussion').length > 0 && (
                            <div>
                              <h4 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-blue-700 mb-3">
                                <MessageCircle className="h-3.5 w-3.5" />
                                Past Discussions ({pastInsights.filter(i => i.type === 'discussion').length})
                              </h4>
                              <div className="space-y-2">
                                {pastInsights.filter(i => i.type === 'discussion').map((item, idx) => (
                                  <div key={`past-disc-${idx}`} className="rounded-xl border border-blue-200 bg-white px-3 py-2.5">
                                    <p className="text-xs text-gray-700 leading-relaxed">{item.text}</p>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* AI Participant Insights (Historical) */}
                          {pastInsights.filter(i => i.type === 'action').length > 0 && (
                            <div>
                              <h4 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-indigo-700 mb-3">
                                <Sparkles className="h-3.5 w-3.5" />
                                Past Insights ({pastInsights.filter(i => i.type === 'action').length})
                              </h4>
                              <div className="space-y-2">
                                {pastInsights.filter(i => i.type === 'action').map((item, idx) => (
                                  <div key={`past-act-${idx}`} className="rounded-xl border border-indigo-200 bg-white px-3 py-2.5">
                                    {item.title && <p className="text-[11px] font-semibold text-gray-900 mb-1">{item.title}</p>}
                                    <p className="text-xs text-gray-700 leading-relaxed">{item.text}</p>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* Meeting Summary */}
                          {liveMeetingSummary && (
                            <div>
                              <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-600 mb-3">Meeting Summary</h4>
                              <div className="prose prose-sm max-w-none text-gray-700">
                                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                  {liveMeetingSummary}
                                </ReactMarkdown>
                              </div>
                            </div>
                          )}
                        </div>
                      </motion.aside>
                    )}
                  </AnimatePresence>
                </>
              )}
            </div>
          </div>

          {/* Custom prompt input at bottom of transcript section */}
          {/* {!isRecording && transcripts.length > 0 && !isMeetingActive && (
            <div className="p-4 border-t border-gray-200">
              <textarea
                placeholder="Add context for AI summary. For example people involved, meeting overview, objective etc..."
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 bg-white shadow-sm min-h-[80px] resize-y"
                value={customPrompt}
                onChange={(e) => setCustomPrompt(e.target.value)}
                disabled={summaryStatus === 'processing'}
              />
            </div>
          )} */}
        </div>

        {(isRecording || transcripts.length > 0) && (
          <aside
            className={`relative flex border-l border-gray-200/40 glass-surface transition-all duration-300 ${isTranscriptPanelCollapsed ? 'w-8' : 'w-[28%] min-w-[240px] max-w-[420px]'
              }`}
          >
            <button
              type="button"
              onClick={() => setIsTranscriptPanelCollapsed((prev) => !prev)}
              className="absolute -left-6 top-20 z-20 rounded-full border bg-white p-1 shadow-lg hover:bg-gray-100"
              style={{ transform: 'translateX(-50%)' }}
              aria-label={isTranscriptPanelCollapsed ? 'Expand transcript panel' : 'Collapse transcript panel'}
            >
              {isTranscriptPanelCollapsed ? (
                <ChevronLeftCircle className="h-6 w-6" />
              ) : (
                <ChevronRightCircle className="h-6 w-6" />
              )}
            </button>

            {isTranscriptPanelCollapsed ? (
              <div className="flex h-full w-full items-center justify-center">
                <span
                  className="text-xs font-medium tracking-wide text-gray-500"
                  style={{ writingMode: 'vertical-rl', textOrientation: 'mixed' }}
                >
                  Transcript
                </span>
              </div>
            ) : (
              <div className="flex h-full w-full flex-col">
                <div className="flex items-center justify-between border-b border-gray-200 px-3 py-2">
                  <h4 className="text-sm font-semibold text-gray-900">Live Transcript</h4>
                  <div className="flex items-center gap-1.5">
                    {transcripts?.length > 0 && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={handleCopyTranscript}
                        title="Copy Transcript"
                        className="h-8 px-2.5"
                      >
                        <Copy className="h-4 w-4" />
                        <span className="hidden md:inline">Copy</span>
                      </Button>
                    )}
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setShowDeviceSettings(true)}
                      title="Input/Output devices selection"
                      className="h-8 px-2.5"
                    >
                      <MicrophoneIcon className="h-4 w-4" />
                      <span className="hidden md:inline">Devices</span>
                    </Button>
                  </div>
                </div>
                <div ref={transcriptContainerRef} className="min-h-0 flex-1 overflow-y-auto px-2 py-3">
                  <TranscriptView
                    transcripts={transcripts}
                    partialTranscript={partialTranscript}
                    isRecording={isRecording}
                    isPaused={isPaused}
                    activeDuration={recordingElapsedSeconds}
                    isProcessing={isProcessingStop}
                    isStopping={isStopping}
                    enableStreaming={isRecording}
                  />
                </div>
              </div>
            )}
          </aside>
        )}

        {/* Recording controls - only show when not in recovery mode OR when recording is active */}
        {(!isProcessingStop && !isSavingTranscript && (!pendingRecoveryId || isRecording)) && (
          <div className="fixed bottom-12 left-0 right-0 z-10">
            <div
              className="flex justify-center pl-8 transition-[margin] duration-300"
              style={{
                marginLeft: sidebarCollapsed ? '4rem' : '16rem'
              }}
            >
              <div className="w-2/3 max-w-[750px] flex flex-col items-center gap-2">
                {/* {isRecording && streamingHealth && ( */}
                {/* <div className="w-full bg-white/95 border border-gray-200 rounded-lg px-3 py-2 shadow-sm text-xs text-gray-700"> */}
                {/* <div className="flex flex-wrap items-center gap-3">
                        <span>conn: <strong>{streamingHealth.active_connections ?? 0}</strong></span>
                        <span>queue: <strong>{streamingHealth.runtime?.queue_depth ?? 0}</strong></span>
                        <span>dropped: <strong>{streamingHealth.runtime?.dropped_audio_chunks ?? 0}</strong></span>
                        <span>stable: <strong>{streamingHealth.manager_stats?.stable_segments ?? 0}</strong></span>
                        <span>volatile: <strong>{streamingHealth.manager_stats?.volatile_segments ?? 0}</strong></span>
                        <span>drift: <strong>{streamingHealth.manager_stats?.semantic_drift_events ?? 0}</strong></span>
                        <span>corrections: <strong>{streamingHealth.manager_stats?.correction_events ?? 0}</strong></span>
                        {streamingHealth.runtime?.reconnect_storm_detected && (
                          <span className="text-amber-700 font-semibold">reconnect-storm</span>
                        )}
                        {streamingHealth.runtime?.backpressure_close_triggered && (
                          <span className="text-red-700 font-semibold">backpressure-hard-limit</span>
                        )}
                      </div> */}
                {/* {!!streamingHealth.runtime?.alert_history?.length && (
                        <div className="mt-2 border-t border-gray-200 pt-2 space-y-1">
                          <div className="flex flex-wrap gap-2">
                            {Object.entries(streamingHealth.runtime?.alert_counts || {}).map(([key, count]) => (
                              <span key={key} className="px-2 py-0.5 rounded bg-amber-100 text-amber-800 border border-amber-200">
                                {key}: <strong>{count}</strong>
                              </span>
                            ))}
                          </div>
                          <div className="space-y-1">
                            {(streamingHealth.runtime?.alert_history || []).slice(-3).reverse().map((alert, idx) => (
                              <div key={`${alert.type}-${alert.timestamp}-${idx}`} className="text-[11px] text-gray-600">
                                <span className="font-semibold">{alert.severity.toUpperCase()}</span>
                                {' · '}
                                <span>{alert.type}</span>
                                {' · '}
                                <span>{new Date(alert.timestamp).toLocaleTimeString()}</span>
                                {' · '}
                                <span>{alert.message}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div> */}
                {/* )} */}
                { !botMeetingId && (
                  <div className="glass-surface rounded-full flex items-center">
                    <RecordingControls
                      isRecording={isRecording}
                      onRecordingStop={(success) => handleRecordingStop(success)}
                      onRecordingStart={handleRecordingStart}
                      onTranscriptReceived={() => {}}
                      onGuardrailAlert={handleGuardrailAlert}
                      onHostSuggestion={handleHostSuggestion}
                      onHostIntervention={handleHostIntervention}
                      onHostStateDelta={handleHostStateDelta}
                      onHostActionAck={handleHostActionAck}
                      onHostSkillAck={(applied) => {
                        if (applied) toast.success('AI Participant skill override applied');
                      }}
                      onBehaviorSpecSync={(payload) => setBehaviorSpec(payload as unknown as BehaviorSpecPayload)}
                      onHostClientReady={handleHostClientReady}
                      hostSkillMarkdown={effectiveHostStyleMarkdown}
                      manualContext={manualMeetingContext}
                      contextApplySignal={contextApplySignal}
                      onContextApplied={(applied) =>
                        setContextAppliedStatus(applied ? 'applied' : 'failed')
                      }
                      onStopInitiated={() => setIsStopping(true)}
                      barHeights={barHeights}
                      onTranscriptionError={(message) => {
                        setErrorMessage(message);
                        setShowErrorAlert(true);
                      }}
                      isRecordingDisabled={isRecordingDisabled}
                      isParentProcessing={isProcessingStop}
                      selectedDevices={selectedDevices}
                      meetingName={meetingTitle}
                      onSessionIdReceived={(sessionId) => {
                        setCurrentSessionId(sessionId);
                        if (trackedMeetingStartRef.current !== sessionId) {
                          trackedMeetingStartRef.current = sessionId;
                          Analytics.trackMeetingStarted(
                            sessionId,
                            meetingTitle || 'Untitled Meeting'
                          );
                        }
                        // Upgrade temporary recovery ids once backend confirms live session id.
                        setPendingRecoveryId((prev) => {
                          if (!prev) return prev;
                          if (prev === sessionId) return prev;
                          return prev.startsWith('recovery-') ? sessionId : prev;
                        });
                      }}
                      initialSessionId={currentSessionId}
                      onPauseChange={setIsPaused}
                      startSignal={resumeStartSignal}
                      onResetStartSignal={resetResumeStartSignal}
                      onBeforeStart={handleBeforeStartRecording}
                    />
                  </div>
                )}
                {/* Recall.ai Bot Invite - Hide during local manual recording */}
                {(!isRecording || !!botMeetingId) && (
                  <BotInvitePanel
                    meetingId={botMeetingId || currentSessionId}
                    isRecording={isRecording || !!botMeetingId}
                  />
                )}
              </div>
            </div>
          </div>
        )}

        {/* Recovery Mode Actions - hide if currently recording */}
        {pendingRecoveryId && !isSavingTranscript && !isRecording && (
          <div className="fixed bottom-12 left-0 right-0 z-10">
            <div
              className="flex justify-center pl-8 transition-[margin] duration-300"
              style={{
                marginLeft: sidebarCollapsed ? '4rem' : '16rem'
              }}
            >
              <div className="bg-amber-50 border border-amber-200 rounded-full shadow-lg px-6 py-3 flex items-center gap-4">
                <div className="flex flex-col">
                  <span className="text-sm font-semibold text-amber-800">Recovery Mode</span>
                  <span className="text-xs text-amber-600 max-w-[200px] truncate" title={meetingTitle}>
                    {meetingTitle}
                  </span>
                </div>
                <div className="h-8 w-px bg-amber-200 mx-2"></div>
                <Button
                  onClick={() => {
                    // Reveal controls and trigger actual websocket/audio start.
                    setPendingRecoveryId(null);
                    setResumeStartSignal((v) => v + 1);
                  }}
                  className="bg-blue-600 hover:bg-blue-700 text-white rounded-full px-6"
                >
                  Resume Meeting
                </Button>
                <Button
                  onClick={() => handleWebAudioRecordingStop()}
                  className="bg-green-600 hover:bg-green-700 text-white rounded-full px-6"
                >
                  Save & Finish
                </Button>
                <Button
                  variant="outline"
                  onClick={handleDiscardRecovery}
                  className="border-red-200 text-red-600 hover:bg-red-50 hover:text-red-700 rounded-full px-4"
                >
                  Discard
                </Button>
              </div>
            </div>
          </div>
        )}

        {/* Processing status overlay */}
        {/* {summaryStatus === 'processing' && !isRecording && (
            <div className="fixed bottom-4 left-0 right-0 z-10">
              <div
                className="flex justify-center pl-8 transition-[margin] duration-300"
                style={{
                  marginLeft: sidebarCollapsed ? '4rem' : '16rem'
                }}
              >
                <div className="w-2/3 max-w-[750px] flex justify-center">
                  <div className="bg-white rounded-lg shadow-lg px-4 py-2 flex items-center space-x-2">
                    <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-gray-900"></div>
                    <span className="text-sm text-gray-700">Finalizing transcription...</span>
                  </div>
                </div>
              </div>
            </div>
          )} */}
        {isSavingTranscript && (
          <div className="fixed bottom-4 left-0 right-0 z-10">
            <div
              className="flex justify-center pl-8 transition-[margin] duration-300"
              style={{
                marginLeft: sidebarCollapsed ? '4rem' : '16rem'
              }}
            >
              <div className="w-2/3 max-w-[750px] flex justify-center">
                <div className="bg-white rounded-lg shadow-lg px-4 py-2 flex items-center space-x-2">
                  <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-gray-900"></div>
                  <span className="text-sm text-gray-700">Saving transcript...</span>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Preferences Modal (Settings) */}
        {showModelSettings && (
          <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
            <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] overflow-hidden flex flex-col">
              {/* Header */}
              <div className="flex justify-between items-center p-6 border-b">
                <h3 className="text-xl font-semibold text-gray-900">Preferences</h3>
                <button
                  onClick={() => setShowModelSettings(false)}
                  className="text-gray-500 hover:text-gray-700"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              {/* Content - Scrollable */}
              <div className="flex-1 overflow-y-auto p-6 space-y-8">
                {/* General Preferences Section */}
                <PreferenceSettings />

                {/* Divider */}
                <div className="border-t pt-8">
                  <h4 className="text-lg font-semibold text-gray-900 mb-4">AI Model Configuration</h4>
                  <div className="space-y-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Summarization Model
                      </label>
                      <div className="flex space-x-2">
                        <select
                          className="px-3 py-2 text-sm bg-white border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                          value={modelConfig.provider}
                          onChange={(e) => {
                            const provider = e.target.value as ModelConfig['provider'];
                            setModelConfig({
                              ...modelConfig,
                              provider,
                              model: (modelOptions[provider] || modelOptions.ollama)[0]
                            });
                          }}
                        >
                          <option value="claude">Claude</option>
                          <option value="groq">Groq</option>
                          <option value="ollama">Ollama</option>
                          <option value="openrouter">OpenRouter</option>
                        </select>

                        <select
                          className="flex-1 px-3 py-2 text-sm bg-white border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                          value={modelConfig.model}
                          onChange={(e) => setModelConfig(prev => ({ ...prev, model: e.target.value }))}
                        >
                          {(modelOptions[modelConfig.provider] || []).map((model: string) => (
                            <option key={model} value={model}>
                              {model}
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>
                    {modelConfig.provider === 'ollama' && (
                      <div>
                        <h4 className="text-lg font-bold mb-4">Available Ollama Models</h4>
                        {error && (
                          <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
                            {error}
                          </div>
                        )}
                        <div className="grid gap-4 max-h-[400px] overflow-y-auto pr-2">
                          {models.map((model) => (
                            <div
                              key={model.id}
                              className={`bg-white p-4 rounded-lg shadow cursor-pointer transition-colors ${modelConfig.model === model.name ? 'ring-2 ring-blue-500 bg-blue-50' : 'hover:bg-gray-50'
                                }`}
                              onClick={() => setModelConfig(prev => ({ ...prev, model: model.name }))}
                            >
                              <h3 className="font-bold">{model.name}</h3>
                              <p className="text-gray-600">Size: {model.size}</p>
                              <p className="text-gray-600">Modified: {model.modified}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* Footer */}
              <div className="border-t p-6 flex justify-end">
                <button
                  onClick={() => setShowModelSettings(false)}
                  className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
                >
                  Done
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Device Settings Modal */}
        {showDeviceSettings && (
          <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <div className="bg-white rounded-lg p-6 max-w-md w-full mx-4 shadow-xl">
              <div className="flex justify-between items-center mb-4">
                <h3 className="text-lg font-semibold text-gray-900">Audio Device Settings</h3>
                <button
                  onClick={() => setShowDeviceSettings(false)}
                  className="text-gray-500 hover:text-gray-700"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              <DeviceSelection
                selectedDevices={selectedDevices}
                onDeviceChange={setSelectedDevices}
                disabled={isRecording}
              />

              <div className="mt-6 flex justify-end">
                <button
                  onClick={() => {
                    const micDevice = selectedDevices.micDevice || 'Default';
                    const systemDevice = selectedDevices.systemDevice || 'Default';
                    toast.success("Devices selected", {
                      description: `Microphone: ${micDevice}, System Audio: ${systemDevice}`
                    });
                    setShowDeviceSettings(false);
                  }}
                  className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
                >
                  Done
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Language Settings Modal */}
        {showLanguageSettings && (
          <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <div className="bg-white rounded-lg p-6 max-w-md w-full mx-4 shadow-xl">
              <div className="flex justify-between items-center mb-4">
                <h3 className="text-lg font-semibold text-gray-900">Language Settings</h3>
                <button
                  onClick={() => setShowLanguageSettings(false)}
                  className="text-gray-500 hover:text-gray-700"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              <LanguageSelection
                selectedLanguage={selectedLanguage}
                onLanguageChange={setSelectedLanguage}
                disabled={isRecording}
                provider={transcriptModelConfig.provider}
              />

              <div className="mt-6 flex justify-end">
                <button
                  onClick={() => setShowLanguageSettings(false)}
                  className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
                >
                  Done
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Model Selection Modal - shown when model loading fails */}
        {showModelSelector && (
          <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <div className="bg-white rounded-lg max-w-4xl w-full mx-4 shadow-xl max-h-[90vh] flex flex-col">
              {/* Fixed Header */}
              <div className="flex justify-between items-center p-6 pb-4 border-b border-gray-200">
                <h3 className="text-lg font-semibold text-gray-900">
                  {modelSelectorMessage ? 'Speech Recognition Setup Required' : 'Transcription Model Settings'}
                </h3>
                <button
                  onClick={() => {
                    setShowModelSelector(false);
                    setModelSelectorMessage(''); // Clear the message when closing
                  }}
                  className="text-gray-500 hover:text-gray-700"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              {/* Scrollable Content */}
              <div className="flex-1 overflow-y-auto p-6 pt-4">
                {/* Only show warning if there's an error message (triggered by transcription error) */}
                {modelSelectorMessage && (
                  <div className="mb-6 p-4 bg-yellow-50 border border-yellow-200 rounded-lg">
                    <div className="flex items-start space-x-3">
                      <span className="text-yellow-600 text-xl">⚠️</span>
                      <div>
                        <h4 className="font-medium text-yellow-800 mb-1">Model Required</h4>
                        <p className="text-sm text-yellow-700">
                          {modelSelectorMessage}
                        </p>
                      </div>
                    </div>
                  </div>
                )}

                <TranscriptSettings
                  transcriptModelConfig={transcriptModelConfig}
                  setTranscriptModelConfig={setTranscriptModelConfig}
                  onModelSelect={() => {
                    setShowModelSelector(false);
                    setModelSelectorMessage('');
                  }}
                />
              </div>

              {/* Fixed Footer */}
              <div className="p-6 pt-4 border-t border-gray-200 flex items-center justify-between">
                {/* Left side: Confidence Indicator Toggle */}
                <div className="flex items-center gap-3">
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      checked={showConfidenceIndicator}
                      onChange={(e) => handleConfidenceToggle(e.target.checked)}
                      className="sr-only peer"
                    />
                    <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full rtl:peer-checked:after:-translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
                  </label>
                  <div>
                    <p className="text-sm font-medium text-gray-700">Show Confidence Indicators</p>
                    <p className="text-xs text-gray-500">Display colored dots showing transcription confidence quality</p>
                  </div>
                </div>

                {/* Right side: Done Button */}
                <button
                  onClick={() => {
                    setShowModelSelector(false);
                    setModelSelectorMessage(''); // Clear the message when closing
                  }}
                  className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-md hover:bg-gray-200 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-gray-500"
                >
                  {modelSelectorMessage ? 'Cancel' : 'Done'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Right side - AI Summary */}
      {/* <div className="flex-1 overflow-y-auto bg-white"> */}
      {/*   <div className="p-4 border-b border-gray-200"> */}
      {/*     <div className="flex items-center"> */}
      {/*       <EditableTitle */}
      {/*         title={meetingTitle} */}
      {/*         isEditing={isEditingTitle} */}
      {/*         onStartEditing={() => setIsEditingTitle(true)} */}
      {/*         onFinishEditing={() => setIsEditingTitle(false)} */}
      {/*         onChange={handleTitleChange} */}
      {/*       /> */}
      {/*     </div> */}
      {/*   </div> */}
      {/*   {/* {isSummaryLoading ? ( */}
      {/*     <div className="flex items-center justify-center h-full"> */}
      {/*       <div className="text-center"> */}
      {/*         <div className="inline-block animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-blue-500 mb-4"></div> */}
      {/*         <p className="text-gray-600">Generating AI Summary...</p> */}
      {/*       </div> */}
      {/*     </div> */}
      {/*   ) : showSummary && ( */}
      {/*     <div className="max-w-4xl mx-auto p-6"> */}
      {/*       {summaryResponse && ( */}
      {/*         <div className="fixed bottom-0 left-0 right-0 bg-white shadow-lg p-4 max-h-1/3 overflow-y-auto"> */}
      {/*           <h3 className="text-lg font-semibold mb-2">Meeting Summary</h3> */}
      {/*           <div className="grid grid-cols-2 gap-4"> */}
      {/*             <div className="bg-white p-4 rounded-lg shadow-sm"> */}
      {/*               <h4 className="font-medium mb-1">Key Points</h4> */}
      {/*               <ul className="list-disc pl-4"> */}
      {/*                 {summaryResponse.summary.key_points.blocks.map((block, i) => ( */}
      {/*                   <li key={i} className="text-sm">{block.content}</li> */}
      {/*                 ))} */}
      {/*               </ul> */}
      {/*             </div> */}
      {/*             <div className="bg-white p-4 rounded-lg shadow-sm mt-4"> */}
      {/*               <h4 className="font-medium mb-1">Action Items</h4> */}
      {/*               <ul className="list-disc pl-4"> */}
      {/*                 {summaryResponse.summary.action_items.blocks.map((block, i) => ( */}
      {/*                   <li key={i} className="text-sm">{block.content}</li> */}
      {/*                 ))} */}
      {/*               </ul> */}
      {/*             </div> */}
      {/*             <div className="bg-white p-4 rounded-lg shadow-sm mt-4"> */}
      {/*               <h4 className="font-medium mb-1">Decisions</h4> */}
      {/*               <ul className="list-disc pl-4"> */}
      {/*                 {summaryResponse.summary.decisions.blocks.map((block, i) => ( */}
      {/*                   <li key={i} className="text-sm">{block.content}</li> */}
      {/*                 ))} */}
      {/*               </ul> */}
      {/*             </div> */}
      {/*             <div className="bg-white p-4 rounded-lg shadow-sm mt-4"> */}
      {/*               <h4 className="font-medium mb-1">Main Topics</h4> */}
      {/*               <ul className="list-disc pl-4"> */}
      {/*                 {summaryResponse.summary.main_topics.blocks.map((block, i) => ( */}
      {/*                   <li key={i} className="text-sm">{block.content}</li> */}
      {/*                 ))} */}
      {/*               </ul> */}
      {/*             </div> */}
      {/*           </div> */}
      {/*           {summaryResponse.raw_summary ? ( */}
      {/*             <div className="mt-4"> */}
      {/*               <h4 className="font-medium mb-1">Full Summary</h4> */}
      {/*               <p className="text-sm whitespace-pre-wrap">{summaryResponse.raw_summary}</p> */}
      {/*             </div> */}
      {/*           ) : null} */}
      {/*         </div> */}
      {/*       )} */}
      {/*       <div className="flex-1 overflow-y-auto p-4"> */}
      {/*         <AISummary  */}
      {/*           summary={aiSummary}  */}
      {/*           status={summaryStatus}  */}
      {/*           error={summaryError} */}
      {/*           onSummaryChange={(newSummary) => setAiSummary(newSummary)} */}
      {/*           onRegenerateSummary={handleRegenerateSummary} */}
      {/*         /> */}
      {/*       </div> */}
      {/*       {summaryStatus !== 'idle' && ( */}
      {/*         <div className={`mt-4 p-4 rounded-lg ${ */}
      {/*           summaryStatus === 'error' ? 'bg-red-100 text-red-700' : */}
      {/*           summaryStatus === 'completed' ? 'bg-green-100 text-green-700' : */}
      {/*           'bg-blue-100 text-blue-700' */}
      {/*         }`}> */}
      {/*           <p className="text-sm font-medium">{getSummaryStatusMessage(summaryStatus)}</p> */}
      {/*         </div> */}
      {/*       )} */}
      {/*     </div> */}
      {/*   )} */}        {/* </div> */}

      {/* Chat Interface - Only show when recording or meeting is active */}
      {(isRecording || isMeetingActive) && (
        <>
          {isChatOpen && (
            <ChatInterface
              meetingId={'current-recording'}
              currentTranscripts={transcripts}
              onClose={() => setIsChatOpen(false)}
            />
          )}

          {!isChatOpen && (
            <div className="fixed bottom-6 right-6 flex flex-col gap-3 z-40">
              {/* Catch Me Up Button with Time Menu */}
              <div className="relative">
                <motion.button
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  whileHover={{ scale: 1.05 }}
                  whileTap={{ scale: 0.95 }}
                  onClick={() => setShowCatchUpMenu(!showCatchUpMenu)}
                  className="p-3 bg-amber-500 text-white rounded-full shadow-lg hover:bg-amber-600 transition-colors flex items-center gap-2"
                  title="Get a quick summary of the meeting so far"
                >
                  <Zap className="w-5 h-5" />
                  <span className="font-medium text-sm">Catch Up</span>
                </motion.button>

                {/* Time Selection Menu */}
                {showCatchUpMenu && (
                  <motion.div
                    initial={{ opacity: 0, scale: 0.9, y: 10 }}
                    animate={{ opacity: 1, scale: 1, y: 0 }}
                    className="absolute bottom-full right-0 mb-2 bg-white rounded-lg shadow-xl border border-gray-200 overflow-hidden min-w-[180px]"
                  >
                    <div className="p-2 border-b border-gray-100">
                      <p className="text-xs text-gray-500 font-medium px-2">Summarize:</p>
                    </div>
                    <div className="p-1">
                      {[
                        { label: 'Last 5 mins', value: 5 },
                        { label: 'Last 10 mins', value: 10 },
                        { label: 'Last 30 mins', value: 30 },
                        { label: 'Entire meeting', value: null },
                      ].map((option) => {
                        const meetingDuration = getMeetingDurationMinutes();
                        const isDisabled = option.value !== null && option.value > meetingDuration && option.value > 2;
                        const isMinNotMet = meetingDuration < 0.5; // Less than 30 seconds

                        return (
                          <button
                            key={option.label}
                            onClick={() => {
                              if (!isDisabled && !isMinNotMet) {
                                handleCatchUp(option.value);
                              }
                            }}
                            disabled={isDisabled || isMinNotMet}
                            className={`w-full text-left px-3 py-2 text-sm rounded transition-colors ${isDisabled || isMinNotMet
                              ? 'text-gray-300 cursor-not-allowed'
                              : 'text-gray-700 hover:bg-amber-50 hover:text-amber-700'
                              }`}
                          >
                            {option.label}
                            {option.value !== null && meetingDuration > 0 && option.value > meetingDuration && (
                              <span className="text-xs text-gray-400 ml-1">(not enough)</span>
                            )}
                          </button>
                        );
                      })}
                    </div>
                    <div className="p-2 border-t border-gray-100">
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          min="2"
                          max={Math.max(2, getMeetingDurationMinutes())}
                          placeholder="Custom"
                          value={customMinutesInput}
                          onChange={(e) => setCustomMinutesInput(e.target.value)}
                          className="w-20 px-2 py-1 text-sm border border-gray-200 rounded focus:outline-none focus:ring-1 focus:ring-amber-500"
                        />
                        <span className="text-xs text-gray-500">mins</span>
                        <button
                          onClick={() => {
                            const mins = parseInt(customMinutesInput);
                            if (mins >= 2 && mins <= getMeetingDurationMinutes()) {
                              handleCatchUp(mins);
                              setCustomMinutesInput('');
                            } else {
                              toast.error(`Enter 2-${getMeetingDurationMinutes()} minutes`);
                            }
                          }}
                          className="px-2 py-1 bg-amber-500 text-white text-xs rounded hover:bg-amber-600"
                        >
                          Go
                        </button>
                      </div>
                    </div>
                  </motion.div>
                )}
              </div>

              {/* Ask AI Button */}
              <motion.button
                initial={{ scale: 0 }}
                animate={{ scale: 1 }}
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
                onClick={() => { Analytics.trackChatPanelOpened(currentSessionId || 'current-recording'); setIsChatOpen(true); }}
                className="p-4 bg-blue-600 text-white rounded-full shadow-lg hover:bg-blue-700 transition-colors flex items-center gap-2"
                title="Ask AI about this active meeting"
              >
                <Bot className="w-6 h-6" />
                <span className="font-medium">Ask AI</span>
              </motion.button>
            </div>
          )}

          {/* Catch Me Up Modal */}
          {isCatchUpOpen && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 20 }}
              className="fixed bottom-6 right-6 w-80 bg-white rounded-lg shadow-2xl border border-gray-200 z-50 overflow-hidden"
            >
              <div className="bg-amber-500 text-white px-4 py-3 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Zap className="w-5 h-5" />
                  <div>
                    <span className="font-semibold">Catch Me Up</span>
                    {catchUpMinutes !== null && (
                      <span className="text-xs opacity-80 ml-1">(Last {catchUpMinutes} mins)</span>
                    )}
                  </div>
                </div>
                <button
                  onClick={() => setIsCatchUpOpen(false)}
                  className="text-white hover:text-amber-100 transition-colors"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
              <div className="p-4 max-h-80 overflow-y-auto">
                {isCatchUpLoading && !catchUpSummary ? (
                  <div className="flex items-center gap-2 text-gray-500">
                    <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-amber-500"></div>
                    <span className="text-sm">Generating summary...</span>
                  </div>
                ) : catchUpSummary ? (
                  <div className="text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">
                    {catchUpSummary}
                    {isCatchUpLoading && (
                      <span className="inline-block w-2 h-4 bg-amber-500 animate-pulse ml-1"></span>
                    )}
                  </div>
                ) : (
                  <p className="text-sm text-gray-500">No transcript available yet.</p>
                )}
              </div>
            </motion.div>
          )}
        </>
      )}

      <Dialog open={showGuardrailContextDialog} onOpenChange={setShowGuardrailContextDialog}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Meeting Context</DialogTitle>
            <DialogDescription className="hidden">
              Configure goal, agenda, participants, and style for AI Participant guidance.
            </DialogDescription>
          </DialogHeader>
          <div className="grid grid-cols-1 gap-3 px-1 py-2">
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">Meeting Goal</label>
              <input
                type="text"
                value={meetingGoalInput}
                onChange={(e) => setMeetingGoalInput(e.target.value)}
                placeholder="What's the meeting goal?"
                className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm focus:border-gray-400 focus:outline-none"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">Agenda</label>
              <textarea
                value={meetingAgendaInput}
                onChange={(e) => setMeetingAgendaInput(e.target.value)}
                placeholder="Key topics to cover..."
                className="w-full min-h-[100px] rounded-md border border-gray-200 px-3 py-2 text-sm focus:border-gray-400 focus:outline-none"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-gray-500 mb-1 block">Participants (Optional)</label>
              <input
                type="text"
                value={meetingParticipantsInput}
                onChange={(e) => setMeetingParticipantsInput(e.target.value)}
                placeholder="Comma separated names"
                className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm focus:border-gray-400 focus:outline-none"
              />
            </div>
          </div>
          <DialogFooter className="mt-2">
            <Button
              onClick={() => {
                applyManualContext();
                setShowGuardrailContextDialog(false);
              }}
              disabled={!hasManualContextValues || contextAppliedStatus === 'applying'}
              className="w-full"
            >
              {contextAppliedStatus === 'applying' ? 'Applying...' : 'Apply Context'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={showHostStyleStartDialog}
        onOpenChange={(open) => {
          if (!open) cancelStartStyleDialog();
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Select AI Participant Style</DialogTitle>
            <DialogDescription>
              Choose a style for this meeting. Your default style will be pre-selected.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <label className="text-xs font-medium text-gray-600">AI Participant Style</label>
            <select
              value={pendingStartStyleId}
              onChange={(e) => setPendingStartStyleId(e.target.value)}
              className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm focus:border-gray-400 focus:outline-none"
            >
              <option value="__default__">
                Use default style ({defaultHostStyleId || 'system:facilitator'})
              </option>
              {activeHostStyles.map((style) => (
                <option key={style.id} value={style.id}>
                  {style.name} ({style.source}){style.is_default ? ' ★' : ''}
                </option>
              ))}
            </select>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={cancelStartStyleDialog}>
              Cancel
            </Button>
            <Button onClick={confirmStartStyleDialog}>
              Start Meeting
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <CalendarMeetingPicker
        open={showCalendarPicker}
        onOpenChange={setShowCalendarPicker}
        onSelectMeeting={handleCalendarMeetingSelect}
      />

      <Dialog open={showReauthModal} onOpenChange={setShowReauthModal}>
        <DialogContent className="sm:max-w-md border-none p-0 bg-transparent shadow-none">
          <div className="bg-white rounded-lg p-6 max-w-md w-full mx-4 shadow-xl border border-gray-200">
            <h3 className="text-lg font-semibold text-gray-900 mb-2">Session Expired</h3>
            <p className="text-gray-600 mb-6">
              Your session has expired, but your meeting transcript is safe locally.
              Please log in again in a new tab, then click "Retry Save".
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => window.open('/login', '_blank')}
                className="px-4 py-2 text-sm font-medium text-blue-600 hover:bg-blue-50 rounded-md transition-colors"
              >
                Log In (New Tab)
              </button>
              <button
                onClick={async () => {
                  setShowReauthModal(false);
                  await handleWebAudioRecordingStop();
                }}
                className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 transition-colors shadow-sm"
              >
                Retry Save
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </motion.div >
  );
}
