import copy
from dataclasses import replace
from datetime import timedelta

import pytest

from src.errors import AmbiguousDelivery, RejectedDelivery, StorageError
from src.models import Snapshot
from src.notifier import dispatch, enqueue, reconcile, recover_inflight


def messages(state):
    return [e.get("text", "") for e in state["outbox"].values()]


def test_baseline_then_new_task_no_duplicates(state, snapshot, task, config, now):
    reconcile(state, "111", snapshot, config, now)
    assert not any("NUEVA TAREA" in m for m in messages(state))
    new = replace(task, id=2, due_at=None)
    snapshot.assignments.append(new)
    reconcile(state, "111", snapshot, config, now + timedelta(minutes=15))
    assert sum("NUEVA TAREA" in m for m in messages(state)) == 1
    original_count = len(state["outbox"])
    reconcile(state, "111", snapshot, config, now + timedelta(minutes=30))
    assert len(state["outbox"]) == original_count


@pytest.mark.parametrize("name,hours", [("7d", 168), ("3d", 72), ("24h", 24), ("3h", 3)])
def test_each_reminder_once(name, hours, state, snapshot, config, now):
    reconcile(state, "111", snapshot, config, now)
    due = now + timedelta(days=8)
    before = due - timedelta(hours=hours, minutes=5)
    after = due - timedelta(hours=hours) + timedelta(minutes=8)
    reconcile(state, "111", snapshot, config, before)
    reconcile(state, "111", snapshot, config, after)
    reconcile(state, "111", snapshot, config, after + timedelta(minutes=15))
    assert sum(f"RECORDATORIO · {name}" in m for m in messages(state)) == 1


def test_outage_only_most_urgent(state, snapshot, config, now):
    reconcile(state, "111", snapshot, config, now)
    reconcile(state, "111", snapshot, config, now + timedelta(days=7, hours=22))
    assert sum("RECORDATORIO" in m for m in messages(state)) == 1
    assert any("RECORDATORIO · 3h" in m for m in messages(state))


def test_changes_deadline_description_points_and_submitted(state, snapshot, task, config, now):
    reconcile(state, "111", snapshot, config, now)
    updated = replace(
        task,
        due_at=(now + timedelta(days=12)).isoformat(),
        description="Haz otra cosa",
        points=20,
        submitted=True,
    )
    snapshot.assignments = [updated]
    reconcile(state, "111", snapshot, config, now + timedelta(minutes=15))
    text = "\n".join(messages(state))
    assert "Fecha anterior" in text and "Nueva fecha" in text
    assert "Descripción actualizada" in text and "10 → 20" in text and "has entregado" in text
    count = len(state["outbox"])
    reconcile(state, "111", snapshot, config, now + timedelta(minutes=30))
    assert len(state["outbox"]) == count


def test_deletion_requires_two_successful_confirmations(state, snapshot, task, config, now):
    reconcile(state, "111", snapshot, config, now)
    missing = Snapshot(snapshot.courses, [], {task.key})
    reconcile(state, "111", missing, config, now + timedelta(minutes=15))
    assert not state["users"]["111"]["tasks"][task.key]["deleted"]
    reconcile(state, "111", missing, config, now + timedelta(minutes=30))
    assert state["users"]["111"]["tasks"][task.key]["deleted"]
    reconcile(state, "111", snapshot, config, now + timedelta(minutes=45))
    assert not state["users"]["111"]["tasks"][task.key]["deleted"]
    assert any("vuelve a estar disponible" in m for m in messages(state))


def test_dispatch_persists_before_send_and_no_repeat(state, store, telegram, now):
    enqueue(state, "e1", 111, "Hola")
    original = telegram.send_message

    def send(chat, text):
        assert store.load()["outbox"]["e1"]["status"] == "sending"
        return original(chat, text)

    telegram.send_message = send
    assert dispatch(state, store, telegram, now)
    assert store.load()["outbox"]["e1"]["status"] == "sent"
    dispatch(state, store, telegram, now)
    assert telegram.sent == [(111, "Hola")]


def test_storage_failure_prevents_send(state, telegram, now):
    enqueue(state, "e1", 111, "Hola")

    class FailingStore:
        def save(self, data):
            raise StorageError("checkpoint failed")

    with pytest.raises(StorageError):
        dispatch(state, FailingStore(), telegram, now)
    assert not telegram.sent


def test_ambiguous_send_not_retried(state, store, telegram, now):
    enqueue(state, "e1", 111, "Hola")

    def ambiguous(chat, text):
        raise AmbiguousDelivery("timeout")

    telegram.send_message = ambiguous
    assert not dispatch(state, store, telegram, now)
    assert state["outbox"]["e1"]["status"] == "uncertain"
    assert dispatch(state, store, telegram, now)


def test_crash_inflight_not_retried(state, store, telegram, now):
    enqueue(state, "e1", 111, "Hola")
    state["outbox"]["e1"]["status"] = "sending"
    recover_inflight(state)
    dispatch(state, store, telegram, now)
    assert not telegram.sent and state["outbox"]["e1"]["status"] == "uncertain"


def test_rate_limit_preserved_for_retry(state, store, telegram, now):
    enqueue(state, "e1", 111, "Hola")
    original = telegram.send_message

    def limited(chat, text):
        raise RejectedDelivery(429, 120)

    telegram.send_message = limited
    assert not dispatch(state, store, telegram, now)
    assert state["outbox"]["e1"]["status"] == "pending"
    telegram.send_message = original
    dispatch(state, store, telegram, now + timedelta(seconds=30))
    assert not telegram.sent
    dispatch(state, store, telegram, now + timedelta(seconds=121))
    assert telegram.sent == [(111, "Hola")]


def test_cancel_obsolete_reminder(state, snapshot, task, config, now, store, telegram):
    reconcile(state, "111", snapshot, config, now)
    reconcile(state, "111", snapshot, config, now + timedelta(days=7, hours=6))
    reminder_id = next(k for k, event in state["outbox"].items() if event.get("kind") == "reminder")
    state["users"]["111"]["tasks"][task.key]["data"]["submitted"] = True
    dispatch(state, store, telegram, now + timedelta(days=7, hours=6))
    assert state["outbox"][reminder_id]["status"] == "cancelled"


def test_two_users_independent_reminders(state, snapshot, task, config, now):
    state["users"]["222"] = copy.deepcopy(state["users"]["111"])
    reconcile(state, "111", snapshot, config, now)
    delivered = Snapshot(snapshot.courses, [replace(task, submitted=True)], set())
    reconcile(state, "222", delivered, config, now)
    reconcile(state, "111", snapshot, config, now + timedelta(days=7, hours=2))
    reconcile(state, "222", delivered, config, now + timedelta(days=7, hours=2))
    reminders = [event for event in state["outbox"].values() if event.get("kind") == "reminder"]
    assert reminders and all(event["chat_id"] == 111 for event in reminders)
