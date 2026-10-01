// Only completed, flushed parts are checkpoints. An interrupted in-flight part
// is regenerated on resume, so neither text nor buffered encoder audio is lost.
let database;
function open() {
  return database ||= new Promise((resolve, reject) => {
    const request = indexedDB.open('audio-studio-recording-v1', 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore('job');
      request.result.createObjectStore('parts', { keyPath: 'index' });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
async function transaction(names, action) {
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(names, 'readwrite');
    tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error);
    action(tx);
  });
}
export async function beginSession(job) {
  return transaction(['job', 'parts'], tx => {
    tx.objectStore('parts').clear(); tx.objectStore('job').put(job, 'current');
  });
}
export async function savePart(job, part) {
  return transaction(['job', 'parts'], tx => {
    tx.objectStore('parts').put(part); tx.objectStore('job').put(job, 'current');
  });
}
export async function saveJob(job) { return transaction(['job'], tx => tx.objectStore('job').put(job, 'current')); }
export async function loadSession() {
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(['job', 'parts']), job = tx.objectStore('job').get('current'), parts = tx.objectStore('parts').getAll();
    tx.oncomplete = () => resolve(job.result ? { job: job.result, parts: parts.result } : null);
    tx.onerror = () => reject(tx.error);
  });
}
