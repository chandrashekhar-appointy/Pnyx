import { openDB, IDBPDatabase } from 'idb';

const DB_NAME = 'pnyx-journal';
const STORE_NAME = 'segments';

export interface TranscriptSegment {
  meetingId: string;
  index: number;
  text: string;
  speaker?: string;
  startTime: number;
}

export class MeetingJournal {
  private static db: Promise<IDBPDatabase>;

  static {
    this.db = openDB(DB_NAME, 1, {
      upgrade(db) {
        // Create an object store with a composite key [meetingId, index]
        db.createObjectStore(STORE_NAME, { keyPath: ['meetingId', 'index'] });
      },
    });
  }

  /**
   * Save a single transcript segment to IndexedDB.
   */
  static async saveSegment(segment: TranscriptSegment): Promise<void> {
    const db = await this.db;
    await db.put(STORE_NAME, segment);
  }

  /**
   * Retrieve all segments for a specific meeting, sorted by index.
   */
  static async getTranscript(meetingId: string): Promise<TranscriptSegment[]> {
    const db = await this.db;
    const tx = db.transaction(STORE_NAME, 'readonly');
    const store = tx.objectStore(STORE_NAME);
    
    // Simplest way: iterate through the store and filter by meetingId manually
    // or use a proper index. Since meetings are small, filter is okay.
    const all = await store.getAll();
    return all
      .filter((s: TranscriptSegment) => s.meetingId === meetingId)
      .sort((a, b) => a.index - b.index);
  }

  /**
   * Clear all segments for a specific meeting.
   */
  static async clearMeeting(meetingId: string): Promise<void> {
    const db = await this.db;
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const store = tx.objectStore(STORE_NAME);
    const keys = await store.getAllKeys();
    
    for (const key of keys) {
      if ((key as any)[0] === meetingId) {
        await store.delete(key);
      }
    }
    await tx.done;
  }
}
