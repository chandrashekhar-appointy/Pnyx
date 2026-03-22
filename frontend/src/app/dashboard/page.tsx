'use client';

import React, { useEffect } from 'react';
import { useSession } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { ArrowLeft, ArrowUpRight, BarChart3, ExternalLink, Loader2 } from 'lucide-react';

const getPostHogProjectUrl = () =>
  process.env.NEXT_PUBLIC_POSTHOG_PROJECT_URL || '';

export default function DashboardPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const posthogProjectUrl = getPostHogProjectUrl();

  useEffect(() => {
    if (status === 'unauthenticated') {
      router.push('/login');
      return;
    }

    if (status === 'authenticated') {
      const adminEmails = (process.env.NEXT_PUBLIC_ADMIN_EMAILS || '')
        .split(',')
        .map((e) => e.trim())
        .filter(Boolean);

      if (
        adminEmails.length > 0 &&
        session?.user?.email &&
        !adminEmails.includes(session.user.email)
      ) {
        router.push('/');
      }
    }
  }, [status, session, router]);

  const openPostHog = () => {
    if (!posthogProjectUrl) {
      return;
    }
    window.open(posthogProjectUrl, '_blank', 'noopener,noreferrer');
  };

  if (status === 'loading') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50">
        <div className="flex flex-col items-center">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600 mb-4" />
          <p className="text-gray-500">Loading...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-full bg-gray-50">
      <div className="max-w-5xl mx-auto px-8 py-10">
        <div className="flex items-center justify-between gap-4 mb-8">
          <div>
            <h1 className="text-3xl font-bold text-gray-900 tracking-tight">
              Analytics
            </h1>
            <p className="text-gray-600 mt-2">
              Product analytics now live in PostHog instead of the legacy in-app dashboard.
            </p>
          </div>
          <button
            onClick={() => router.push('/')}
            className="flex items-center px-4 py-2 bg-white border border-gray-200 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
          >
            <ArrowLeft className="w-4 h-4 mr-2" />
            Back to App
          </button>
        </div>

        <div className="max-w-3xl">
          <div className="bg-white border border-gray-200 rounded-2xl shadow-sm p-8">
            <div className="flex items-start gap-4 mb-6">
              <div className="w-12 h-12 rounded-xl bg-blue-100 text-blue-700 flex items-center justify-center">
                <BarChart3 className="w-6 h-6" />
              </div>
              <div>
                <h2 className="text-xl font-semibold text-gray-900">
                  Open PostHog Analytics
                </h2>
                <p className="text-gray-600 mt-1">
                  Use PostHog for event trends, funnels, retention, feature adoption, and user journeys.
                </p>
              </div>
            </div>

            <div className="space-y-3 text-sm text-gray-700 mb-8">
              <div className="flex items-start gap-2">
                <span className="mt-1 h-2 w-2 rounded-full bg-blue-500" />
                <span>The old `/analytics/dashboard/metrics` view is no longer the primary source of product analytics.</span>
              </div>
              <div className="flex items-start gap-2">
                <span className="mt-1 h-2 w-2 rounded-full bg-blue-500" />
                <span>Feature events now flow through the frontend analytics wrapper into PostHog.</span>
              </div>
              <div className="flex items-start gap-2">
                <span className="mt-1 h-2 w-2 rounded-full bg-blue-500" />
                <span>Build and manage dashboards directly in PostHog instead of relying on the legacy internal page.</span>
              </div>
            </div>

            <div className="flex flex-wrap gap-3">
              <button
                onClick={openPostHog}
                disabled={!posthogProjectUrl}
                className="inline-flex items-center px-5 py-3 rounded-xl bg-gray-900 text-white font-medium hover:bg-gray-800 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
              >
                Open PostHog
                <ArrowUpRight className="w-4 h-4 ml-2" />
              </button>

              {posthogProjectUrl && (
                <a
                  href={posthogProjectUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center px-5 py-3 rounded-xl bg-white border border-gray-200 text-gray-700 font-medium hover:bg-gray-50 transition-colors"
                >
                  Open in New Tab
                  <ExternalLink className="w-4 h-4 ml-2" />
                </a>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
