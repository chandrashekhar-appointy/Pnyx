'use client';

import { useEffect, useMemo, useState } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { authFetch } from '@/lib/api';
import { toast } from 'sonner';

const TEMPLATE_SKILLS: Record<string, string> = {
  meeting_assistant: `# Meeting Assistant

## Who You Are
You are a helpful meeting assistant who quietly observes and surfaces the most important moments so nothing gets lost. You are neutral, evidence-based, and never take sides. You speak only when you have something genuinely useful to contribute.

## When to Speak
- When participants agree on something — capture the decision
- When a question stays unresolved for several minutes
- When someone takes on a task or action item
- When the discussion drifts significantly from the meeting goal
- When an important point might get lost in the conversation

## When to Stay Silent
- When participants are actively debating — let them finish their thought
- During casual conversation or small talk
- When the meeting just started and people are settling in
- When someone is expressing frustration — they need space, not analysis

## How to Sound
- Neutral and concise — one sentence per insight when possible
- State facts and observations, never opinions
- Use "This was noted:" or "Participants agreed:" not "You should"
- Be professional but not robotic

## What to Track
- ✅ Decision: Explicit agreements, commitments, or choices made by participants
- ❓ Open Question: Issues raised but left unresolved that need follow-up
- 📋 Action Item: Specific tasks assigned to or accepted by participants
- 💡 Key Insight: Important observations, risks, or context worth preserving

## What to Ignore
- Side conversations unrelated to the meeting topic
- Routine procedural talk (muting, screen sharing, etc.)
- Repetitions of already-captured decisions or action items`,
  
  product_manager: `# Product Manager Assistant

## Who You Are
You are a highly analytical Product Manager assistant observing a user research or product sync meeting. Your goal is to extract user pain points, feature requests, and engineering commitments.

## When to Speak
- When a user explicitly mentions a problem, bug, or pain point
- When someone suggests a new feature or improvement
- When engineering gives a timeline or commitment to build something

## When to Stay Silent
- During general small talk or introductions
- When engineers are debating deeply technical implementation details
- When participants are discussing unrelated internal team logistics

## How to Sound
- Empathic to user pain points, but analytical and objective
- Extremely concise, formatting everything as actionable tickets
- State facts ("User reported 404 error") not opinions ("The app is bad")

## What to Track
- 🚨 Pain Point: A friction point, bug, or frustration expressed by a user
- 💡 Feature Request: A specific ask or capability the user wants
- ⏳ Engineering Commitment: A promise made by the team regarding a timeline or fix
- ✅ Decision: An explicit choice made on how to move forward

## What to Ignore
- Casual pleasantries or weather talk
- Live debugging steps that aren't finalized solutions`,

  scrum_master: `# Scrum Master

## Who You Are
You are a diligent Scrum Master observing a standup or agile ceremony. Your core focus is uncovering blockers, tracking sprint commitments, and ensuring accountability.

## When to Speak
- When a participant mentions they are stuck, blocked, or need help
- When a deadline is shifted or at risk
- When a new dependency is discovered between team members

## When to Stay Silent
- When participants are giving routine "what I did yesterday" updates that have no issues
- During technical deep-dives (encourage taking it offline instead)

## How to Sound
- Decisive, action-oriented, and focused on unblocking
- Highlight risks clearly

## What to Track
- 🛑 Blocker: Anything preventing a team member from completing their work
- ⚠️ Timeline Risk: Mention of a deadline slipping or scope increasing
- 🤝 Dependency: When one person needs something from another to proceed
- 📋 Action Item: Specific tasks assigned to a person with a deadline

## What to Ignore
- Routine status updates where everything is on track
- Discussions about code formatting or syntax`
};

interface MeetingSkillResponse {
  meeting_id: string;
  skill_markdown: string;
  is_active: boolean;
  source: string;
}

interface MeetingAIHostSkillDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  meetingId: string;
}

export function MeetingAIHostSkillDialog({ open, onOpenChange, meetingId }: MeetingAIHostSkillDialogProps) {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [isActive, setIsActive] = useState(true);
  const [skillMarkdown, setSkillMarkdown] = useState('');

  const lineCount = useMemo(() => skillMarkdown.split('\n').length, [skillMarkdown]);

  const loadMeetingProfile = async () => {
    if (!meetingId) return;
    setLoading(true);
    try {
      const res = await authFetch(`/meeting-ai-host-skill/${meetingId}`, { method: 'GET' });
      if (!res.ok) {
        if (res.status === 403) {
          toast.error('You do not have permission to view meeting AI Participant skill');
          return;
        }
        throw new Error('Failed to load meeting AI Participant skill');
      }
      const data = (await res.json()) as MeetingSkillResponse;
      setSkillMarkdown(data.skill_markdown || '');
      setIsActive(Boolean(data.is_active));
    } catch (error) {
      console.error(error);
      toast.error('Failed to load meeting AI Participant skill');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!open) return;
    void loadMeetingProfile();
  }, [open, meetingId]);

  const applyTemplate = (name: keyof typeof TEMPLATE_SKILLS) => {
    setSkillMarkdown(TEMPLATE_SKILLS[name]);
  };

  const saveMeetingProfile = async () => {
    if (!meetingId) return;
    setSaving(true);
    try {
      const res = await authFetch('/meeting-ai-host-skill', {
        method: 'POST',
        body: JSON.stringify({
          meeting_id: meetingId,
          skill_markdown: skillMarkdown,
          is_active: isActive,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || 'Failed to save meeting AI Participant skill');
      }
      toast.success('Meeting AI Participant skill saved');
    } catch (error) {
      console.error(error);
      toast.error(error instanceof Error ? error.message : 'Failed to save meeting AI Participant skill');
    } finally {
      setSaving(false);
    }
  };

  const deleteMeetingProfile = async () => {
    if (!meetingId) return;
    setDeleting(true);
    try {
      const res = await authFetch(`/meeting-ai-host-skill/${meetingId}`, { method: 'DELETE' });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || 'Failed to delete meeting AI Participant skill');
      }
      setSkillMarkdown('');
      setIsActive(true);
      toast.success('Meeting AI Participant skill deleted');
    } catch (error) {
      console.error(error);
      toast.error(error instanceof Error ? error.message : 'Failed to delete meeting AI Participant skill');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Meeting AI Participant Skill</DialogTitle>
          <DialogDescription>
            This profile applies only to this meeting and overrides your default AI Participant style for this meeting.
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="text-sm text-gray-600">Loading...</div>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => applyTemplate('meeting_assistant')}
                className="rounded border border-gray-300 bg-gray-50 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-100"
              >
                Meeting Assistant
              </button>
              <button
                type="button"
                onClick={() => applyTemplate('product_manager')}
                className="rounded border border-gray-300 bg-gray-50 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-100"
              >
                Product Manager
              </button>
              <button
                type="button"
                onClick={() => applyTemplate('scrum_master')}
                className="rounded border border-gray-300 bg-gray-50 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-100"
              >
                Scrum Master
              </button>
            </div>

            <label className="inline-flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={isActive}
                onChange={(e) => setIsActive(e.target.checked)}
                className="h-4 w-4 rounded border-gray-300"
              />
              Active for this meeting
            </label>

            <textarea
              value={skillMarkdown}
              onChange={(e) => setSkillMarkdown(e.target.value)}
              placeholder={'---\nname: "Custom Participant"\ndescription: "..."\n---\n\n# Role\n...\n'}
              className="w-full min-h-[280px] rounded-md border border-gray-300 p-3 text-sm font-mono focus:border-gray-500 focus:outline-none"
            />

            <div className="flex items-center justify-between">
              <div className="text-xs text-gray-500">
                {lineCount} lines · {skillMarkdown.length} chars · Max 20000 chars
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={deleteMeetingProfile}
                  disabled={deleting || saving}
                  className="rounded border border-red-300 bg-red-50 px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-100 disabled:opacity-50"
                >
                  {deleting ? 'Deleting...' : 'Delete'}
                </button>
                <button
                  type="button"
                  onClick={saveMeetingProfile}
                  disabled={saving || deleting || skillMarkdown.length > 20000}
                  className="rounded bg-gray-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-gray-800 disabled:opacity-50"
                >
                  {saving ? 'Saving...' : 'Save'}
                </button>
              </div>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
