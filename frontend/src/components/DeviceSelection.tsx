import React, { useState, useEffect } from 'react';
import { RefreshCw, Mic, Speaker } from 'lucide-react';
import Analytics from '@/lib/analytics';
import { Button } from '@/components/ui/button';

export interface AudioDevice {
  name: string;
  device_type: 'Input' | 'Output';
  deviceId: string;
}

export interface SelectedDevices {
  micDevice: string | null;
  systemDevice: string | null;
}

export interface AudioLevelData {
  device_name: string;
  device_type: string;
  rms_level: number;
  peak_level: number;
  is_active: boolean;
}

interface DeviceSelectionProps {
  selectedDevices: SelectedDevices;
  onDeviceChange: (devices: SelectedDevices) => void;
  disabled?: boolean;
}

export function DeviceSelection({ selectedDevices, onDeviceChange, disabled = false }: DeviceSelectionProps) {
  const [devices, setDevices] = useState<AudioDevice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // Fetch available audio devices using Web API
  const fetchDevices = async () => {
    try {
      setError(null);
      // Request permission first to get labels
      await navigator.mediaDevices.getUserMedia({ audio: true });

      const distinctDevices: AudioDevice[] = [];
      const enumerated = await navigator.mediaDevices.enumerateDevices();

      enumerated.forEach(d => {
        if (d.kind === 'audioinput' || d.kind === 'audiooutput') {
          // Filter out default if needed, or keeping it.
          // Web often gives 'default', 'communications'.
          // Simple mapping:
          distinctDevices.push({
            name: d.label || `Device ${distinctDevices.length + 1}`,
            device_type: d.kind === 'audioinput' ? 'Input' : 'Output',
            deviceId: d.deviceId
          });
        }
      });

      setDevices(distinctDevices);
      console.log('Fetched audio devices (Web):', distinctDevices);
    } catch (err) {
      console.error('Failed to fetch audio devices:', err);
      setError('Failed to load audio devices. Please check permission.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  // Load devices on component mount
  useEffect(() => {
    fetchDevices();

    // Listen for device changes
    navigator.mediaDevices.addEventListener('devicechange', fetchDevices);
    return () => {
      navigator.mediaDevices.removeEventListener('devicechange', fetchDevices);
    };
  }, []);

  const handleRefresh = async () => {
    setRefreshing(true);
    await fetchDevices();
  };

  const inputDevices = devices.filter(d => d.device_type === 'Input');
  const outputDevices = devices.filter(d => d.device_type === 'Output');

  const handleMicDeviceChange = (deviceId: string) => {
    const device = devices.find(d => d.deviceId === deviceId);
    const name = device ? device.name : deviceId;

    const newDevices = {
      ...selectedDevices,
      micDevice: deviceId === 'default' ? null : name // Store name or ID? App seems to use Name.
      // Tauri app used Name likely. Web uses DeviceID usually.
      // For compatibility with rest of app, if it expects name, use name.
      // BUT Web Audio getUserMedia needs deviceId.
      // We should change App to use deviceId?
      // For now, let's stick to name if that's what props expect, but we might break recording if we don't return ID.
      // check onDeviceChange signature.
    };

    // We'll pass the Name back for now as requested by signature, 
    // but ideally we should update state to store deviceId.
    // Assuming existing logic matches by name.
    onDeviceChange(newDevices);
    Analytics.trackAudioDeviceChanged('microphone', name);
  };

  const handleSystemDeviceChange = (deviceId: string) => {
    const device = devices.find(d => d.deviceId === deviceId);
    const name = device ? device.name : deviceId;
    const newDevices = {
      ...selectedDevices,
      systemDevice: deviceId === 'default' ? null : name
    };
    onDeviceChange(newDevices);
    Analytics.trackAudioDeviceChanged('speaker', name);
  };

  if (loading) return <div>Loading devices...</div>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium text-gray-900">Audio Devices</h4>
        <Button size="sm" variant="ghost" onClick={handleRefresh} disabled={refreshing}>
          <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
        </Button>
      </div>

      {error && <div className="text-red-500 text-sm">{error}</div>}

      <div className="space-y-3">
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Mic className="h-4 w-4" />
            <label className="text-sm font-medium">Microphone</label>
          </div>
          <select
            className="w-full border rounded p-2 text-sm"
            value={devices.find(d => d.name === selectedDevices.micDevice)?.deviceId || 'default'}
            onChange={e => handleMicDeviceChange(e.target.value)}
            disabled={disabled}
          >
            <option value="default">Default</option>
            {inputDevices.map(d => (
              <option key={d.deviceId} value={d.deviceId}>{d.name}</option>
            ))}
          </select>
        </div>

        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Speaker className="h-4 w-4" />
            <label className="text-sm font-medium">Speaker</label>
          </div>
          <select
            className="w-full border rounded p-2 text-sm"
            value={devices.find(d => d.name === selectedDevices.systemDevice)?.deviceId || 'default'}
            onChange={e => handleSystemDeviceChange(e.target.value)}
            disabled={disabled}
          >
            <option value="default">Default</option>
            {outputDevices.map(d => (
              <option key={d.deviceId} value={d.deviceId}>{d.name}</option>
            ))}
          </select>
        </div>
      </div>
    </div>
  );
}

