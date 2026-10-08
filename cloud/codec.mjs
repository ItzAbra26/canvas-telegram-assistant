// Fernet compatible with Python cryptography; all crypto uses Web Crypto.
const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true });
const join = (...parts) => {
  const result = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let offset = 0;
  for (const part of parts) { result.set(part, offset); offset += part.length; }
  return result;
};
export const unbase64 = (value) => Uint8Array.from(atob(value.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
export const base64 = (bytes) => {
  let value = '';
  for (let i = 0; i < bytes.length; i += 8192) value += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return btoa(value).replace(/\+/g, '-').replace(/\//g, '_');
};
async function keys(secret) {
  const raw = unbase64(secret);
  if (raw.length !== 32) throw new Error('Invalid encryption key');
  return {
    mac: await crypto.subtle.importKey('raw', raw.subarray(0, 16), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']),
    aes: await crypto.subtle.importKey('raw', raw.subarray(16), 'AES-CBC', false, ['encrypt', 'decrypt']),
  };
}
async function transform(bytes, stream) {
  const body = new Blob([bytes]).stream().pipeThrough(stream);
  return new Uint8Array(await new Response(body).arrayBuffer());
}
export async function seal(value, secret) {
  const key = await keys(secret);
  const iv = crypto.getRandomValues(new Uint8Array(16));
  const head = new Uint8Array(9);
  head[0] = 0x80;
  new DataView(head.buffer).setBigUint64(1, BigInt(Math.floor(Date.now() / 1000)));
  const plain = await transform(encoder.encode(JSON.stringify(value)), new CompressionStream('gzip'));
  const encrypted = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-CBC', iv }, key.aes, plain));
  const signed = join(head, iv, encrypted);
  const signature = new Uint8Array(await crypto.subtle.sign('HMAC', key.mac, signed));
  return base64(join(signed, signature));
}
export async function open(value, secret) {
  const key = await keys(secret);
  const bytes = unbase64(value);
  if (bytes.length < 73 || bytes[0] !== 0x80) throw new Error('Invalid ciphertext');
  if (!await crypto.subtle.verify('HMAC', key.mac, bytes.subarray(-32), bytes.subarray(0, -32))) throw new Error('Invalid signature');
  let plain = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-CBC', iv: bytes.subarray(9, 25) }, key.aes, bytes.subarray(25, -32)));
  if (plain[0] === 31 && plain[1] === 139) plain = await transform(plain, new DecompressionStream('gzip'));
  return JSON.parse(decoder.decode(plain));
}
export async function hash(value) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(value)))].map(b => b.toString(16).padStart(2, '0')).join('');
}
export function validateState(state) {
  if (!state || state.version !== 1 || !state.users || !state.outbox || !Number.isSafeInteger(state.offset)) throw new Error('Invalid state');
  return state;
}
