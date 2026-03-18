/**
 * AudioWorklet processor for capturing raw PCM audio.
 * Runs in separate audio thread for best performance.
 *
 * Captures microphone input → Downsamples to 16kHz → Converts to PCM → Sends to main thread
 */
class AudioStreamProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();

    // Get sample rate from options (passed from main thread)
    this.inputSampleRate = options.processorOptions?.sampleRate || 48000;
    this.outputSampleRate = 16000; // Target for Groq

    // Calculate downsampling ratio
    this.downsampleRatio = this.inputSampleRate / this.outputSampleRate;

    this.bufferSize = 4096; // Output buffer size
    this.buffer = new Float32Array(this.bufferSize);
    this.bufferIndex = 0;

    // Downsampling state with anti-aliasing filter
    this.sampleIndex = 0;
    this.filterBuffer = [0, 0, 0]; // Simple 3-tap low-pass filter

    this.port.onmessage = (event) => {
      if (event.data?.type === 'flush') {
        this.flushBuffer();
      }
    };

    console.log(`[AudioWorklet] Initialized: ${this.inputSampleRate}Hz → ${this.outputSampleRate}Hz (ratio: ${this.downsampleRatio})`);
  }

  flushBuffer() {
    if (this.bufferIndex <= 0) {
      return;
    }

    const pcmData = new Int16Array(this.bufferIndex);
    for (let j = 0; j < this.bufferIndex; j++) {
      const s = Math.max(-1, Math.min(1, this.buffer[j]));
      pcmData[j] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }

    this.port.postMessage(pcmData.buffer, [pcmData.buffer]);
    this.bufferIndex = 0;
  }

  process(inputs, outputs, parameters) {
    const input = inputs[0];

    // No input means mic not ready yet
    if (!input || !input[0]) {
      return true;
    }

    const samples = input[0]; // Mono channel

    // Process each sample with improved downsampling
    for (let i = 0; i < samples.length; i++) {
      this.sampleIndex++;

      // Apply simple low-pass filter to reduce aliasing
      this.filterBuffer[0] = this.filterBuffer[1];
      this.filterBuffer[1] = this.filterBuffer[2];
      this.filterBuffer[2] = samples[i];

      const filtered = (this.filterBuffer[0] + 2 * this.filterBuffer[1] + this.filterBuffer[2]) / 4;

      // Downsample: take every Nth filtered sample
      if (this.sampleIndex >= this.downsampleRatio) {
        this.sampleIndex = 0;

        // Add downsampled sample to buffer
        this.buffer[this.bufferIndex++] = filtered;

        // Send to main thread when buffer full
        if (this.bufferIndex >= this.bufferSize) {
          this.flushBuffer();
        }
      }
    }

    return true; // Keep processor alive
  }
}

registerProcessor('audio-stream-processor', AudioStreamProcessor);
