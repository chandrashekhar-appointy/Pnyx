import posthog from 'posthog-js';

export interface AnalyticsProperties {
  [key: string]: any;
}

export interface DeviceInfo {
  platform: string;
  os_version: string;
  architecture: string;
}

export interface UserSession {
  session_id: string;
  user_id: string;
  start_time: string;
  last_heartbeat: string;
  is_active: boolean;
}

const ANALYTICS_OPT_IN_KEY = 'analyticsOptedIn';
const ANONYMOUS_USER_ID_KEY = 'meeting_copilot_user_id';
const FIRST_SEEN_AT_KEY = 'analytics_first_seen_at';
const LAST_ACTIVE_DATE_KEY = 'analytics_last_active_date';
const MEETING_COUNT_TODAY_KEY = 'analytics_meetings_count_today';
const MEETING_COUNT_DATE_KEY = 'analytics_meetings_count_date';
const USED_FEATURES_KEY = 'analytics_used_features';
const POSTHOG_ALIAS_KEY = 'posthog_alias_user_id';
const DEFAULT_POSTHOG_HOST = 'https://us.i.posthog.com';

const isBrowser = () => typeof window !== 'undefined';

const getAnalyticsOptIn = (): boolean => {
  if (!isBrowser()) {
    return false;
  }
  const storedOptIn = window.localStorage.getItem(ANALYTICS_OPT_IN_KEY);
  if (storedOptIn === null) {
    window.localStorage.setItem(ANALYTICS_OPT_IN_KEY, 'true');
    return true;
  }
  return storedOptIn === 'true';
};

const setAnalyticsOptIn = (enabled: boolean): void => {
  if (!isBrowser()) {
    return;
  }
  window.localStorage.setItem(ANALYTICS_OPT_IN_KEY, String(enabled));
};

const getPostHogKey = (): string => process.env.NEXT_PUBLIC_POSTHOG_KEY || '';

const getPostHogHost = (): string =>
  process.env.NEXT_PUBLIC_POSTHOG_HOST || DEFAULT_POSTHOG_HOST;

const getAppEnv = (): string =>
  process.env.NEXT_PUBLIC_APP_ENV || process.env.NODE_ENV || 'development';

const fallbackHash = (value: string): string => {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash << 5) - hash + value.charCodeAt(i);
    hash |= 0;
  }
  return `anon_${Math.abs(hash)}`;
};

export class Analytics {
  private static initialized = false;
  private static currentUserId: string | null = null;
  private static currentSessionId: string | null = null;
  private static posthogEnabled = false;

  private static isConfigured(): boolean {
    return Boolean(getPostHogKey());
  }

  private static sanitizeProperties(
    properties?: AnalyticsProperties
  ): AnalyticsProperties {
    const sanitized = { ...(properties || {}) };
    sanitized.environment = sanitized.environment || getAppEnv();
    if (this.currentSessionId && !sanitized.session_id) {
      sanitized.session_id = this.currentSessionId;
    }
    if (this.currentUserId && !sanitized.user_id) {
      sanitized.user_id = this.currentUserId;
    }
    return sanitized;
  }

  private static async normalizeUserId(userId?: string | null): Promise<string> {
    if (!userId) {
      return this.getPersistentUserId();
    }

    const normalized = userId.trim().toLowerCase();
    if (!normalized.includes('@') || !isBrowser()) {
      return normalized;
    }

    try {
      const encoded = new TextEncoder().encode(normalized);
      const digest = await window.crypto.subtle.digest('SHA-256', encoded);
      const hex = Array.from(new Uint8Array(digest))
        .map((byte) => byte.toString(16).padStart(2, '0'))
        .join('');
      return `user_${hex}`;
    } catch {
      return fallbackHash(normalized);
    }
  }

  static async init(userId?: string): Promise<void> {
    if (!isBrowser()) {
      return;
    }

    this.currentUserId = await this.normalizeUserId(userId);
    this.posthogEnabled = this.isConfigured();

    if (!this.posthogEnabled) {
      this.initialized = true;
      console.info(
        '[Analytics] PostHog key is not configured. Analytics calls will no-op.'
      );
      return;
    }

    if (!this.initialized) {
      posthog.init(getPostHogKey(), {
        api_host: getPostHogHost(),
        autocapture: false,
        capture_pageview: false,
        capture_pageleave: true,
        persistence: 'localStorage+cookie',
      });
      this.initialized = true;
    }

    if (getAnalyticsOptIn()) {
      posthog.opt_in_capturing();
    } else {
      posthog.opt_out_capturing();
    }
  }

  static async disable(): Promise<void> {
    setAnalyticsOptIn(false);
    if (this.posthogEnabled) {
      posthog.opt_out_capturing();
    }
  }

  static async enable(): Promise<void> {
    setAnalyticsOptIn(true);
    if (!this.initialized) {
      await this.init();
    }
    if (this.posthogEnabled) {
      posthog.opt_in_capturing();
    }
  }

  static async isEnabled(): Promise<boolean> {
    return getAnalyticsOptIn() && this.initialized;
  }

  static async track(
    eventName: string,
    properties?: AnalyticsProperties
  ): Promise<void> {
    if (!(await this.isEnabled()) || !this.posthogEnabled) {
      return;
    }

    try {
      posthog.capture(eventName, this.sanitizeProperties(properties));
    } catch (error) {
      console.warn('[Analytics] Failed to capture event:', eventName, error);
    }
  }

  static async identify(
    userId: string,
    properties?: AnalyticsProperties
  ): Promise<void> {
    if (!userId) {
      return;
    }

    const normalizedUserId = await this.normalizeUserId(userId);
    this.currentUserId = normalizedUserId;

    if (!this.initialized) {
      await this.init(normalizedUserId);
    }

    if (!this.posthogEnabled || !getAnalyticsOptIn()) {
      return;
    }

    const anonymousId = window.localStorage.getItem(ANONYMOUS_USER_ID_KEY);
    const aliasedUserId = window.localStorage.getItem(POSTHOG_ALIAS_KEY);

    try {
      if (
        anonymousId &&
        anonymousId !== normalizedUserId &&
        aliasedUserId !== normalizedUserId
      ) {
        posthog.alias(normalizedUserId, anonymousId);
        window.localStorage.setItem(POSTHOG_ALIAS_KEY, normalizedUserId);
      }
      posthog.identify(normalizedUserId, this.sanitizeProperties(properties));
    } catch (error) {
      console.warn('[Analytics] Failed to identify user:', error);
    }
  }

  static async startSession(userId: string): Promise<string | null> {
    if (!isBrowser()) {
      return null;
    }

    const sessionId =
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `web-session-${Date.now()}`;

    this.currentSessionId = sessionId;
    this.currentUserId = await this.normalizeUserId(userId || this.currentUserId);

    if (this.posthogEnabled) {
      posthog.register({
        session_id: sessionId,
        user_id: this.currentUserId,
      });
    }

    return sessionId;
  }

  static async endSession(): Promise<void> {
    if (!this.currentSessionId) {
      return;
    }
    await this.track('session_ended', { session_id: this.currentSessionId });
    if (this.posthogEnabled) {
      posthog.unregister('session_id');
    }
    this.currentSessionId = null;
  }

  static async trackDailyActiveUser(): Promise<void> {
    await this.track('daily_active_user');
  }

  static async trackUserFirstLaunch(): Promise<void> {
    await this.track('user_first_launch');
  }

  static async isSessionActive(): Promise<boolean> {
    return Boolean(this.currentSessionId);
  }

  static async getPersistentUserId(): Promise<string> {
    if (!isBrowser()) {
      return 'server';
    }

    let userId = window.localStorage.getItem(ANONYMOUS_USER_ID_KEY);
    if (!userId) {
      userId =
        typeof crypto !== 'undefined' && 'randomUUID' in crypto
          ? crypto.randomUUID()
          : `user_${Date.now()}_${Math.random().toString(36).slice(2, 11)}`;
      window.localStorage.setItem(ANONYMOUS_USER_ID_KEY, userId);
    }
    return userId;
  }

  static async checkAndTrackFirstLaunch(): Promise<void> {
    if (!isBrowser()) {
      return;
    }

    if (!window.localStorage.getItem(FIRST_SEEN_AT_KEY)) {
      window.localStorage.setItem(FIRST_SEEN_AT_KEY, new Date().toISOString());
      await this.trackUserFirstLaunch();
    }
  }

  static async checkAndTrackDailyUsage(): Promise<void> {
    if (!isBrowser()) {
      return;
    }

    const today = new Date().toISOString().slice(0, 10);
    const lastActiveDate = window.localStorage.getItem(LAST_ACTIVE_DATE_KEY);
    if (lastActiveDate !== today) {
      window.localStorage.setItem(LAST_ACTIVE_DATE_KEY, today);
      await this.trackDailyActiveUser();
    }
  }

  static getCurrentUserId(): string | null {
    return this.currentUserId;
  }

  static async getPlatform(): Promise<string> {
    return 'Web';
  }

  static async getOSVersion(): Promise<string> {
    if (!isBrowser()) {
      return 'unknown';
    }
    return window.navigator.userAgent;
  }

  static async getDeviceInfo(): Promise<DeviceInfo> {
    if (!isBrowser()) {
      return {
        platform: 'unknown',
        os_version: 'unknown',
        architecture: 'unknown',
      };
    }

    return {
      platform: window.navigator.platform || 'Web',
      os_version: window.navigator.userAgent || 'Web',
      architecture: 'unknown',
    };
  }

  static async calculateDaysSince(storageKey: string): Promise<number | null> {
    if (!isBrowser()) {
      return null;
    }

    const value = window.localStorage.getItem(storageKey);
    if (!value) {
      return null;
    }

    const parsedDate = new Date(value);
    if (Number.isNaN(parsedDate.getTime())) {
      return null;
    }

    const diffMs = Date.now() - parsedDate.getTime();
    return Math.floor(diffMs / (1000 * 60 * 60 * 24));
  }

  static async updateMeetingCount(): Promise<void> {
    if (!isBrowser()) {
      return;
    }

    const today = new Date().toISOString().slice(0, 10);
    const storedDate = window.localStorage.getItem(MEETING_COUNT_DATE_KEY);
    const currentCount =
      Number(window.localStorage.getItem(MEETING_COUNT_TODAY_KEY) || '0') || 0;

    if (storedDate !== today) {
      window.localStorage.setItem(MEETING_COUNT_DATE_KEY, today);
      window.localStorage.setItem(MEETING_COUNT_TODAY_KEY, '1');
      return;
    }

    window.localStorage.setItem(
      MEETING_COUNT_TODAY_KEY,
      String(currentCount + 1)
    );
  }

  static async getMeetingsCountToday(): Promise<number> {
    if (!isBrowser()) {
      return 0;
    }

    const today = new Date().toISOString().slice(0, 10);
    if (window.localStorage.getItem(MEETING_COUNT_DATE_KEY) !== today) {
      return 0;
    }

    return Number(window.localStorage.getItem(MEETING_COUNT_TODAY_KEY) || '0') || 0;
  }

  static async hasUsedFeatureBefore(featureName: string): Promise<boolean> {
    if (!isBrowser()) {
      return false;
    }

    const features = JSON.parse(
      window.localStorage.getItem(USED_FEATURES_KEY) || '[]'
    ) as string[];
    return features.includes(featureName);
  }

  static async markFeatureUsed(featureName: string): Promise<void> {
    if (!isBrowser()) {
      return;
    }

    const features = new Set(
      JSON.parse(window.localStorage.getItem(USED_FEATURES_KEY) || '[]') as string[]
    );
    features.add(featureName);
    window.localStorage.setItem(
      USED_FEATURES_KEY,
      JSON.stringify(Array.from(features))
    );
  }

  static async trackSessionStarted(sessionId: string): Promise<void> {
    await this.track('session_started', { session_id: sessionId });
  }

  static async trackSessionEnded(sessionId: string): Promise<void> {
    await this.track('session_ended', { session_id: sessionId });
  }

  static async trackMeetingCompleted(
    meetingId: string,
    metrics: any
  ): Promise<void> {
    await this.track('meeting_completed', { meeting_id: meetingId, ...metrics });
  }

  static async trackFeatureUsedEnhanced(
    featureName: string,
    properties?: Record<string, any>
  ): Promise<void> {
    await this.track('feature_used', { feature: featureName, ...properties });
  }

  static async trackCopy(
    copyType: 'transcript' | 'summary',
    properties?: Record<string, any>
  ): Promise<void> {
    await this.track('content_copied', { type: copyType, ...properties });
  }

  static async trackMeetingStarted(
    meetingId: string,
    meetingTitle: string
  ): Promise<void> {
    await this.updateMeetingCount();
    await this.track('meeting_started', {
      meeting_id: meetingId,
      title_length: meetingTitle.length,
    });
  }

  static async trackRecordingStarted(meetingId: string): Promise<void> {
    await this.track('recording_started', { meeting_id: meetingId });
  }

  static async trackRecordingStopped(
    meetingId: string,
    durationSeconds?: number
  ): Promise<void> {
    await this.track('recording_stopped', {
      meeting_id: meetingId,
      duration: durationSeconds,
    });
  }

  static async trackMeetingDeleted(meetingId: string): Promise<void> {
    await this.track('meeting_deleted', { meeting_id: meetingId });
  }

  static async trackSettingsChanged(
    settingType: string,
    newValue: string
  ): Promise<void> {
    await this.track('settings_changed', {
      setting_type: settingType,
      new_value: newValue,
    });
  }

  static async trackFeatureUsed(featureName: string): Promise<void> {
    await this.markFeatureUsed(featureName);
    await this.track('feature_used', { feature: featureName });
  }

  static async trackPageView(pageName: string): Promise<void> {
    await this.track('page_view', { page: pageName });
  }

  static async trackButtonClick(
    buttonName: string,
    location?: string
  ): Promise<void> {
    await this.track('button_click', { button: buttonName, location });
  }

  static async trackError(
    errorType: string,
    errorMessage: string
  ): Promise<void> {
    await this.track('error_occurred', {
      error_type: errorType,
      message: errorMessage,
    });
  }

  static async trackAppStarted(): Promise<void> {
    await this.track('app_started');
  }

  static async cleanup(): Promise<void> {
    return;
  }

  static reset(): void {
    this.currentUserId = null;
    this.currentSessionId = null;
    if (this.posthogEnabled) {
      posthog.reset();
    }
  }

  static async waitForInitialization(_timeout: number = 5000): Promise<boolean> {
    return this.initialized;
  }

  static async trackBackendConnection(success: boolean, error?: string) {
    await this.track('backend_connection', { success, error });
  }

  static async trackTranscriptionError(errorMessage: string) {
    await this.track('transcription_error', { message: errorMessage });
  }

  static async trackTranscriptionSuccess(duration?: number) {
    await this.track('transcription_success', { duration });
  }

  static async trackSummaryGenerationStarted(
    modelProvider: string,
    modelName: string,
    transcriptLength: number,
    timeSinceRecordingMinutes?: number
  ) {
    await this.track('notes_generation_started', {
      provider: modelProvider,
      model: modelName,
      transcript_length: transcriptLength,
      time_since_recording_minutes: timeSinceRecordingMinutes,
    });
  }

  static async trackSummaryGenerationCompleted(
    modelProvider: string,
    modelName: string,
    success: boolean,
    durationSeconds?: number,
    errorMessage?: string
  ) {
    await this.track('notes_generated', {
      provider: modelProvider,
      model: modelName,
      llm_model: `${modelProvider}_${modelName}`,
      success,
      duration: durationSeconds,
      error: errorMessage,
    });
  }

  static async trackSummaryRegenerated(
    modelProvider: string,
    modelName: string
  ) {
    await this.track('notes_regenerated', {
      llm_model: `${modelProvider}_${modelName}`,
    });
  }

  static async trackModelChanged(
    oldProvider: string,
    oldModel: string,
    newProvider: string,
    newModel: string
  ) {
    await this.track('model_changed', {
      old_provider: oldProvider,
      old_model: oldModel,
      new_provider: newProvider,
      new_model: newModel,
    });
  }

  static async trackCustomPromptUsed(length: number) {
    await this.track('custom_prompt_used', { prompt_length: length });
  }
}

export default Analytics;
