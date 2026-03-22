import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { authFetch } from '@/lib/api';
import { toast } from 'sonner';
import { Trash2, Key, CheckCircle2, AlertCircle } from 'lucide-react';
import Analytics from '@/lib/analytics';

interface PersonalKey {
    provider: string;
    masked_key: string;
}

const PROVIDERS = [
    { id: 'groq', name: 'Groq (Whisper)' },
    { id: 'deepgram', name: 'Deepgram (Diarization)' },
    // { id: 'gemini', name: 'Gemini (Google)' },
    // { id: 'openai', name: 'OpenAI' },
    // { id: 'claude', name: 'Claude (Anthropic)' },
];

export function PersonalKeysSettings() {
    const [keys, setKeys] = useState<Record<string, string>>({});
    const [loading, setLoading] = useState(true);
    const [savingFor, setSavingFor] = useState<string | null>(null);
    const [newKeys, setNewKeys] = useState<Record<string, string>>({});

    useEffect(() => {
        fetchKeys();
    }, []);

    const fetchKeys = async () => {
        try {
            const response = await authFetch('/api/user/keys');
            if (response.ok) {
                const data = await response.json();
                setKeys(data);
            }
        } catch (error) {
            console.error('Failed to fetch personal keys:', error);
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async (provider: string) => {
        const api_key = newKeys[provider];
        if (!api_key) return;

        setSavingFor(provider);
        try {
            const response = await authFetch('/api/user/keys', {
                method: 'POST',
                body: JSON.stringify({ provider, api_key }),
            });

            if (response.ok) {
                await Analytics.trackSettingsChanged('personal_key_saved', provider);
                toast.success(`Personal key for ${provider} saved!`);
                setNewKeys({ ...newKeys, [provider]: '' });
                fetchKeys();
            } else {
                toast.error('Failed to save key');
            }
        } catch (error) {
            toast.error('Error saving key');
        } finally {
            setSavingFor(null);
        }
    };

    const handleDelete = async (provider: string) => {
        if (!confirm(`Are you sure you want to remove your personal key for ${provider}?`)) return;

        try {
            const response = await authFetch(`/api/user/keys/${provider}`, {
                method: 'DELETE',
            });

            if (response.ok) {
                await Analytics.trackSettingsChanged('personal_key_deleted', provider);
                toast.success(`Personal key for ${provider} removed`);
                fetchKeys();
            }
        } catch (error) {
            toast.error('Error removing key');
        }
    };

    if (loading) {
        return <div className="p-4 text-center">Loading personal keys...</div>;
    }

    return (
        <div className="space-y-6 max-w-2xl mx-auto">
            <div className="flex flex-col gap-2">
                <h3 className="text-lg font-semibold">Personal API Keys</h3>
                <p className="text-sm text-muted-foreground">
                    Provide your own API keys to use for meeting summaries. These will be prioritized over system-wide keys.
                    Keys are stored encrypted and never exposed in full.
                </p>
            </div>

            <div className="grid gap-4">
                {PROVIDERS.map((p) => (
                    <Card key={p.id}>
                        <CardHeader className="pb-3">
                            <div className="flex justify-between items-center">
                                <CardTitle className="text-sm font-medium flex items-center gap-2">
                                    <Key className="h-4 w-4" />
                                    {p.name}
                                </CardTitle>
                                {keys[p.id] && (
                                    <span className="text-xs bg-green-100 text-green-700 px-2 py-1 rounded-full flex items-center gap-1">
                                        <CheckCircle2 className="h-3 w-3" />
                                        Active
                                    </span>
                                )}
                            </div>
                        </CardHeader>
                        <CardContent>
                            <div className="flex gap-2">
                                <div className="flex-1">
                                    {keys[p.id] ? (
                                        <div className="flex items-center justify-between bg-muted/50 p-2 rounded border border-dashed text-sm">
                                            <span className="font-mono text-muted-foreground">{keys[p.id]}</span>
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                onClick={() => handleDelete(p.id)}
                                                className="text-destructive hover:text-destructive hover:bg-destructive/10 h-8"
                                            >
                                                <Trash2 className="h-4 w-4" />
                                            </Button>
                                        </div>
                                    ) : (
                                        <Input
                                            type="password"
                                            placeholder="Enter your API key"
                                            value={newKeys[p.id] || ''}
                                            onChange={(e) => setNewKeys({ ...newKeys, [p.id]: e.target.value })}
                                            className="h-9"
                                        />
                                    )}
                                </div>
                                {!keys[p.id] && (
                                    <Button
                                        size="sm"
                                        disabled={!newKeys[p.id] || savingFor === p.id}
                                        onClick={() => handleSave(p.id)}
                                    >
                                        {savingFor === p.id ? 'Saving...' : 'Save'}
                                    </Button>
                                )}
                            </div>
                        </CardContent>
                    </Card>
                ))}
            </div>

            <Card className="bg-blue-50 border-blue-100 italic">
                <CardContent className="pt-6">
                    <div className="flex gap-3 text-blue-800 text-sm">
                        <AlertCircle className="h-5 w-5 shrink-0" />
                        <p>
                            When a personal key is active, it will be used for all summaries generated by you,
                            instead of using the system's pooled usage.
                        </p>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
