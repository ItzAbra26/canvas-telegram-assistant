import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { ImmediateBot, Telegram } from '../bot.mjs';
import { InitialCanvas, CanvasError } from '../canvas.mjs';
import { MAX_FILE_BYTES, menu } from '../interactions.mjs';

const messages = JSON.parse(readFileSync(new URL('../messages.json', import.meta.url)));
const config = { canvas_base_url: 'https://medac.instructure.com', timezone: 'Europe/Madrid', max_users: 15, test_mode: true, recent_days: 7 };
const task = { id: 20, course_id: 10, course_name: 'Interfaces', name: 'Práctica', description: 'Descripción completa '.repeat(100), created_at: null, unlock_at: null, due_at: null, points: 10, url: config.canvas_base_url + '/courses/10/assignments/20', submission_state: 'unsubmitted', submitted: false, excused: false, requires_submission: true };
const raw = () => ({ id: 20, course_id: 10, name: 'Práctica', due_at: null, published: true, locked_for_user: false, group_category_id: null, submission_types: ['online_upload'], allowed_extensions: ['pdf','zip'], submission: { workflow_state: 'unsubmitted', attempt: 0, submitted_at: null } });
const receipt = () => ({ user_id: 123, assignment_id: 20, workflow_state: 'submitted', submitted_at: new Date().toISOString(), attempt: 1, attachments: [{ id: 70 }] });
const document = (values = {}) => ({ file_id: 'SYNTHETIC_FILE', file_name: 'trabajo.pdf', file_size: 4, mime_type: 'application/pdf', ...values });
const msg = (id, text, uid = 111) => ({ update_id: id, message: { from: { id: uid }, chat: { type: 'private', id: uid }, message_id: id, text } });
const cb = (id, data, uid = 111, chat = uid) => ({ update_id: id, callback_query: { id: `callback${id}`, from: { id: uid }, data, message: { chat: { id: chat, type: 'private' }, message_id: 100, from: { id: 999, is_bot: true } } } });
async function setup() {
  const state = { version: 1, offset: 0, commands_registered: true, outbox: {}, users: { '111': { token: 'SYNTHETIC_PRIVATE', canvas_user_id: 123, connected_at: '2026-10-08T12:00:00Z', onboarding: false, initialized: true, courses: [{ id: 10, name: 'Interfaces' }], tasks: { '10:20': { data: structuredClone(task), revision: 0, reminders: { '7d': 'sent' }, remaining: null, first_seen_at: '2026-10-08T12:00:00Z' } } } } };
  const store = { state, async load() { return { state: structuredClone(this.state) }; }, async change(fn) { const current = structuredClone(this.state); if (fn(current) !== false) this.state = current; return this.state; } };
  const telegram = { sent: [], answered: [], downloads: 0, async send(chat, text, markup) { this.sent.push({ chat, text, markup }); return this.sent.length; }, async answer(id) { this.answered.push(id); }, async download() { this.downloads++; return new Blob(['DATA']); }, async remove() { return true; } };
  const canvas = { reads: 0, uploads: 0, submissions: 0, async assignment() { this.reads++; return raw(); }, async snapshot() { return { courses: [{ id: 10, name: 'Interfaces' }], assignments: [structuredClone(task)] }; }, async upload() { this.uploads++; return 70; }, async submitFile() { this.submissions++; return receipt(); } };
  return { bot: new ImmediateBot(config, store, telegram, () => canvas, messages), store, telegram, canvas };
}
async function ready(env) {
  await env.bot.receive(cb(1, 'task:10:20'));
  const update = msg(2); update.message.document = document();
  await env.bot.receive(update);
  return env.store.state.users['111'].delivery.nonce;
}
test('menu callbacks acknowledge the button and keep personal results private', async () => {
  const env = await setup();
  await env.bot.receive(cb(1, 'cmd:resumen:1'));
  assert.equal(env.telegram.answered.length, 1);
  assert.equal(env.telegram.sent[0].chat, 111);
  assert.deepEqual(env.telegram.sent[0].markup, menu());
  await env.bot.receive(cb(2, 'cmd:resumen:1', 222, 111));
  assert.equal(env.telegram.sent.length, 1);
});
test('picker is paginated and cannot select another students assignment', async () => {
  const env = await setup(), user = env.store.state.users['111'];
  for (let i = 21; i < 36; i++) user.tasks[`10:${i}`] = { ...structuredClone(user.tasks['10:20']), data: { ...task, id: i } };
  await env.bot.receive(cb(1, 'list:1'));
  assert.equal(env.telegram.sent.at(-1).markup.inline_keyboard.filter(row => row[0].callback_data.startsWith('task:')).length, 8);
  assert.ok(env.telegram.sent.at(-1).markup.inline_keyboard.some(row => row.some(b => b.callback_data === 'list:2')));
  await env.bot.receive(cb(2, 'task:99:200'));
  assert.equal(env.canvas.reads, 0);
  assert.equal(user.delivery, undefined);
});
test('receiving a document never submits until its matching confirmation', async () => {
  const env = await setup(), nonce = await ready(env);
  assert.equal(env.canvas.uploads, 0); assert.equal(env.canvas.submissions, 0); assert.equal(env.telegram.downloads, 0);
  assert.match(env.telegram.sent.at(-1).text, /Revisa antes de entregar/);
  const job = await env.bot.receive(cb(3, `confirm:${nonce}`));
  assert.equal(job.kind, 'submit'); assert.equal(env.canvas.submissions, 0);
  assert.equal(await env.bot.receive(cb(3, `confirm:${nonce}`)), null);
  await env.bot.runJob(job);
  assert.equal(env.canvas.submissions, 1);
  assert.equal(env.store.state.users['111'].tasks['10:20'].data.submitted, true);
  assert.equal(env.store.state.users['111'].delivery.stage, 'sent');
  assert.equal(env.store.state.users['111'].delivery.file, undefined);
  assert.match(env.telegram.sent.at(-1).text, /Entrega confirmada por Canvas/);
  await env.bot.runJob(job);
  assert.equal(env.canvas.uploads, 1); assert.equal(env.canvas.submissions, 1);
});
test('replacing the file invalidates the old confirmation and rejects excess size', async () => {
  const env = await setup(), old = await ready(env);
  const tooBig = msg(3); tooBig.message.document = document({ file_size: MAX_FILE_BYTES + 1 });
  await env.bot.receive(tooBig);
  assert.equal(env.store.state.users['111'].delivery.nonce, old);
  const newer = msg(4); newer.message.document = document({ file_name: 'nuevo.pdf' }); await env.bot.receive(newer);
  assert.notEqual(env.store.state.users['111'].delivery.nonce, old);
  assert.equal(await env.bot.receive(cb(5, `confirm:${old}`)), null);
  assert.equal(env.canvas.submissions, 0);
});
test('closed tasks, external tools, extensions and expiry prevent submission', async () => {
  for (const modification of [{ locked_for_user: true }, { submission_types: ['external_tool'] }]) {
    const env = await setup(); env.canvas.assignment = async () => ({ ...raw(), ...modification });
    await env.bot.receive(cb(1, 'task:10:20'));
    assert.equal(env.store.state.users['111'].delivery, undefined);
  }
  const env = await setup(); await env.bot.receive(cb(1, 'task:10:20'));
  const invalid = msg(2); invalid.message.document = document({ file_name: 'evil.exe' }); await env.bot.receive(invalid);
  assert.equal(env.store.state.users['111'].delivery.file, undefined);
  env.store.state.users['111'].delivery.expires_at = '2000-01-01T00:00:00Z';
  const valid = msg(3); valid.message.document = document(); await env.bot.receive(valid);
  assert.equal(env.store.state.users['111'].delivery.file, undefined);
});
test('group, late and repeat submissions are explicit in the confirmation', async () => {
  const env = await setup(); env.canvas.assignment = async () => ({ ...raw(), due_at: '2000-01-01T00:00:00Z', group_category_id: 8, submission: { workflow_state: 'submitted', attempt: 1, submitted_at: '2000-01-01T00:00:00Z' } });
  await ready(env);
  assert.match(env.telegram.sent.at(-1).text, /nuevo intento/);
  assert.match(env.telegram.sent.at(-1).text, /grupo/);
  assert.match(env.telegram.sent.at(-1).text, /tardía/);
});
test('changed assignment and cancellation never submit', async () => {
  const env = await setup(), nonce = await ready(env), job = await env.bot.receive(cb(3, `confirm:${nonce}`));
  env.canvas.assignment = async () => ({ ...raw(), name: 'Changed' });
  await env.bot.runJob(job);
  assert.equal(env.canvas.submissions, 0); assert.equal(env.canvas.uploads, 0);
  assert.match(env.telegram.sent.at(-1).text, /cambió/);
  const cancelled = await setup(), id = await ready(cancelled);
  await cancelled.bot.receive(cb(3, `cancel:${id}`));
  assert.equal(await cancelled.bot.receive(cb(4, `confirm:${id}`)), null);
  assert.equal(cancelled.canvas.submissions, 0);
});
test('disconnect while uploading prevents the final POST and resurrection', async () => {
  const env = await setup(), id = await ready(env), job = await env.bot.receive(cb(3, `confirm:${id}`));
  env.canvas.upload = async () => { await env.bot.receive(msg(4, '/desconectar')); return 70; };
  await env.bot.runJob(job);
  assert.equal(env.canvas.submissions, 0); assert.equal(env.store.state.users['111'], undefined);
});
test('ambiguous submission is never automatically repeated or labelled delivered', async () => {
  const env = await setup(), id = await ready(env), job = await env.bot.receive(cb(3, `confirm:${id}`));
  env.canvas.submitFile = async () => { env.canvas.submissions++; throw new CanvasError(); };
  await env.bot.runJob(job);
  assert.equal(env.store.state.users['111'].delivery.stage, 'uncertain');
  assert.equal(env.store.state.users['111'].tasks['10:20'].data.submitted, false);
  await env.bot.runJob(job);
  assert.equal(env.canvas.submissions, 1);
  assert.match(env.telegram.sent.at(-1).text, /No volveré/);
});
test('manual refresh preserves the hourly comparison and reminder history', async () => {
  const env = await setup();
  const updated = { ...task, description: 'Changed full description '.repeat(100), due_at: '2026-12-01T12:00:00Z' };
  env.canvas.snapshot = async () => ({ courses: [{ id: 10, name: 'Interfaces' }], assignments: [updated, { ...task, id: 21 }] });
  const job = await env.bot.receive(cb(1, 'cmd:actualizar:1')); assert.equal(job.kind, 'refresh');
  await env.bot.runJob(job);
  const user = env.store.state.users['111'];
  assert.equal(user.tasks['10:20'].hourly_data.description, task.description);
  assert.equal(user.tasks['10:20'].data.description, updated.description);
  assert.deepEqual(user.tasks['10:20'].reminders, { '7d': 'sent' });
  assert.equal(user.tasks['10:21'].manual_unnotified, true);
  assert.match(env.telegram.sent.at(-1).text, /Canvas actualizado/);
  assert.equal(await env.bot.receive(cb(2, 'cmd:actualizar:1')), null);
});
test('failed refresh retains every task', async () => {
  const env = await setup(); env.canvas.snapshot = async () => { throw new CanvasError(503); };
  const before = structuredClone(env.store.state.users['111'].tasks);
  const job = await env.bot.receive(msg(1, '/actualizar')); await env.bot.runJob(job);
  assert.deepEqual(env.store.state.users['111'].tasks, before);
  assert.equal(env.store.state.users['111'].refresh.stage, 'failed');
});

test('SCORM is absent from summary, task picker, notices and submission selection', async () => {
  const env = await setup(); env.store.state.users['111'].tasks['10:20'].data.name = 'Unidad sCoRm';
  await env.bot.receive(msg(1,'/resumen')); assert.match(env.telegram.sent.at(-1).text,/Total pendientes: 0/);
  await env.bot.receive(cb(2,'list:1')); assert.match(env.telegram.sent.at(-1).text,/No hay tareas/);
  await env.bot.receive(cb(3,'task:10:20')); assert.equal(env.canvas.reads,0);
  env.store.state.outbox['u111:task:10:20:new'] = {status:'pending',chat_id:111,text:'Old notice'};
  const before = env.telegram.sent.length; await env.bot.dispatch('u111:task:10:20:new');
  assert.equal(env.telegram.sent.length,before);
  assert.equal(env.store.state.outbox['u111:task:10:20:new'].status,'cancelled');
});

test('Canvas skips SCORM even when its submission data is missing', async () => {
  const client = new InitialCanvas(config.canvas_base_url,'SYNTHETIC_PRIVATE',x=>x,async url => url.includes('/assignments?') ? Response.json([{id:20,name:'SCORM unidad 1'}]) : Response.json([{id:10,name:'Interfaces'}]));
  const snapshot = await client.snapshot();
  assert.deepEqual(snapshot.assignments,[]);
  assert.deepEqual(snapshot.ignored,{'10:20':'SCORM unidad 1'});
});

test('pending token renewal still allows refreshing the previously connected account', async () => {
  const env = await setup(); env.store.state.users['111'].onboarding = true;
  const job = await env.bot.receive(msg(1,'/actualizar'));
  assert.equal(job.kind,'refresh'); await env.bot.runJob(job);
  assert.equal(env.store.state.users['111'].refresh.stage,'done');
  assert.equal(env.store.state.users['111'].onboarding,true);
});

test('test mode disables refresh and submission even with saved credentials', async () => {
  const env = await setup(); env.bot.config = {...config,test_mode:false};
  assert.equal(await env.bot.receive(msg(1,'/actualizar')),null);
  assert.equal(await env.bot.receive(cb(2,'task:10:20')),null);
  assert.equal(env.canvas.reads,0);
  assert.equal(env.store.state.users['111'].refresh,undefined);
});
test('Telegram downloads stay private and verify the exact size', async () => {
  const urls = [];
  const telegram = new Telegram('SYNTHETIC_PRIVATE', async (url, args) => {
    urls.push(url); assert.equal(args.redirect, 'manual');
    return url.endsWith('getFile') ? Response.json({ ok: true, result: { file_path: 'documents/file_1.pdf', file_size: 4 } }) : new Response('DATA');
  });
  assert.equal((await telegram.download({ file_id: 'SYNTHETIC', size: 4, mime: 'application/pdf' }, AbortSignal.timeout(1000))).size, 4);
  assert.equal(urls[1], 'https://api.telegram.org/file/botSYNTHETIC_PRIVATE/documents/file_1.pdf');
  await assert.rejects(telegram.download({ file_id: 'SYNTHETIC', size: 3, mime: 'application/pdf' }, AbortSignal.timeout(1000)));
});
test('official upload uses signed multipart with file last and no Canvas token at storage', async () => {
  const calls = [];
  const client = new InitialCanvas(config.canvas_base_url, 'SYNTHETIC_PRIVATE', x => x, async (url, args) => {
    calls.push({ url, args });
    if (url.endsWith('/files')) return Response.json({ upload_url: 'https://storage.example.edu/upload', upload_params: { policy: 'SIGNED', key: 'OPAQUE' } });
    if (url.includes('storage.example.edu')) {
      assert.equal(args.headers, undefined);
      assert.deepEqual([...args.body.keys()], ['policy','key','file']);
      assert.equal(args.body.get('file').size, 4);
      return new Response('', { status: 302, headers: { location: config.canvas_base_url + '/api/v1/files/70/create_success?uuid=OPAQUE' } });
    }
    return Response.json({ id: 70, size: 4 });
  });
  assert.equal(await client.upload(10, 20, { name: 'work.pdf', mime: 'application/pdf' }, new Blob(['DATA'])), 70);
  assert.equal(calls[0].args.headers.Authorization, 'Bearer SYNTHETIC_PRIVATE');
  assert.equal(calls[2].args.headers.Authorization, 'Bearer SYNTHETIC_PRIVATE');
});
test('upload redirect cannot send a bearer token to a third party', async () => {
  let count = 0;
  const client = new InitialCanvas(config.canvas_base_url, 'SYNTHETIC_PRIVATE', x => x, async () => ++count === 1 ? Response.json({ upload_url: 'https://storage.example.edu/upload', upload_params: {} }) : new Response('', { status: 302, headers: { location: 'https://evil.example/api/v1/files/70' } }));
  await assert.rejects(client.upload(10, 20, { name: 'work.pdf', mime: 'application/pdf' }, new Blob(['DATA'])));
  assert.equal(count, 2);
});
test('final submission uses the current token owner and verifies the uploaded attachment', async () => {
  let posts = 0;
  const client = new InitialCanvas(config.canvas_base_url, 'SYNTHETIC_PRIVATE', x => x, async (url, args) => {
    if (args.method === 'POST') { posts++; const body = JSON.parse(args.body); assert.deepEqual(body, { submission: { submission_type: 'online_upload', file_ids: [70] } }); return Response.json(receipt()); }
    throw new Error('No fallback needed');
  });
  assert.equal((await client.submitFile(10, 20, 70, 123)).attempt, 1); assert.equal(posts, 1);
  const wrong = new InitialCanvas(config.canvas_base_url, 'SYNTHETIC_PRIVATE', x => x, async () => Response.json({ ...receipt(), user_id: 999 }));
  await assert.rejects(wrong.submitFile(10, 20, 70, 123), error => error.reason === 'unconfirmed');
});
