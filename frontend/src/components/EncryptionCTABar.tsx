'use client';

import { useState, useEffect } from 'react';
import { Shield, ArrowRight, X } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { authFetch } from '@/lib/api';
import Link from 'next/link';

interface EncryptionCTABarProps {
    isRecording: boolean;
}

export function EncryptionCTABar({ isRecording }: EncryptionCTABarProps) {
    const [isVisible, setIsVisible] = useState(false);
    const [isDismissed, setIsDismissed] = useState(false);

    useEffect(() => {
        const checkStatus = async () => {
            if (isRecording || isDismissed) {
                setIsVisible(false);
                return;
            }

            try {
                const response = await authFetch('/api/user/encryption-status', {
                    preventLogout: true
                });
                if (response.ok) {
                    const data = await response.json();
                    setIsVisible(!data.enabled);
                }
            } catch (error) {
                console.error('Failed to fetch encryption status:', error);
            }
        };

        checkStatus();
    }, [isRecording, isDismissed]);

    if (!isVisible) return null;

    return (
        <AnimatePresence>
            <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                className="bg-blue-600 text-white overflow-hidden"
            >
                <div className="max-w-7xl mx-auto px-4 py-2 flex items-center justify-between text-sm">
                    <div className="flex items-center gap-2">
                        <Shield className="h-4 w-4" />
                        <span className="font-medium">
                            Enable Zero-Knowledge Encryption to protect your meetings.
                        </span>
                        <Link 
                            href="/settings?tab=encryption"
                            className="ml-2 underline underline-offset-4 hover:text-blue-100 flex items-center gap-1 font-semibold"
                        >
                            Configure now <ArrowRight className="h-3 w-3" />
                        </Link>
                    </div>
                    <button 
                        onClick={() => setIsDismissed(true)}
                        className="p-1 hover:bg-blue-500 rounded-full transition-colors"
                        aria-label="Dismiss"
                    >
                        <X className="h-4 w-4" />
                    </button>
                </div>
            </motion.div>
        </AnimatePresence>
    );
}
