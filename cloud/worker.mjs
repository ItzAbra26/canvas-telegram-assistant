import { convert } from 'html-to-text';
import messages from './messages.json' with { type: 'json' };
import { D1DB } from './d1-db.mjs';
import { createHandler } from './handler.mjs';

const cleanHTML = html => convert(html || '', { wordwrap: false, selectors: [
  { selector: 'script', format: 'skip' }, { selector: 'style', format: 'skip' },
  { selector: 'a', options: { ignoreHref: true } },
] }).replace(/\s+/g, ' ').trim();
export default {
  fetch(request, env, ctx) {
    return createHandler(new D1DB(env.BOT_DB), messages, cleanHTML, fetch,
      promise => ctx.waitUntil(promise.catch(() => console.error('Initial sync deferred to hourly check.'))))(request);
  },
};
