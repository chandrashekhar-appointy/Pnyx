import { useState, useEffect, useCallback, useRef } from 'react';

import { toast } from 'sonner';
import Analytics from '@/lib/analytics';

export function useTemplates() {
  const [availableTemplates, setAvailableTemplates] = useState<Array<{
    id: string;
    name: string;
    description: string;
  }>>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<string>('standard_meeting');
  const [templateChanged, setTemplateChanged] = useState<boolean>(false);
  const previousTemplateRef = useRef<string | null>(null);

  // Fetch available templates on mount
  useEffect(() => {
    // Mock templates for web version
    const templates = [
      { id: 'standard_meeting', name: 'Standard Meeting', description: 'Standard meeting summary with key points and action items' },
      { id: 'smart_notes', name: 'Freestyle', description: 'No fixed structure. Lets the AI shape the notes directly from the transcript.' },
      { id: 'daily_standup', name: 'Daily Standup', description: 'Concise update on progress, plans, and blockers' },
      { id: 'interview', name: 'Interview', description: 'Candidate assessment and key discussion points' },
      { id: 'brainstorming', name: 'Brainstorming', description: 'Capture ideas, suggestions, and creative concepts' },
      { id: 'standup', name: 'Stand Up', description: 'Quick standup format with updates, blockers, and actions' }
    ];
    setAvailableTemplates(templates);
  }, []);

  // Handle template selection - triggers regeneration
  const handleTemplateSelection = useCallback((templateId: string, templateName: string) => {
    // Only trigger change if actually different
    if (templateId !== selectedTemplate) {
      const previousTemplate = selectedTemplate;
      previousTemplateRef.current = previousTemplate;
      setSelectedTemplate(templateId);
      setTemplateChanged(true);
      // Save template preference to localStorage for future meetings
      localStorage.setItem('selectedTemplate', templateId);
      toast.info('Regenerating notes with new template...', {
        description: `Using "${templateName}" template`,
      });
      Analytics.trackFeatureUsed('template_selected');
      Analytics.trackNotesTemplateSwitched(previousTemplate, templateId, {
        new_template_name: templateName,
      });
    }
  }, [selectedTemplate]);

  // Reset template changed flag after it's been consumed
  const acknowledgeTemplateChange = useCallback(() => {
    setTemplateChanged(false);
  }, []);

  // Load template preference from localStorage on mount
  useEffect(() => {
    const savedTemplate = localStorage.getItem('selectedTemplate');
    if (savedTemplate) {
      setSelectedTemplate(savedTemplate);
    } else {
      setSelectedTemplate('standard_meeting');
    }
  }, []);

  return {
    availableTemplates,
    selectedTemplate,
    handleTemplateSelection,
    templateChanged,
    acknowledgeTemplateChange,
  };
}
