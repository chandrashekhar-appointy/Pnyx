import * as Sentry from '@sentry/nextjs';

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT || process.env.NODE_ENV,
    release: process.env.NEXT_PUBLIC_SENTRY_RELEASE,
    tracesSampleRate: parseFloat(process.env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE || '0.05'),
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: parseFloat(process.env.NEXT_PUBLIC_SENTRY_ERROR_REPLAY_RATE || '0'),
    sendDefaultPii: false,
    // Ignore noisy network errors that don't indicate a bug we can fix.
    ignoreErrors: [
      'ResizeObserver loop limit exceeded',
      'NetworkError when attempting to fetch resource.',
      'AbortError',
    ],
  });
}
