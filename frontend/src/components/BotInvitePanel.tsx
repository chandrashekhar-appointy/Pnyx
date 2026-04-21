'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { Bot, Loader2, Phone, PhoneOff, Link2, CheckCircle2, AlertCircle, X, Clock } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useSidebar } from '@/components/Sidebar/SidebarProvider';
import { authFetch } from '@/lib/api';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { toast } from 'sonner';
import Analytics from '@/lib/analytics';

interface BotInvitePanelProps {
  meetingId: string | null;
  isRecording: boolean;
}

type BotStatus = 'none' | 'requesting' | 'joining' | 'recording' | 'completed' | 'fatal';

interface BotStatusData {
  recall_bot_id?: string;
  status: BotStatus;
  bot_name?: string;
  meeting_url?: string;
  duration_seconds?: number;
  error_message?: string;
  created_at?: string;
}

const STATUS_LABELS: Record<BotStatus, string> = {
  none: 'No Bot',
  requesting: 'Requesting...',
  joining: 'Joining meeting...',
  recording: 'Recording',
  completed: 'Completed',
  fatal: 'Error',
};

const STATUS_COLORS: Record<BotStatus, string> = {
  none: 'text-gray-400',
  requesting: 'text-amber-500',
  joining: 'text-blue-500',
  recording: 'text-emerald-500',
  completed: 'text-gray-500',
  fatal: 'text-red-500',
};

const STATUS_DOT_COLORS: Record<BotStatus, string> = {
  none: 'bg-gray-300',
  requesting: 'bg-amber-400',
  joining: 'bg-blue-400',
  recording: 'bg-emerald-400',
  completed: 'bg-gray-400',
  fatal: 'bg-red-400',
};

export const BotInvitePanel: React.FC<BotInvitePanelProps> = ({
  meetingId,
  isRecording,
}) => {
  const [meetingUrl, setMeetingUrl] = useState('');
  const [botStatus, setBotStatus] = useState<BotStatusData | null>(null);
  const [isSpawning, setIsSpawning] = useState(false);
  const [isRemoving, setIsRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const router = useRouter();
  const { refetchMeetings, setActiveBotMeetingId } = useSidebar();

  // Validate meeting URL
  const isValidUrl = useCallback((url: string): boolean => {
    const patterns = [
      /zoom\.us\/j\//i,
      /meet\.google\.com\//i,
      /teams\.microsoft\.com\//i,
      /teams\.live\.com\//i,
      /webex\.com\//i,
    ];
    return patterns.some((p) => p.test(url));
  }, []);

  // Poll bot status
  const fetchBotStatus = useCallback(async () => {
    if (!meetingId) return;
    try {
      const res = await authFetch(`/api/meetings/${meetingId}/bot-status`, {
        method: 'GET',
        preventLogout: true,
      });
      if (!res.ok) return;
      const data = await res.json();
      if (data.status && data.status !== 'none') {
        setBotStatus(data as BotStatusData);
      } else {
        setBotStatus(null);
      }
    } catch {
      // Silently fail for polling
    }
  }, [meetingId]);

  // Start/stop polling based on active bot
  useEffect(() => {
    if (!meetingId) return;
    // Initial fetch
    fetchBotStatus();

    // Poll every 5s while bot is active
    if (botStatus && ['requesting', 'joining', 'recording'].includes(botStatus.status)) {
      pollRef.current = setInterval(fetchBotStatus, 5000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [meetingId, botStatus?.status, fetchBotStatus]);

  const handleInviteBot = async () => {
    if (!meetingUrl.trim()) return;
    if (!isValidUrl(meetingUrl)) {
      setError('Enter a valid Zoom, Google Meet, or Teams URL');
      Analytics.trackBotInvalidUrl(meetingUrl.slice(0, 60));
      return;
    }

    setIsSpawning(true);
    setError(null);
    let urlDomain = 'unknown';
    try { urlDomain = new URL(meetingUrl.trim()).hostname; } catch { /* ignore */ }
    Analytics.trackBotInviteSent({ meeting_id: meetingId, url_domain: urlDomain });

    try {
      let activeMeetingId = meetingId;
      
      if (!activeMeetingId) {
        const createRes = await authFetch('/api/meetings/create', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: 'Live Bot Session' }),
        });
        
        if (!createRes.ok) {
          throw new Error('Failed to create a new generic meeting for the bot');
        }
        
        const createData = await createRes.json();
        activeMeetingId = createData.meeting_id;
      }

      const res = await authFetch(`/api/meetings/${activeMeetingId}/invite-bot`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ meeting_url: meetingUrl.trim() }),
      });

      if (!res.ok) {
        const errData = await res.json();
        const msg = errData?.detail?.message || errData?.detail || 'Failed to send bot';
        setError(String(msg));
        Analytics.trackBotInviteFailed({ error_message: String(msg), url_domain: urlDomain });
        toast.error('Bot invite failed', { description: String(msg) });
        return;
      }

      const data = await res.json();
      setBotStatus({
        recall_bot_id: data.recall_bot_id,
        status: 'requesting',
      });
      setMeetingUrl('');
      Analytics.trackBotInviteSuccess({ meeting_id: activeMeetingId, recall_bot_id: data.recall_bot_id, url_domain: urlDomain });
      toast.success('Bot sent!', { description: 'Pnyx AI Assistant is joining the meeting.' });
      
      // Navigate to bot mode
      refetchMeetings();
      setActiveBotMeetingId(activeMeetingId as string);
      router.push(`/?botMeeting=${activeMeetingId}`);
      
    } catch (e: any) {
      setError(e.message || 'Failed to send bot');
    } finally {
      setIsSpawning(false);
    }
  };

  // Remove bot
  const handleRemoveBot = async () => {
    if (!meetingId) return;
    setIsRemoving(true);
    try {
      const res = await authFetch(`/api/meetings/${meetingId}/bot`, {
        method: 'DELETE',
      });
      if (res.ok) {
        setBotStatus(null);
        Analytics.trackBotRemoved({ meeting_id: meetingId });
        toast.success('Bot removed from meeting');
      }
    } catch {
      toast.error('Failed to remove bot');
    } finally {
      setIsRemoving(false);
    }
  };

  const activeBotStatuses: BotStatus[] = ['requesting', 'joining', 'recording'];
  const hasActiveBot = botStatus && activeBotStatuses.includes(botStatus.status);
  const isCompleted = botStatus?.status === 'completed';
  const isFatal = botStatus?.status === 'fatal';

  return (
    <TooltipProvider>
      <div className="glass-surface rounded-xl px-4 py-3 space-y-3">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Bot className="h-4 w-4 text-indigo-500" />
            <span className="text-xs font-semibold text-gray-700 uppercase tracking-wider">
              Meeting Bot
            </span>
          </div>

          {/* Status badge */}
          {botStatus && botStatus.status !== 'none' && (
            <div className="flex items-center gap-1.5">
              <span
                className={`w-2 h-2 rounded-full ${STATUS_DOT_COLORS[botStatus.status]} ${
                  botStatus.status === 'recording' ? 'animate-pulse' : ''
                }`}
              />
              <span className={`text-xs font-medium ${STATUS_COLORS[botStatus.status]}`}>
                {STATUS_LABELS[botStatus.status]}
              </span>
            </div>
          )}
        </div>

        {/* Active bot HUD */}
        {hasActiveBot && (
          <div className="flex items-center justify-between bg-gray-50/80 rounded-lg px-3 py-2">
            <div className="flex items-center gap-2 min-w-0">
              <Phone className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
              <span className="text-xs text-gray-600 truncate">
                {botStatus?.bot_name || 'Pnyx AI Assistant'}
              </span>
            </div>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  onClick={handleRemoveBot}
                  disabled={isRemoving}
                  className="text-red-400 hover:text-red-600 transition-colors p-1 rounded hover:bg-red-50"
                >
                  {isRemoving ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <PhoneOff className="h-3.5 w-3.5" />
                  )}
                </button>
              </TooltipTrigger>
              <TooltipContent>Remove bot from meeting</TooltipContent>
            </Tooltip>
          </div>
        )}

        {/* Completed state */}
        {isCompleted && (
          <div className="flex items-center gap-2 bg-gray-50/80 rounded-lg px-3 py-2">
            <CheckCircle2 className="h-3.5 w-3.5 text-gray-400" />
            <span className="text-xs text-gray-500">
              Bot session completed
              {botStatus?.duration_seconds && botStatus.duration_seconds > 0 && (
                <> &middot; {Math.round(botStatus.duration_seconds / 60)}m</>
              )}
            </span>
          </div>
        )}

        {/* Error state */}
        {isFatal && (
          <div className="flex items-center gap-2 bg-red-50/80 rounded-lg px-3 py-2">
            <AlertCircle className="h-3.5 w-3.5 text-red-400" />
            <span className="text-xs text-red-600 truncate">
              {botStatus?.error_message || 'Bot encountered an error'}
            </span>
          </div>
        )}

        {/* URL input + Send button (only when no active bot) */}
        {!hasActiveBot && (
          <div className="space-y-2">
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Link2 className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-gray-400" />
                <input
                  type="url"
                  value={meetingUrl}
                  onChange={(e) => {
                    setMeetingUrl(e.target.value);
                    setError(null);
                  }}
                  placeholder="Paste Zoom / Meet / Teams link"
                  className="w-full pl-8 pr-3 py-2 text-xs bg-white/60 border border-gray-200
                             rounded-lg placeholder:text-gray-400 focus:outline-none
                             focus:ring-2 focus:ring-indigo-300 focus:border-transparent
                             transition-all"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && meetingUrl.trim()) handleInviteBot();
                  }}
                  disabled={isSpawning}
                />
              </div>
              <button
                onClick={handleInviteBot}
                disabled={!meetingUrl.trim() || isSpawning}
                className="px-3 py-2 text-xs font-medium text-white bg-indigo-500
                           hover:bg-indigo-600 disabled:bg-gray-300 disabled:cursor-not-allowed
                           rounded-lg transition-colors flex items-center gap-1.5 whitespace-nowrap"
              >
                {isSpawning ? (
                  <>
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Sending...
                  </>
                ) : (
                  <>
                    <Bot className="h-3 w-3" />
                    Send Bot
                  </>
                )}
              </button>
            </div>
            {error && (
              <div className="flex items-center gap-1.5 text-xs text-red-500">
                <AlertCircle className="h-3 w-3 shrink-0" />
                <span>{error}</span>
              </div>
            )}
            {!meetingId && (
              <p className="text-[10px] text-gray-400">
                Paste a meeting URL and Pnyx will create a meeting automatically when you send the bot
              </p>
            )}
          </div>
        )}
      </div>
    </TooltipProvider>
  );
};
