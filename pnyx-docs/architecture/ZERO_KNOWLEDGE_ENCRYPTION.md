# Architecture: Zero-Knowledge Encryption (E2EE)

This document specifies the "Zero-Knowledge" encryption model for Meeting Co-Pilot, ensuring that only the user can decrypt their audio recordings, transcripts, and AI notes.

## Core Principles
1. **User Ownership**: The user owns the only copy of the private key required for decryption.
2. **Ephemeral Backend**: The backend only sees raw data in RAM during active processing (transcription/summarization) and never stores it unencrypted.
3. **No Central Recovery**: If the user loses their private key and backup, the data is unrecoverable.

---

## 1. Key Management

### Key Pair Generation (Client-Side)
- **Algorithm**: `ECDH` (Elliptic Curve Diffie-Hellman) using the `P-256` curve (Web Crypto API).
- **Public Key**: Exported as `spki` (Subject Public Key Info) and stored in the backend (Postgres).
- **Private Key**: Exported as `pkcs8` and stored **only** in the user's browser (IndexedDB).
- **Backup**: Users are prompted to download their private key as a recovery file (`pnyx-recovery-key.txt`).

### Per-User Key Persistence
- The key pair is generated **once** per account.
- If the browser storage is cleared, the user must upload their backup key to restored access.

---

## 2. Live Meeting Flow

### Live Transcription Storage
- **Browser**: Every transcript segment received via WebSocket is immediate written to a local **IndexedDB** table (`live_meeting_journal`).
- **Backend**: Does **not** save transcript segments to the database or bucket during the live stream.

### Audio Streaming (PCM Chunks)
- Individual 1-10s PCM chunks are sent to the bucket **unencrypted**. 
- *Rationale*: Allows real-time VAD and STT (Whisper) without managing decryption keys in the backend's high-frequency streaming loop.
- these chunks are **deleted** after the final encrypted audio is generated.

---

## 3. Finalization & Encryption (The "Seal")

When the meeting ends, the following atomic steps occur:

1. **Transcript Upload**: The browser pulls the full history from IndexedDB and sends it to the backend as a single payload.
2. **AI Summarization**: The backend generates notes (RAM-only operation).
3. **Audio Merging**: The backend merges PCM chunks into a `recording.wav` (temporary RAM/Buffer).
4. **Symmetric Layer (AES)**:
    - The backend generates a random **256-bit AES-GCM Meeting Key**.
    - The `recording.wav` and `notes.json` are encrypted using this AES key.
5. **Asymmetric Layer (ECC Wrapping)**:
    - The backend retrieves the user's **Public Key** from Postgres.
    - The AES Meeting Key is **encrypted (wrapped)** using the user's Public Key.
6. **Persistence**:
    - The encrypted audio and notes are saved to GCS.
    - The wrapped AES key is saved to the meeting metadata in GCS/Postgres.
    - Unencrypted PCM chunks are deleted.

---

## 4. Decryption & Viewing

1. **Download**: The browser downloads the encrypted notes and the wrapped AES key.
2. **Unwrapping**: The browser uses the local **Private Key** (from IndexedDB) to decrypt (unwrap) the AES Meeting Key.
3. **Locally Decrypt**: The decrypted AES key is used to decrypt the notes and audio locally within the browser.

---

## 5. Security Summary

| Threat | Outcome |
| :--- | :--- |
| **S3/GCS Bucket Leak** | Attacker finds only encrypted blobs. |
| **Postgres Database Breach** | Attacker finds only Public Keys. Data is safe. |
| **Rogue Admin Access** | Cannot read past meetings. Can only intercept live meetings while the ephemeral data is in RAM. |
| **User Loses Key** | **Total data loss.** (By design). |
