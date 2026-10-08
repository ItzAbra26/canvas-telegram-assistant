import gzip
import json
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import dotenv_values

from scripts.cloud_admin import bootstrap


def test_bootstrap_encrypts_all_secrets_and_configure_keeps_the_checkpoint(
    tmp_path, monkeypatch, config
):
    schema = Path("cloud/d1-schema.sql").read_text(encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("scripts.cloud_admin.Config.from_env", lambda: config)
    Path(".env").write_text("", encoding="utf-8")
    target = Path("data/setup.sql")
    bootstrap("https://synthetic.workers.dev/", target, None, False)
    sql = target.read_text(encoding="utf-8")
    values = dotenv_values(".env")
    assert config.telegram_token not in sql and config.encryption_key not in sql
    assert values["STATE_API_KEY"] not in sql and values["TELEGRAM_WEBHOOK_SECRET"] not in sql
    connection = sqlite3.connect(":memory:")
    connection.executescript(schema + sql)
    ciphertext = connection.execute("SELECT webhook_config FROM bot_configuration").fetchone()[0]
    runtime = json.loads(
        gzip.decompress(
            Fernet((values["TELEGRAM_WEBHOOK_SECRET"] + "=").encode()).decrypt(ciphertext.encode())
        )
    )
    assert runtime["telegram_token"] == config.telegram_token
    assert runtime["max_users"] == 15
    original = connection.execute("SELECT * FROM bot_parts").fetchall()
    bootstrap("https://synthetic.workers.dev/", target, None, False, runtime_only=True)
    connection.executescript(target.read_text(encoding="utf-8"))
    assert connection.execute("SELECT * FROM bot_parts").fetchall() == original
    assert dotenv_values(".env")["STATE_API_KEY"] == values["STATE_API_KEY"]
    connection.close()
