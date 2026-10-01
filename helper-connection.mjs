import { nativeRequest } from './native-client.mjs?v=queue-1';
export const KEY_STORAGE = 'qt-local-helper-key';
export function pairingKey() {
  const match = location.hash.match(/^#native=([a-f0-9]{64})$/);
  let key = match?.[1] || '';
  try { key ||= localStorage.getItem(KEY_STORAGE) || ''; if (key) localStorage.setItem(KEY_STORAGE, key); } catch {}
  if (match) history.replaceState(null, '', location.pathname + location.search);
  return key;
}
export async function helperJson(path, key, body) {
  return (await nativeRequest(path, key, body)).json();
}
