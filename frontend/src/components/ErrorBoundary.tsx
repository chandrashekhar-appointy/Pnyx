'use client';

import React from 'react';
import * as Sentry from '@sentry/nextjs';

interface Props {
  children: React.ReactNode;
  fallback?: React.ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    // Send to Sentry if it's wired up; otherwise just log.
    try {
      Sentry.captureException(error, { extra: { componentStack: info.componentStack } });
    } catch {
      // ignore — Sentry might not be initialized
    }
    if (process.env.NODE_ENV === 'development') {
      console.error('[ErrorBoundary]', error, info);
    }
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="flex min-h-screen flex-col items-center justify-center gap-4 p-6 text-center">
          <h1 className="text-xl font-semibold">Something went wrong.</h1>
          <p className="text-sm text-zinc-600 max-w-md">
            We've been notified and are looking into it. You can try refreshing
            the page; if the problem persists, please contact support.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
          >
            Refresh page
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
