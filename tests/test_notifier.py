import copy
from dataclasses import replace
from datetime import timedelta

import pytest

from src.errors import AmbiguousDelivery, RejectedDelivery, StorageError
from src.models import Snapshot
from src.notifier import dispatch, enqueue, reconcile, recover_inflight


def messages(state):
    return [e.get("text", "") for e in state["outbox"].values()]


def test_scorm_never_alerts_and_pending_legacy_notices_are_cancelled(
    state, snapshot, task, config, now, store, telegram
):
    reconcile(state, "111", snapshot, config, now)
    enqueue(state, f"u111:task:{task.key}:new", 111, "Old task notice")
    snapshot.assignments = [replace(task, name="Unidad 1 · sCoRm")]
    reconcile(state, "111", snapshot, config, now + timedelta(hours=1))
    assert state["users"]["111"]["tasks"][task.key]["ignored"]
    assert dispatch(state, store, telegram, now)
    assert all("Old task notice" not in text for _, text in telegram.sent)
    assert state["outbox"][f"u111:task:{task.key}:new"]["status"] == "cancelled"


def test_manual_refresh_does_not_swallow_changes_or_new_task_alerts(
    state, snapshot, task, config, now
):
    reconcile(state, "111", snapshot, config, now)
    record = state["users"]["111"]["tasks"][task.key]
    record["hourly_data"] = copy.deepcopy(record["data"])
    updated = replace(task, description="Descripción nueva", due_at=None)
    record["data"] = updated.to_dict()
    new = replace(task, id=2)
    new_record = copy.deepcopy(record)
    new_record.update(data=new.to_dict(), manual_unnotified=True)
    state["users"]["111"]["tasks"][new.key] = new_record
    snapshot.assignments = [updated, new]
    reconcile(state, "111", snapshot, config, now + timedelta(hours=1))
    assert sum("NUEVA TAREA" in text for text in messages(state)) == 1
    assert any("Descripción actualizada" in text for text in messages(state))
    assert any("Fecha anterior" in text for text in messages(state))
    assert "hourly_data" not in record
    count = len(state["outbox"])
    reconcile(state, "111", snapshot, config, now + timedelta(hours=2))
    assert len(state["outbox"]) == count


def test_abandoned_delivery_is_uncertain_and_never_requeued(state, now):
    delivery = {
        "stage": "processing",
        "started_at": (now - timedelta(minutes=3)).isoformat(),
        "nonce": "synthetic-operation",
        "file": {"file_id": "synthetic-private-file"},
    }
    state["users"]["111"]["delivery"] = delivery
    recover_inflight(state, now)
    assert delivery["stage"] == "uncertain"
    assert "file" not in delivery
    assert len(state["outbox"]) == 1
    recover_inflight(state, now + timedelta(minutes=10))
    assert len(state["outbox"]) == 1


def test_hourly_retry_preserves_buttons_from_webhook(state, store, telegram, now):
    sent = []
    markup = {"inline_keyboard": [[{"text": "Resumen", "callback_data": "cmd:resumen:1"}]]}

    def send(chat, text, reply_markup=None):
        sent.append((chat, text, reply_markup))
        return 42

    telegram.send_message = send
    enqueue(state, "interactive-retry", 111, "Respuesta", reply_markup=markup)
    assert dispatch(state, store, telegram, now)
    assert sent == [(111, "Respuesta", markup)]


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
