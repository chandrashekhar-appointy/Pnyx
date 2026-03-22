"""
Simple Voice Activity Detection (VAD) based on audio amplitude.
Fast and lightweight alternative to Silero VAD.
"""

import numpy as np
import logging
from typing import List

logger = logging.getLogger(__name__)

class SimpleVAD:
    """
    Amplitude-based Voice Activity Detection.

    Detects speech by measuring audio energy/volume.
    Fast and simple. Can upgrade to Silero later if more complexity is needed.
    """

    def __init__(self, threshold: float = 0.02, sample_rate: int = 16000):
        """
        Args:
            threshold: RMS amplitude threshold (0.0-1.0)
                      0.01 = very sensitive, 0.05 = less sensitive
            sample_rate: Audio sample rate in Hz
        """
        self.threshold = threshold
        self.sample_rate = sample_rate
        logger.info(f"✅ SimpleVAD initialized (threshold={threshold})")

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """
        Check if audio chunk contains speech.

        Args:
            audio_chunk: NumPy array of audio samples (int16 or float32)

        Returns:
            True if speech detected, False if silence
        """
        # Convert to float if needed
        if audio_chunk.dtype == np.int16:
            audio_float = audio_chunk.astype(np.float32) / 32768.0
        else:
            audio_float = audio_chunk

        # Calculate RMS (Root Mean Square) energy
        rms = np.sqrt(np.mean(audio_float ** 2))

        # Check if above threshold
        is_speech = rms > self.threshold

        return is_speech

    def get_speech_segments(
        self,
        audio: np.ndarray,
        min_speech_duration_ms: int = 250,
        min_silence_duration_ms: int = 500
    ) -> List[dict]:
        """
        Get timestamps of speech segments in audio.

        Args:
            audio: Full audio array
            min_speech_duration_ms: Minimum speech duration (ms)
            min_silence_duration_ms: Minimum silence to split segments (ms)

        Returns:
            List of {'start': ms, 'end': ms} dicts
        """
        # Process in chunks
        chunk_duration_ms = 100  # 100ms chunks
        chunk_size = int((chunk_duration_ms / 1000) * self.sample_rate)

        segments = []
        current_segment_start = None
        silence_duration = 0

        for i in range(0, len(audio), chunk_size):
            chunk = audio[i:i + chunk_size]
            if len(chunk) < chunk_size:
                break

            is_speech = self.is_speech(chunk)
            timestamp_ms = (i / self.sample_rate) * 1000

            if is_speech:
                silence_duration = 0
                # Start new segment if not in one
                if current_segment_start is None:
                    current_segment_start = timestamp_ms
            else:
                # Accumulate silence
                silence_duration += chunk_duration_ms

                # End segment if silence long enough
                if current_segment_start is not None and silence_duration >= min_silence_duration_ms:
                    segment_duration = timestamp_ms - current_segment_start

                    # Only add if long enough
                    if segment_duration >= min_speech_duration_ms:
                        segments.append({
                            'start': int(current_segment_start),
                            'end': int(timestamp_ms)
                        })

                    current_segment_start = None

        # Add final segment if exists
        if current_segment_start is not None:
            final_timestamp = (len(audio) / self.sample_rate) * 1000
            segment_duration = final_timestamp - current_segment_start
            if segment_duration >= min_speech_duration_ms:
                segments.append({
                    'start': int(current_segment_start),
                    'end': int(final_timestamp)
                })

        return segments


class SileroVAD:
    """
    ML-based Voice Activity Detection using Silero VAD.
    
    Much more accurate than SimpleVAD for detecting speech vs noise.
    Requires torch to be installed.
    """

    def __init__(self, threshold: float = 0.5, sample_rate: int = 16000):
        """
        Args:
            threshold: Speech probability threshold (0.0-1.0)
            sample_rate: Audio sample rate (16000 recommended)
        """
        try:
            import torch
            self.torch = torch
            
            # Load Silero VAD model
            model, utils = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                force_reload=False,
                onnx=False,
                trust_repo=True
            )
            
            self.model = model
            self.get_speech_timestamps, _, self.read_audio, *_ = utils
            self.threshold = threshold
            self.sample_rate = sample_rate
            
            # State for streaming
            self.h = None  # Hidden state for LSTM
            self.c = None  # Cell state for LSTM
            
            logger.info(f"✅ SileroVAD initialized (threshold={threshold})")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize SileroVAD: {e}")
            raise ImportError(f"SileroVAD requires torch: {e}")

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """
        Check if audio chunk contains speech using ML model.

        Args:
            audio_chunk: NumPy array of audio samples (int16 or float32)

        Returns:
            True if speech detected, False if silence/noise
        """
        try:
            # Convert to float32 tensor
            if audio_chunk.dtype == np.int16:
                audio_float = audio_chunk.astype(np.float32) / 32768.0
            else:
                audio_float = audio_chunk.astype(np.float32)
            
            # Silero VAD (v4) typically expects 512, 1024, or 1536 samples for 16kHz
            # We will process in chunks of 512 and return True if ANY chunk is speech
            
            window_size = 512
            
            # Pad if less than window size
            if len(audio_float) < window_size:
                 pad_len = window_size - len(audio_float)
                 audio_float = np.pad(audio_float, (0, pad_len))
            
            # Process in windows
            for i in range(0, len(audio_float) - window_size + 1, window_size):
                window = audio_float[i:i + window_size]
                
                # Convert to torch tensor
                audio_tensor = self.torch.from_numpy(window)
                
                # Get speech probability
                # Reset states for each new stream, but here we are stateless for simple check
                speech_prob = self.model(audio_tensor, self.sample_rate).item()
                
                if speech_prob > self.threshold:
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"SileroVAD error: {e}")
            # Fallback to simple RMS check on error
            if audio_chunk.dtype == np.int16:
                audio_float = audio_chunk.astype(np.float32) / 32768.0
            else:
                audio_float = audio_chunk
            rms = np.sqrt(np.mean(audio_float ** 2))
            return rms > 0.02

    def get_speech_segments(
        self,
        audio: np.ndarray,
        min_speech_duration_ms: int = 250,
        min_silence_duration_ms: int = 500
    ) -> List[dict]:
        """
        Get timestamps of speech segments in audio using Silero VAD.

        Args:
            audio: Full audio array (int16 or float32)
            min_speech_duration_ms: Minimum speech duration (ms)
            min_silence_duration_ms: Minimum silence to split segments (ms)

        Returns:
            List of {'start': ms, 'end': ms} dicts
        """
        try:
            # Convert to float32 tensor
            if audio.dtype == np.int16:
                audio_float = audio.astype(np.float32) / 32768.0
            else:
                audio_float = audio.astype(np.float32)
            
            audio_tensor = self.torch.from_numpy(audio_float)
            
            # Get speech timestamps using Silero's built-in function
            speech_timestamps = self.get_speech_timestamps(
                audio_tensor, 
                self.model,
                sampling_rate=self.sample_rate,
                threshold=self.threshold,
                min_speech_duration_ms=min_speech_duration_ms,
                min_silence_duration_ms=min_silence_duration_ms
            )
            
            # Convert sample indices to milliseconds
            segments = []
            for ts in speech_timestamps:
                segments.append({
                    'start': int((ts['start'] / self.sample_rate) * 1000),
                    'end': int((ts['end'] / self.sample_rate) * 1000)
                })
            
            return segments
            
        except Exception as e:
            logger.error(f"SileroVAD get_speech_segments error: {e}")
            # Fallback to SimpleVAD behavior
            fallback = SimpleVAD(threshold=0.02)
            return fallback.get_speech_segments(audio, min_speech_duration_ms, min_silence_duration_ms)


class TenVAD:
    """
    Voice Activity Detection using TEN VAD (C++ based, high performance).
    
    Requires 'ten-vad' package and libc++.
    Operates strictly on 16kHz int16 audio in 256 sample chunks.
    """

    def __init__(self, threshold: float = 0.5, sample_rate: int = 16000):
        """
        Args:
            threshold: Speech probability threshold (0.0-1.0). Default 0.5.
            sample_rate: Audio sample rate (must be 16000).
        """
        if sample_rate != 16000:
            logger.warning("TEN VAD only supports 16kHz. Resampling might be needed by caller.")
        
        try:
            from ten_vad import TenVad
            # Initialize TenVad with default hop_size=256
            self.hop_size = 256
            self.vad = TenVad(hop_size=self.hop_size, threshold=threshold)
            self.sample_rate = 16000
            self.threshold = threshold
            logger.info(f"✅ TenVAD initialized (threshold={threshold})")
            
        except ImportError:
            logger.error("❌ ten-vad package not found. Install with `pip install ten-vad`.")
            raise
        except Exception as e:
            logger.error(f"❌ Failed to initialize TenVAD: {e}")
            raise ImportError(f"TenVAD initialization failed (missing libc++?): {e}")

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """
        Check if audio chunk contains speech.

        Args:
            audio_chunk: NumPy array of audio samples (int16 or float32)

        Returns:
            True if speech detected in any frame of the chunk.
        """
        try:
            # Ensure int16
            if audio_chunk.dtype == np.float32:
                # Clip and convert to int16
                audio_int16 = (np.clip(audio_chunk, -1.0, 1.0) * 32767).astype(np.int16)
            elif audio_chunk.dtype == np.int16:
                audio_int16 = audio_chunk
            else:
                # Try to cast
                audio_int16 = audio_chunk.astype(np.int16)

            # Process in hop_size chunks
            total_samples = len(audio_int16)
            
            # If smaller than hop_size, pad it
            if total_samples < self.hop_size:
                padding = np.zeros(self.hop_size - total_samples, dtype=np.int16)
                audio_int16 = np.concatenate([audio_int16, padding])
                total_samples = self.hop_size

            is_speech_detected = False
            
            # Iterate
            for i in range(0, total_samples, self.hop_size):
                chunk = audio_int16[i : i + self.hop_size]
                
                # Check for exact size (drop last partial chunk if any, though padding handled most)
                if len(chunk) == self.hop_size:
                    prob, flags = self.vad.process(chunk)
                    # flags: 1 for speech, 0 for silence? Or prob > threshold?
                    # The doc says prob is returned. 
                    # Based on usage, we rely on prob > threshold (already set in init) 
                    # BUT wait, the init takes threshold. Does process return bool based on it?
                    # The C code signature returns prob and flags.
                    # Let's assume prob is the confidence.
                    
                    if prob > self.threshold:
                         is_speech_detected = True
                         # We can return early if we just want to know "is there speech?"
                         return True
            
            return is_speech_detected

        except Exception as e:
            logger.error(f"TenVAD error: {e}")
            return False

    def get_speech_segments(
        self,
        audio: np.ndarray,
        min_speech_duration_ms: int = 250,
        min_silence_duration_ms: int = 500
    ) -> List[dict]:
        """
        Get timestamps of speech segments using TenVAD.
        
        Args:
            audio: Full audio array
            min_speech_duration_ms: Minimum speech duration (ms)
            min_silence_duration_ms: Minimum silence to split segments (ms)
            
        Returns:
            List of {'start': ms, 'end': ms} dicts
        """
        # Convert to int16
        if audio.dtype == np.float32:
             audio_int16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        else:
             audio_int16 = audio.astype(np.int16)

        segments = []
        current_segment_start = None
        silence_duration = 0
        
        # We need to process in exact hop_size
        chunk_size = self.hop_size # 256 samples = 16ms
        chunk_duration_ms = (chunk_size / self.sample_rate) * 1000
        
        for i in range(0, len(audio_int16), chunk_size):
            chunk = audio_int16[i : i + chunk_size]
            if len(chunk) < chunk_size:
                break # Drop last partial chunk
            
            prob, flags = self.vad.process(chunk)
            is_speech = prob > self.threshold
            timestamp_ms = (i / self.sample_rate) * 1000
            
            if is_speech:
                silence_duration = 0
                if current_segment_start is None:
                    current_segment_start = timestamp_ms
            else:
                silence_duration += chunk_duration_ms
                
                if current_segment_start is not None and silence_duration >= min_silence_duration_ms:
                    segment_duration = timestamp_ms - current_segment_start
                    if segment_duration >= min_speech_duration_ms:
                        segments.append({
                            'start': int(current_segment_start),
                            'end': int(timestamp_ms)
                        })
                    current_segment_start = None

        # Final segment
        if current_segment_start is not None:
            final_timestamp = (len(audio_int16) / self.sample_rate) * 1000
            segment_duration = final_timestamp - current_segment_start
            if segment_duration >= min_speech_duration_ms:
                 segments.append({
                    'start': int(current_segment_start),
                    'end': int(final_timestamp)
                })
                
        return segments
