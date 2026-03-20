import React, { useContext, useState } from 'react';
import { Switch } from '@/components/ui/switch';
import { Info, Loader2 } from 'lucide-react';
import { AnalyticsContext } from './AnalyticsProvider';
import { Analytics } from '@/lib/analytics';
import AnalyticsDataModal from './AnalyticsDataModal';
import { useSession } from 'next-auth/react';


export default function AnalyticsConsentSwitch() {
  const { setIsAnalyticsOptedIn, isAnalyticsOptedIn } = useContext(AnalyticsContext);
  const { data: session } = useSession();
  const [isProcessing, setIsProcessing] = useState(false);
  const [showModal, setShowModal] = useState(false);

  // Note: Store loading is handled by AnalyticsProvider to avoid race conditions

  const handleToggle = async (enabled: boolean) => {
    // If user is trying to DISABLE, show the modal first
    if (!enabled) {
      setShowModal(true);
      // Track that user viewed the transparency modal
      try {
        Analytics.track('analytics_transparency_viewed');
      } catch (error) {
        console.error('Failed to track transparency view:', error);
      }
      return; // Don't disable yet, wait for modal confirmation
    }

    // If ENABLING, proceed immediately
    await performToggle(enabled);
  };

  const performToggle = async (enabled: boolean) => {
    // Optimistic update - immediately update UI state
    setIsAnalyticsOptedIn(enabled);
    setIsProcessing(true);

    try {
      localStorage.setItem('analyticsOptedIn', String(enabled));

      if (enabled) {
        const userId =
          session?.user?.email || (await Analytics.getPersistentUserId());

        await Analytics.enable();

        await Analytics.identify(userId, {
          app_version: '0.1.1',
          platform: 'web',
          os: navigator.platform,
          user_agent: navigator.userAgent,
        });

        const sessionId = await Analytics.startSession(userId);
        if (sessionId) {
          await Analytics.trackSessionStarted(sessionId);
        }
        await Analytics.trackAppStarted();
        await Analytics.track('analytics_enabled');
      } else {
        await Analytics.track('analytics_disabled');
        await Analytics.disable();
      }
    } catch (error) {
      console.error('Failed to toggle analytics:', error);
      // Revert the optimistic update on error
      setIsAnalyticsOptedIn(!enabled);
      // You could also show a toast notification here to inform the user
    } finally {
      setIsProcessing(false);
    }
  };

  const handleConfirmDisable = async () => {
    setShowModal(false);
    await performToggle(false);
  };

  const handleCancelDisable = () => {
    setShowModal(false);
    // Keep analytics enabled, no state change needed
  };

  const handlePrivacyPolicyClick = () => {
    try {
      window.open('https://github.com/Zackriya-Solutions/meeting-minutes/blob/main/PRIVACY_POLICY.md', '_blank');
    } catch (error) {
      console.error('Failed to open privacy policy link:', error);
    }
  };

  return (
    <>
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-semibold text-gray-800 mb-2">Usage Analytics</h3>
          <p className="text-sm text-gray-600 mb-4">
            Help us improve Pnyx by sharing anonymous usage data. No personal content is collected—everything stays on your device.
          </p>
        </div>

        <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg border border-gray-200">
          <div>
            <h4 className="font-semibold text-gray-800">Enable Analytics</h4>
            <p className="text-sm text-gray-600">
              {isProcessing ? 'Updating...' : 'Anonymous usage patterns only'}
            </p>
          </div>
          <div className="flex items-center gap-2 ml-4">
            {isProcessing && (
              <Loader2 className="w-4 h-4 animate-spin text-gray-500" />
            )}
            <Switch
              checked={isAnalyticsOptedIn}
              onCheckedChange={handleToggle}
              disabled={isProcessing}
            />
          </div>
        </div>

        <div className="flex items-start gap-2 p-2 bg-blue-50 rounded border border-blue-200">
          <Info className="w-4 h-4 text-blue-600 mt-0.5 flex-shrink-0" />
          <div className="text-xs text-blue-700">
            <p className="mb-1">
              Your meetings, transcripts, and recordings remain completely private and local.
            </p>
            <button
              onClick={handlePrivacyPolicyClick}
              className="text-blue-600 hover:text-blue-800 underline hover:no-underline"
            >
              View Privacy Policy
            </button>
          </div>
        </div>
      </div>

      {/* 2-Step Opt-Out Modal */}
      <AnalyticsDataModal
        isOpen={showModal}
        onClose={handleCancelDisable}
        onConfirmDisable={handleConfirmDisable}
      />
    </>
  );
}
