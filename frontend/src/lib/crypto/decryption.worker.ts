// Decryption Web Worker
// Handles heavy AES-GCM decryption off the main thread

/**
 * Decrypts a buffer using AES-GCM in a Web Worker.
 */
async function decrypt(
  sessionKeyRaw: ArrayBuffer,
  nonce: Uint8Array,
  encryptedData: Uint8Array
): Promise<ArrayBuffer> {
  // Import the key in the worker context
  const sessionKey = await crypto.subtle.importKey(
    'raw',
    sessionKeyRaw,
    'AES-GCM',
    false,
    ['decrypt']
  );

  // Perform decryption
  return await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: nonce.buffer as ArrayBuffer },
    sessionKey,
    encryptedData.buffer as ArrayBuffer
  );
}

// Worker message handler
self.onmessage = async (e: MessageEvent) => {
  const { id, sessionKeyRaw, nonce, encryptedData } = e.data;

  try {
    const decryptedBuffer = await decrypt(sessionKeyRaw, nonce, encryptedData);
    // Transfer the ArrayBuffer back to the main thread efficiently
    (self as any).postMessage({ id, decryptedBuffer }, [decryptedBuffer]);
  } catch (error) {
    console.error('Worker decryption error:', error);
    (self as any).postMessage({ id, error: error instanceof Error ? error.message : 'Decryption failed' });
  }
};
