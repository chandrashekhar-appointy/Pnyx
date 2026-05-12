import React, { useState, useRef, useEffect } from 'react';
import { Send, Bot, User, Loader2, X, Check, ArrowRight } from 'lucide-react';
import DOMPurify from 'isomorphic-dompurify';
import { authFetch } from '@/lib/api';
import { Button } from '@/components/ui/button';
import Analytics from '@/lib/analytics';

const SAFE_INLINE_TAGS = ['strong', 'em', 'code', 'a', 'br'];
const SAFE_INLINE_ATTRS = ['class', 'href', 'target', 'rel'];

function sanitizeInline(html: string): string {
    if (typeof window === 'undefined') return html; // Skip during SSR to avoid jsdom build errors
    return DOMPurify.sanitize(html, {
        ALLOWED_TAGS: SAFE_INLINE_TAGS,
        ALLOWED_ATTR: SAFE_INLINE_ATTRS,
        ALLOW_DATA_ATTR: false,
    });
}

function escapeHtml(s: string): string {
    return s
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

interface Message {
    role: 'user' | 'assistant';
    content: string;            // Chat-display text (for user messages, errors, "thinking" placeholder)
    isRefinement?: boolean;     // If true, this message represents a refined version
    changes?: string[];         // Changelog bullets to show in chat (changes-only view)
    updatedDocument?: string;   // Full refined notes — used by Apply button, not shown in chat
}

interface RefineNotesSidebarProps {
    meetingId: string;
    onClose: () => void;
    currentNotes: string;
    onApplyRefinement: (newNotes: string) => void;
}

// Simple markdown renderer component
function MarkdownContent({ content }: { content: string }) {
    const renderMarkdown = (text: string) => {
        const lines = text.split('\n');
        const elements: React.ReactNode[] = [];
        let listItems: string[] = [];
        let listType: 'ul' | 'ol' | null = null;

        const flushList = () => {
            if (listItems.length > 0 && listType) {
                const ListTag = listType;
                elements.push(
                    <ListTag key={elements.length} className={listType === 'ul' ? 'list-disc ml-4 my-2 space-y-1' : 'list-decimal ml-4 my-2 space-y-1'}>
                        {listItems.map((item, i) => <li key={i} className="text-sm">{renderInline(item)}</li>)}
                    </ListTag>
                );
                listItems = [];
                listType = null;
            }
        };

        const renderInline = (line: string): React.ReactNode => {
            // Escape any raw HTML in the source first so AI/user-provided <script> etc. cannot execute.
            let safe = escapeHtml(line);
            // Re-introduce a small allowlist of inline markdown formatting.
            safe = safe.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
            safe = safe.replace(/__(.+?)__/g, '<strong>$1</strong>');
            safe = safe.replace(/\*([^*]+)\*/g, '<em>$1</em>');
            safe = safe.replace(/`([^`]+)`/g, '<code class="bg-zinc-200 dark:bg-zinc-700 px-1 rounded text-xs">$1</code>');
            safe = safe.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_m, text, url) => {
                // Block javascript:/data: URLs — only http(s)/mailto/relative.
                const trimmed = url.trim();
                const ok = /^(https?:|mailto:|\/|#)/i.test(trimmed);
                const href = ok ? trimmed : '#';
                return `<a href="${href}" target="_blank" rel="noopener noreferrer" class="text-blue-500 underline">${text}</a>`;
            });
            // Final defense in depth — DOMPurify strips anything still dangerous.
            return <span dangerouslySetInnerHTML={{ __html: sanitizeInline(safe) }} />;
        };

        lines.forEach((line, index) => {
            // Headers
            if (line.startsWith('### ')) {
                flushList();
                elements.push(<h3 key={index} className="font-bold text-base mt-3 mb-1 text-zinc-900 dark:text-zinc-100">{renderInline(line.slice(4))}</h3>);
            } else if (line.startsWith('## ')) {
                flushList();
                elements.push(<h2 key={index} className="font-bold text-lg mt-4 mb-2 text-zinc-900 dark:text-zinc-100">{renderInline(line.slice(3))}</h2>);
            } else if (line.startsWith('# ')) {
                flushList();
                elements.push(<h1 key={index} className="font-bold text-xl mt-4 mb-2 text-zinc-900 dark:text-zinc-100">{renderInline(line.slice(2))}</h1>);
            }
            // Bullet lists
            else if (line.match(/^[\*\-]\s+/)) {
                if (listType !== 'ul') flushList();
                listType = 'ul';
                listItems.push(line.replace(/^[\*\-]\s+/, ''));
            }
            // Numbered lists
            else if (line.match(/^\d+\.\s+/)) {
                if (listType !== 'ol') flushList();
                listType = 'ol';
                listItems.push(line.replace(/^\d+\.\s+/, ''));
            }
            // Horizontal rule
            else if (line.match(/^---+$/)) {
                flushList();
                elements.push(<hr key={index} className="my-3 border-zinc-300 dark:border-zinc-700" />);
            }
            // Regular paragraph
            else if (line.trim()) {
                flushList();
                elements.push(<p key={index} className="text-sm my-1">{renderInline(line)}</p>);
            }
            // Empty line
            else {
                flushList();
                elements.push(<div key={index} className="h-2" />);
            }
        });

        flushList();
        return elements;
    };

    return <div className="markdown-content">{renderMarkdown(content)}</div>;
}

export function RefineNotesSidebar({ meetingId, onClose, currentNotes, onApplyRefinement }: RefineNotesSidebarProps) {
    const [messages, setMessages] = useState<Message[]>([
        {
            role: 'assistant',
            content:
                'Hi! I can refine these notes two ways:\n\n' +
                '**Targeted edits** — say what to change and which section:\n' +
                '- "Add a bullet about timeline to Next Steps"\n' +
                '- "Fix typos in Decisions"\n' +
                '- "Make the Action Items section more concise"\n\n' +
                '**Full regenerate** — start the instruction with **rewrite**, **regenerate**, or **focus on**:\n' +
                '- "Regenerate focusing on technical decisions"\n' +
                '- "Rewrite the whole notes from scratch as bullet points"',
        },
    ]);
    const [input, setInput] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const messagesEndRef = useRef<HTMLDivElement>(null);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages]);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        console.log('[RefineNotes] handleSubmit fired', {
            inputLength: input.trim().length,
            isLoading,
            currentNotesLength: currentNotes?.length ?? 0,
            meetingId,
        });
        if (!input.trim() || isLoading) {
            console.warn('[RefineNotes] Aborting: empty input or already loading');
            return;
        }

        const userMessage = input.trim();
        setInput('');
        setMessages(prev => [...prev, { role: 'user', content: userMessage }]);
        setIsLoading(true);

        Analytics.trackNotesRefined(userMessage.length, {
            meeting_id: meetingId,
            current_notes_length: currentNotes.length,
        }).catch(err => console.warn('[RefineNotes] Analytics failed (ignored):', err));

        try {
            setMessages(prev => [...prev, { role: 'assistant', content: '', isRefinement: true }]);

            console.log('[RefineNotes] Calling /refine-notes …');
            const response = await authFetch('/refine-notes', {
                method: 'POST',
                body: JSON.stringify({
                    meeting_id: meetingId,
                    current_notes: currentNotes,
                    user_instruction: userMessage,
                    model: 'gemini',
                    model_name: 'gemini-2.5-flash',
                }),
            });
            console.log('[RefineNotes] /refine-notes responded', response.status);

            if (!response.ok) {
                let detail = `Request failed: ${response.status}`;
                try {
                    const body = await response.json();
                    if (body && typeof body.detail === 'string') detail = body.detail;
                } catch {
                    try { detail = await response.text(); } catch { /* keep default */ }
                }
                throw new Error(detail);
            }

            const data = await response.json();
            const changes: string[] = Array.isArray(data.changes) ? data.changes : [];
            const updatedDocument: string = typeof data.updated_document === 'string' ? data.updated_document : '';

            console.log('[RefineNotes] Parsed response', {
                changesCount: changes.length,
                updatedDocumentLength: updatedDocument.length,
            });

            if (!updatedDocument.trim()) {
                throw new Error('Refine returned an empty document. Please rephrase your request.');
            }

            setMessages(prev => {
                const newMessages = [...prev];
                const last = newMessages[newMessages.length - 1];
                if (last && last.role === 'assistant' && last.isRefinement) {
                    last.changes = changes.length > 0 ? changes : ['Updated your notes.'];
                    last.updatedDocument = updatedDocument;
                    last.content = ''; // changelog is rendered from `changes`, not `content`
                }
                return newMessages;
            });
        } catch (error) {
            console.error('[RefineNotes] Refinement error:', error);
            const errorText = error instanceof Error ? error.message : String(error);
            setMessages(prev => {
                const newMessages = [...prev];
                const lastMessage = newMessages[newMessages.length - 1];
                if (lastMessage && lastMessage.role === 'assistant' && !lastMessage.changes && !lastMessage.content) {
                    newMessages.pop();
                }
                return [
                    ...newMessages,
                    {
                        role: 'assistant',
                        content: `Sorry, I couldn't refine the notes.\n\n**Error:** ${errorText}`,
                    },
                ];
            });
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <div className="flex flex-col h-full bg-white border-l border-zinc-200 shadow-xl fixed right-0 top-0 bottom-0 z-50 w-[400px]">
            {/* Header */}
            <div className="flex items-center justify-between p-4 border-b border-zinc-200 bg-gradient-to-r from-purple-50 to-blue-50">
                <h3 className="font-semibold text-zinc-900 flex items-center gap-2">
                    <Bot className="w-5 h-5 text-purple-600" />
                    Refine Notes
                </h3>
                <button
                    onClick={onClose}
                    className="p-1 hover:bg-zinc-200 rounded-full transition-colors"
                >
                    <X className="w-5 h-5 text-zinc-500" />
                </button>
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4 bg-zinc-50">
                {messages.map((msg, idx) => (
                    <div
                        key={idx}
                        className={`flex flex-col gap-2 ${msg.role === 'user' ? 'items-end' : 'items-start'}`}
                    >
                        <div className={`
                            rounded-lg p-3 max-w-[90%] shadow-sm
                            ${msg.role === 'user'
                                ? 'bg-blue-600 text-white'
                                : 'bg-white text-zinc-800 border border-zinc-200'}
                        `}>
                            {msg.role === 'assistant' && !msg.content && !msg.changes && isLoading && idx === messages.length - 1 ? (
                                <div className="flex items-center gap-2 text-zinc-500">
                                    <Loader2 className="w-4 h-4 animate-spin" />
                                    <span className="text-sm">Thinking...</span>
                                </div>
                            ) : msg.role === 'assistant' && msg.changes && msg.changes.length > 0 ? (
                                <div>
                                    <p className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2">
                                        Changes
                                    </p>
                                    <ul className="list-disc ml-4 space-y-1">
                                        {msg.changes.map((c, ci) => (
                                            <li key={ci} className="text-sm text-zinc-800">{c}</li>
                                        ))}
                                    </ul>
                                </div>
                            ) : (
                                <MarkdownContent content={msg.content} />
                            )}
                        </div>

                        {/* Apply Button for Refinements */}
                        {msg.role === 'assistant' && msg.isRefinement && msg.updatedDocument && !isLoading && idx === messages.length - 1 && (
                            <div className="flex gap-2 mt-1">
                                <Button
                                    size="sm"
                                    variant="default"
                                    className="bg-green-600 hover:bg-green-700 h-8 text-xs"
                                    onClick={() => {
                                        const refined = (msg.updatedDocument || '').trim();
                                        const currentLen = currentNotes.length;
                                        const refinedLen = refined.length;
                                        const shrinkRatio =
                                            currentLen > 0 ? refinedLen / currentLen : 1;
                                        console.log('[RefineNotes] Apply requested', {
                                            currentLen,
                                            refinedLen,
                                            shrinkRatio: Number(shrinkRatio.toFixed(3)),
                                        });

                                        // Safety net: a targeted edit should produce a doc
                                        // approximately the same size as the input. If the
                                        // refined output drops below 70% of the input length,
                                        // the model likely rewrote everything instead of
                                        // editing in place.
                                        if (currentLen > 200 && shrinkRatio < 0.7) {
                                            const shrinkPct = Math.round((1 - shrinkRatio) * 100);
                                            const confirmed = window.confirm(
                                                `⚠️ Heads up: the refined version is ${shrinkPct}% shorter than your current notes ` +
                                                `(${currentLen} → ${refinedLen} chars).\n\n` +
                                                `This usually means the AI replaced your whole document instead of ` +
                                                `editing the part you asked about. Applying will overwrite everything ` +
                                                `with the shorter version.\n\n` +
                                                `Apply anyway? (Cancel = keep your current notes)`
                                            );
                                            if (!confirmed) {
                                                console.log('[RefineNotes] Apply cancelled by user (shrink guard)');
                                                return;
                                            }
                                        }

                                        Analytics.trackRefineNotesApplied(meetingId);
                                        onApplyRefinement(refined);
                                    }}
                                >
                                    <Check className="w-3 h-3 mr-1" />
                                    Apply Changes
                                </Button>
                            </div>
                        )}
                    </div>
                ))}
                <div ref={messagesEndRef} />
            </div>

            {/* Input */}
            <form onSubmit={handleSubmit} className="p-4 border-t border-zinc-200 bg-white">
                <div className="relative">
                    <input
                        type="text"
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        placeholder="How should I change the notes?"
                        className="w-full pl-4 pr-10 py-3 rounded-lg border border-zinc-300 bg-transparent focus:outline-none focus:ring-2 focus:ring-purple-500"
                        disabled={isLoading}
                    />
                    <button
                        type="submit"
                        disabled={!input.trim() || isLoading}
                        className="absolute right-2 top-1/2 -translate-y-1/2 p-2 text-purple-600 hover:text-purple-700 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                    </button>
                </div>
            </form>
        </div>
    );
}
