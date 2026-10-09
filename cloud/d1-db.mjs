import { hash, open, seal } from './codec.mjs';

export class Conflict extends Error {}
const blank = () => ({ version: 1, users: {}, offset: 0, outbox: {}, commands_registered: false });
const equal = (a, b) => {
  if (a === b) return true;
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object' || Array.isArray(a) !== Array.isArray(b)) return false;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every(k => Object.hasOwn(b, k) && equal(a[k], b[k]));
};
export const validPart = name => name === 'meta' || /^[a-f0-9]{64}:(control|full|view)$/.test(name);

export class D1DB {
  constructor(binding) { this.db = binding; this.partitioned = true; }
  runtime() { return this.db.prepare('SELECT * FROM bot_configuration WHERE id=1').first(); }
  async index() {
    const rows = await this.db.prepare('SELECT c.revision, p.id FROM bot_checkpoint c LEFT JOIN bot_parts p ON p.id NOT LIKE ? WHERE c.id=1').bind('%:view').all();
    if (!rows.results.length) throw new Error('Missing checkpoint');
    return { version: rows.results[0].revision, format: 'partitioned', parts: rows.results.map(r => r.id).filter(Boolean) };
  }
  async read(version, id) {
    const row = await this.db.prepare('SELECT c.revision, p.ciphertext FROM bot_checkpoint c LEFT JOIN bot_parts p ON p.id=? WHERE c.id=1').bind(id).first();
    if (!row || row.revision !== version) throw new Conflict();
    if (!row.ciphertext) throw new Error('Missing partition');
    return { ciphertext: row.ciphertext };
  }
  async commit(version, parts, deleted = []) {
    // D1 batch is one transaction. A nonce makes EVERY write conditional on the CAS.
    const nonce = crypto.randomUUID();
    const statements = [this.db.prepare('UPDATE bot_checkpoint SET revision=revision+1, nonce=? WHERE id=1 AND revision=?').bind(nonce, version)];
    for (const [id, ciphertext] of Object.entries(parts)) {
      statements.push(this.db.prepare(`INSERT INTO bot_parts (id, ciphertext)
        SELECT ?, ? WHERE EXISTS (SELECT 1 FROM bot_checkpoint WHERE id=1 AND revision=? AND nonce=?)
        ON CONFLICT(id) DO UPDATE SET ciphertext=excluded.ciphertext`).bind(id, ciphertext, version + 1, nonce));
    }
    for (const id of deleted) statements.push(this.db.prepare('DELETE FROM bot_parts WHERE id=? AND EXISTS (SELECT 1 FROM bot_checkpoint WHERE id=1 AND revision=? AND nonce=?)').bind(id, version + 1, nonce));
    if (statements.length > 50) throw new Error('Too many changed partitions');
    const result = await this.db.batch(statements);
    return result[0].meta.changes === 1 ? version + 1 : null;
  }
  async scoped(uid, key) {
    return new D1StateStore(this, key, uid, await hash(uid));
  }
  async makeStore(config, key, body) {
    const id = body.callback_query?.from?.id ?? body.message?.from?.id;
    return this.scoped(Number.isSafeInteger(id) && id > 0 ? String(id) : '0', key);
  }
  async makeSmokeStore(config, key, ready = false) {
    const index = await this.index();
    const meta = await open((await this.read(index.version, 'meta')).ciphertext, key);
    if (ready) for (const uid of Object.keys(meta.users)) {
      const part = await open((await this.read(index.version, await hash(uid) + ':control')).ciphertext, key);
      const user = part.users[uid];
      if (user?.token && !user.disabled && !user.onboarding) return this.scoped(uid, key);
    }
    return this.scoped(Object.keys(meta.users)[0] || '0', key);
  }
}

export class D1StateStore {
  constructor(db, key, uid, scope) { this.db = db; this.key = key; this.uid = uid; this.scope = scope; }
  async load(full = false) {
    const dataPart = this.scope + (full ? ':full' : ':view');
    const rows = await this.db.db.prepare('SELECT c.revision, p.id, p.ciphertext FROM bot_checkpoint c LEFT JOIN bot_parts p ON p.id IN (?, ?, ?) WHERE c.id=1').bind('meta', this.scope + ':control', dataPart).all();
    const parts = {};
    for (const row of rows.results) if (row.id) parts[row.id] = await open(row.ciphertext, this.key);
    if (!parts.meta) throw new Error('Missing metadata');
    const state = structuredClone(parts.meta);
    const control = parts[this.scope + ':control'] || blank();
    const view = parts[dataPart] || blank();
    state.outbox = structuredClone(control.outbox);
    if (state.users[this.uid]) {
      if (!control.users[this.uid] || !view.users[this.uid]) throw new Error('Incomplete student state');
      state.users[this.uid] = { ...structuredClone(view.users[this.uid]), ...structuredClone(control.users[this.uid]) };
    }
    return { version: rows.results[0].revision, state, parts };
  }
  async change(callback, fullData = false) {
    for (let attempt = 0; attempt < 5; attempt++) {
      const { version, state, parts: previous } = await this.load(fullData);
      if (callback(state) === false) return state;
      const meta = structuredClone(state);
      meta.users = Object.fromEntries(Object.keys(state.users).map(uid => [uid, { onboarding: false }]));
      meta.outbox = {};
      const control = blank(); control.outbox = state.outbox;
      const parts = { meta, [this.scope + ':control']: control }, deleted = [];
      const user = state.users[this.uid];
      if (user) {
        control.users[this.uid] = { ...user };
        delete control.users[this.uid].tasks; delete control.users[this.uid].courses;
        const full = blank();
        full.users[this.uid] = { onboarding: false, tasks: user.tasks || {}, courses: user.courses || [] };
        // Commands edit only the small control partition. Preserve full descriptions.
        if (!equal(full, previous[this.scope + (fullData ? ':full' : ':view')])) {
          parts[this.scope + ':full'] = full;
          const view = structuredClone(full);
          for (const task of Object.values(view.users[this.uid].tasks)) {
            task.data.description = task.data.description.slice(0, 400);
            delete task.hourly_data;
          }
          parts[this.scope + ':view'] = view;
        }
      } else {
        deleted.push(this.scope + ':full', this.scope + ':view');
      }
      const changed = {};
      for (const [id, part] of Object.entries(parts)) if (!equal(part, previous[id])) {
        const ciphertext = await seal(part, this.key);
        if (ciphertext.length > 1800000) throw new Error('Partition too large');
        changed[id] = ciphertext;
      }
      if (await this.db.commit(version, changed, deleted) === version + 1) return state;
    }
    throw new Conflict();
  }
}
