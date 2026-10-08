import { nativeRequest } from './native-client.mjs?v=queue-1';
export const KEY_STORAGE = 'qt-local-helper-key';
let sessionKey = '';
export function importHelperPairing(value, storage) {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value.trim()))
    throw new Error('Choose the helper pairing file containing its 64-character key. No API key is needed here.');
  const key = value.trim();
  if (!storage && typeof localStorage !== 'undefined') storage = localStorage;
  try { storage?.setItem(KEY_STORAGE, key); } catch {}
  sessionKey = key;
  return key;
}
export function pairingKey() {
  const match = location.hash.match(/^#native=([a-f0-9]{64})$/);
  let key = match?.[1] || sessionKey;
  try { key ||= localStorage.getItem(KEY_STORAGE) || ''; if (key) localStorage.setItem(KEY_STORAGE, key); } catch {}
  if (match) history.replaceState(null, '', location.pathname + location.search);
  sessionKey = key;
  return key;
}
export async function helperJson(path, key, body) {
  return (await nativeRequest(path, key, body)).json();
}
