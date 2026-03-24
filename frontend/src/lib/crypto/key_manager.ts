// import { openDB, IDBPDatabase } from 'idb';

const DB_NAME = 'pnyx-crypto';
const STORE_NAME = 'keys';
const KEY_NAME = 'main-identity';

export interface KeyPair {
  publicKey: CryptoKey;
  privateKey: CryptoKey;
}

export class KeyManager {
  private static _db: Promise<any> | null = null;

  private static async getDb(): Promise<any> {
    if (!this._db) {
      if (typeof window === 'undefined') {
        return Promise.reject(new Error('IndexedDB is not available on the server'));
      }
      const { openDB } = await import('idb');
      this._db = openDB(DB_NAME, 1, {
        upgrade(db) {
          if (!db.objectStoreNames.contains(STORE_NAME)) {
            db.createObjectStore(STORE_NAME);
          }
        },
      });
    }
    return this._db;
  }

  /**
   * Check if a private key exists locally.
   */
  static async hasPrivateKey(): Promise<boolean> {
    const pair = await this.getKeyPair();
    return !!(pair && pair.privateKey);
  }

  /**
   * Helper to generate, store and return public key as base64.
   */
  static async generateAndStoreKeyPair(): Promise<{ publicKey: string }> {
    const pair = await this.generateKeyPair();
    await this.storeKeyPair(pair);
    const pubBase64 = await this.exportPublicKey(pair.publicKey);
    return { publicKey: pubBase64 };
  }

  /**
   * Export the private key as base64 string.
   */
  static async getPrivateKeyBase64(): Promise<string | null> {
    const pair = await this.getKeyPair();
    if (!pair || !pair.privateKey) return null;
    return await this.exportPrivateKey(pair.privateKey);
  }

  /**
   * Permanently delete keys from this browser.
   */
  static async destroyKeys(): Promise<void> {
    console.log('🗝️ KeyManager: Deleting keys from IndexedDB...');
    const db = await this.getDb();
    await db.delete(STORE_NAME, KEY_NAME);
    console.log('✅ KeyManager: Keys deleted.');
  }

  /**
   * Generate a new P-256 ECC key pair for the user.
   */
  static async generateKeyPair(): Promise<KeyPair> {
    const pair = await window.crypto.subtle.generateKey(
      {
        name: 'ECDH',
        namedCurve: 'P-256',
      },
      true, // extractable (needed for backup)
      ['deriveKey', 'deriveBits']
    );
    return pair as KeyPair;
  }

  /**
   * Store the key pair securely in IndexedDB.
   */
  static async storeKeyPair(pair: KeyPair): Promise<void> {
    console.log('🗝️ KeyManager: Storing key pair in IndexedDB...', { KEY_NAME });
    const db = await this.getDb();
    await db.put(STORE_NAME, pair, KEY_NAME);
    console.log('✅ KeyManager: Key pair stored successfully.');
  }

  /**
   * Retrieve the stored key pair.
   */
  static async getKeyPair(): Promise<KeyPair | null> {
    try {
      const db = await this.getDb();
      const pair = await db.get(STORE_NAME, KEY_NAME);
      console.log('🗝️ KeyManager: Retrieved key pair from IndexedDB:', !!pair);
      return pair || null;
    } catch (e) {
      console.error('❌ KeyManager: Failed to retrieve key pair:', e);
      return null;
    }
  }

  /**
   * Export the Public Key as SPKI Base64 (to send to server).
   */
  static async exportPublicKey(key: CryptoKey): Promise<string> {
    const exported = await window.crypto.subtle.exportKey('spki', key);
    return btoa(String.fromCharCode(...new Uint8Array(exported)));
  }

  /**
   * Export the Private Key as PKCS8 Base64 (for user backup).
   */
  static async exportPrivateKey(key: CryptoKey): Promise<string> {
    const exported = await window.crypto.subtle.exportKey('pkcs8', key);
    return btoa(String.fromCharCode(...new Uint8Array(exported)));
  }

  /**
   * Import a Private Key from a backup string.
   */
  static async importPrivateKey(base64: string): Promise<CryptoKey> {
    const binary = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    return await window.crypto.subtle.importKey(
      'pkcs8',
      binary,
      { name: 'ECDH', namedCurve: 'P-256' },
      true,
      ['deriveKey', 'deriveBits']
    );
  }

  /**
   * Decrypt (Unwrap) an AES session key using the local Private Key.
   */
  static async decryptSessionKey(
    privateKey: CryptoKey,
    ephemeralPublicKeyDer: Uint8Array,
    kekNonce: Uint8Array,
    wrappedAesKey: Uint8Array
  ): Promise<CryptoKey> {
    // 1. Import the ephemeral public key sent by the server
    const ephemeralPubKey = await window.crypto.subtle.importKey(
      'spki',
      ephemeralPublicKeyDer.buffer as ArrayBuffer,
      { name: 'ECDH', namedCurve: 'P-256' },
      true,
      []
    );

    // 2. Derive Shared Secret (ECDH)
    const sharedSecret = await window.crypto.subtle.deriveBits(
      {
        name: 'ECDH',
        public: ephemeralPubKey,
      },
      privateKey,
      256
    );

    // 3. Derive KEK via HKDF (to match backend)
    const baseKey = await window.crypto.subtle.importKey(
      'raw',
      sharedSecret,
      'HKDF',
      false,
      ['deriveKey']
    );

    const kek = await window.crypto.subtle.deriveKey(
      {
        name: 'HKDF',
        hash: 'SHA-256',
        salt: new Uint8Array(),
        info: new TextEncoder().encode('pnyx-key-wrapping'),
      },
      baseKey,
      { name: 'AES-GCM', length: 256 },
      false,
      ['decrypt']
    );

    // 4. Decrypt (Unwrap) the actual Session Key
    const decryptedAesBytes = await window.crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: kekNonce.buffer as ArrayBuffer },
      kek,
      wrappedAesKey.buffer as ArrayBuffer
    );

    return await window.crypto.subtle.importKey(
      'raw',
      decryptedAesBytes,
      'AES-GCM',
      true,
      ['decrypt']
    );
  }

  /**
   * Decrypt a document (audio or notes) using a decrypted Session Key.
   */
  static async decryptDocument(
    sessionKey: CryptoKey,
    nonce: Uint8Array,
    encryptedData: Uint8Array
  ): Promise<ArrayBuffer> {
    return await window.crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: nonce.buffer as ArrayBuffer },
      sessionKey,
      encryptedData.buffer as ArrayBuffer
    );
  }

  /**
   * Decrypt a document (audio or notes) using a Web Worker.
   * Prevents UI hangs for large files.
   */
  static async decryptDocumentAsync(
    sessionKey: CryptoKey,
    nonce: Uint8Array,
    encryptedData: Uint8Array
  ): Promise<ArrayBuffer> {
    const sessionKeyRaw = await window.crypto.subtle.exportKey('raw', sessionKey);
    
    return new Promise((resolve, reject) => {
      const worker = new Worker(new URL('./decryption.worker.ts', import.meta.url));
      
      worker.onmessage = (e) => {
        const { decryptedBuffer, error } = e.data;
        if (error) {
          reject(new Error(error));
        } else {
          resolve(decryptedBuffer);
        }
        worker.terminate();
      };
      
      worker.onerror = (e) => {
        reject(new Error('Worker error: ' + e.message));
        worker.terminate();
      };
      
      // Send data to worker, including the buffer as a transferable object
      worker.postMessage({
        sessionKeyRaw,
        nonce,
        encryptedData
      }, [sessionKeyRaw, encryptedData.buffer as ArrayBuffer]);
    });
  }
}
