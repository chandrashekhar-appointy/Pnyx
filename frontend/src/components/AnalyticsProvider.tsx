'use client';

import React, { useEffect, ReactNode, useRef, useState, createContext, useContext, Suspense } from 'react';
import { useSession } from 'next-auth/react';
import { usePathname, useSearchParams } from 'next/navigation';
import Analytics from '@/lib/analytics';

interface AnalyticsProviderProps {
  children: ReactNode;
}

interface AnalyticsContextType {
  isAnalyticsOptedIn: boolean;
  setIsAnalyticsOptedIn: (optedIn: boolean) => void;
  isAnalyticsInitialized: boolean;
}

export const AnalyticsContext = createContext<AnalyticsContextType>({
  isAnalyticsOptedIn: true,
  setIsAnalyticsOptedIn: () => { },
  isAnalyticsInitialized: false,
});

/**
 * PageViewTracker component uses useSearchParams and must be wrapped in Suspense.
 */
function PageViewTracker() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { isAnalyticsOptedIn, isAnalyticsInitialized } = useContext(AnalyticsContext);
  const trackedPathRef = useRef<string | null>(null);

  useEffect(() => {
    if (!isAnalyticsOptedIn || !isAnalyticsInitialized) {
      return;
    }

    const url = searchParams?.toString()
      ? `${pathname}?${searchParams.toString()}`
      : pathname;

    if (trackedPathRef.current === url) {
      return;
    }
    trackedPathRef.current = url;
    Analytics.trackPageView(url).catch(console.error);
  }, [isAnalyticsOptedIn, isAnalyticsInitialized, pathname, searchParams]);

  return null;
}

export default function AnalyticsProvider({ children }: AnalyticsProviderProps) {
  const { data: session, status } = useSession();
  const [isAnalyticsOptedIn, setIsAnalyticsOptedIn] = useState(true);
  const [isAnalyticsInitialized, setIsAnalyticsInitialized] = useState(false);
  const initializedRef = useRef(false);

  // Automatically update the Analytics internal user ID when the session loads
  useEffect(() => {
    if (status === 'authenticated' && session?.user?.email) {
      Analytics.identify(session.user.email, {
        name: session.user.name || undefined
      }).catch(console.error);
    }
  }, [status, session]);

  useEffect(() => {
    let beforeUnloadBound = false;
    let sessionId: string | null = null;

    const initializeAnalytics = async () => {
      const storedOptIn = localStorage.getItem('analyticsOptedIn');
      const analyticsOptedIn = storedOptIn === null ? true : storedOptIn === 'true';
      if (storedOptIn === null) {
        localStorage.setItem('analyticsOptedIn', 'true');
      }
      setIsAnalyticsOptedIn(analyticsOptedIn);

      const userId = session?.user?.email || await Analytics.getPersistentUserId();
      await Analytics.init(userId);

      if (!analyticsOptedIn) {
        initializedRef.current = false;
        setIsAnalyticsInitialized(false);
        return;
      }

      const deviceInfo = await Analytics.getDeviceInfo();
      await Analytics.identify(userId, {
        app_version: '0.1.1',
        platform: deviceInfo.platform,
        os_version: deviceInfo.os_version,
        architecture: deviceInfo.architecture,
        user_agent: navigator.userAgent,
      });

      if (!initializedRef.current) {
        sessionId = await Analytics.startSession(userId);
        if (sessionId) {
          await Analytics.trackSessionStarted(sessionId);
        }
        await Analytics.checkAndTrackFirstLaunch();
        await Analytics.trackAppStarted();
        await Analytics.checkAndTrackDailyUsage();
        initializedRef.current = true;
        setIsAnalyticsInitialized(true);
      }

      const handleBeforeUnload = async () => {
        if (sessionId) {
          await Analytics.trackSessionEnded(sessionId);
        }
        await Analytics.cleanup();
      };

      window.addEventListener('beforeunload', handleBeforeUnload);
      beforeUnloadBound = true;

      return () => {
        window.removeEventListener('beforeunload', handleBeforeUnload);
      };
    };

    let cleanupFn: (() => void) | undefined;
    initializeAnalytics()
      .then((cleanup) => {
        cleanupFn = cleanup;
      })
      .catch(console.error);

    return () => {
      if (cleanupFn) {
        cleanupFn();
      } else if (beforeUnloadBound) {
        window.onbeforeunload = null;
      }
    };
  }, [session?.user?.email]);

  // Separate effect to handle re-initialization when analytics is toggled
  useEffect(() => {
    // Reset initialized flag when analytics is disabled to allow re-initialization
    if (!isAnalyticsOptedIn) {
      initializedRef.current = false;
      setIsAnalyticsInitialized(false);
    }
  }, [isAnalyticsOptedIn]);

  return (
    <AnalyticsContext.Provider value={{
      isAnalyticsOptedIn,
      setIsAnalyticsOptedIn,
      isAnalyticsInitialized
    }}>
      <Suspense fallback={null}>
        <PageViewTracker />
      </Suspense>
      {children}
    </AnalyticsContext.Provider>
  );
}
