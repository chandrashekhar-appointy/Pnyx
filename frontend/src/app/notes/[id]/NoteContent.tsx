'use client';

import React from 'react';
import DOMPurify from 'isomorphic-dompurify';
import { Clock, Users, Calendar, Tag } from 'lucide-react';

const NOTE_ALLOWED_TAGS = ['h1', 'h2', 'h3', 'p', 'ul', 'ol', 'li', 'strong', 'em', 'code', 'pre', 'br', 'a'];
const NOTE_ALLOWED_ATTR = ['href', 'target', 'rel'];

function escapeHtml(s: string): string {
    return s
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

interface Note {
  title: string;
  date: string;
  time?: string;
  attendees?: string[];
  tags: string[];
  content: string;
}

export default function NoteContent({ note }: { note: Note }) {
  return (
    <div className="p-8 max-w-4xl mx-auto">
      <div className="mb-8">
        <h1 className="text-3xl font-bold mb-4">{note.title}</h1>
        
        <div className="flex flex-wrap gap-4 text-gray-600">
          {note.date && (
            <div className="flex items-center gap-1">
              <Calendar className="w-4 h-4" />
              <span>{note.date}</span>
            </div>
          )}
          
          {note.time && (
            <div className="flex items-center gap-1">
              <Clock className="w-4 h-4" />
              <span>{note.time}</span>
            </div>
          )}
          
          {note.attendees && (
            <div className="flex items-center gap-1">
              <Users className="w-4 h-4" />
              <span>{note.attendees.join(', ')}</span>
            </div>
          )}
        </div>

        <div className="flex gap-2 mt-4">
          {note.tags.map((tag) => (
            <div key={tag} className="flex items-center gap-1 bg-blue-100 text-blue-800 px-2 py-1 rounded-full text-sm">
              <Tag className="w-3 h-3" />
              {tag}
            </div>
          ))}
        </div>
      </div>

      <div className="prose prose-blue max-w-none">
        <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(
          note.content.split('\n').map(line => {
            const escaped = escapeHtml(line);
            if (line.startsWith('# ')) {
              return `<h1>${escapeHtml(line.slice(2))}</h1>`;
            } else if (line.startsWith('## ')) {
              return `<h2>${escapeHtml(line.slice(3))}</h2>`;
            } else if (line.startsWith('- ')) {
              return `<li>${escapeHtml(line.slice(2))}</li>`;
            }
            return escaped;
          }).join('\n'),
          { ALLOWED_TAGS: NOTE_ALLOWED_TAGS, ALLOWED_ATTR: NOTE_ALLOWED_ATTR }
        ) }} />
      </div>
    </div>
  );
}
