let database;
function db() {
  return database ||= new Promise((resolve, reject) => {
    const request = indexedDB.open('qt-image-studio', 1);
    request.onupgradeneeded = () => { request.result.createObjectStore('projects'); request.result.createObjectStore('images'); };
    request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
  });
}
async function operation(store, mode, action) {
  const database = await db();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(store, mode);
    const request = action(transaction.objectStore(store));
    transaction.oncomplete = () => resolve(request.result);
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error || new Error('Storage transaction canceled'));
  });
}
export const saveProjectState = state => operation('projects', 'readwrite', store => store.put(state, 'active'));
export const loadProjectState = () => operation('projects', 'readonly', store => store.get('active'));
export const saveImageBlob = (id, blob) => operation('images', 'readwrite', store => store.put(blob, id));
export const loadImageBlob = id => operation('images', 'readonly', store => store.get(id));
