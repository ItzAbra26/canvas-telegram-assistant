import copy
import json
from dataclasses import replace

import pytest
import responses

from src.demo import demo_update
from src.errors import StorageConflict, StorageError
from src.remote_store import RemoteStore, _rebase, _replace_in_place
from src.service import BotService
from src.storage import EncryptedCodec

URL = "https://example.supabase.co/functions/v1/canvas-telegram-immediate"


def test_rebase_preserves_buttons_and_sync_without_overwriting_each_other(state):
    local, remote = copy.deepcopy(state), copy.deepcopy(state)
    local["users"]["111"]["last_sync"] = "2026-10-09T12:00:00Z"
    remote["webhook_updates"] = {"500": "2026-10-09T12:00:01Z"}
    remote["offset"] = 501
    remote["users"]["111"]["delivery"] = {"stage": "ready"}
    remote["outbox"]["update:500"] = {"status": "sent", "chat_id": 111}
    merged = _rebase(state, local, remote)
    assert merged["users"]["111"]["last_sync"] == local["users"]["111"]["last_sync"]
    assert merged["users"]["111"]["delivery"] == {"stage": "ready"}
    assert merged["offset"] == 501 and merged["outbox"]["update:500"]["status"] == "sent"


@pytest.mark.parametrize("change", ["disconnect", "token", "canvas_user_id", "connected_at"])
def test_rebase_never_applies_a_snapshot_to_a_different_connection(state, change):
    local, remote = copy.deepcopy(state), copy.deepcopy(state)
    local["users"]["111"]["last_sync"] = "2026-10-09T12:00:00Z"
    if change == "disconnect":
        del remote["users"]["111"]
    else:
        remote["users"]["111"][change] = "different"
    with pytest.raises(StorageConflict):
        _rebase(state, local, remote)


def test_rebase_cannot_claim_an_event_already_claimed_by_another_process(state):
    state["outbox"]["event"] = {"chat_id": 111, "text": "Hello", "status": "pending"}
    local, remote = copy.deepcopy(state), copy.deepcopy(state)
    local["outbox"]["event"]["status"] = remote["outbox"]["event"]["status"] = "sending"
    with pytest.raises(StorageConflict):
        _rebase(state, local, remote)


def test_rebase_rejects_conflicting_updates_to_same_task(state):
    local, remote = copy.deepcopy(state), copy.deepcopy(state)
    state["users"]["111"]["last_sync"] = "old"
    local["users"]["111"]["last_sync"] = "checker"
    remote["users"]["111"]["last_sync"] = "manual-refresh"
    with pytest.raises(StorageConflict):
        _rebase(state, local, remote)


@responses.activate
def test_atomic_snapshot_and_rebase_save_keep_notifier_references(config, state):
    from src.partitions import partition_state

    codec = EncryptedCodec(config.encryption_key)
    state["outbox"]["event"] = {"chat_id": 111, "text": "Hello", "status": "pending"}
    remote = copy.deepcopy(state)
    remote["offset"] = 7
    remote["users"]["111"]["delivery"] = {"stage": "ready"}

    def snapshot(value, version):
        parts = {
            key: codec.encode(part).decode()
            for key, part in partition_state(value).items()
            if not key.endswith(":view")
        }
        return {
            "version": version,
            "format": "partitioned",
            "parts": list(parts),
            "ciphertexts": parts,
        }

    replies = [snapshot(state, 1), None, snapshot(remote, 2), {"version": 3}]
    calls = []

    def callback(request):
        calls.append(json.loads(request.body))
        value = replies.pop(0)
        return (
            409 if value is None else 200,
            {"Content-Type": "application/json"},
            json.dumps(value or {}),
        )

    responses.add_callback(responses.POST, URL, callback=callback)
    store = RemoteStore(URL, "synthetic_key", config.encryption_key)
    loaded = store.load()
    assert len(calls) == 1
    event = loaded["outbox"]["event"]
    event["status"] = "sending"
    store.save(loaded)
    assert store.version == 3 and loaded["offset"] == 7
    assert loaded["outbox"]["event"] is event
    assert loaded["users"]["111"]["delivery"]["stage"] == "ready"
    assert [call["action"] for call in calls] == ["load", "save", "load", "save"]


def test_replace_in_place_keeps_mutable_references(state):
    user = state["users"]["111"]
    changed = copy.deepcopy(state)
    changed["users"]["111"]["last_sync"] = "synthetic"
    _replace_in_place(state, changed)
    assert state["users"]["111"] is user and user["last_sync"] == "synthetic"


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
