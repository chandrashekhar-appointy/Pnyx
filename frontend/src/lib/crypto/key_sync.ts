import { authFetch } from '@/lib/api';
import { KeyManager } from './key_manager';

/**
 * Ensure the server has the public key that matches the browser's local private key.
 * This prevents new encrypted artifacts from being sealed to a stale server-side key.
 */
export async function syncEncryptionPublicKey(): Promise<string | null> {
  const keyPair = await KeyManager.getKeyPair();
  if (!keyPair?.privateKey) {
    return null;
  }

  if (!keyPair.publicKey) {
    throw new Error('Local encryption key is incomplete');
  }

  const publicKey = await KeyManager.exportPublicKey(keyPair.publicKey);
  const response = await authFetch('/api/user/encryption-key', {
    method: 'POST',
    body: JSON.stringify({ public_key: publicKey }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to sync encryption key');
  }

  return publicKey;
}
