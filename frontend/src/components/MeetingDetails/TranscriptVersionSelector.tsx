'use client';

import { useState, useEffect } from 'react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { authFetch } from '@/lib/api';
import { Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import Analytics from '@/lib/analytics';

interface TranscriptVersion {
  version_num: number;
  source: string;
  is_authoritative: boolean;
  created_at: string;
  confidence_metrics: {
    avg_confidence: number;
    confident_count: number;
    uncertain_count: number;
    overlap_count: number;
  };
}

interface TranscriptVersionSelectorProps {
  meetingId: string;
  onVersionChange: (versionNum: number) => void;
  currentVersionNum?: number;
  refreshTrigger?: any; // New prop to trigger re-fetch
}

export function TranscriptVersionSelector({
  meetingId,
  onVersionChange,
  currentVersionNum,
  refreshTrigger
}: TranscriptVersionSelectorProps) {
  const [versions, setVersions] = useState<TranscriptVersion[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchVersions();
  }, [meetingId, refreshTrigger]); // Add refreshTrigger to dependencies

  const fetchVersions = async () => {
    try {
      const response = await authFetch(`/meetings/${meetingId}/versions`);
      if (response.ok) {
        const data = await response.json();
        setVersions(data.versions);
      }
    } catch (error) {
      console.error('Failed to fetch transcript versions:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteVersion = async (e: React.MouseEvent, versionNum: number) => {
    e.stopPropagation(); // Prevent select closing
    if (!confirm(`Are you sure you want to delete version v${versionNum}?`)) return;

    try {
      const response = await authFetch(`/meetings/${meetingId}/versions/${versionNum}`, {
        method: 'DELETE'
      });
      if (response.ok) {
        Analytics.trackTranscriptVersionDeleted(meetingId, versionNum);
        // If the deleted version was the selected one, switch back to live
        if (currentVersionNum === versionNum) {
          onVersionChange(-1);
        }
        fetchVersions();
      } else {
        const err = await response.json();
        alert(err.detail || 'Failed to delete version');
      }
    } catch (error) {
      console.error('Error deleting version:', error);
      alert('Failed to delete version');
    }
  };

  if (loading) return null;

  return (
    <div className="flex items-center gap-2">
      <span className="text-sm font-medium text-gray-700">Version:</span>
      <Select
        value={currentVersionNum?.toString()}
        onValueChange={(val: string) => {
          const versionNum = parseInt(val);
          Analytics.trackTranscriptVersionSwitched(meetingId, versionNum);
          onVersionChange(versionNum);
        }}
      >
        <SelectTrigger className="w-[280px]">
          <SelectValue placeholder="Select version" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="-1">
            <div className="flex items-center justify-between w-full gap-2 pr-2">
              <span>Live Transcript</span>
              <Badge variant="outline" className="ml-2 text-xs">Current</Badge>
            </div>
          </SelectItem>
          {versions.map((v: TranscriptVersion) => (
            <div key={v.version_num} className="flex items-center w-full">
              <SelectItem value={v.version_num.toString()} className="flex-1">
                <div className="flex items-center justify-between w-full gap-2">
                  <span>v{v.version_num} ({v.source})</span>
                  {v.is_authoritative && <Badge variant="secondary" className="ml-2 text-xs">Active</Badge>}
                  {v.confidence_metrics && (
                    <span className="text-xs text-gray-400 ml-auto mr-4">
                      {Math.round(v.confidence_metrics.avg_confidence * 100)}%
                    </span>
                  )}
                </div>
              </SelectItem>
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6 text-gray-400 hover:text-red-500 hover:bg-transparent"
                onClick={(e: React.MouseEvent) => handleDeleteVersion(e, v.version_num)}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
