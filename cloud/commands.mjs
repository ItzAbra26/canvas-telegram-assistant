export function escape(value, limit = 240) {
  let result = '';
  for (const char of String(value)) {
    const encoded = ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#x27;' })[char] || char;
    if (result.length + encoded.length > limit - 1) return result + '…';
    result += encoded;
  }
  return result;
}
const stamp = value => value ? new Date(value).getTime() : null;
const formatters = new Map();
const formatter = (locale, options) => {
  const key = locale + JSON.stringify(options);
  if (!formatters.has(key)) formatters.set(key, new Intl.DateTimeFormat(locale, options));
  return formatters.get(key);
};
export const pending = a => a.requires_submission && !a.submitted && !a.excused;
export function day(value, zone = 'Europe/Madrid') {
  const parts = formatter('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(value));
  const fields = Object.fromEntries(parts.map(p => [p.type, p.value]));
  return `${fields.year}-${fields.month}-${fields.day}`;
}
const plusDays = (value, n) => new Date(Date.parse(value + 'T12:00:00Z') + n * 86400000).toISOString().slice(0, 10);
function clock(value, zone) {
  return formatter('en-GB', { timeZone: zone, hour: '2-digit', minute: '2-digit' }).format(new Date(value));
}
export function dateLabel(value, zone = 'Europe/Madrid') {
  if (!value) return 'Sin fecha límite';
  const date = formatter('es-ES', { timeZone: zone, weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }).format(new Date(value)).replace(',', '');
  return date[0].toUpperCase() + date.slice(1) + ' · ' + clock(value, zone);
}
export function timeLeft(value, now) {
  if (!value) return 'Sin fecha límite';
  const seconds = Math.floor((stamp(value) - stamp(now)) / 1000);
  if (seconds <= 0) return 'Plazo vencido';
  const days = Math.floor(seconds / 86400), hours = Math.floor(seconds % 86400 / 3600), minutes = Math.floor(seconds % 3600 / 60);
  return days ? `Quedan ${days} días y ${hours} horas` : hours ? `Quedan ${hours} horas y ${minutes} minutos` : `Quedan ${Math.max(1, minutes)} minutos`;
}
function link(value, base) {
  try {
    const url = new URL(value), origin = new URL(base);
    if (url.protocol !== 'https:' || url.host !== origin.host || url.username || url.password || url.search || url.hash || value.length > 1000) return '';
    return `<a href="${escape(value, 2000)}">🔗 Abrir en Canvas</a>`;
  } catch { return ''; }
}
export function taskBlock(a, config, now, description = false) {
  const status = a.submitted ? '✅ Entregada' : a.excused ? '⚪ Exenta' : !a.requires_submission ? 'ℹ️ Sin entrega requerida' : a.due_at && stamp(a.due_at) < stamp(now) ? '🔴 Atrasada' : '🟠 Pendiente';
  let text = `📚 <b>${escape(a.course_name, 120)}</b>\n📝 ${escape(a.name, 180)}\n📅 ${dateLabel(a.due_at, config.timezone)}\n⏳ ${timeLeft(a.due_at, now)}\n${status}`;
  if (description) text += `\n💯 Puntos: ${a.points ?? 'No indicados'}\n${escape(a.description, 400)}`;
  const url = link(a.url, config.canvas_base_url);
  return text + (url ? '\n' + url : '');
}
export function pack(blocks, limit = 3500) {
  const pages = []; let current = '';
  for (const block of blocks) {
    if (block.length > limit) throw new Error('Message too large');
    const next = current ? current + '\n\n' + block : block;
    if (next.length > limit) { pages.push(current); current = block; } else current = next;
  }
  if (current) pages.push(current);
  return pages;
}
export function render(command, user, config, now, messages, page = 1) {
  if (command === 'ayuda') return [messages.help];
  if (command === 'privacidad') return [messages.privacy];
  if (command === 'estado') {
    const connection = user.disabled ? 'Token caducado/revocado: vuelve a conectar con /start.' : user.token ? 'Cuenta conectada.' : 'Sin cuenta conectada. Usa /start.';
    return [`🔌 <b>Estado</b>\n${connection}\nÚltima sincronización: ${dateLabel(user.last_sync, config.timezone)}\nCanvas se revisa cada hora; los comandos usan los datos guardados.`];
  }
  if (!user.token) return ['Primero conecta tu propia cuenta con /start.'];
  const active = new Set((user.courses || []).map(c => c.id));
  const tasks = Object.values(user.tasks || {}).filter(r => !r.deleted && active.has(r.data.course_id)).map(r => r.data);
  const sort = rows => [...rows].sort((a, b) => (stamp(a.due_at) ?? Infinity) - (stamp(b.due_at) ?? Infinity) || a.course_name.localeCompare(b.course_name) || a.name.localeCompare(b.name));
  const todo = sort(tasks.filter(pending));
  const blocks = user.last_sync ? [`🕒 Última revisión: ${dateLabel(user.last_sync, config.timezone)}`] : [];
  if (user.sync_error || user.disabled) blocks.push('⚠️ No he podido actualizar Canvas. Estos son los últimos datos guardados; el estado puede haber cambiado.');
  if (!user.initialized) return [...blocks, 'Todavía no hay una sincronización completa. Lo volveré a intentar en la siguiente revisión.'];
  const current = stamp(now);
  if (command === 'resumen') {
    const urgent = todo.filter(a => a.due_at && stamp(a.due_at) <= current + 86400000).length;
    const week = todo.filter(a => a.due_at && stamp(a.due_at) > current + 86400000 && stamp(a.due_at) <= current + 7 * 86400000).length;
    const later = todo.filter(a => a.due_at && stamp(a.due_at) > current + 7 * 86400000).length;
    let text = `📚 <b>RESUMEN</b>\n\n🔴 Urgentes: ${urgent}\n🟠 Esta semana: ${week}\n🟢 Más adelante: ${later}\n⚪ Sin fecha: ${todo.filter(a => !a.due_at).length}\n\n<b>Total pendientes: ${todo.length}</b>`;
    const next = todo.find(a => a.due_at && stamp(a.due_at) >= current);
    if (next) {
      const today = day(now, config.timezone), dueDay = day(next.due_at, config.timezone);
      const label = dueDay === today ? 'Hoy' : dueDay === plusDays(today, 1) ? 'Mañana' : null;
      text += `\n\n<b>Próxima entrega:</b>\n${escape(next.course_name, 120)}\n${escape(next.name, 180)}\n${label ? label + ' · ' + clock(next.due_at, config.timezone) : dateLabel(next.due_at, config.timezone)}`;
    } else text += todo.length ? '\n\nNo hay próximas entregas con fecha futura. Revisa /atrasadas y las tareas sin fecha.' : '\n\n✅ No tienes tareas pendientes.';
    return [...blocks, text];
  }
  if (command === 'asignaturas') {
    blocks.push('📚 <b>ASIGNATURAS ACTIVAS</b>');
    for (const course of user.courses || []) blocks.push(`📚 ${escape(course.name, 180)}\n📝 Pendientes: ${todo.filter(a => a.course_id === course.id).length}`);
    if (!user.courses?.length) blocks.push('No hay cursos activos visibles en Canvas.');
    return blocks;
  }
  let selected;
  const today = day(now, config.timezone);
  if (['hoy', 'manana', 'semana'].includes(command)) {
    selected = sort(tasks.filter(a => {
      if (!a.due_at) return false;
      const dueDay = day(a.due_at, config.timezone);
      return command === 'semana' ? dueDay >= today && dueDay < plusDays(today, 7) : dueDay === plusDays(today, command === 'manana' ? 1 : 0);
    }));
  } else if (command === 'pendientes') selected = todo;
  else if (command === 'atrasadas') selected = todo.filter(a => a.due_at && stamp(a.due_at) < current);
  else if (command === 'ultimas') {
    const recent = a => Math.max(stamp(a.created_at) || 0, stamp(user.tasks[`${a.course_id}:${a.id}`].first_seen_at));
    selected = tasks.filter(a => recent(a) >= current - config.recent_days * 86400000).sort((a, b) => recent(b) - recent(a));
  } else return ['No reconozco ese comando. Consulta /ayuda.'];
  const titles = { hoy: 'ENTREGAS DE HOY', manana: 'ENTREGAS DE MAÑANA', semana: 'PRÓXIMOS 7 DÍAS', pendientes: 'TAREAS PENDIENTES', atrasadas: 'TAREAS ATRASADAS', ultimas: 'TAREAS RECIENTES' };
  blocks.push(`📋 <b>${titles[command]}</b> · ${selected.length}`);
  if (command === 'ultimas') blocks.push('Canvas no expone una fecha exacta de publicación. Uso creación o primera detección.');
  if (config.page_size && selected.length > config.page_size) {
    const total = Math.ceil(selected.length / config.page_size);
    page = Math.min(Math.max(1, page), total);
    selected = selected.slice((page - 1) * config.page_size, page * config.page_size);
    blocks.push(`Página ${page} de ${total}. ` + (page < total ? `Continúa con <code>/${command} ${page + 1}</code>.` : 'Última página.'));
  }
  blocks.push(...selected.map(a => taskBlock(a, config, now, command === 'ultimas')));
  if (!selected.length) blocks.push('No hay tareas en esta categoría.');
  return blocks;
}
