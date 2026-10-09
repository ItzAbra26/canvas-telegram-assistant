import { open, seal, validateState } from './codec.mjs';
import { actionable, ignored, lost, pack, render } from './commands.mjs';
import { Actions, normalizeUpdate, menu } from './interactions.mjs';

export class StateStore {
  constructor(db, key) { this.db = db; this.key = key; }
  async load() {
    const row = await this.db.load();
    if (!row || !Number.isSafeInteger(row.version) || typeof row.ciphertext !== 'string') throw new Error('Missing state');
    return { version: row.version, state: validateState(await open(row.ciphertext, this.key)) };
  }
  async change(callback) {
    for (let attempt = 0; attempt < 5; attempt++) {
      const { state, version } = await this.load();
      const changed = callback(state);
      if (changed === false) return state;
      const ciphertext = await seal(validateState(state), this.key);
      if (await this.db.save(version, ciphertext) === version + 1) return state;
    }
    throw new Error('Concurrent checkpoint');
  }
}
export class TelegramError extends Error {
  constructor(status = 0, retry = 0, ambiguous = false) { super('Telegram unavailable'); this.status = status; this.retry = retry; this.ambiguous = ambiguous; }
}
export class Telegram {
  constructor(token, fetcher = fetch) { this.root = `https://api.telegram.org/bot${token}/`; this.fetcher = (url, init) => fetcher(url, init); }
  async call(method, payload) {
    let response, body;
    try {
      response = await this.fetcher(this.root + method, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), redirect: 'manual', signal: AbortSignal.timeout(15000) });
      body = await response.json();
    } catch { throw new TelegramError(0, 0, method === 'sendMessage'); }
    if (!body?.ok || !('result' in body)) {
      const code = Number(body?.error_code || response.status);
      const error = new TelegramError(code, Number(body?.parameters?.retry_after || 0), method === 'sendMessage' && (code >= 500 || response.status >= 500 || (response.status >= 300 && response.status < 400)));
      error.operation = method;
      const description = String(body?.description || '').toLowerCase();
      error.reason = description.includes('resolve') || description.includes('host') ? 'dns' : description.includes('commands') ? 'command-list' : description.includes('secret') ? 'webhook-secret' : description.includes('webhook') ? 'webhook-url' : 'api-rejected';
      error.diagnostic = ['failed to resolve host', 'name or service not known', 'failed to get host ip address', 'wrong ip address', 'ssl error', 'certificate verify failed', 'connection refused'].find(value => description.includes(value)) || null;
      throw error;
    }
    return body.result;
  }
  async send(chat, text, markup) {
    const result = await this.call('sendMessage', { chat_id: chat, text, parse_mode: 'HTML', link_preview_options: { is_disabled: true }, reply_markup: markup });
    if (!Number.isSafeInteger(result?.message_id)) throw new TelegramError(0, 0, true);
    return result.message_id;
  }
  async remove(chat, message) {
    try { return await this.call('deleteMessage', { chat_id: chat, message_id: message }) === true; } catch { return false; }
  }
  async answer(id) {
    try { await this.call('answerCallbackQuery', { callback_query_id: id }); } catch { /* An expired button must not prevent a normal response. */ }
  }
  async download(document, signal) {
    const info = await this.call('getFile', { file_id: document.file_id });
    const path = info?.file_path;
    if (typeof path !== 'string' || !/^[A-Za-z0-9_/-]+\.[A-Za-z0-9]+$/.test(path) || path.includes('..') || path.startsWith('/') || (info.file_size != null && info.file_size !== document.size)) throw new TelegramError();
    let response;
    try { response = await this.fetcher(this.root.replace('/bot', '/file/bot') + path, { redirect: 'manual', signal: AbortSignal.any([AbortSignal.timeout(15000), signal]) }); }
    catch { throw new TelegramError(); }
    if (!response.ok || (Number(response.headers.get('content-length')) > document.size)) throw new TelegramError();
    const reader = response.body.getReader(), chunks = []; let size = 0;
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      size += value.length;
      if (size > document.size) { await reader.cancel(); throw new TelegramError(); }
      chunks.push(value);
    }
    if (size !== document.size) throw new TelegramError();
    return new Blob(chunks, { type: document.mime });
  }
}
const enqueue = (state, id, chat, text, markup = menu()) => { state.outbox[id] ||= { status: 'pending', chat_id: chat, text, reply_markup: markup }; };
const pages = (state, id, chat, blocks) => pack(blocks).forEach((text, index) => enqueue(state, `${id}:${index}`, chat, text));
function forget(state, uid) {
  delete state.users[uid];
  for (const [key, event] of Object.entries(state.outbox)) if (event.chat_id === Number(uid)) delete state.outbox[key];
}
export class ImmediateBot {
  constructor(config, store, telegram, canvasFactory, messages) {
    this.config = config; this.store = store; this.telegram = telegram; this.canvasFactory = canvasFactory; this.messages = messages;
  }
  async dispatch(prefix) {
    const { state } = await this.store.load();
    const ids = Object.keys(state.outbox).filter(id => id === prefix || id.startsWith(prefix + ':'));
    for (const id of ids) {
      let event;
      await this.store.change(current => {
        event = null; // A failed CAS may retry after another process sent this event.
        const row = current.outbox[id];
        if (!row || row.status !== 'pending' || (row.not_before && Date.parse(row.not_before) > Date.now())) return false;
        const match = id.match(/^u(\d+):task:(\d+:\d+):/), record = match && current.users[match[1]]?.tasks?.[match[2]];
        if (record && (record.ignored || ignored(record.data))) { row.status = 'cancelled'; delete row.text; return; }
        row.status = 'sending'; row.started_at = new Date().toISOString(); event = structuredClone(row);
      });
      if (!event) continue;
      let result;
      try { result = { status: 'sent', message_id: await this.telegram.send(event.chat_id, event.text, event.reply_markup), sent_at: new Date().toISOString() }; }
      catch (error) {
        if (!(error instanceof TelegramError)) throw error;
        result = error.ambiguous ? { status: 'uncertain' } : [400, 403].includes(error.status) ? { status: 'cancelled' } : { status: 'pending', not_before: new Date(Date.now() + Math.max(60, error.retry) * 1000).toISOString() };
      }
      await this.store.change(current => {
        if (!current.outbox[id]) return false;
        Object.assign(current.outbox[id], result);
        if (result.status !== 'pending') delete current.outbox[id].text;
      });
    }
  }
  async receive(update, now = new Date().toISOString()) {
    if (!Number.isSafeInteger(update?.update_id)) throw new Error('Invalid update');
    const message = normalizeUpdate(update), chat = message.chat || {}, sender = message.from || {};
    const uid = String(sender.id);
    const allowed = chat.type === 'private' && Number.isSafeInteger(sender.id) && sender.id > 0 && chat.id === sender.id && !sender.is_bot && (!this.config.allowed_users?.length || this.config.allowed_users.includes(sender.id));
    const prefix = `update:${update.update_id}`;
    if (allowed && update.callback_query?.id) await this.telegram.answer?.(update.callback_query.id);
    const { state: previous } = await this.store.load();
    if (update.update_id < (this.config.polling_cutoff || 0) || previous.webhook_updates?.[update.update_id]) { await this.dispatch(prefix); return null; }
    const text = typeof message.text === 'string' ? message.text.trim() : '';
    const command = text.startsWith('/') ? text.split(/\s/, 1)[0].split('@', 1)[0].toLowerCase() : '';
    if (allowed && ['/actualizar', '/entregar', '/tarea', '/confirmar', '/cancelar_entrega'].includes(command) || allowed && message.document) {
      return new Actions(this).receive(update, message, now);
    }
    let deleted = false, profile = null, profileError = { status: 0, reason: 'network' };
    if (allowed && (message.forward_origin || (text && !command))) deleted = await this.telegram.remove(sender.id, message.message_id);
    if (allowed && text && !command && !message.forward_origin && previous.users[uid]?.onboarding && this.config.test_mode && text.length >= 16 && text.length <= 512 && /^[\x21-\x7e]+$/.test(text)) {
      try { profile = await this.canvasFactory(text).profile(); } catch (error) {
        profileError = { status: Number.isSafeInteger(error.status) ? error.status : 0, reason: ['network', 'unauthorized', 'forbidden', 'challenge', 'html-response', 'scope', 'invalid-token', 'http'].includes(error.reason) ? error.reason : 'network' };
      }
    }
    let connected = null;
    await this.store.change(state => {
      connected = null;
      if (state.webhook_updates?.[update.update_id]) return false;
      state.webhook_updates ||= {};
      state.webhook_updates[update.update_id] = now;
      for (const [id, seenAt] of Object.entries(state.webhook_updates)) if (Date.parse(seenAt) < Date.parse(now) - 3 * 86400000) delete state.webhook_updates[id];
      state.offset = Math.max(state.offset, update.update_id + 1);
      if (!allowed) return;
      let user = state.users[uid] || {};
      const reply = value => enqueue(state, prefix, sender.id, value);
      if (message.forward_origin) { reply('No acepto tokens reenviados. Usa /start y envía tu propio token directamente.'); return; }
      if (command === '/start') {
        if (!this.config.test_mode) { reply('El registro con tokens manuales está desactivado.'); return; }
        if (this.config.invite_code && text.slice(text.indexOf(' ') + 1) !== this.config.invite_code) { reply('Para entrar en la práctica usa /start seguido del código de clase que te ha dado el profesor.'); return; }
        if (!state.users[uid] && Object.keys(state.users).length >= this.config.max_users) { reply('La práctica ha alcanzado el límite de usuarios. Consulta al profesor.'); return; }
        user = state.users[uid] ||= { onboarding: false, tasks: {}, courses: [] };
        Object.assign(user, { onboarding: true, onboarding_at: now });
        reply(this.messages.start);
      } else if (command === '/cancelar') {
        if (user.delivery?.stage === 'processing') { reply('Tu entrega ya está en curso. No puedo anular una solicitud enviada a Canvas; revisa el resultado allí.'); return; }
        if (state.users[uid]) { user.onboarding = false; if (user.delivery?.stage !== 'processing') delete user.delivery; if (!user.token) delete state.users[uid]; }
        reply('Registro o entrega pendiente cancelados. Puedes volver con /start o /entregar.');
      } else if (command === '/desconectar') {
        forget(state, uid); reply('🔌 Conexión y datos activos borrados. Revoca tu token también en Canvas. Las copias anteriores pueden seguir en su historial; consulta /privacidad.');
      } else if (command === '/id') {
        reply(`Tu identificador de Telegram es <code>${sender.id}</code>. Solo se muestra en este chat privado.`);
      } else if (command && command.slice(1) in this.messages.commands) {
        user.uncertain_count = Object.values(state.outbox).filter(event => event.chat_id === sender.id && event.status === 'uncertain').length;
        const argument = text.split(/\s+/)[1];
        const page = /^\d{1,5}$/.test(argument || '') ? Number(argument) : 1;
        pages(state, prefix, sender.id, render(command.slice(1), user, this.config, now, this.messages, page));
      } else if (command) reply('No reconozco ese comando. Usa /ayuda.');
      else if (text) {
        if (!user.onboarding || !this.config.test_mode) { reply('Para conectar o cambiar tu cuenta usa /start. No envíes credenciales fuera del registro.'); return; }
        if (text.length < 16 || text.length > 512 || !/^[\x21-\x7e]+$/.test(text)) { reply('No parece un token. Copia solo el token de Canvas, sin espacios ni contraseña, o usa /cancelar.'); return; }
        if (profile === null) {
          user.registration_error = { ...profileError, at: now };
          const reason = profileError.status === 401 ? 'Canvas no ha aceptado este token (HTTP 401). Copia el valor completo que aparece al crearlo en medac.instructure.com, no su nombre ni un valor oculto.'
            : profileError.status === 403 && ['challenge', 'html-response'].includes(profileError.reason) ? 'Canvas ha bloqueado la conexión del servidor (HTTP 403). Esto no confirma que tu token esté caducado.'
            : profileError.status === 403 ? 'Canvas ha denegado el acceso (HTTP 403). Puede faltar permiso del centro; no significa que el token esté caducado.'
            : profileError.status === 429 ? 'Canvas está limitando las consultas. Espera un momento antes de reintentarlo.'
            : 'Canvas no está disponible o no ha devuelto un perfil válido.';
          reply(reason + ' Vuelve a enviar el token para reintentarlo, o /cancelar.');
          if (!deleted) enqueue(state, prefix + ':delete', sender.id, '⚠️ No pude borrar el mensaje con tu token. Bórralo tú desde Telegram.');
          return;
        }
        if (user.canvas_user_id != null && user.canvas_user_id !== profile) {
          forget(state, uid); user = state.users[uid] = { onboarding: false, tasks: {}, courses: [] };
        }
        Object.assign(user, { token: text, canvas_user_id: profile, onboarding: false, disabled: false, sync_error: null, connected_at: now });
        delete user.registration_error;
        reply('✅ <b>Cuenta conectada</b>\n' + (deleted ? '🔒 He borrado el mensaje que contenía el token.' : '⚠️ No pude borrar el mensaje con tu token: bórralo tú desde Telegram.') + '\nTus tareas y avisos llegarán solo a este chat. Estoy preparando la primera carga; después se actualizarán cada hora.');
        if (!user.initialized) connected = { uid, token: text, connected_at: now };
      } else reply('Envía el token como texto durante /start, o consulta /ayuda.');
    });
    await this.dispatch(prefix);
    return connected;
  }
  async firstSync(connection) {
    const { uid, token, connected_at } = connection;
    let snapshot;
    try { snapshot = await this.canvasFactory(token).snapshot(); }
    catch (error) {
      await this.store.change(state => {
        const user = state.users[uid];
        if (!user || user.token !== token || user.connected_at !== connected_at || user.initialized) return false;
        user.sync_error = error.status || 'network';
        enqueue(state, `u${uid}:first-load:${connected_at}`, Number(uid), '⚠️ No pude completar la primera carga. Tu conexión se conserva y volveré a intentarlo en la revisión horaria.');
      });
      await this.dispatch(`u${uid}:first-load:${connected_at}`); return;
    }
    const now = new Date().toISOString();
    await this.store.change(state => {
      const user = state.users[uid];
      if (!user || user.token !== token || user.connected_at !== connected_at || user.initialized) return false;
      const tasks = {};
      for (const assignment of snapshot.assignments) {
        if (ignored(assignment)) continue;
        const remaining = assignment.due_at ? (Date.parse(assignment.due_at) - Date.parse(now)) / 1000 : null;
        const reminders = {};
        for (const [label, seconds] of [['7d', 604800], ['3d', 259200], ['24h', 86400], ['3h', 10800]]) if (remaining !== null && remaining <= seconds) reminders[label] = 'skipped_initial';
        tasks[`${assignment.course_id}:${assignment.id}`] = { data: assignment, revision: 0, first_seen_at: now, reminders, remaining, deleted: false, missing_count: 0 };
      }
      Object.assign(user, { courses: snapshot.courses, tasks, initialized: true, last_sync: now, sync_error: null });
      enqueue(state, `u${uid}:baseline`, Number(uid), `📚 <b>Primera sincronización completada</b>\n${snapshot.courses.length} asignaturas · ${snapshot.assignments.filter(a => actionable(a, now)).length} tareas pendientes · ${snapshot.assignments.filter(a => lost(a, now)).length} perdidas.\nHe guardado las tareas existentes. Desde ahora avisaré de las nuevas y sus cambios. Usa /resumen.`);
    });
    await this.dispatch(`u${uid}:baseline`);
  }
  async runJob(connection) {
    if (connection.kind === 'refresh') return new Actions(this).refresh(connection);
    if (connection.kind === 'submit') return new Actions(this).submit(connection);
    return this.firstSync(connection);
  }
}
