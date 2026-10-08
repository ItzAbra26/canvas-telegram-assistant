import { hash, open, validateState } from './codec.mjs';
import { ImmediateBot, StateStore, Telegram, TelegramError } from './bot.mjs';
import { InitialCanvas } from './canvas.mjs';
import { Conflict, validPart } from './d1-db.mjs';

export function createHandler(db, messages, cleanHTML, fetcher = fetch, background = promise => promise.catch(() => {})) {
  return async request => {
    if (request.method === 'GET') return Response.json({ service: 'canvas-telegram-assistant', status: 'ready' });
    if (request.method !== 'POST') return new Response('Method not allowed', { status: 405 });
    try {
      const webhookKey = request.headers.get('X-Telegram-Bot-Api-Secret-Token');
      const stateKey = request.headers.get('X-Canvas-State-Key');
      if ((!webhookKey && !stateKey) || (webhookKey && stateKey)) return new Response('Unauthorized', { status: 401 });
      const runtime = await db.runtime();
      const key = webhookKey || stateKey;
      const kind = webhookKey ? 'webhook' : 'state';
      if (!runtime || key.length !== 43 || await hash(key) !== runtime[kind + '_hash']) return new Response('Unauthorized', { status: 401 });
      // Only the caller holds the wrapping key. Database backups hold ciphertext + hashes.
      const config = await open(runtime[kind + '_config'], key);
      if (db.partitioned) config.page_size = 8;
      const raw = await request.text();
      if (raw.length > (webhookKey ? 100000 : 11000000)) return new Response('Too large', { status: 413 });
      let body;
      try { body = JSON.parse(raw); } catch { return new Response('Invalid JSON', { status: 400 }); }
      const telegram = new Telegram(config.telegram_token, fetcher);
      if (stateKey) {
        if (body.action === 'load') return Response.json(db.partitioned ? await db.index() : await db.load());
        if (body.action === 'read' && db.partitioned) {
          if (!Number.isSafeInteger(body.version) || !validPart(body.id)) return new Response('Invalid partition', {status:400});
          return Response.json(await db.read(body.version, body.id));
        }
        if (body.action === 'save') {
          if (db.partitioned) {
            const parts = body.parts, deleted = body.deleted || [];
            if (!Number.isSafeInteger(body.version) || body.version < 0 || !parts || typeof parts !== 'object' || Array.isArray(parts) || !Array.isArray(deleted) || Object.keys(parts).length + deleted.length > 49 || deleted.some(id => !validPart(id) || id === 'meta') || Object.entries(parts).some(([id, value]) => !validPart(id) || typeof value !== 'string' || value.length > 1800000 || !/^[A-Za-z0-9_=-]+$/.test(value))) return new Response('Invalid partitions', {status:400});
            const version = await db.commit(body.version, parts, deleted);
            return version === null ? new Response('Concurrent checkpoint', {status:409}) : Response.json({version});
          }
          if (!Number.isSafeInteger(body.version) || body.version < 0 || typeof body.ciphertext !== 'string' || body.ciphertext.length > 10000000) return new Response('Invalid checkpoint', { status: 400 });
          validateState(await open(body.ciphertext, config.encryption_key));
          const version = await db.save(body.version, body.ciphertext);
          return version === null ? new Response('Concurrent checkpoint', { status: 409 }) : Response.json({ version });
        }
        if (body.action === 'setup') {
          const identity = await telegram.call('getMe', {});
          if (!identity?.is_bot || !identity.username) throw new Error('Invalid bot');
          await telegram.call('setMyCommands', { commands: Object.entries(messages.commands).map(([command, description]) => ({ command, description })) });
          const result = await telegram.call('setWebhook', { url: config.webhook_url, ip_address: config.webhook_ip || undefined, secret_token: config.webhook_secret, max_connections: 1, allowed_updates: ['message'], drop_pending_updates: false });
          if (result !== true) throw new Error('Webhook not confirmed');
          return Response.json({ bot_username: identity.username, webhook_url: config.webhook_url, webhook_installed: true });
        }
        if (body.action === 'status') {
          const info = await telegram.call('getWebhookInfo', {});
          return Response.json({ webhook_active: info.url === config.webhook_url, pending_updates: info.pending_update_count, last_error_date: info.last_error_date || null });
        }
        if (body.action === 'smoke') {
          const store = db.makeSmokeStore ? await db.makeSmokeStore(config, config.encryption_key) : new StateStore(db, config.encryption_key);
          const { state } = await store.load();
          const uid = Object.keys(state.users)[0];
          if (!uid) return Response.json({ delivered: false, reason: 'No users registered' });
          const start = performance.now();
          await telegram.send(Number(uid), '✅ <b>Respuestas inmediatas activadas</b>\nAhora /start, /ayuda y los demás comandos responden al escribirlos. Canvas se actualiza cada hora. Prueba /estado.');
          return Response.json({ delivered: true, milliseconds: Math.round(performance.now() - start) });
        }
        return new Response('Unknown action', { status: 400 });
      }
      if (!Number.isSafeInteger(body?.update_id)) return new Response('Invalid update', { status: 400 });
      const store = db.makeStore ? await db.makeStore(config, config.encryption_key, body) : new StateStore(db, config.encryption_key);
      const bot = new ImmediateBot(config, store, telegram, token => new InitialCanvas(config.canvas_base_url, token, cleanHTML, fetcher), messages);
      const connection = await bot.receive(body);
      if (connection) background(bot.firstSync(connection));
      return Response.json({ ok: true });
    } catch (error) {
      if (error instanceof Conflict) return new Response('Concurrent checkpoint', {status:409});
      if (error instanceof TelegramError) return Response.json({ service: 'Telegram', status: error.status, operation: error.operation || null, reason: error.reason || 'network', diagnostic: error.diagnostic || null, retry_after: error.retry, network_error: !error.status }, {status:503});
      // No exception URLs, incoming messages, IDs, tokens, or provider response bodies.
      console.error('Canvas bot: request failed; retained checkpoint.');
      return new Response('Temporarily unavailable', { status: 503 });
    }
  };
}
