"use client";

import { useState, useEffect, useRef } from 'react';
import { Play, Pause, Loader2 } from 'lucide-react';
import { authFetch } from '@/lib/api';
import { apiUrl } from '@/lib/config';
import { toast } from 'sonner';

interface AudioPlayerProps {
  meetingId: string;
}

export function AudioPlayer({ meetingId }: AudioPlayerProps) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const resolveBackendUrl = (url: string) => {
    if (!url) return url;
    if (/^https?:\/\//i.test(url) || url.startsWith('blob:')) return url;
    return `${apiUrl.replace(/\/$/, '')}${url.startsWith('/') ? url : `/${url}`}`;
  };

  useEffect(() => {
    const fetchUrl = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const res = await authFetch(`/meetings/${meetingId}/recording-url`);
        if (res.ok) {
          const data = await res.json();
          if (data.encrypted) {
            setError("Encrypted");
            return;
          }
          setAudioUrl(resolveBackendUrl(data.url));
        } else {
          if (res.status === 404) {
             setError("No recording");
          } else if (res.status === 409) {
             setError("Processing");
          } else {
             console.error("Failed to load audio url");
             setError("Error loading");
          }
        }
      } catch (err) {
        console.error("Audio fetch error:", err);
        setError("Error");
      } finally {
        setIsLoading(false);
      }
    };

    if (meetingId) {
      fetchUrl();
    }
  }, [meetingId]);

  const togglePlay = () => {
    if (!audioRef.current || !audioUrl) return;
    
    if (isPlaying) {
      audioRef.current.pause();
    } else {
      audioRef.current.play().catch(e => {
        console.error("Play failed:", e);
        toast.error("Playback failed");
      });
    }
  };

  const handleTimeUpdate = () => {
    if (audioRef.current) {
      setCurrentTime(audioRef.current.currentTime);
    }
  };

  const handleLoadedMetadata = () => {
    if (audioRef.current) {
      setDuration(audioRef.current.duration);
    }
  };
  
  const handleSeek = (e: React.ChangeEvent<HTMLInputElement>) => {
    const time = parseFloat(e.target.value);
    if (audioRef.current) {
      audioRef.current.currentTime = time;
      setCurrentTime(time);
    }
  };

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  if (error === "No recording") return null;
  if (error === "Processing") return null;
  if (error === "Encrypted") return null;
  if (isLoading) return null; // Don't show skeleton to keep UI clean, just pop in when ready
  if (!audioUrl) return null;

  return (
    <div className="w-full bg-white border-b border-gray-200 px-4 py-3 flex items-center gap-4 shadow-sm sticky top-0 z-20">
      <button 
        onClick={togglePlay}
        className="w-10 h-10 flex-shrink-0 flex items-center justify-center bg-indigo-600 rounded-full text-white hover:bg-indigo-700 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2"
      >
        {isPlaying ? <Pause size={16} fill="currentColor" /> : <Play size={16} fill="currentColor" className="ml-0.5" />}
      </button>
      
      <div className="flex-1 flex flex-col gap-1">
        <div className="flex justify-between text-xs text-gray-500 font-medium">
            <span>{formatTime(currentTime)}</span>
            <span>{formatTime(duration || 0)}</span>
        </div>
        <div className="relative w-full h-2 bg-gray-200 rounded-full overflow-hidden">
           <div 
             className="absolute top-0 left-0 h-full bg-indigo-500 transition-all duration-100"
             style={{ width: `${(currentTime / (duration || 1)) * 100}%` }}
           />
           <input 
             type="range" 
             min="0" 
             max={duration || 0} 
             value={currentTime} 
             onChange={handleSeek}
             className="absolute top-0 left-0 w-full h-full opacity-0 cursor-pointer"
           />
        </div>
      </div>
      
      <audio 
        ref={audioRef} 
        src={audioUrl} 
        onTimeUpdate={handleTimeUpdate}
        onLoadedMetadata={handleLoadedMetadata}
        onEnded={() => setIsPlaying(false)}
        onPause={() => setIsPlaying(false)}
        onPlay={() => setIsPlaying(true)}
      />
    </div>
  );
}
