'use client';

import { signIn } from 'next-auth/react';
import { useState, useEffect } from 'react';
import Analytics from '@/lib/analytics';

/**
 * Login Page
 * Simple Google OAuth login
 */
export default function LoginPage() {
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => { Analytics.trackPageView('login'); }, []);

    // Preserve the URL the user was originally trying to reach (and its query
    // params — important for the calendar email "Start Pnyx" flow which sends
    // ?autoStart=true). NextAuth's withAuth middleware appends ?callbackUrl=
    // when bouncing unauth users here; honor it instead of forcing '/'.
    const getCallbackUrl = (): string => {
        if (typeof window === 'undefined') return '/';
        const params = new URLSearchParams(window.location.search);
        const cb = params.get('callbackUrl');
        if (!cb) return '/';
        // Only allow same-origin relative paths to prevent open-redirect abuse.
        try {
            const parsed = new URL(cb, window.location.origin);
            if (parsed.origin !== window.location.origin) return '/';
            return parsed.pathname + parsed.search + parsed.hash;
        } catch {
            return '/';
        }
    };

    const handleGoogleSignIn = async () => {
        setIsLoading(true);
        setError(null);
        Analytics.trackLoginAttempted('google');

        try {
            await signIn('google', { callbackUrl: getCallbackUrl() });
        } catch (err) {
            setError('Failed to sign in. Please try again.');
            Analytics.trackLoginFailed('google', 'Failed to sign in');
            setIsLoading(false);
        }
    };

    return (
        <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 flex items-center justify-center p-4">
            <div className="w-full max-w-md">
                {/* Logo/Title Section */}
                <div className="text-center mb-8">
                    <img
                        src="/image.png"
                        alt="Pnyx Logo"
                        className="w-48 h-48 object-contain mx-auto mb-2"
                    />
                    <h1 className="text-3xl font-bold text-gray-900">Pnyx</h1>
                    <p className="text-gray-600 mt-2">AI-powered meeting transcription & notes</p>
                </div>

                {/* Login Card */}
                <div className="bg-white rounded-2xl shadow-xl p-8">
                    <h2 className="text-xl font-semibold text-gray-800 text-center mb-6">
                        Sign in to continue
                    </h2>

                    {/* Error Message */}
                    {error && (
                        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
                            {error}
                        </div>
                    )}

                    {/* Google Sign In Button */}
                    <button
                        onClick={handleGoogleSignIn}
                        disabled={isLoading}
                        className={`w-full flex items-center justify-center gap-3 px-4 py-3 border border-gray-300 rounded-lg font-medium transition-all duration-200 ${isLoading
                                ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                                : 'bg-white hover:bg-gray-50 text-gray-700 hover:shadow-md'
                            }`}
                    >
                        {isLoading ? (
                            <svg className="animate-spin h-5 w-5 text-gray-400" viewBox="0 0 24 24">
                                <circle
                                    className="opacity-25"
                                    cx="12"
                                    cy="12"
                                    r="10"
                                    stroke="currentColor"
                                    strokeWidth="4"
                                    fill="none"
                                />
                                <path
                                    className="opacity-75"
                                    fill="currentColor"
                                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                                />
                            </svg>
                        ) : (
                            <svg className="w-5 h-5" viewBox="0 0 24 24">
                                <path
                                    fill="#4285F4"
                                    d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                                />
                                <path
                                    fill="#34A853"
                                    d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                                />
                                <path
                                    fill="#FBBC05"
                                    d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                                />
                                <path
                                    fill="#EA4335"
                                    d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                                />
                            </svg>
                        )}
                        <span>{isLoading ? 'Signing in...' : 'Continue with Google'}</span>
                    </button>

                    {/* Sign-in Notice */}
                    <div className="mt-6 text-center">
                        <p className="text-sm text-gray-500">
                            Sign in with an approved Google account for this workspace
                        </p>
                    </div>
                </div>

                {/* Footer */}
                <p className="text-center text-sm text-gray-500 mt-6">
                    Having trouble? Contact IT support
                </p>
            </div>
        </div>
    );
}
