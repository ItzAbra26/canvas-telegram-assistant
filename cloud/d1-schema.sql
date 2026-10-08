CREATE TABLE IF NOT EXISTS bot_checkpoint (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  revision INTEGER NOT NULL DEFAULT 0,
  nonce TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS bot_parts (
  id TEXT PRIMARY KEY,
  ciphertext TEXT NOT NULL CHECK(length(ciphertext) < 1800001)
);
CREATE TABLE IF NOT EXISTS bot_configuration (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  webhook_hash TEXT NOT NULL,
  webhook_config TEXT NOT NULL,
  state_hash TEXT NOT NULL,
  state_config TEXT NOT NULL
);
INSERT OR IGNORE INTO bot_checkpoint (id) VALUES (1);
