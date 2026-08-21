/**
 * IndexedDB Offline-First Persistence Utility for Pixovo PTE Engine
 * Caches high-res original photo blobs in browser disk storage (IndexedDB).
 * Prevents data loss during network disconnections, browser crashes, or tab closes.
 */

const DB_NAME = 'pixovo_offline_db';
const DB_VERSION = 1;
const STORE_NAME = 'pending_originals';

function openDB() {
  return new Promise((resolve, reject) => {
    if (!window.indexedDB) {
      reject(new Error("IndexedDB not supported in this browser"));
      return;
    }

    const request = window.indexedDB.open(DB_NAME, DB_VERSION);

    request.onerror = (e) => reject(e.target.error);

    request.onsuccess = (e) => resolve(e.target.result);

    request.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: 'photoId' });
      }
    };
  });
}

/**
 * Saves original file blob to IndexedDB linked to photoId.
 */
export async function saveOriginalBlob(photoId, fileBlob) {
  try {
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction([STORE_NAME], 'readwrite');
      const store = transaction.objectStore(STORE_NAME);
      
      const record = {
        photoId,
        blob: fileBlob,
        filename: fileBlob.name,
        timestamp: Date.now()
      };

      const req = store.put(record);
      req.onsuccess = () => resolve(true);
      req.onerror = (e) => reject(e.target.error);
    });
  } catch (err) {
    console.warn(`[IndexedDB] Failed to save blob for ${photoId}:`, err);
    return false;
  }
}

/**
 * Retrieves all pending unsynced original file blobs from IndexedDB.
 */
export async function getPendingBlobs() {
  try {
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction([STORE_NAME], 'readonly');
      const store = transaction.objectStore(STORE_NAME);
      const req = store.getAll();

      req.onsuccess = () => resolve(req.result || []);
      req.onerror = (e) => reject(e.target.error);
    });
  } catch (err) {
    console.warn("[IndexedDB] Failed to get pending blobs:", err);
    return [];
  }
}

/**
 * Removes original file blob from IndexedDB after successful server upload.
 */
export async function removeOriginalBlob(photoId) {
  try {
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction([STORE_NAME], 'readwrite');
      const store = transaction.objectStore(STORE_NAME);
      const req = store.delete(photoId);

      req.onsuccess = () => resolve(true);
      req.onerror = (e) => reject(e.target.error);
    });
  } catch (err) {
    console.warn(`[IndexedDB] Failed to remove blob for ${photoId}:`, err);
    return false;
  }
}
