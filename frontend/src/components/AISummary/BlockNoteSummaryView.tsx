"use client";

import { useState, useEffect, useCallback, useRef, forwardRef, useImperativeHandle } from 'react';
import dynamic from 'next/dynamic';
import { Summary, SummaryDataResponse, SummaryFormat, BlockNoteBlock } from '@/types';
import { AISummary } from './index';
import { Block } from '@blocknote/core';
import { useCreateBlockNote } from '@blocknote/react';
import { BlockNoteView } from '@blocknote/shadcn';
import "@blocknote/shadcn/style.css";

// Dynamically import BlockNote Editor to avoid SSR issues
const Editor = dynamic(() => import('../BlockNoteEditor/Editor'), { ssr: false });

interface BlockNoteSummaryViewProps {
  summaryData: SummaryDataResponse | Summary | null;
  onSave?: (data: { markdown?: string; summary_json?: BlockNoteBlock[] }) => void;
  onSummaryChange?: (summary: Summary) => void;
  status?: 'idle' | 'processing' | 'summarizing' | 'regenerating' | 'completed' | 'error';
  error?: string | null;
  onRegenerateSummary?: () => void;
  meeting?: {
    id: string;
    title: string;
    created_at: string;
  };
  onDirtyChange?: (isDirty: boolean) => void;
}

export interface BlockNoteSummaryViewRef {
  saveSummary: () => Promise<void>;
  getMarkdown: () => Promise<string>;
  isDirty: boolean;
}

// Format detection helper
function detectSummaryFormat(data: any): { format: SummaryFormat; data: any } {
  if (!data) {
    return { format: 'legacy', data: null };
  }

  // Priority 1: BlockNote format (has summary_json)
  if (data.summary_json && Array.isArray(data.summary_json)) {
    console.log('✅ FORMAT: BLOCKNOTE (summary_json exists)');
    return { format: 'blocknote', data };
  }

  // Priority 2: Markdown format
  const markdownContent = data.markdown || data.meeting_summary;
  if (markdownContent && typeof markdownContent === 'string') {
    console.log('✅ FORMAT: MARKDOWN (will parse to BlockNote)');
    // Normalize to include markdown field for the rest of the component
    return { 
      format: 'markdown', 
      data: { ...data, markdown: markdownContent } 
    };
  }

  // Priority 3: Legacy JSON
  const hasLegacyStructure = data.MeetingName ||
    data.MeetingNotes ||
    Object.keys(data).some(key =>
      typeof data[key] === 'object' && data[key]?.title && (data[key]?.blocks || data[key]?.sections)
    );

  if (hasLegacyStructure) {
    console.log('✅ FORMAT: LEGACY (custom JSON or MeetingNotes)');
    return { format: 'legacy', data };
  }

  return { format: 'legacy', data: null };
}

function legacySummaryToMarkdown(data: any): string {
  if (!data || typeof data !== 'object') return '';

  const lines: string[] = [];
  const meetingName = data.MeetingName || data.meeting_name || data.title;
  if (typeof meetingName === 'string' && meetingName.trim()) {
    lines.push(`# ${meetingName.trim()}`, '');
  }

  const appendBlock = (block: any) => {
    if (!block) return;
    if (typeof block === 'string') {
      if (block.trim()) lines.push(`- ${block.trim()}`);
      return;
    }
    const text =
      block.text ||
      block.content ||
      block.description ||
      block.title ||
      block.value;
    if (typeof text === 'string' && text.trim()) {
      lines.push(`- ${text.trim()}`);
    }
  };

  const appendSection = (title: string, section: any) => {
    const sectionTitle = section?.title || title;
    if (sectionTitle) lines.push(`## ${String(sectionTitle).trim()}`);

    const blocks = Array.isArray(section?.blocks)
      ? section.blocks
      : Array.isArray(section?.sections)
        ? section.sections
        : Array.isArray(section)
          ? section
          : [];

    blocks.forEach(appendBlock);
    lines.push('');
  };

  const meetingNotes = data.MeetingNotes;
  if (meetingNotes?.sections && Array.isArray(meetingNotes.sections)) {
    meetingNotes.sections.forEach((section: any, index: number) =>
      appendSection(section?.title || `Section ${index + 1}`, section)
    );
  } else {
    Object.entries(data).forEach(([key, section]) => {
      if (key === 'MeetingName' || key === 'meeting_name' || key === 'title') return;
      if (section && typeof section === 'object') appendSection(key, section);
    });
  }

  return lines.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

export const BlockNoteSummaryView = forwardRef<BlockNoteSummaryViewRef, BlockNoteSummaryViewProps>(({
  summaryData,
  onSave,
  onSummaryChange,
  status = 'idle',
  error = null,
  onRegenerateSummary,
  meeting,
  onDirtyChange
}, ref) => {
  const { format, data } = detectSummaryFormat(summaryData);
  const [isDirty, setIsDirty] = useState(false);
  const [currentBlocks, setCurrentBlocks] = useState<Block[]>([]);
  const [isSaving, setIsSaving] = useState(false);
  const isContentLoaded = useRef(false);

  // Create BlockNote editor for markdown parsing
  const editor = useCreateBlockNote({
    initialContent: undefined
  });

  // Parse markdown to blocks when format is markdown
  useEffect(() => {
    if (format === 'markdown' && data?.markdown && editor) {
      const loadMarkdown = async () => {
        try {
          const markdownText = typeof data.markdown === 'string' ? data.markdown : String(data.markdown ?? '');
          console.log('📝 BLOCKNOTE VIEW: Parsing markdown to BlockNote blocks...', {
            length: markdownText.length,
            preview: markdownText.substring(0, 100) + '...'
          });
          const blocks = await editor.tryParseMarkdownToBlocks(markdownText);
          console.log('📝 BLOCKNOTE VIEW: Generated blocks count:', blocks.length);
          if (Array.isArray(blocks) && blocks.length > 0) {
            editor.replaceBlocks(editor.document, blocks);
          }
          console.log('✅ BLOCKNOTE VIEW: Markdown parsed and applied successfully');

          // Delay to ensure editor has finished rendering before allowing onChange
          setTimeout(() => {
            isContentLoaded.current = true;
          }, 100);
        } catch (err) {
          console.error('❌ BLOCKNOTE VIEW: Failed to parse markdown:', err);
        }
      };
      loadMarkdown();
    }
  }, [format, data?.markdown, editor]);

  // Set content loaded flag for blocknote format
  useEffect(() => {
    if (format === 'blocknote' && data?.summary_json) {
      // Delay to ensure editor has finished rendering
      setTimeout(() => {
        isContentLoaded.current = true;
      }, 100);
    }
  }, [format, data?.summary_json]);

  const handleEditorChange = useCallback((blocks: Block[]) => {
    // Only set dirty flag if content has finished loading
    if (isContentLoaded.current) {
      setCurrentBlocks(blocks);
      setIsDirty(true);
    }
  }, []);

  // Notify parent of dirty state changes
  useEffect(() => {
    if (onDirtyChange) {
      onDirtyChange(isDirty);
    }
  }, [isDirty, onDirtyChange]);

  const handleSave = useCallback(async () => {
    if (!onSave || !isDirty) return;

    setIsSaving(true);
    try {
      console.log('💾 Saving BlockNote content...');

      // Generate markdown from current blocks
      const markdown = await editor.blocksToMarkdownLossy(currentBlocks);

      onSave({
        markdown: markdown,
        summary_json: currentBlocks as unknown as BlockNoteBlock[]
      });

      setIsDirty(false);
      console.log('✅ Save successful');
    } catch (err) {
      console.error('❌ Save failed:', err);
      alert('Failed to save changes. Please try again.');
    } finally {
      setIsSaving(false);
    }
  }, [onSave, isDirty, currentBlocks, editor]);

  // Expose methods to parent via ref
  useImperativeHandle(ref, () => ({
    saveSummary: handleSave,
    getMarkdown: async () => {
      try {
        console.log('🔍 getMarkdown called, format:', format);
        console.log('🔍 currentBlocks length:', currentBlocks.length);
        console.log('🔍 data:', data);

        // For markdown format - use the main editor
        if (format === 'markdown' && editor) {
          console.log('📝 Using markdown editor, blocks:', editor.document.length);
          const markdown = await editor.blocksToMarkdownLossy(editor.document);
          console.log('📝 Generated markdown length:', markdown.length);
          return markdown;
        }

        // For blocknote format - use currentBlocks state
        if (format === 'blocknote') {
          console.log('📝 BlockNote format, currentBlocks:', currentBlocks.length);
          if (currentBlocks.length > 0 && editor) {
            const markdown = await editor.blocksToMarkdownLossy(currentBlocks);
            console.log('📝 Generated markdown from blocks, length:', markdown.length);
            return markdown;
          }
          // Fallback: if we have the original data with markdown
          if (data?.markdown) {
            console.log('📝 Using fallback markdown from data');
            return data.markdown;
          }
        }

        // For legacy format, convert the structured notes so Refine has real content.
        if (format === 'legacy') {
          const markdown = legacySummaryToMarkdown(summaryData);
          console.log('📝 Converted legacy summary to markdown, length:', markdown.length);
          return markdown;
        }

        console.warn('⚠️ Cannot generate markdown, returning empty');
        return '';
      } catch (err) {
        console.error('❌ Failed to generate markdown:', err);
        return '';
      }
    },
    isDirty
  }), [handleSave, isDirty, editor, format, currentBlocks, data, summaryData]);

  // Render legacy format
  if (format === 'legacy') {
    console.log('🎨 Rendering LEGACY format');
    return (
      <AISummary
        summary={summaryData as Summary}
        status={status}
        error={error}
        onSummaryChange={onSummaryChange || (() => { })}
        onRegenerateSummary={onRegenerateSummary || (() => { })}
        meeting={meeting}
      />
    );
  }

  // Render BlockNote format (has summary_json)
  if (format === 'blocknote') {
    console.log('🎨 Rendering BLOCKNOTE format (direct)');
    return (
      <div className="flex flex-col w-full">
        <div className="w-full ai-summary-content">
          <Editor
            initialContent={data.summary_json}
            onChange={(blocks) => {
              console.log('📝 Editor blocks changed:', blocks.length);
              handleEditorChange(blocks);
            }}
            editable={true}
          />
        </div>
      </div>
    );
  }

  // Render Markdown format (parse and display in BlockNote)
  if (format === 'markdown') {
    console.log('🎨 Rendering MARKDOWN format (parsed to BlockNote)');
    return (
      <div className="flex flex-col w-full">
        <div className="w-full ai-summary-content">
          <BlockNoteView
            editor={editor}
            editable={true}
            onChange={() => {
              if (isContentLoaded.current) {
                handleEditorChange(editor.document);
              }
            }}
            theme="light"
          />
        </div>
      </div>
    );
  }

  return null;
});

BlockNoteSummaryView.displayName = 'BlockNoteSummaryView';
