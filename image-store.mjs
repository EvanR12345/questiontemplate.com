let database;
function db() {
  if (database) return database;
  const pending = new Promise((resolve, reject) => {
    const request = indexedDB.open('qt-image-studio', 1);
    request.onupgradeneeded = () => { request.result.createObjectStore('projects'); request.result.createObjectStore('images'); };
    request.onsuccess = () => {
      const connection = request.result;
      const invalidate = () => { if (database === pending) database = undefined; };
      connection.onclose = invalidate;
      connection.onversionchange = () => { invalidate(); connection.close(); };
      resolve(connection);
    };
    request.onerror = () => { if (database === pending) database = undefined; reject(request.error); };
  });
  database = pending;
  return pending;
}
async function operation(store, mode, action, retry = true) {
  const connection = await db();
  let transaction;
  try { transaction = connection.transaction(store, mode); }
  catch (error) {
    // A closing connection rejects before any read or write has started.
    if (!retry || error.name !== 'InvalidStateError') throw error;
    database = undefined;
    connection.close();
    return operation(store, mode, action, false);
  }
  return new Promise((resolve, reject) => {
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
export const saveStudioProject = project => operation('projects', 'readwrite', store => store.put(project, 'studio:' + project.id));
export const loadStudioProject = id => operation('projects', 'readonly', store => store.get('studio:' + id));
export const listStudioProjects = async () => (await operation('projects', 'readonly', store => store.getAll())).filter(p => p?.schemaVersion === 1 && p?.chapters);
