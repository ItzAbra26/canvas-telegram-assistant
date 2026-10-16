import { escape } from './commands.mjs';

// Eight uploads leave room for validation and Telegram calls within Workers Free's 50 fetches.
export const MAX_FILES = 8;
export const MAX_BATCH_BYTES = 20 * 1024 * 1024;
export const MAX_BATCH_INPUT = 50 * 1024 * 1024;
export const deliveryEntries = session => session.files || (session.file ? [{ file: session.file, original_file: session.original_file, optimization: session.optimization }] : []);
export const deliveryFiles = session => deliveryEntries(session).map(entry => entry.file);
export const preparing = session => deliveryEntries(session).some(entry => entry.optimization);
export const fits = session => {
  const files = deliveryFiles(session);
  return files.length > 0 && files.length <= MAX_FILES && files.every(f => f.size > 0 && f.size <= 10 * 1024 * 1024) && files.reduce((n, f) => n + f.size, 0) <= MAX_BATCH_BYTES;
};
export function migrateFiles(session) {
  if (!session.files) {
    session.files = deliveryEntries(session).map(entry => ({ ...entry, id: crypto.randomUUID().replaceAll('-', '') }));
    session.batch_id = crypto.randomUUID().replaceAll('-', '');
  }
  delete session.file; delete session.original_file; delete session.optimization;
  return session.files;
}
export const fileList = session => deliveryEntries(session).map((entry, i) => `${i + 1}. 📎 ${escape(entry.file.name, 100)} · ${(entry.file.size / 1048576).toFixed(2)} MB${entry.optimization ? ' · 🗜️ preparando' : ''}`).join('\n');
export function deliveryButtons(session) {
  const rows = [];
  if (!preparing(session) && fits(session)) rows.push([{ text: `✅ Entregar ahora (${deliveryFiles(session).length})`, callback_data: `confirm:${session.nonce}` }]);
  const originals = deliveryEntries(session).map(entry => entry.original_file || entry.file);
  if (deliveryEntries(session).some(entry => entry.original_file) && originals.every(f => f.size <= 10 * 1024 * 1024) && originals.reduce((n, f) => n + f.size, 0) <= MAX_BATCH_BYTES) rows.push([{text: '📎 Usar originales', callback_data: `original:${session.nonce}`}]);
  for (const [i, entry] of deliveryEntries(session).entries()) rows.push([{ text: `🗑️ Quitar ${i + 1}: ${entry.file.name}`.slice(0, 60), callback_data: `remove:${session.nonce}:${i}` }]);
  rows.push([{ text: '❌ Cancelar', callback_data: `cancel:${session.nonce}` }]);
  return { inline_keyboard: rows };
}
