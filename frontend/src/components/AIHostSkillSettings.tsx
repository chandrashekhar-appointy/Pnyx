'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { authFetch } from '@/lib/api';
import { toast } from 'sonner';
import Analytics from '@/lib/analytics';

interface StyleItem {
  id: string;
  name: string;
  source: 'system' | 'user';
  read_only: boolean;
  is_default: boolean;
  is_active: boolean;
  skill_markdown: string;
}

interface StylesResponse {
  styles: StyleItem[];
  default_style_id: string;
}

export function AIHostSkillSettings() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [styles, setStyles] = useState<StyleItem[]>([]);
  const [defaultStyleId, setDefaultStyleId] = useState('system:facilitator');
  const [selectedStyleId, setSelectedStyleId] = useState('system:facilitator');
  const [draftName, setDraftName] = useState('');
  const [draftMarkdown, setDraftMarkdown] = useState('');
  const [draftActive, setDraftActive] = useState(true);
  const [isCreating, setIsCreating] = useState(false);
  // When isCreating is true, the editor starts in "describe" mode (user types
  // a plain-English description and clicks Generate). Once generated, it
  // switches to the markdown editor so the user can fine-tune.
  const [creationStep, setCreationStep] = useState<'describe' | 'edit'>('describe');
  const [describePrompt, setDescribePrompt] = useState('');
  const [generating, setGenerating] = useState(false);
  const hasTrackedAskBeforeMeetingRef = useRef(false);
  const [askBeforeMeeting, setAskBeforeMeeting] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    return localStorage.getItem('ai_host_ask_before_meeting') === 'true';
  });

  const selectedStyle = useMemo(
    () => styles.find((style) => style.id === selectedStyleId) || null,
    [styles, selectedStyleId]
  );

  const loadStyles = async () => {
    setLoading(true);
    try {
      const res = await authFetch('/api/user/ai-host-styles', { method: 'GET' });
      if (!res.ok) throw new Error('Failed to load AI Participant styles');
      const data = (await res.json()) as StylesResponse;
      setStyles(data.styles || []);
      setDefaultStyleId(data.default_style_id || 'system:facilitator');
      const selected = data.styles?.some((s) => s.id === data.default_style_id)
        ? data.default_style_id
        : (data.styles?.[0]?.id || 'system:facilitator');
      setSelectedStyleId(selected);
      setIsCreating(false);
    } catch (error) {
      console.error(error);
      toast.error('Failed to load AI Participant styles');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadStyles();
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    localStorage.setItem('ai_host_ask_before_meeting', askBeforeMeeting ? 'true' : 'false');
    if (!hasTrackedAskBeforeMeetingRef.current) {
      hasTrackedAskBeforeMeetingRef.current = true;
      return;
    }
    Analytics.trackSettingsChanged('ai_host_ask_before_meeting', askBeforeMeeting ? 'enabled' : 'disabled');
  }, [askBeforeMeeting]);

  useEffect(() => {
    if (!selectedStyle || isCreating) return;
    setDraftName(selectedStyle.name || '');
    setDraftMarkdown(selectedStyle.skill_markdown || '');
    setDraftActive(Boolean(selectedStyle.is_active));
  }, [selectedStyle, isCreating]);

  const startCreate = () => {
    setIsCreating(true);
    setCreationStep('describe');
    setDescribePrompt('');
    setDraftName('');
    setDraftMarkdown('');
    setDraftActive(true);
  };

  const cancelCreate = () => {
    setIsCreating(false);
    setCreationStep('describe');
    setDescribePrompt('');
    setSelectedStyleId(defaultStyleId || 'system:facilitator');
  };

  const generateFromPrompt = async () => {
    const prompt = describePrompt.trim();
    if (!prompt) {
      toast.error('Please describe how you want your AI to behave');
      return;
    }
    setGenerating(true);
    try {
      const res = await authFetch('/api/user/ai-host-skill/generate-from-prompt', {
        method: 'POST',
        body: JSON.stringify({ prompt, suggested_name: draftName.trim() || undefined }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || 'Failed to generate skill');
      }
      const data = (await res.json()) as { name: string; skill_markdown: string };
      setDraftName(data.name || 'Custom Style');
      setDraftMarkdown(data.skill_markdown || '');
      setCreationStep('edit');
      toast.success('Generated. Review and tweak before saving.');
    } catch (error) {
      console.error('[AIHostSkill] generate failed', error);
      toast.error(error instanceof Error ? error.message : 'Failed to generate skill');
    } finally {
      setGenerating(false);
    }
  };

  const save = async () => {
    if (draftMarkdown.length > 20000) {
      toast.error('Skill markdown exceeds max length (20000)');
      return;
    }
    setSaving(true);
    try {
      if (isCreating) {
        const res = await authFetch('/api/user/ai-host-styles', {
          method: 'POST',
          body: JSON.stringify({
            name: draftName,
            skill_markdown: draftMarkdown,
            is_active: draftActive,
            set_default: false,
          }),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body?.detail || 'Failed to create style');
        }
        await Analytics.trackSettingsChanged('ai_host_style_created', draftName || 'custom');
        toast.success('Custom AI Participant style created');
      } else if (selectedStyle && selectedStyle.source === 'user') {
        const res = await authFetch(`/api/user/ai-host-styles/${selectedStyle.id}`, {
          method: 'PUT',
          body: JSON.stringify({
            name: draftName,
            skill_markdown: draftMarkdown,
            is_active: draftActive,
          }),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body?.detail || 'Failed to update style');
        }
        await Analytics.trackSettingsChanged('ai_host_style_updated', selectedStyle.id);
        toast.success('Custom AI Participant style updated');
      }
      await loadStyles();
    } catch (error) {
      console.error(error);
      toast.error(error instanceof Error ? error.message : 'Failed to save style');
    } finally {
      setSaving(false);
    }
  };

  const setDefault = async () => {
    const styleId = isCreating ? '' : (selectedStyle?.id || '');
    if (!styleId) {
      toast.error('Please select a style first');
      return;
    }
    setSaving(true);
    try {
      const res = await authFetch('/api/user/ai-host-styles/default', {
        method: 'POST',
        body: JSON.stringify({ style_id: styleId }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || 'Failed to set default style');
      }
      await Analytics.trackSettingsChanged('ai_host_default_style', styleId);
      toast.success('Default AI Participant style updated');
      await loadStyles();
    } catch (error) {
      console.error(error);
      toast.error(error instanceof Error ? error.message : 'Failed to set default style');
    } finally {
      setSaving(false);
    }
  };

  const deleteSelected = async () => {
    if (!selectedStyle || selectedStyle.source !== 'user') return;
    setSaving(true);
    try {
      const res = await authFetch(`/api/user/ai-host-styles/${selectedStyle.id}`, {
        method: 'DELETE',
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || 'Failed to delete style');
      }
      await Analytics.trackSettingsChanged('ai_host_style_deleted', selectedStyle.id);
      toast.success('Custom AI Participant style deleted');
      await loadStyles();
    } catch (error) {
      console.error(error);
      toast.error(error instanceof Error ? error.message : 'Failed to delete style');
    } finally {
      setSaving(false);
    }
  };

  const readOnly = !isCreating && Boolean(selectedStyle?.read_only);

  if (loading) {
    return <div className="text-sm text-gray-600">Loading AI Participant styles...</div>;
  }

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm space-y-4">
        <div>
          <h3 className="text-lg font-semibold text-gray-900">AI Participant Style Library</h3>
          <p className="text-sm text-gray-600 mt-1">
            System styles are read-only. Create custom styles for your team and set one as default for quick start.
          </p>
        </div>

        <label className="inline-flex items-center gap-2 text-sm text-gray-700">
          <input
            type="checkbox"
            checked={askBeforeMeeting}
            onChange={(e) => setAskBeforeMeeting(e.target.checked)}
            className="h-4 w-4 rounded border-gray-300"
          />
          Ask style before every meeting start
        </label>

        <div className="flex flex-wrap items-center gap-2">
          <select
            value={isCreating ? '__creating__' : selectedStyleId}
            onChange={(e) => {
              const next = e.target.value;
              if (next === '__creating__') {
                startCreate();
                return;
              }
              setIsCreating(false);
              setSelectedStyleId(next);
            }}
            className="rounded border border-gray-300 px-3 py-2 text-sm"
          >
            {styles.map((style) => (
              <option key={style.id} value={style.id}>
                {style.is_default ? '★ ' : ''}{style.name} ({style.source})
              </option>
            ))}
            <option value="__creating__">+ New Custom Style</option>
          </select>

          <button
            type="button"
            onClick={setDefault}
            disabled={saving || isCreating}
            className="rounded border border-blue-300 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100 disabled:opacity-50"
          >
            Set As Default
          </button>

          {selectedStyle?.source === 'user' && !isCreating && (
            <button
              type="button"
              onClick={deleteSelected}
              disabled={saving}
              className="rounded border border-red-300 bg-red-50 px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-100 disabled:opacity-50"
            >
              Delete Style
            </button>
          )}
        </div>

        <div className="text-xs text-gray-500">
          Current default: <span className="font-medium">{defaultStyleId}</span>
        </div>

        {isCreating && creationStep === 'describe' ? (
          <div className="space-y-3 rounded-lg border border-purple-200 bg-purple-50/40 p-4">
            <div>
              <h4 className="text-sm font-semibold text-purple-900">
                Describe how you want your AI to behave
              </h4>
              <p className="mt-1 text-xs text-purple-800/80">
                Write it like you'd brief a teammate. We'll turn it into a
                style document you can review and edit before saving.
              </p>
            </div>

            <div className="space-y-2">
              <label className="text-xs font-medium text-gray-600">
                Style name <span className="text-gray-400">(optional)</span>
              </label>
              <input
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                placeholder="e.g. Quiet Note-Taker"
                className="w-full rounded border border-gray-300 px-3 py-2 text-sm"
              />
            </div>

            <div className="space-y-2">
              <label className="text-xs font-medium text-gray-600">
                Tell us what you want
              </label>
              <textarea
                value={describePrompt}
                onChange={(e) => setDescribePrompt(e.target.value)}
                placeholder={
                  'Examples:\n• "Be quiet during the meeting and only flag decisions and risks."\n• "Catch when we go off-agenda and remind us politely."\n• "Track every action item with an owner and due date."'
                }
                className="w-full min-h-[160px] rounded-md border border-gray-300 p-3 text-sm focus:border-purple-500 focus:outline-none"
                disabled={generating}
              />
              <div className="text-[11px] text-gray-500">
                {describePrompt.length} / 4000 characters
              </div>
            </div>

            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={cancelCreate}
                disabled={generating}
                className="rounded border border-gray-300 bg-gray-50 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={generateFromPrompt}
                disabled={generating || !describePrompt.trim()}
                className="rounded bg-purple-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-purple-700 disabled:opacity-50"
              >
                {generating ? 'Generating…' : 'Generate Style'}
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="space-y-2">
              <label className="text-xs font-medium text-gray-600">Style name</label>
              <input
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                disabled={readOnly}
                className="w-full rounded border border-gray-300 px-3 py-2 text-sm disabled:bg-gray-100 disabled:text-gray-500"
              />
            </div>

            <label className="inline-flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={draftActive}
                onChange={(e) => setDraftActive(e.target.checked)}
                disabled={readOnly}
                className="h-4 w-4 rounded border-gray-300"
              />
              Active style
            </label>

            {isCreating && (
              <div className="rounded-md border border-emerald-200 bg-emerald-50/60 px-3 py-2 text-xs text-emerald-900">
                Generated from your description. Edit any line below before saving.
              </div>
            )}

            <textarea
              value={draftMarkdown}
              onChange={(e) => setDraftMarkdown(e.target.value)}
              disabled={readOnly}
              className="w-full min-h-[320px] rounded-md border border-gray-300 p-3 text-sm font-mono focus:border-gray-500 focus:outline-none disabled:bg-gray-100 disabled:text-gray-500"
            />

            <div className="flex items-center justify-between">
              <div className="text-xs text-gray-500">
                {draftMarkdown.split('\n').length} lines · {draftMarkdown.length} chars · Max 20000 chars
              </div>
              <div className="flex items-center gap-2">
                {isCreating && (
                  <>
                    <button
                      type="button"
                      onClick={() => setCreationStep('describe')}
                      className="rounded border border-purple-300 bg-purple-50 px-3 py-1.5 text-xs font-medium text-purple-700 hover:bg-purple-100"
                    >
                      ← Re-describe
                    </button>
                    <button
                      type="button"
                      onClick={cancelCreate}
                      className="rounded border border-gray-300 bg-gray-50 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-100"
                    >
                      Cancel
                    </button>
                  </>
                )}
                <button
                  type="button"
                  onClick={save}
                  disabled={saving || readOnly || draftMarkdown.length > 20000 || !draftName.trim()}
                  className="rounded bg-gray-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-gray-800 disabled:opacity-50"
                >
                  {saving ? 'Saving...' : isCreating ? 'Create Style' : 'Save Changes'}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
