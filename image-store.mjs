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
    transaction.onerror = event => reject(transaction.error || event.target?.error || new Error('Browser storage request failed'));
    transaction.onabort = () => reject(transaction.error || new Error('Storage transaction canceled'));
  });
}
export const saveProjectState = state => operation('projects', 'readwrite', store => store.put(state, 'active'));
export const loadProjectState = () => operation('projects', 'readonly', store => store.get('active'));
export const saveImageBlob = (id, blob) => operation('images', 'readwrite', store => store.put(blob, id));
export const loadImageBlob = id => operation('images', 'readonly', store => store.get(id));
const projectSummary = project => ({
  id: project.id,
  name: project.name,
  revision: project.revision,
  chapters: project.chapters.length,
  unsynced: Boolean(project._unsynced),
});
export const saveStudioProject = project => operation('projects', 'readwrite', store => {
  store.put(projectSummary(project), 'summary:' + project.id);
  return store.put(project, 'studio:' + project.id);
});
export const loadStudioProject = id => operation('projects', 'readonly', store => store.get('studio:' + id));
export const listStudioProjects = async () => (await operation('projects', 'readonly', store => store.getAll())).filter(p => p?.schemaVersion === 1 && p?.chapters);


// Older databases contain only full project records. Index each missing summary
// once, reading the current project in the same transaction as the index write.
function indexLegacyProject(key) {
  return operation('projects', 'readwrite', store => {
    const result = {};
    const request = store.get(key);
    request.onsuccess = () => {
      const project = request.result;
      if (project?.schemaVersion !== 1 || !Array.isArray(project.chapters)) return;
      result.result = projectSummary(project);
      store.put(result.result, 'summary:' + project.id);
    };
    return result;
  });
}

export async function listStudioProjectSummaries() {
  const range = prefix => IDBKeyRange.bound(prefix, prefix + '\uffff');
  const [keys, summaries] = await Promise.all([
    operation('projects', 'readonly', store => store.getAllKeys(range('studio:'))),
    operation('projects', 'readonly', store => store.getAll(range('summary:'))),
  ]);
  const byId = new Map(summaries.map(summary => [summary.id, summary]));
  const result = [];
  for (const key of keys) {
    const summary = byId.get(key.slice('studio:'.length)) || await indexLegacyProject(key);
    if (summary) result.push(summary);
  }
  return result;
}
