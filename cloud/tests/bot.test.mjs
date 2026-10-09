import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { base64, hash, open, seal } from '../codec.mjs';
import { ImmediateBot, StateStore, Telegram, TelegramError } from '../bot.mjs';
import { day, dateLabel, pack, render } from '../commands.mjs';
import { createHandler } from '../handler.mjs';
import { InitialCanvas, CanvasError } from '../canvas.mjs';

const messages = JSON.parse(readFileSync(new URL('../messages.json', import.meta.url), 'utf8'));
const key = base64(new Uint8Array(32).fill(7));
const webhookKey = base64(new Uint8Array(32).fill(8)).replace(/=+$/, '');
const stateKey = base64(new Uint8Array(32).fill(9)).replace(/=+$/, '');
const config = { telegram_token: 'SYNTHETIC_TELEGRAM_TOKEN', encryption_key: key, canvas_base_url: 'https://medac.instructure.com', timezone: 'Europe/Madrid', max_users: 15, recent_days: 7, test_mode: true, allowed_users: [], invite_code: '', polling_cutoff: 0, webhook_secret: webhookKey, webhook_url: 'https://example.supabase.co/functions/v1/canvas-telegram-immediate' };
const blank = () => ({ version: 1, users: {}, outbox: {}, offset: 0, commands_registered: true });
const update = (id, uid, text) => ({ update_id: id, message: { message_id: id, from: { id: uid, is_bot: false }, chat: { id: uid, type: 'private' }, text } });
class DB {
  async initialize(state = blank()) { this.row = { version: 0, ciphertext: await seal(state, key) }; this.conflicts = 0; this.runtimeValue = { webhook_hash: await hash(webhookKey), webhook_config: await seal(config, webhookKey), state_hash: await hash(stateKey), state_config: await seal(config, stateKey) }; return this; }
  async load() { return structuredClone(this.row); }
  async runtime() { return this.runtimeValue; }
  async save(version, ciphertext) {
    if (this.conflicts > 0) { this.conflicts--; return null; }
    if (this.row.version !== version) return null;
    this.row = { version: version + 1, ciphertext }; return version + 1;
  }
}
class FakeTelegram {
  constructor() { this.sent = []; this.deleted = []; this.failure = null; }
  async send(chat, text) { if (this.failure) throw this.failure; this.sent.push({ chat, text }); return this.sent.length; }
  async remove(chat, message) { this.deleted.push({ chat, message }); return true; }
}
const assignment = (id = 1) => ({ id, course_id: 10, course_name: 'Diseño de Interfaces', name: `Práctica ${id}`, description: 'Texto <sin HTML>', created_at: '2026-10-08T09:00:00Z', unlock_at: null, due_at: '2026-10-09T21:59:00Z', points: 10, url: `https://medac.instructure.com/courses/10/assignments/${id}`, submission_state: 'unsubmitted', submitted: false, excused: false, requires_submission: true });
async function setup(state = blank(), overrides = {}) {
  const db = await new DB().initialize(state), store = new StateStore(db, key), telegram = new FakeTelegram();
  const canvasFactory = token => ({ async profile() { return Number(token.split('_').at(-1)) + 1; }, async snapshot() { return { courses: [{ id: 10, name: 'Diseño de Interfaces' }], assignments: [assignment()] }; } });
  const bot = new ImmediateBot({ ...config, ...overrides }, store, telegram, canvasFactory, messages);
  return { bot, db, store, telegram };
}
function python(code, input) {
  const result = spawnSync(process.env.PYTHON_EXECUTABLE || 'python', ['-c', code], { cwd: fileURLToPath(new URL('../../', import.meta.url)), input: JSON.stringify(input), encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}
test('Fernet + gzip roundtrip and tamper rejection', async () => {
  const cipher = await seal(blank(), key);
  assert.deepEqual(await open(cipher, key), blank());
  const raw = atob(cipher.replace(/-/g, '+').replace(/_/g, '/'));
  const tampered = base64(Uint8Array.from(raw, (c, i) => c.charCodeAt(0) ^ (i === 26 ? 1 : 0)));
  await assert.rejects(open(tampered, key));
  await assert.rejects(open(cipher, stateKey));
});
test('JavaScript checkpoints decrypt in Python and both legacy/new Python formats decrypt here', async () => {
  const cipher = await seal(blank(), key);
  const result = python("import sys,json; from src.storage import EncryptedCodec; from cryptography.fernet import Fernet; i=json.load(sys.stdin); c=EncryptedCodec(i['key']); s=c.decode(i['cipher'].encode()); print(json.dumps(dict(state=s,compressed=c.encode(s).decode(),legacy=Fernet(i['key'].encode()).encrypt(json.dumps(s).encode()).decode())))", { key, cipher });
  assert.deepEqual(result.state, blank());
  assert.deepEqual(await open(result.compressed, key), blank());
  assert.deepEqual(await open(result.legacy, key), blank());
});
test('start replies immediately without reading Canvas', async () => {
  const { bot, store, telegram } = await setup();
  bot.canvasFactory = () => { throw new Error('Canvas must not be read by start'); };
  await bot.receive(update(1, 111, '/start'));
  assert.equal(telegram.sent.length, 1);
  assert.match(telegram.sent[0].text, /cada hora/);
  assert.equal((await store.load()).state.users['111'].onboarding, true);
});
test('retrying a delivered update does not send it twice', async () => {
  const { bot, telegram } = await setup();
  await bot.receive(update(1, 111, '/start'));
  await bot.receive(update(1, 111, '/start'));
  assert.equal(telegram.sent.length, 1);
});
test('out of order updates are not dropped', async () => {
  const { bot, telegram, store } = await setup();
  await bot.receive(update(5, 111, '/start'));
  await bot.receive(update(4, 222, '/start'));
  assert.equal(telegram.sent.length, 2);
  assert.equal(Object.keys((await store.load()).state.users).length, 2);
});
test('migration skips polling updates that were already processed', async () => {
  const { bot, telegram } = await setup(blank(), { polling_cutoff: 10 });
  await bot.receive(update(9, 111, '/start'));
  assert.equal(telegram.sent.length, 0);
});
test('15 separate accounts, delivery statuses and capacity limit', async () => {
  const { bot, store, telegram } = await setup();
  for (let i = 0; i < 15; i++) {
    await bot.receive(update(i * 2 + 1, 1000 + i, '/start'));
    const connected = await bot.receive(update(i * 2 + 2, 1000 + i, `SYNTHETIC_CLASS_TOKEN_${i}`));
    assert.equal(connected.uid, String(1000 + i));
  }
  await bot.receive(update(31, 2000, '/start'));
  const { state } = await store.load();
  assert.equal(Object.keys(state.users).length, 15);
  assert.equal(new Set(Object.values(state.users).map(u => u.token)).size, 15);
  assert.equal(telegram.deleted.length, 15);
  assert.match(telegram.sent.at(-1).text, /límite/);
});
test('group messages and forwarded tokens cannot register students', async () => {
  const { bot, store, telegram } = await setup();
  const group = update(1, 111, '/start'); group.message.chat.type = 'group';
  await bot.receive(group);
  await bot.receive(update(2, 111, '/start'));
  const forward = update(3, 111, 'SYNTHETIC_CLASS_TOKEN_0'); forward.message.forward_origin = { type: 'user' };
  await bot.receive(forward);
  assert.equal((await store.load()).state.users['111'].token, undefined);
  assert.equal(telegram.deleted.length, 1);
});
test('private identity mismatch and allowlist are enforced', async () => {
  const { bot, telegram } = await setup(blank(), { allowed_users: [111] });
  await bot.receive(update(1, 222, '/start'));
  const mismatch = update(2, 111, '/start'); mismatch.message.chat.id = 222;
  await bot.receive(mismatch);
  assert.equal(telegram.sent.length, 0);
});
test('first load builds Python compatible state and preserves crossed thresholds', async () => {
  const { bot, store } = await setup();
  await bot.receive(update(1, 111, '/start'));
  const connection = await bot.receive(update(2, 111, 'SYNTHETIC_CLASS_TOKEN_0'));
  await bot.firstSync(connection);
  const { state } = await store.load();
  assert.equal(state.users['111'].initialized, true);
  assert.equal(state.users['111'].tasks['10:1'].reminders['7d'], 'skipped_initial');
  const valid = python("import sys,json; from src.storage import validate_state; print(json.dumps(bool(validate_state(json.load(sys.stdin)))))", state);
  assert.equal(valid, true);
});
test('disconnect removes credentials; first-load background cannot resurrect them', async () => {
  const { bot, store } = await setup();
  await bot.receive(update(1, 111, '/start'));
  const connection = await bot.receive(update(2, 111, 'SYNTHETIC_CLASS_TOKEN_0'));
  await bot.receive(update(3, 111, '/desconectar'));
  await bot.firstSync(connection);
  assert.deepEqual((await store.load()).state.users, {});
});
test('state conflicts are retried without repeating messages', async () => {
  const { bot, db, telegram } = await setup(); db.conflicts = 2;
  await bot.receive(update(1, 111, '/start'));
  assert.equal(telegram.sent.length, 1);
});
test('ambiguous Telegram delivery is never resent automatically', async () => {
  const { bot, telegram, store } = await setup();
  telegram.failure = new TelegramError(0, 0, true);
  await bot.receive(update(1, 111, '/start'));
  telegram.failure = null;
  await bot.receive(update(1, 111, '/start'));
  assert.equal(telegram.sent.length, 0);
  assert.equal((await store.load()).state.outbox['update:1'].status, 'uncertain');
});
test('explicit rate-limit rejection retains the message for the hourly retry', async () => {
  const { bot, telegram, store } = await setup();
  telegram.failure = new TelegramError(429, 120);
  await bot.receive(update(1, 111, '/start'));
  const event = (await store.load()).state.outbox['update:1'];
  assert.equal(event.status, 'pending'); assert.ok(event.not_before); assert.ok(event.text);
});
test('Madrid date changes and daylight saving', () => {
  assert.equal(day('2026-10-08T22:30:00Z'), '2026-10-09');
  assert.match(dateLabel('2026-10-25T01:30:00Z'), /02:30$/);
});
test('all cached task commands agree with the Python implementation', async () => {
  const now = '2026-10-08T12:00:00Z', user = { token: 'SYNTHETIC_TOKEN', initialized: true, courses: [{ id: 10, name: 'Diseño de Interfaces' }], tasks: {}, last_sync: now };
  for (let i = 0; i < 6; i++) {
    const a = assignment(i + 1); a.due_at = i === 5 ? null : new Date(Date.parse(now) + (i - 1) * 86400000).toISOString();
    a.submitted = i === 2; a.excused = i === 3;
    user.tasks[`10:${a.id}`] = { data: a, first_seen_at: now, reminders: {}, revision: 0 };
  }
  for (const command of ['hoy', 'manana', 'semana', 'pendientes', 'atrasadas', 'ultimas', 'asignaturas', 'resumen']) {
    const expected = python("import sys,json; from datetime import datetime; from src.commands import render_command; from src.config import Config; i=json.load(sys.stdin); c=Config('https://medac.instructure.com','synthetic','synthetic'); print(json.dumps(render_command(i['command'],i['user'],c,datetime.fromisoformat(i['now'].replace('Z','+00:00'))),ensure_ascii=False))", { command, user, now });
    assert.deepEqual(render(command, user, config, now, messages), expected, command);
  }
});
test('messages are packed without cutting HTML and safe links stay in Canvas', () => {
  assert.equal(pack(['a'.repeat(2000), 'b'.repeat(2000)]).length, 2);
  const user = { token: 'SYNTHETIC', initialized: true, courses: [{ id: 10 }], tasks: { '10:1': { data: { ...assignment(), name: '<script> &', url: 'https://evil.example/' } } } };
  const text = render('pendientes', user, config, '2026-10-08T12:00:00Z', messages).join('\n');
  assert.match(text, /&lt;script&gt; &amp;/); assert.doesNotMatch(text, /href=/);
});
test('webhook rejects unauthenticated requests before parsing any student update', async () => {
  const db = await new DB().initialize();
  const handler = createHandler(db, messages, x => x);
  for (const headers of [{}, { 'X-Telegram-Bot-Api-Secret-Token': 'wrong' }]) {
    assert.equal((await handler(new Request(config.webhook_url, { method: 'POST', headers, body: '{}' }))).status, 401);
  }
  assert.equal(db.row.version, 0);
});
test('authenticated webhook returns promptly and sends a real Bot API shaped response', async () => {
  const db = await new DB().initialize(), sent = [];
  const fetcher = async (url, args) => { sent.push({ url, payload: JSON.parse(args.body) }); return Response.json({ ok: true, result: { message_id: 1 } }); };
  const handler = createHandler(db, messages, x => x, fetcher);
  const response = await handler(new Request(config.webhook_url, { method: 'POST', headers: { 'X-Telegram-Bot-Api-Secret-Token': webhookKey }, body: JSON.stringify(update(1, 111, '/ayuda')) }));
  assert.equal(response.status, 200); assert.equal(sent.length, 1);
  assert.equal(sent[0].payload.parse_mode, 'HTML'); assert.equal(sent[0].payload.chat_id, 111);
});
test('authenticated state bridge enforces compare-and-swap and accepts Python ciphertext', async () => {
  const db = await new DB().initialize(), handler = createHandler(db, messages, x => x);
  const request = body => new Request(config.webhook_url, { method: 'POST', headers: { 'X-Canvas-State-Key': stateKey }, body: JSON.stringify(body) });
  const loaded = await (await handler(request({ action: 'load' }))).json();
  assert.equal((await handler(request({ action: 'save', version: loaded.version, ciphertext: loaded.ciphertext }))).status, 200);
  assert.equal((await handler(request({ action: 'save', version: loaded.version, ciphertext: loaded.ciphertext }))).status, 409);
});
test('Canvas token is never forwarded to pagination on another origin', async () => {
  let calls = 0;
  const client = new InitialCanvas('https://medac.instructure.com', 'SYNTHETIC', x => x, async () => { calls++; return new Response('[]', { headers: { Link: '<https://evil.example/api/v1/courses>; rel="next"' } }); });
  await assert.rejects(client.snapshot(), CanvasError); assert.equal(calls, 1);
});
test('Telegram network failures do not expose token URLs', async () => {
  const bot = new Telegram('SYNTHETIC_PRIVATE', async () => { throw new Error('private URL'); });
  await assert.rejects(bot.send(111, 'Hola'), error => error.ambiguous && !error.message.includes('SYNTHETIC') && !error.message.includes('URL'));
});

test('paginated commands expose every task in small complete pages', () => {
  const tasks = Object.fromEntries(Array.from({length:17}, (_,index) => [`10:${index+1}`, {data:assignment(index+1),first_seen_at:'2026-10-08T09:00:00Z',reminders:{},revision:0}]));
  const user = {token:'SYNTHETIC',initialized:true,courses:[{id:10,name:'Interfaces'}],tasks};
  const seen = new Set();
  for (let page=1;page<=3;page++) {
    const blocks = render('pendientes',user,{...config,page_size:8},'2026-10-08T10:00:00Z',messages,page);
    assert.ok(blocks.filter(block=>block.includes('📝')).length<=8);
    for (const block of blocks) for (const match of block.matchAll(/📝 Práctica (\d+)/g)) seen.add(Number(match[1]));
    assert.ok(pack(blocks).every(text=>text.length<=3500));
    if (page<3) assert.match(blocks.join('\n'),new RegExp(`/pendientes ${page+1}`));
  }
  assert.equal(seen.size,17);
});
test('native fetch is called without a class instance receiver', async () => {
  async function strictFetch(url) {
    assert.equal(this,undefined);
    return url.includes('telegram.org') ? Response.json({ok:true,result:{message_id:42}}) : Response.json({id:123});
  }
  assert.equal(await new Telegram('SYNTHETIC',strictFetch).send(111,'Hola'),42);
  assert.equal(await new InitialCanvas(config.canvas_base_url,'SYNTHETIC',x=>x,strictFetch).profile(),123);
});

test('Canvas API requests identify the application and request JSON', async () => {
  const client = new InitialCanvas(config.canvas_base_url, 'SYNTHETIC_PRIVATE', x => x, async (url, args) => {
    assert.equal(url, config.canvas_base_url + '/api/v1/users/self/profile');
    assert.equal(args.headers.Authorization, 'Bearer SYNTHETIC_PRIVATE');
    assert.equal(args.headers.Accept, 'application/json');
    assert.match(args.headers['User-Agent'], /^CanvasTelegramAssistant\//);
    assert.equal(args.redirect, 'manual');
    return Response.json({ id: 123 });
  });
  assert.equal(await client.profile(), 123);
});

test('Canvas HTML denial is classified without exposing the response body', async () => {
  const client = new InitialCanvas(config.canvas_base_url, 'SYNTHETIC_PRIVATE', x => x, async () => new Response('<html>PRIVATE_PROVIDER_RESPONSE</html>', { status: 403, headers: { 'Content-Type': 'text/html' } }));
  await assert.rejects(client.profile(), error => error.status === 403 && error.reason === 'html-response' && !error.message.includes('PRIVATE'));
});

test('registration separates access denial from authentication failure and safely retries', async () => {
  for (const [status, reason, expected] of [[403, 'html-response', /conexión del servidor/], [403, 'scope', /Puede faltar permiso/], [401, 'invalid-token', /valor completo/]]) {
    const { bot, telegram, store } = await setup();
    await bot.receive(update(1, 111, '/start'));
    bot.canvasFactory = () => ({ async profile() { throw new CanvasError(status, reason); } });
    const token = 'SYNTHETIC_NEW_PRIVATE_TOKEN';
    await bot.receive(update(2, 111, token));
    assert.match(telegram.sent.at(-1).text, expected);
    assert.doesNotMatch(telegram.sent.at(-1).text, /\(caducado, revocado|SYNTHETIC/);
    let user = (await store.load()).state.users['111'];
    assert.equal(user.token, undefined);
    assert.equal(user.onboarding, true);
    assert.equal(user.registration_error.status, status);
    assert.equal(user.registration_error.reason, reason);
    bot.canvasFactory = () => ({ async profile() { return 123; } });
    await bot.receive(update(3, 111, token));
    user = (await store.load()).state.users['111'];
    assert.equal(user.registration_error, undefined);
    assert.equal(user.canvas_user_id, 123);
    assert.match(telegram.sent.at(-1).text, /Cuenta conectada/);
  }
});

test('authenticated Canvas health returns only endpoint status, never student data', async () => {
  const state = blank();
  state.users['111'] = { onboarding: false, token: 'SYNTHETIC_PRIVATE', tasks: {}, courses: [] };
  const db = await new DB().initialize(state);
  const handler = createHandler(db, messages, x => x, async () => Response.json({ id: 123, name: 'PRIVATE_STUDENT_NAME', email: 'PRIVATE_EMAIL' }));
  const request = headers => new Request(config.webhook_url, { method: 'POST', headers, body: JSON.stringify({ action: 'canvas-health' }) });
  assert.equal((await handler(request({}))).status, 401);
  const response = await handler(request({ 'X-Canvas-State-Key': stateKey }));
  assert.equal(response.status, 200);
  const result = await response.json();
  assert.equal(result.checks.length, 2);
  assert.ok(result.checks.every(check => check.status === 200 && check.valid_user));
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE|111|123/);
});

test('deployment refresh queries Canvas and returns only safe counts', async () => {
  const state = blank();
  state.users['111'] = {onboarding:false,token:'SYNTHETIC_PRIVATE',canvas_user_id:123,tasks:{},courses:[]};
  const db = await new DB().initialize(state), calls = [];
  const handler = createHandler(db,messages,x=>x,async (url,args) => {
    calls.push(url);
    if (url.includes('telegram.org')) return Response.json({ok:true,result:{message_id:42}});
    if (url.includes('/assignments?')) return Response.json([{id:20,name:'PRIVATE_WORK',description:'PRIVATE_DESCRIPTION',due_at:null,created_at:null,unlock_at:null,points_possible:10,submission_types:['online_upload'],published:true,submission:{workflow_state:'unsubmitted'}}]);
    return Response.json([{id:10,name:'PRIVATE_COURSE'}]);
  });
  const response = await handler(new Request(config.webhook_url,{method:'POST',headers:{'X-Canvas-State-Key':stateKey},body:JSON.stringify({action:'refresh-probe'})}));
  assert.equal(response.status,200);
  const result = await response.json();
  assert.equal(result.updated,true); assert.equal(result.tasks,1); assert.equal(result.courses,1);
  assert.doesNotMatch(JSON.stringify(result),/PRIVATE|111|123/);
  assert.ok(calls.some(url=>url.includes('sendMessage')));
});
