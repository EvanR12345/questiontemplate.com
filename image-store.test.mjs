import test from 'node:test';
import assert from 'node:assert/strict';

function fakeStorage() {
  const values = new Map(), connections = [];
  let opens = 0, writes = 0, failOpen = false, failWrite = false;
  globalThis.indexedDB = { open() {
    opens++;
    const request = {};
    queueMicrotask(() => {
      if (failOpen) {
        failOpen = false;
        request.error = new DOMException('Unavailable', 'UnknownError');
        request.onerror();
        return;
      }
      const connection = {
        closing: false,
        close() { this.closing = true; },
        transaction() {
          if (this.closing) throw new DOMException('Connection is closing', 'InvalidStateError');
          const transaction = {
            objectStore() { return {
              put(value, key) {
                writes++;
                const result = { result: key };
                queueMicrotask(() => {
                  if (failWrite) {
                    transaction.error = new DOMException('Full', 'QuotaExceededError');
                    transaction.onabort();
                  } else { values.set(key, value); transaction.oncomplete(); }
                });
                return result;
              },
              get(key) {
                const result = { result: values.get(key) };
                queueMicrotask(() => transaction.oncomplete());
                return result;
              },
            }; },
          };
          return transaction;
        },
      };
      connections.push(connection);
      request.result = connection;
      request.onsuccess();
    });
    return request;
  } };
  return { connections, values, get opens() { return opens; }, get writes() { return writes; },
    failNextOpen() { failOpen = true; }, failWrites() { failWrite = true; } };
}

let revision = 0;
const loadStore = () => import('./image-store.mjs?test=' + ++revision);

test('reopens a closing connection before writing and preserves saved chapters', async () => {
  const storage = fakeStorage(), store = await loadStore();
  const project = { id: 'story', chapters: [{ sourceText: 'Chapter one' }, { sourceText: 'Chapter two' }] };
  await store.saveStudioProject(project);
  storage.connections[0].closing = true;
  assert.deepEqual(await store.loadStudioProject('story'), project);
  await store.saveStudioProject({ ...project, name: 'Saved production' });
  assert.equal(storage.opens, 2);
  assert.equal(storage.writes, 2);
});

test('unexpected close and version changes invalidate the cached connection', async () => {
  const storage = fakeStorage(), store = await loadStore();
  await store.saveProjectState({ title: 'Keep me' });
  storage.connections[0].closing = true;
  storage.connections[0].onclose();
  assert.deepEqual(await store.loadProjectState(), { title: 'Keep me' });
  storage.connections[1].onversionchange();
  assert.equal(storage.connections[1].closing, true);
  assert.deepEqual(await store.loadProjectState(), { title: 'Keep me' });
  assert.equal(storage.opens, 3);
});

test('an unsuccessful open does not permanently poison browser storage', async () => {
  const storage = fakeStorage(), store = await loadStore();
  storage.failNextOpen();
  await assert.rejects(store.loadProjectState(), { name: 'UnknownError' });
  await store.saveProjectState({ title: 'Recovered' });
  assert.deepEqual(await store.loadProjectState(), { title: 'Recovered' });
  assert.equal(storage.opens, 2);
});

test('failed writes report the original error without repeating the write', async () => {
  const storage = fakeStorage(), store = await loadStore();
  storage.failWrites();
  await assert.rejects(store.saveProjectState({ title: 'Quota' }), { name: 'QuotaExceededError' });
  assert.equal(storage.writes, 1);
  assert.equal(storage.opens, 1);
});
