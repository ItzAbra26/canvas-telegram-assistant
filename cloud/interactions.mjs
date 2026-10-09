import { dateLabel, escape, pack, render } from './commands.mjs';
import { CanvasError } from './canvas.mjs';

export const MAX_FILE_BYTES = 10 * 1024 * 1024;
const nonce = () => crypto.randomUUID().replaceAll('-', '');
const sameAccount = (user, job) => user?.token === job.token && user.connected_at === job.connected_at && !user.disabled && (!user.onboarding || job.kind === 'refresh');
const activeTasks = user => {
  const courses = new Set((user.courses || []).map(c => c.id));
  return Object.values(user.tasks || {}).filter(r => !r.deleted && courses.has(r.data.course_id) && r.data.requires_submission)
    .map(r => r.data).sort((a, b) => Number(a.submitted) - Number(b.submitted) || (Date.parse(a.due_at) || Infinity) - (Date.parse(b.due_at) || Infinity));
};
export function menu() {
  return { inline_keyboard: [
    [{ text: '📚 Resumen', callback_data: 'cmd:resumen:1' }, { text: '📝 Pendientes', callback_data: 'cmd:pendientes:1' }],
    [{ text: '📅 Hoy', callback_data: 'cmd:hoy:1' }, { text: '📆 Esta semana', callback_data: 'cmd:semana:1' }],
    [{ text: '🔄 Actualizar', callback_data: 'cmd:actualizar:1' }, { text: '📤 Entregar tarea', callback_data: 'list:1' }],
    [{ text: '📚 Asignaturas', callback_data: 'cmd:asignaturas:1' }, { text: '❓ Ayuda', callback_data: 'cmd:ayuda:1' }],
  ] };
}
export function normalizeUpdate(update) {
  const cb = update.callback_query;
  if (!cb) return update.message || {};
  const value = typeof cb.data === 'string' ? cb.data : '';
  let text = '/boton_desconocido', match;
  if ((match = value.match(/^cmd:(resumen|pendientes|hoy|manana|semana|atrasadas|ultimas|asignaturas|ayuda|estado|actualizar):([1-9]\d{0,4})$/))) text = `/${match[1]} ${match[2]}`;
  else if ((match = value.match(/^list:([1-9]\d{0,4})$/))) text = `/entregar ${match[1]}`;
  else if ((match = value.match(/^task:(\d{1,16}):(\d{1,16})$/))) text = `/tarea ${match[1]}:${match[2]}`;
  else if ((match = value.match(/^(confirm|cancel):([a-f0-9]{32})$/))) text = `/${match[1] === 'confirm' ? 'confirmar' : 'cancelar_entrega'} ${match[2]}`;
  return { chat: cb.message?.chat, from: cb.from, message_id: cb.message?.message_id, text };
}
function queue(state, prefix, uid, blocks, markup = menu()) {
  for (const [index, text] of pack(Array.isArray(blocks) ? blocks : [blocks]).entries()) state.outbox[`${prefix}:${index}`] ||= { status: 'pending', chat_id: Number(uid), text, reply_markup: markup };
}
function fileInfo(raw) {
  if (typeof raw?.file_id !== 'string' || raw.file_id.length > 256 || !Number.isSafeInteger(raw.file_size) || raw.file_size <= 0 || raw.file_size > MAX_FILE_BYTES) return null;
  if (typeof raw.file_name !== 'string' || raw.file_name.length > 200 || /[\x00-\x1f\x7f/\\]/.test(raw.file_name) || !raw.file_name.includes('.')) return null;
  return { file_id: raw.file_id, name: raw.file_name, size: raw.file_size, mime: typeof raw.mime_type === 'string' && /^[\w.+-]+\/[\w.+-]+$/.test(raw.mime_type) ? raw.mime_type : 'application/octet-stream' };
}
function validateAssignment(raw, file = null) {
  if (!raw || !Number.isSafeInteger(raw.id) || typeof raw.name !== 'string' || !Array.isArray(raw.submission_types) || !raw.submission_types.includes('online_upload')) throw new CanvasError(400, 'unsupported');
  if (raw.locked_for_user === true || raw.published === false || raw.submission?.excused) throw new CanvasError(403, 'locked');
  if (file && raw.allowed_extensions?.length && !raw.allowed_extensions.map(x => String(x).toLowerCase().replace(/^\./, '')).includes(file.name.split('.').at(-1).toLowerCase())) throw new CanvasError(400, 'extension');
}
const errorText = error => error.reason === 'unsupported' ? 'Esta tarea no admite archivos. Ábrela en Canvas para entregarla con el método indicado.'
  : error.reason === 'locked' ? 'Canvas indica que la tarea está cerrada o no disponible para entregar.'
  : error.reason === 'extension' ? 'Ese formato no está permitido para esta tarea. Revisa los formatos admitidos.'
  : error.status === 401 ? 'Canvas no acepta tu token. Vuelve a conectar con /start.'
  : error.status === 403 ? 'Canvas ha denegado el acceso. Comprueba los permisos o abre la tarea en Canvas.'
  : 'No pude consultar Canvas. Conserva el archivo y vuelve a intentarlo más tarde.';

export class Actions {
  constructor(bot) { this.bot = bot; this.store = bot.store; this.config = bot.config; }
  async receive(update, message, now) {
    const uid = String(message.from.id), prefix = `update:${update.update_id}`;
    const { state: previous } = await this.store.load();
    const prior = previous.users[uid] || {};
    const [rawCommand, argument] = (typeof message.text === 'string' ? message.text : '').split(/\s+/);
    const command = rawCommand.toLowerCase().split('@')[0];
    let assignment = null, selectionError = null;
    const key = /^\d{1,16}:\d{1,16}$/.test(argument || '') ? argument : null;
    if (this.config.test_mode && command === '/tarea' && key && prior.token && !prior.disabled && !prior.onboarding && prior.tasks?.[key] && !prior.tasks[key].deleted && (prior.courses || []).some(c => c.id === prior.tasks[key].data.course_id) && prior.delivery?.stage !== 'processing') {
      try { assignment = await this.bot.canvasFactory(prior.token).assignment(...key.split(':').map(Number)); validateAssignment(assignment); }
      catch (error) { assignment = null; selectionError = error; }
    }
    let job = null;
    await this.store.change(state => {
      job = null;
      if (state.webhook_updates?.[update.update_id]) return false;
      state.webhook_updates ||= {}; state.webhook_updates[update.update_id] = now;
      for (const [id, seen] of Object.entries(state.webhook_updates)) if (Date.parse(seen) < Date.parse(now) - 3 * 86400000) delete state.webhook_updates[id];
      state.offset = Math.max(state.offset, update.update_id + 1);
      const user = state.users[uid];
      const reply = (text, markup) => queue(state, prefix, uid, text, markup);
      if (!this.config.test_mode) { reply('Las consultas y entregas de esta práctica están desactivadas.'); return; }
      if (!user?.token || user.disabled || user.onboarding && command !== '/actualizar') { reply('Primero termina la conexión de tu cuenta con /start. Si ya tenías una cuenta y solo quieres salir del registro, usa /cancelar.'); return; }
      if (message.forward_origin) { reply('Envía tu archivo directamente en este chat privado, sin reenviarlo.'); return; }
      if (command === '/actualizar') {
        if (user.delivery?.stage === 'processing') { reply('Espera a que termine la entrega antes de actualizar.'); return; }
        if (user.refresh?.stage === 'running' && Date.parse(now) - Date.parse(user.refresh.at) < 120000) { reply('🔄 Ya estoy actualizando tus tareas. Te avisaré al terminar.'); return; }
        if (user.refresh?.at && Date.parse(now) - Date.parse(user.refresh.at) < 60000) { reply('Espera un minuto entre actualizaciones para no saturar Canvas.'); return; }
        user.refresh = { stage: 'running', at: now, nonce: nonce() };
        job = { kind: 'refresh', uid, token: user.token, connected_at: user.connected_at, nonce: user.refresh.nonce };
        reply('🔄 Estoy consultando Canvas. Te enviaré el resumen actualizado al terminar.'); return;
      }
      if (command === '/cancelar_entrega') {
        if (!user.delivery || user.delivery.nonce !== argument) { reply('Ese botón ya no corresponde a tu entrega actual.'); return; }
        if (user.delivery.stage === 'processing') { reply('La entrega ya está en curso. No puedo anular una solicitud enviada a Canvas; comprueba el resultado allí.'); return; }
        if (user.delivery.stage === 'sent') { reply('Esa entrega ya fue confirmada por Canvas. Este botón no puede anularla.'); return; }
        delete user.delivery; reply('Entrega pendiente cancelada. No se ha enviado ninguna nueva solicitud de entrega.'); return;
      }
      if (user.delivery?.stage === 'processing') { reply('📤 Tu entrega está en curso. Si pasan dos minutos sin confirmación, comprueba la tarea en Canvas antes de volver a entregarla.'); return; }
      if (command === '/entregar') {
        const tasks = activeTasks(user), total = Math.max(1, Math.ceil(tasks.length / 8));
        const page = Math.min(total, Math.max(1, Number(argument) || 1));
        const rows = tasks.slice((page - 1) * 8, page * 8).map(a => [{ text: `${a.submitted ? '✅' : '📝'} ${a.course_name} · ${a.name}`.slice(0, 100), callback_data: `task:${a.course_id}:${a.id}` }]);
        const nav = [];
        if (page > 1) nav.push({ text: '⬅️ Anterior', callback_data: `list:${page - 1}` });
        if (page < total) nav.push({ text: 'Siguiente ➡️', callback_data: `list:${page + 1}` });
        if (nav.length) rows.push(nav);
        rows.push([{ text: '📚 Resumen', callback_data: 'cmd:resumen:1' }]);
        reply(tasks.length ? `📤 <b>Selecciona una tarea</b>\nPágina ${page} de ${total}. Primero aparecen las pendientes.\nCanvas comprobará si admite archivos al seleccionarla.` : 'No hay tareas con entrega en tus cursos activos. Prueba /actualizar.', { inline_keyboard: rows }); return;
      }
      if (command === '/tarea') {
        if (!sameAccount(user, { token: prior.token, connected_at: prior.connected_at }) || !assignment || !user.tasks?.[key] || user.tasks[key].deleted || !(user.courses || []).some(c => c.id === user.tasks[key].data.course_id)) { reply(selectionError ? errorText(selectionError) : 'No encuentro esa tarea en tu cuenta. Vuelve a seleccionarla con /entregar.'); return; }
        const data = user.tasks[key].data;
        user.delivery = { nonce: nonce(), key, stage: 'await_file', expires_at: new Date(Date.parse(now) + 15 * 60000).toISOString(), connected_at: user.connected_at, name: assignment.name, course_name: data.course_name, due_at: assignment.due_at || null,
          allowed_extensions: assignment.allowed_extensions || [], group: Boolean(assignment.group_category_id), attempt: assignment.submission?.attempt ?? null, submitted_at: assignment.submission?.submitted_at ?? null };
        const extensions = user.delivery.allowed_extensions;
        reply(`📤 <b>${escape(assignment.name, 180)}</b>\n📚 ${escape(data.course_name, 120)}\n📅 ${dateLabel(assignment.due_at, this.config.timezone)}\n\nEnvía <b>un archivo como documento</b>, de hasta 10 MB.${extensions.length ? '\nFormatos: ' + escape(extensions.join(', '), 150) : ''}\nNo se entregará hasta que lo confirmes. La selección caduca en 15 minutos.`, { inline_keyboard: [[{ text: '❌ Cancelar', callback_data: `cancel:${user.delivery.nonce}` }]] }); return;
      }
      const session = user.delivery;
      if (!session || Date.parse(session.expires_at) <= Date.parse(now) || session.connected_at !== user.connected_at) { reply('Selecciona primero una tarea con /entregar. Si la selección ha caducado, vuelve a elegirla.'); return; }
      if (message.document) {
        if (!['await_file', 'ready'].includes(session.stage)) { reply('Vuelve a elegir la tarea con /entregar antes de enviar otro archivo. Si el resultado anterior era incierto, compruébalo primero en Canvas.'); return; }
        const file = fileInfo(message.document);
        if (!file) { reply('Envía el archivo como documento, con nombre y extensión, de hasta 10 MB. Para varios archivos, usa un ZIP si la tarea lo permite.'); return; }
        if (session.allowed_extensions.length && !session.allowed_extensions.map(x => String(x).toLowerCase().replace(/^\./, '')).includes(file.name.split('.').at(-1).toLowerCase())) { reply('Este formato no está permitido. Formatos admitidos: ' + escape(session.allowed_extensions.join(', '), 150)); return; }
        session.file = file; session.stage = 'ready'; session.nonce = nonce();
        const warnings = (session.submitted_at || session.attempt ? '\n⚠️ Ya existe una entrega: crearás un nuevo intento.' : '') + (session.group ? '\n👥 Es una tarea de grupo: la entrega puede afectar a sus miembros.' : '') + (session.due_at && Date.parse(session.due_at) < Date.parse(now) ? '\n⏰ El plazo ha vencido: Canvas puede registrarla como entrega tardía.' : '');
        reply(`📤 <b>Revisa antes de entregar</b>\n📚 ${escape(session.course_name, 120)}\n📝 ${escape(session.name, 180)}\n📎 ${escape(file.name, 220)} · ${(file.size / 1024 / 1024).toFixed(2)} MB${warnings}\n\nAl pulsar <b>Entregar ahora</b>, autorizas a subir este archivo y registrar la entrega en Canvas con tu cuenta.`, { inline_keyboard: [[{ text: '✅ Entregar ahora', callback_data: `confirm:${session.nonce}` }, { text: '❌ Cancelar', callback_data: `cancel:${session.nonce}` }]] }); return;
      }
      if (command === '/confirmar') {
        if (user.refresh?.stage === 'running' && Date.parse(now) - Date.parse(user.refresh.at) < 120000) { reply('Espera a que termine la actualización y vuelve a pulsar Entregar ahora.'); return; }
        if (session.nonce !== argument || session.stage !== 'ready' || !session.file) { reply('Ese botón ha caducado o corresponde a otro archivo. Selecciona de nuevo la tarea.'); return; }
        session.stage = 'processing'; session.started_at = now;
        job = { kind: 'submit', uid, nonce: session.nonce, token: user.token, connected_at: user.connected_at, canvas_user_id: user.canvas_user_id, session: structuredClone(session) };
        reply('📤 Estoy subiendo el archivo y registrando la entrega. Espera la confirmación de Canvas; no vuelvas a pulsar el botón.'); return;
      }
      reply('Selecciona una tarea con /entregar o usa /actualizar.');
    });
    await this.bot.dispatch(prefix);
    return job;
  }
  async refresh(job) {
    const { state: checkpoint } = await this.store.load();
    const owner = checkpoint.users[job.uid];
    if (!sameAccount(owner, job) || owner.refresh?.nonce !== job.nonce || owner.refresh.stage !== 'running') return;
    const prefix = `u${job.uid}:refresh:${job.nonce}`;
    let snapshot, error;
    try { snapshot = await this.bot.canvasFactory(job.token).snapshot(); } catch (failure) { error = failure; }
    const now = new Date().toISOString();
    await this.store.change(state => {
      const user = state.users[job.uid];
      if (!sameAccount(user, job) || user.refresh?.nonce !== job.nonce || user.refresh.stage !== 'running') return false;
      user.refresh.stage = error ? 'failed' : 'done';
      if (error) { user.sync_error = error.status || 'network'; queue(state, prefix, job.uid, '⚠️ No pude terminar la actualización. Tus datos anteriores se conservan. ' + errorText(error)); return; }
      const baseline = !user.initialized;
      for (const a of snapshot.assignments) {
        const key = `${a.course_id}:${a.id}`, record = user.tasks[key];
        if (record) {
          record.hourly_data ||= structuredClone(record.data);
          record.data = a; record.deleted = false;
        } else {
          const remaining = a.due_at ? (Date.parse(a.due_at) - Date.parse(now)) / 1000 : null;
          const reminders = {};
          if (baseline) for (const [label, seconds] of [['7d',604800],['3d',259200],['24h',86400],['3h',10800]]) if (remaining !== null && remaining <= seconds) reminders[label] = 'skipped_initial';
          user.tasks[key] = { data: a, revision: 0, first_seen_at: now, reminders, remaining, deleted: false, missing_count: 0, manual_unnotified: !baseline };
        }
      }
      Object.assign(user, { courses: snapshot.courses, initialized: true, last_sync: now, sync_error: null });
      queue(state, prefix, job.uid, ['✅ <b>Canvas actualizado</b>', ...render('resumen', user, this.config, now, this.bot.messages)]);
    }, true);
    await this.bot.dispatch(prefix);
  }
  async submit(job) {
    const { state: checkpoint } = await this.store.load();
    const owner = checkpoint.users[job.uid];
    if (!sameAccount(owner, job) || owner.delivery?.nonce !== job.nonce || owner.delivery.stage !== 'processing' || owner.delivery.submission_started) return;
    const prefix = `u${job.uid}:delivery:${job.nonce}`;
    const signal = AbortSignal.timeout(22000), client = this.bot.canvasFactory(job.token);
    client.signal = signal;
    let result, failure, submitStarted = false;
    try {
      const [course, assignmentId] = job.session.key.split(':').map(Number);
      const current = await client.assignment(course, assignmentId);
      validateAssignment(current, job.session.file);
      if (current.name !== job.session.name || (current.due_at || null) !== job.session.due_at || Boolean(current.group_category_id) !== job.session.group || (current.submission?.attempt ?? null) !== job.session.attempt || (current.submission?.submitted_at ?? null) !== job.session.submitted_at) throw new CanvasError(409, 'changed');
      if (job.session.due_at && Date.parse(job.session.due_at) >= Date.parse(job.session.started_at) && Date.parse(job.session.due_at) < Date.now()) throw new CanvasError(409, 'changed');
      const blob = await this.bot.telegram.download(job.session.file, signal);
      const fileId = await client.upload(course, assignmentId, job.session.file, blob);
      // Persist intent BEFORE the only submission POST. Retries never repeat it.
      let permitted = false;
      await this.store.change(state => {
        permitted = false;
        const user = state.users[job.uid], session = user?.delivery;
        if (!sameAccount(user, job) || session?.nonce !== job.nonce || session.stage !== 'processing' || session.submission_started) return false;
        session.submission_started = true; permitted = true;
      });
      if (!permitted) throw new CanvasError(409, 'cancelled');
      submitStarted = true;
      result = await client.submitFile(course, assignmentId, fileId, job.canvas_user_id);
    } catch (error) { failure = error; }
    await this.store.change(state => {
      const user = state.users[job.uid], session = user?.delivery;
      if (!sameAccount(user, job) || session?.nonce !== job.nonce) return false;
      const uncertain = Boolean(failure && submitStarted && (!failure.status || failure.status >= 500 || failure.status >= 300 && failure.status < 400 || failure.status === 408 || failure.reason === 'unconfirmed'));
      session.stage = failure ? uncertain ? 'uncertain' : 'failed' : 'sent';
      delete session.file;
      if (failure) {
        const text = uncertain ? '⚠️ Canvas no confirmó el resultado. <b>No volveré a enviar la entrega automáticamente.</b> Abre la tarea en Canvas y comprueba si aparece antes de reintentarlo.'
          : failure.reason === 'changed' ? '⚠️ La tarea o su entrega cambió desde que la seleccionaste. No he registrado una nueva entrega. Vuelve a elegirla y revisa los datos.'
          : failure.reason === 'cancelled' ? 'La conexión cambió. No he enviado una nueva solicitud de entrega.'
          : '⚠️ No se ha confirmado una nueva entrega. ' + errorText(failure) + ' Comprueba la tarea en Canvas antes de reintentarlo.';
        queue(state, prefix, job.uid, text); return;
      }
      const record = user.tasks[session.key];
      if (record) { record.data.submitted = true; record.data.submission_state = result.workflow_state; }
      session.submitted_at = result.submitted_at;
      queue(state, prefix, job.uid, `✅ <b>Entrega confirmada por Canvas</b>\n📚 ${escape(session.course_name, 120)}\n📝 ${escape(session.name, 180)}\n📎 ${escape(job.session.file.name, 220)}\n📅 ${dateLabel(result.submitted_at, this.config.timezone)}\nIntento: ${result.attempt}\n\nLa tarea aparece como entregada.`, menu());
    }, true);
    await this.bot.dispatch(prefix);
  }
}
