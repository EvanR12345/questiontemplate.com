// Only completed, flushed parts are checkpoints. An interrupted in-flight part
// is regenerated on resume, so neither text nor buffered encoder audio is lost.
export function checkpointVersion(job) {
  // Older saved recordings use their metadata as the first comparison.
  return job ? job.checkpointId || JSON.stringify(job) : null;
}

export function createSessionStore(name) {
  let database;
  function open() {
    return database ||= new Promise((resolve, reject) => {
      const request = indexedDB.open(name, 1);
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
      const abort = error => {
        tx.abort();
        reject(error);
      };
      try { action(tx, abort); } catch (error) { abort(error); }
    });
  }
  async function writeCheckpoint(job, part, replace, expectedVersion) {
    let written = false;
    await transaction(part || replace ? ['job', 'parts'] : ['job'], (tx, abort) => {
      const jobs = tx.objectStore('job');
      const write = () => {
        if (replace) tx.objectStore('parts').clear();
        if (part) tx.objectStore('parts').put(part);
        jobs.put(job, 'current');
        written = true;
      };
      if (expectedVersion === undefined) { write(); return; }
      const current = jobs.get('current');
      current.onsuccess = () => {
        try {
          if (checkpointVersion(current.result) === expectedVersion) write();
        } catch (error) { abort(error); }
      };
    });
    return written;
  }
  function beginSession(job, firstPart, expectedVersion) {
    return writeCheckpoint(job, firstPart, true, expectedVersion);
  }
  function savePart(job, part, expectedVersion) {
    return writeCheckpoint(job, part, false, expectedVersion);
  }
  function saveJob(job, expectedVersion) {
    return writeCheckpoint(job, null, false, expectedVersion);
  }
  async function loadCheckpointVersion() {
    const db = await open();
    return new Promise((resolve, reject) => {
      const job = db.transaction('job').objectStore('job').get('current');
      job.onsuccess = () => {
        try { resolve(checkpointVersion(job.result)); } catch (error) { reject(error); }
      };
      job.onerror = () => reject(job.error);
    });
  }
  async function loadSession() {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(['job', 'parts']), job = tx.objectStore('job').get('current'), parts = tx.objectStore('parts').getAll();
      tx.oncomplete = () => resolve(job.result ? { job: job.result, parts: parts.result } : null);
      tx.onerror = () => reject(tx.error);
    });
  }
  return { beginSession, savePart, saveJob, loadCheckpointVersion, loadSession };
}

export const { beginSession, savePart, saveJob, loadCheckpointVersion, loadSession } = createSessionStore('audio-studio-recording-v1');
