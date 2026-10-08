import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { D1DB } from '../d1-db.mjs';
import { open, seal, base64, hash } from '../codec.mjs';
import { ImmediateBot } from '../bot.mjs';

const messages = JSON.parse(readFileSync(new URL('../messages.json', import.meta.url)));
const key = base64(crypto.getRandomValues(new Uint8Array(32)));
const blank = () => ({version:1, users:{}, offset:0, outbox:{}, commands_registered:true});
class SQLiteBinding {
  constructor() { this.db = new DatabaseSync(':memory:'); this.db.exec(readFileSync(new URL('../d1-schema.sql', import.meta.url), 'utf8')); }
  prepare(sql) {
    const stmt = this.db.prepare(sql); let values = [];
    const query = { bind(...args) { values = args; return query; },
      async first() { return stmt.get(...values) || null; },
      async all() { return {results:stmt.all(...values)}; },
      run() { return {meta:{changes:Number(stmt.run(...values).changes)}}; } };
    return query;
  }
  async batch(statements) {
    this.db.exec('BEGIN');
    try { const results = statements.map(s => s.run()); this.db.exec('COMMIT'); return results; }
    catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }
}
const python = (code, input) => {
  const result = spawnSync(process.env.PYTHON_EXECUTABLE || 'python', ['-c', code], {cwd:fileURLToPath(new URL('../../',import.meta.url)),input:JSON.stringify(input), encoding:'utf8', env:{...process.env,PYTHONIOENCODING:'utf-8'}});
  assert.equal(result.status,0,result.stderr); return JSON.parse(result.stdout);
};
async function initialize(state) {
  const db = new D1DB(new SQLiteBinding());
  const parts = python("import sys,json; from src.partitions import partition_state; i=json.load(sys.stdin); print(json.dumps(partition_state(i)))", state);
  const encrypted = {};
  for (const [id,part] of Object.entries(parts)) encrypted[id] = await seal(part,key);
  assert.equal(await db.commit(0,encrypted),1);
  return db;
}
async function restore(db) {
  const index = await db.index(), parts = {};
  for (const id of index.parts) parts[id] = await open((await db.read(index.version,id)).ciphertext,key);
  return python("import sys,json; from src.partitions import merge_parts; print(json.dumps(merge_parts(json.load(sys.stdin))))",parts);
}
test('D1 real SQL CAS cannot overwrite a newer checkpoint', async () => {
  const db = await initialize(blank());
  assert.equal(await db.commit(1,{meta:await seal({...blank(),offset:2},key)}),2);
  assert.equal(await db.commit(1,{meta:await seal({...blank(),offset:99},key)}),null);
  assert.equal((await restore(db)).offset,2);
  await assert.rejects(db.read(1,'meta'));
});
test('15 students stay isolated and command writes preserve full assignment descriptions', async () => {
  const state = blank();
  const description = 'Descripción completa '.repeat(1000);
  for (let i=0;i<15;i++) state.users[1000+i] = {onboarding:false, token:`SYNTHETIC_TOKEN_${i}`,canvas_user_id:i+1,initialized:true,courses:[{id:10,name:'Interfaces'}],tasks:{'10:1':{data:{id:1,course_id:10,course_name:'Interfaces',name:'Práctica',description,created_at:null,unlock_at:null,due_at:null,points:10,url:'https://medac.instructure.com/courses/10/assignments/1',submission_state:'unsubmitted',submitted:false,excused:false,requires_submission:true},revision:0,reminders:{}}}};
  const db = await initialize(state);
  const before = await db.read(1,await hash('1000')+':full');
  const store = await db.scoped('1000',key);
  const cached = (await store.load()).state;
  assert.equal(cached.users['1000'].tasks['10:1'].data.description.length,400);
  assert.equal(cached.users['1001'].token,undefined);
  const sent = [];
  const bot = new ImmediateBot({max_users:15,test_mode:true,timezone:'Europe/Madrid',canvas_base_url:'https://medac.instructure.com',recent_days:7},store,{async send(chat,text){sent.push({chat,text});return sent.length;},async remove(){return true;}},()=>{throw new Error('No Canvas call for commands');},messages);
  await bot.receive({update_id:5,message:{message_id:5,text:'/resumen',chat:{id:1000,type:'private'},from:{id:1000,is_bot:false}}});
  await bot.receive({update_id:5,message:{message_id:5,text:'/resumen',chat:{id:1000,type:'private'},from:{id:1000,is_bot:false}}});
  assert.equal(sent.length,1);
  const after = await db.index();
  assert.equal((await db.read(after.version,await hash('1000')+':full')).ciphertext,before.ciphertext);
  const restored = await restore(db);
  assert.equal(restored.users['1000'].tasks['10:1'].data.description,description);
  assert.equal(Object.keys(restored.users).length,15);
});
test('registration and disconnect use the same partition schema as Python', async () => {
  const db = await initialize(blank());
  const store = await db.scoped('111',key);
  await store.change(state=>{state.users['111']={onboarding:true,tasks:{},courses:[]};});
  assert.equal((await restore(db)).users['111'].onboarding,true);
  await store.change(state=>{delete state.users['111'];});
  assert.deepEqual((await restore(db)).users,{});
});
