import json
from dataclasses import replace

import pytest
import responses

from src.demo import demo_update
from src.errors import StorageError
from src.remote_store import RemoteStore
from src.service import BotService
from src.storage import EncryptedCodec

URL = "https://example.supabase.co/functions/v1/canvas-telegram-immediate"


@responses.activate
def test_shared_state_load_save_and_unchanged_checkpoint(config, state):
    codec = EncryptedCodec(config.encryption_key)
    responses.post(URL, json={"version": 3, "ciphertext": codec.encode(state).decode()})
    responses.post(URL, json={"version": 4})
    store = RemoteStore(URL, "synthetic_access_key", config.encryption_key)
    assert store.load() == state
    state["offset"] = 101
    store.save(state)
    saved = json.loads(responses.calls[1].request.body)
    assert saved["version"] == 3 and codec.decode(saved["ciphertext"].encode()) == state
    assert responses.calls[0].request.headers["X-Canvas-State-Key"] == "synthetic_access_key"
    assert "Authorization" not in responses.calls[0].request.headers
    store.save(state)
    assert len(responses.calls) == 2


@responses.activate
def test_concurrent_webhook_write_stops_checker_without_overwriting(config, state):
    codec = EncryptedCodec(config.encryption_key)
    responses.post(URL, json={"version": 1, "ciphertext": codec.encode(state).decode()})
    responses.post(URL, status=409)
    store = RemoteStore(URL, "synthetic_key", config.encryption_key)
    store.load()
    state["offset"] = 99
    with pytest.raises(StorageError):
        store.save(state)
    assert store.version == 1


@responses.activate
@pytest.mark.parametrize("body", [None, {}, {"version": 1, "ciphertext": "corrupt"}])
def test_missing_or_corrupt_remote_state_is_never_reset(config, body):
    responses.post(URL, json=body)
    with pytest.raises(StorageError):
        RemoteStore(URL, "synthetic_key", config.encryption_key).load()
    assert len(responses.calls) == 1


def test_remote_save_requires_prior_load(config, state):
    with pytest.raises(StorageError):
        RemoteStore(URL, "synthetic_key", config.encryption_key).save(state)


def test_hourly_cycle_does_not_poll_telegram(config, store, telegram, state, snapshot, now):
    from tests.test_service import factory_with

    store.save(state)
    telegram.updates = [demo_update(100, 111, "/desconectar")]

    def forbidden(*args, **kwargs):
        raise AssertionError("Hourly job must not read Telegram updates")

    telegram.get_updates = forbidden
    assert BotService(config, store, telegram, factory_with(snapshot)).cycle(
        now, receive_updates=False
    )
    saved = store.load()
    assert saved["users"]["111"]["initialized"] and saved["offset"] == state["offset"]


def test_webhook_main_accepts_active_webhook(monkeypatch, config, telegram):
    from src.main import main

    monkeypatch.setattr("sys.argv", ["bot", "--sync-only"])
    monkeypatch.setattr(
        "src.main.Config.from_env", lambda: replace(config, delivery_mode="webhook")
    )
    telegram.webhook_info = lambda: {"url": URL}
    monkeypatch.setattr("src.main.TelegramBot", lambda token: telegram)
    observed = []
    monkeypatch.setattr(
        "src.main.BotService.cycle",
        lambda self, now, receive_updates: observed.append(receive_updates) or True,
    )
    assert main() == 0 and observed == [False]
