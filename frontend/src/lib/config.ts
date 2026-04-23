/**
 * Centralized Configuration for Pnyx
 *
 * Automatically switches between development and production URLs
 * based on NODE_ENV environment variable.
 *
 * Development (npm run dev): Uses localhost
 * Production: Uses runtime config from the rendered page, with env/fallbacks as backup.
 */

const isDevelopment = process.env.NODE_ENV === 'development';
const developmentApiUrl = 'http://localhost:5167';
const developmentWsUrl = 'ws://localhost:5167/ws/streaming-audio';
const productionApiFallback = 'https://meet.quexio.com';
const productionWsFallback = 'wss://meet.quexio.com/ws/streaming-audio';

declare global {
  interface Window {
    __PNYX_RUNTIME_CONFIG__?: {
      backendUrl?: string;
      wsUrl?: string;
    };
  }
}

const getRuntimeConfig = () =>
  typeof window === 'undefined' ? undefined : window.__PNYX_RUNTIME_CONFIG__;

export const getApiUrl = () =>
  isDevelopment
    ? developmentApiUrl
    : (getRuntimeConfig()?.backendUrl || process.env.NEXT_PUBLIC_BACKEND_URL || productionApiFallback);

export const getWsUrl = () =>
  isDevelopment
    ? developmentWsUrl
    : (getRuntimeConfig()?.wsUrl || process.env.NEXT_PUBLIC_WS_URL || productionWsFallback);

export const config = {
  get apiUrl() {
    return getApiUrl();
  },
  get wsUrl() {
    return getWsUrl();
  },

  // Debug mode - enables extra logging
  debug: isDevelopment,

  // Environment name for logging
  environment: isDevelopment ? 'development' : 'production',
};

// Log configuration on startup (client-side only)
if (typeof window !== 'undefined' && config.debug) {
  console.log('[Config] Environment:', config.environment);
  console.log('[Config] API URL:', getApiUrl());
  console.log('[Config] WS URL:', getWsUrl());
}

// Export individual values for convenience
export const { debug } = config;
