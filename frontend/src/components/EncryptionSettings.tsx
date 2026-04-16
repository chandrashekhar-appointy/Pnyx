'use client';

import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { KeyManager } from '@/lib/crypto/key_manager';
import { authFetch } from '@/lib/api';
import { toast } from 'sonner';
import { Shield, Download, RefreshCw, Trash2, AlertTriangle, Key, CheckCircle2, Lock, X } from 'lucide-react';
import Analytics from '@/lib/analytics';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { syncEncryptionPublicKey } from '@/lib/crypto/key_sync';

export function EncryptionSettings() {
    const [hasKey, setHasKey] = useState<boolean>(false);
    const [isEncryptionEnabled, setIsEncryptionEnabled] = useState<boolean>(false);
    const [loading, setLoading] = useState(true);
    const [actionLoading, setActionLoading] = useState(false);
    const [showBackupModal, setShowBackupModal] = useState(false);
    const [showDeactivateModal, setShowDeactivateModal] = useState(false);
    const [hasDownloadedBackup, setHasDownloadedBackup] = useState(false);

    useEffect(() => {
        const init = async () => {
            await Promise.all([checkKeyStatus(), fetchEncryptionStatus()]);
            setLoading(false);
        };
        init();
    }, []);

    const fetchEncryptionStatus = async () => {
        try {
            const response = await authFetch('/api/user/encryption-status');
            if (response.ok) {
                const data = await response.json();
                setIsEncryptionEnabled(!!data.enabled);
            }
        } catch (error) {
            console.error('Failed to fetch encryption status:', error);
        }
    };

    const checkKeyStatus = async () => {
        try {
            const exists = await KeyManager.hasPrivateKey();
            setHasKey(exists);
        } catch (error) {
            console.error('Failed to check encryption key status:', error);
        }
    };

    const handleBackupKey = async () => {
        try {
            const privateKey = await KeyManager.getPrivateKeyBase64();
            if (!privateKey) {
                toast.error('Could not retrieve private key for backup.');
                return;
            }

            const blob = new Blob([privateKey], { type: 'text/plain' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `meeting-copilot-private-key-${new Date().toISOString().split('T')[0]}.txt`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            
            toast.success('Private key downloaded. Store it safely!');
            setHasDownloadedBackup(true);
            Analytics.track('encryption_key_backed_up');
        } catch (error) {
            toast.error('Failed to export private key.');
        }
    };

    const handleRotateKeys = async () => {
        if (hasKey && !confirm('Are you sure you want to rotate your encryption keys? This will generate a new pair. Old meetings will remain encrypted with your old key—make sure you have it backed up!')) {
            return;
        }

        setActionLoading(true);
        try {
            const keys = await KeyManager.generateAndStoreKeyPair();
            const response = await authFetch('/api/user/encryption-key', {
                method: 'POST',
                body: JSON.stringify({ public_key: keys.publicKey }),
            });

            if (response.ok) {
                toast.success('Encryption keys generated. Backup required.');
                setHasKey(true);
                setHasDownloadedBackup(false);
                setShowBackupModal(true);
                Analytics.track('encryption_keys_rotated');
            } else {
                toast.error('Failed to sync new public key to server.');
            }
        } catch (error) {
            toast.error('Failed to rotate encryption keys.');
        } finally {
            setActionLoading(false);
        }
    };

    const handleDeactivate = async () => {
        // Proceeding with deactivation (modal already confirmed)
        setShowDeactivateModal(false);
        setActionLoading(true);
        try {
            // 1. Remove public key from server
            const response = await authFetch('/api/user/encryption-key', {
                method: 'DELETE',
            });

            if (response.ok) {
                // 2. Clear local keys
                await KeyManager.destroyKeys();
                setHasKey(false);
                setIsEncryptionEnabled(false);
                toast.success('Zero-Knowledge Encryption deactivated.');
                Analytics.track('encryption_deactivated');
            } else {
                toast.error('Failed to remove public key from server.');
            }
        } catch (error) {
            toast.error('Failed to deactivate encryption.');
        } finally {
            setActionLoading(false);
        }
    };

    const handleToggleEncryption = async (enabled: boolean) => {
        // If enabling for the first time without keys, generate them first.
        if (enabled && !hasKey) {
            // No need for a confirm here if we show the mandatory backup modal later, 
            // but we should warn them once.
            if (!confirm('This will generate a secure encryption key pair for your meetings. You must download a backup of this key to avoid losing access to your data. Proceed?')) {
                return;
            }
            setActionLoading(true);
            try {
                const keys = await KeyManager.generateAndStoreKeyPair();
                await authFetch('/api/user/encryption-key', {
                    method: 'POST',
                    body: JSON.stringify({ public_key: keys.publicKey }),
                });
                setHasKey(true);
                setHasDownloadedBackup(false);
                setShowBackupModal(true);
                toast.success('Encryption keys generated. Backup required.');
                // Proceed to update the server status as well
            } catch (error) {
                toast.error('Failed to generate keys.');
                return; 
            } finally {
                setActionLoading(false);
            }
        }

        try {
            const response = await authFetch('/api/user/encryption-status', {
                method: 'POST',
                body: JSON.stringify({ enabled }),
            });

            if (response.ok) {
                setIsEncryptionEnabled(enabled);
                toast.success(`Encryption for new meetings is now ${enabled ? 'ENABLED' : 'DISABLED'}.`);
                Analytics.track('encryption_toggle_changed', { enabled });
            } else {
                const errorData = await response.json().catch(() => ({}));
                console.error('Failed to update encryption status:', errorData);
                toast.error(`Failed to update encryption status: ${errorData.detail || 'Unknown error'}`);
                // Revert toggle if failed? 
                // setIsEncryptionEnabled(!enabled);
            }
        } catch (error) {
            console.error('Network error while updating status:', error);
            toast.error('Network error while updating status.');
        } finally {
            setActionLoading(false);
        }
    };

    if (loading) {
        return <div className="p-4 text-center">Checking security status...</div>;
    }

    return (
        <div className="space-y-6 max-w-2xl mx-auto">
            <div className="flex flex-col gap-2 text-center sm:text-left">
                <h3 className="text-lg font-semibold flex items-center gap-2 justify-center sm:justify-start">
                    <Shield className="h-5 w-5 text-blue-600" />
                    Zero-Knowledge Encryption (E2EE)
                </h3>
                <p className="text-sm text-muted-foreground">
                    Protect your meetings with end-to-end encryption. Only you hold the key to decrypt your audio and transcripts.
                </p>
            </div>

            <Card className={hasKey ? "border-green-100 bg-green-50/30" : "border-yellow-100 bg-yellow-50/30"}>
                <CardHeader className="pb-3">
                    <div className="flex justify-between items-center">
                        <CardTitle className="text-base font-medium flex items-center gap-2">
                            {isEncryptionEnabled ? (
                                <>
                                    <CheckCircle2 className="h-5 w-5 text-green-600" />
                                    Encryption Active
                                </>
                            ) : (
                                <>
                                    <Shield className={`h-5 w-5 ${hasKey ? "text-blue-600" : "text-gray-400"}`} />
                                    {hasKey ? "Encryption Paused" : "Encryption Off"}
                                </>
                            )}
                        </CardTitle>
                        <div className="flex items-center space-x-2">
                            <Label htmlFor="encryption-toggle" className="text-xs font-medium cursor-pointer">
                                {isEncryptionEnabled ? "ENABLED" : "DISABLED"}
                            </Label>
                            <Switch 
                                id="encryption-toggle" 
                                checked={isEncryptionEnabled} 
                                onCheckedChange={handleToggleEncryption}
                                disabled={actionLoading}
                            />
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="space-y-4">
                    <p className="text-sm leading-relaxed">
                        {isEncryptionEnabled 
                            ? "New meetings will be end-to-end encrypted. Your data is sealed before it reaches our servers."
                            : hasKey 
                                ? "Encryption is currently disabled for new meetings, but you still have keys for your existing sensitive recordings."
                                : "Encryption is currently disabled. Enable it to ensure your meeting data stays private to you."
                        }
                    </p>
                    
                    {!hasKey ? (
                        <Button 
                            className="w-full sm:w-auto bg-blue-600 hover:bg-blue-700" 
                            onClick={handleRotateKeys}
                            disabled={actionLoading}
                        >
                            <Key className="mr-2 h-4 w-4" />
                            {actionLoading ? 'Initializing...' : 'Initialize Encryption'}
                        </Button>
                    ) : (
                        <div className="flex flex-col sm:flex-row gap-2">
                            <Button 
                                variant="outline" 
                                className="flex-1 bg-white" 
                                onClick={handleBackupKey}
                            >
                                <Download className="mr-2 h-4 w-4" />
                                Download Backup
                            </Button>
                            <Button 
                                variant="outline" 
                                className="flex-1 bg-white"
                                onClick={handleRotateKeys}
                                disabled={actionLoading}
                            >
                                <RefreshCw className={`mr-2 h-4 w-4 ${actionLoading ? 'animate-spin' : ''}`} />
                                Rotate Keys
                            </Button>
                        </div>
                    )}

                    {!hasKey && (
                        <div className="pt-2">
                            <div className="relative">
                                <div className="absolute inset-0 flex items-center">
                                    <span className="w-full border-t border-gray-200" />
                                </div>
                                <div className="relative flex justify-center text-xs uppercase">
                                    <span className="bg-white px-2 text-muted-foreground font-medium bg-yellow-50/0">Or restore from backup</span>
                                </div>
                            </div>
                            <div className="mt-4">
                                <label className="block text-xs font-medium text-gray-700 mb-2">Paste your Private Key string:</label>
                                <div className="flex gap-2">
                                    <textarea
                                        className="flex-1 min-h-[80px] text-[10px] font-mono p-2 border rounded bg-white resize-none"
                                        placeholder="Paste your base64 private key here..."
                                        id="restore-key-input"
                                    />
                                    <Button 
                                        size="sm" 
                                        className="h-auto px-4"
                                        onClick={async () => {
                                            const textarea = document.getElementById('restore-key-input') as HTMLTextAreaElement;
                                            const val = textarea?.value?.trim();
                                            if (!val) {
                                                toast.error('Please paste a key value.');
                                                return;
                                            }
                                            setActionLoading(true);
                                            try {
                                                const key = await KeyManager.importPrivateKey(val);
                                                const publicKey = await KeyManager.derivePublicKey(key);
                                                await KeyManager.storeKeyPair({
                                                    privateKey: key,
                                                    publicKey,
                                                });
                                                await syncEncryptionPublicKey();
                                                setHasKey(true);
                                                toast.success('Private key restored and synced successfully!');
                                                textarea.value = '';
                                                Analytics.track('encryption_key_restored');
                                            } catch (e) {
                                                console.error('Failed to restore private key:', e);
                                                toast.error('Could not restore and sync this private key.');
                                            } finally {
                                                setActionLoading(false);
                                            }
                                        }}
                                        disabled={actionLoading}
                                    >
                                        Restore
                                    </Button>
                                </div>
                            </div>
                        </div>
                    )}
                </CardContent>
            </Card>

            {hasKey && (
                <Card className="border-red-100">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium text-red-700">Danger Zone</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                            <div className="space-y-1">
                                <p className="text-xs text-red-600 font-medium">Deactivate Encryption</p>
                                <p className="text-[11px] text-muted-foreground leading-tight">
                                    Clears your keys. Existing meetings will become inaccessible.
                                </p>
                            </div>
                            <Button 
                                variant="destructive" 
                                size="sm" 
                                className="w-full sm:w-auto h-8 px-4"
                                onClick={() => setShowDeactivateModal(true)}
                                disabled={actionLoading}
                            >
                                <Trash2 className="mr-2 h-3.5 w-3.5" />
                                Deactivate
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            )}

            <div className="bg-blue-50/50 rounded-lg p-4 border border-blue-100">
                <h4 className="text-sm font-medium text-blue-900 mb-2 flex items-center gap-2">
                    <Shield className="h-4 w-4" />
                    How it works
                </h4>
                <ul className="text-xs text-blue-800 space-y-2 list-disc pl-4">
                    <li>Your <strong>Private Key</strong> never leaves your device.</li>
                    <li>The server only stores your <strong>Public Key</strong> to encrypt data for you.</li>
                    <li>If you lose your browser data AND don't have a backup, your meetings are <strong>gone forever</strong>.</li>
                    <li>We (Meeting Co-Pilot) can <strong>never</strong> help you recover your data without your key.</li>
                    <li><strong>Opt-in:</strong> We only encrypt meetings recorded while encryption is enabled.</li>
                </ul>
            </div>

            {/* Mandatory Backup Modal */}
            <Dialog open={showBackupModal} onOpenChange={(open) => {
                if (!open && !hasDownloadedBackup) {
                    toast.error("Download required", {
                        description: "You must download a backup of your key for safety."
                    });
                    return;
                }
                setShowBackupModal(open);
            }}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Lock className="h-5 w-5 text-blue-600" />
                            Key Backup Required
                        </DialogTitle>
                        <DialogDescription>
                            Your new encryption keys have been generated. Since this is <strong>Zero-Knowledge</strong>, we cannot recover your data if you lose access to this browser.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 flex gap-3">
                        <AlertTriangle className="h-5 w-5 text-amber-600 shrink-0" />
                        <div className="text-xs text-amber-800 leading-relaxed">
                            <p className="font-bold mb-1">IMPORTANT:</p>
                            Download this file and store it in a password manager or secure cloud storage. Without it, your encrypted meetings are gone forever if you clear your browser data.
                        </div>
                    </div>
                    <DialogFooter className="sm:justify-start flex-col sm:flex-row gap-2">
                        <Button 
                            type="button" 
                            variant="default" 
                            className="w-full sm:w-auto bg-blue-600 hover:bg-blue-700"
                            onClick={handleBackupKey}
                        >
                            <Download className="mr-2 h-4 w-4" />
                            Download Private Key
                        </Button>
                        <Button 
                            type="button" 
                            variant="outline" 
                            className="w-full sm:w-auto"
                            disabled={!hasDownloadedBackup}
                            onClick={() => setShowBackupModal(false)}
                        >
                            I've saved it
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Deactivate Modal */}
            <Dialog open={showDeactivateModal} onOpenChange={setShowDeactivateModal}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2 text-red-600">
                            <AlertTriangle className="h-5 w-5" />
                            Deactivate Encryption?
                        </DialogTitle>
                        <DialogDescription>
                            This will permanently remove your encryption keys from this browser and the server.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="bg-red-50 border border-red-200 text-red-800 text-sm rounded-lg p-4">
                        <strong>WARNING:</strong> You will NOT be able to access existing encrypted meetings unless you have a backup of your private key!
                    </div>
                    <DialogFooter className="sm:justify-end gap-2 mt-4">
                        <Button
                            type="button"
                            variant="outline"
                            onClick={() => setShowDeactivateModal(false)}
                            disabled={actionLoading}
                        >
                            Cancel
                        </Button>
                        <Button
                            type="button"
                            variant="destructive"
                            onClick={handleDeactivate}
                            disabled={actionLoading}
                        >
                            {actionLoading ? 'Deactivating...' : 'Yes, Deactivate'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
