/**
 * Centralized Configuration for Pnyx
 * 
 * Automatically switches between development and production URLs
 * based on NODE_ENV environment variable.
 * 
 * Development (npm run dev): Uses localhost
 * Production (npm run build / Vercel): Uses env vars or the Bifrost backend fallback
 */

const isDevelopment = process.env.NODE_ENV === 'development';

export const config = {
  // HTTP API base URL
  apiUrl: process.env.NEXT_PUBLIC_BACKEND_URL || 
    (isDevelopment ? 'http://localhost:5167' : 'https://pnyx-dev-206432.bifrost.saastack.site'),
  
  // WebSocket URL for real-time streaming
  wsUrl: process.env.NEXT_PUBLIC_WS_URL || 
    (isDevelopment 
      ? 'ws://localhost:5167/ws/streaming-audio' 
      : 'wss://pnyx-dev-206432.bifrost.saastack.site/ws/streaming-audio'),
  
  // Debug mode - enables extra logging
  debug: isDevelopment,
  
  // Environment name for logging
  environment: isDevelopment ? 'development' : 'production',
};

// Log configuration on startup (client-side only)
if (typeof window !== 'undefined' && config.debug) {
  console.log('[Config] Environment:', config.environment);
  console.log('[Config] API URL:', config.apiUrl);
  console.log('[Config] WS URL:', config.wsUrl);
}

// Export individual values for convenience
export const { apiUrl, wsUrl, debug } = config;
