import copy
import json

import pytest
import responses

from src.errors import StorageConflict, StorageError
from src.notifier import recover_inflight
from src.partitions import merge_parts, partition_state, scope_id
from src.remote_store import RemoteStore
from src.storage import EncryptedCodec, empty_state

URL = "https://bot.example.workers.dev/"


def test_partition_roundtrip_preserves_tokens_descriptions_and_unregistered_responses(state, task):
    state["users"]["111"]["tasks"] = {
        "10:1": {"data": task.to_dict(), "revision": 0, "reminders": {}}
    }
    state["outbox"]["orphan"] = {"chat_id": 222, "text": "Ayuda", "status": "pending"}
    original = copy.deepcopy(state)
    parts = partition_state(state)
    assert merge_parts(parts) == original
    assert state == original
    assert all("111" not in name and "222" not in name for name in parts)
    assert (
        len(
            parts[scope_id("111") + ":view"]["users"]["111"]["tasks"]["10:1"]["data"]["description"]
        )
        <= 400
    )


def test_missing_student_partition_is_never_replaced_with_empty_data(state):
    parts = partition_state(state)
    del parts[scope_id("111") + ":full"]
    with pytest.raises(StorageError):
        merge_parts(parts)
    with pytest.raises(StorageError):
        merge_parts({})


@responses.activate
def test_remote_partitioned_checkpoint_saves_only_changed_parts(config, state):
    codec = EncryptedCodec(config.encryption_key)
    parts = partition_state(state)
    responses.post(
        URL,
        json={
            "version": 8,
            "format": "partitioned",
            "parts": [p for p in parts if not p.endswith(":view")],
        },
    )
    for name, part in parts.items():
        if not name.endswith(":view"):
            responses.post(URL, json={"ciphertext": codec.encode(part).decode()})
    responses.post(URL, json={"version": 9})
    store = RemoteStore(URL, "synthetic", config.encryption_key)
    assert store.load() == state
    state["users"]["111"]["sync_error"] = "network"
    store.save(state)
    saved = json.loads(responses.calls[-1].request.body)
    assert list(saved["parts"]) == [scope_id("111") + ":control"]
    assert saved["deleted"] == [] and saved["version"] == 8
    store.save(state)
    assert json.loads(responses.calls[-1].request.body) == saved


@responses.activate
def test_partition_read_conflict_does_not_mix_snapshots(config):
    responses.post(URL, json={"version": 8, "format": "partitioned", "parts": ["meta"]})
    responses.post(URL, status=409)
    store = RemoteStore(URL, "synthetic", config.encryption_key)
    with pytest.raises(StorageConflict):
        store.load()
    assert store.version is None


def test_hourly_recovery_does_not_interrupt_live_webhook_delivery(now):
    state = empty_state()
    state["outbox"]["live"] = {
        "chat_id": 111,
        "status": "sending",
        "text": "Live",
        "started_at": now.isoformat(),
    }
    state["outbox"]["old"] = {
        "chat_id": 111,
        "status": "sending",
        "text": "Old",
        "started_at": "2020-01-01T00:00:00Z",
    }
    recover_inflight(state, now)
    assert state["outbox"]["live"]["status"] == "sending"
    assert state["outbox"]["old"]["status"] == "uncertain"
