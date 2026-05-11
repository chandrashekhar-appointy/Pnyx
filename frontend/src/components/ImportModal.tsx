import React, { useState, useRef } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Upload, Loader2, FileAudio, AlertCircle } from 'lucide-react';
import { toast } from 'sonner';
import { authFetch } from '@/lib/api';
import { useSidebar } from '@/components/Sidebar/SidebarProvider';
import Analytics from '@/lib/analytics';
import { useRouter } from 'next/navigation';

interface ImportModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function ImportModal({ isOpen, onClose }: ImportModalProps) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0); // Mock progress for now
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { refetchMeetings } = useSidebar();
  const router = useRouter();

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const selectedFile = e.target.files[0];
      setFile(selectedFile);
      Analytics.trackImportFileSelected(
        selectedFile.type || selectedFile.name.split('.').pop()?.toLowerCase() || 'unknown',
        parseFloat((selectedFile.size / (1024 * 1024)).toFixed(2))
      );
      // Auto-set title from filename if empty
      if (!title) {
        const name = selectedFile.name.replace(/\.[^/.]+$/, ""); // Remove extension
        setTitle(name);
      }
    }
  };

  const handleUpload = async () => {
    if (!file) {
      toast.error("Please select a file");
      return;
    }

    setIsUploading(true);
    setUploadProgress(10); // Start progress

    try {
      const formData = new FormData();
      formData.append('file', file);
      if (title) formData.append('title', title);

      // Simulate progress since fetch doesn't support it natively easily
      const progressInterval = setInterval(() => {
        setUploadProgress(prev => Math.min(prev + 10, 90));
      }, 500);

      const response = await authFetch('/upload-meeting-recording', {
        method: 'POST',
        body: formData,
        // Content-Type header is not set manually for FormData, browser sets it with boundary
        // authFetch wrapper might need adjustment if it forces Content-Type json
      });

      clearInterval(progressInterval);
      setUploadProgress(100);

      if (!response.ok) {
        throw new Error('Upload failed');
      }

      const data = await response.json();
      await Analytics.trackAudioImported({
        meeting_id: data.meeting_id,
        file_type: file.type || file.name.split('.').pop()?.toLowerCase() || 'unknown',
        file_size_bytes: file.size,
        title_length: title.length,
      });
      toast.success("File uploaded successfully", {
        description: "Processing started in background"
      });
      
      onClose();
      setFile(null);
      setTitle('');
      setUploadProgress(0);
      
      // Refresh sidebar list and open the imported meeting immediately.
      await refetchMeetings();
      if (data.meeting_id) {
        router.push(`/meeting-details?id=${encodeURIComponent(data.meeting_id)}`);
      }

    } catch (error) {
      console.error("Upload error:", error);
      Analytics.trackImportUploadFailed(error instanceof Error ? error.message : 'Unknown error');
      toast.error("Failed to upload file");
      setUploadProgress(0);
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && !isUploading && onClose()}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Import Recording</DialogTitle>
        </DialogHeader>

        <div className="py-4 space-y-4">
          <div 
            className={`border-2 border-dashed rounded-lg p-8 text-center transition-colors ${
              file ? 'border-blue-500 bg-blue-50' : 'border-gray-300 hover:bg-gray-50'
            }`}
            onClick={() => !isUploading && fileInputRef.current?.click()}
          >
            <input
              type="file"
              ref={fileInputRef}
              onChange={handleFileChange}
              accept="audio/*,video/*"
              className="hidden"
              disabled={isUploading}
            />
            
            {file ? (
              <div className="flex flex-col items-center gap-2">
                <FileAudio className="w-10 h-10 text-blue-500" />
                <span className="font-medium text-blue-700">{file.name}</span>
                <span className="text-xs text-gray-500">{(file.size / (1024 * 1024)).toFixed(2)} MB</span>
              </div>
            ) : (
              <div className="flex flex-col items-center gap-2 cursor-pointer">
                <Upload className="w-10 h-10 text-gray-400" />
                <span className="font-medium text-gray-600">Click to upload or drag and drop</span>
                <span className="text-xs text-gray-400">Supports MP3, WAV, M4A, MP4</span>
              </div>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Meeting Title
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Weekly Sync"
              className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              disabled={isUploading}
            />
          </div>

          {isUploading && (
            <div className="space-y-2">
              <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                <div 
                  className="h-full bg-blue-600 transition-all duration-300 ease-out"
                  style={{ width: `${uploadProgress}%` }}
                />
              </div>
              <p className="text-xs text-center text-gray-500">
                Uploading and starting processing...
              </p>
            </div>
          )}
        </div>

        <DialogFooter>
          <button
            onClick={onClose}
            disabled={isUploading}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 hover:bg-gray-200 rounded-md disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleUpload}
            disabled={!file || isUploading}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-md disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isUploading && <Loader2 className="w-4 h-4 animate-spin" />}
            {isUploading ? 'Importing...' : 'Import'}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
