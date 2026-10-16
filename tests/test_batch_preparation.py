import copy
from datetime import timedelta

import pytest

from src.compression_worker import run_once
from src.errors import BotError
from src.notifier import dispatch


def batch(state, now, sizes=(100000, 200000)):
    files = []
    for i, size in enumerate(sizes):
        file = {
            "file_id": f"SOURCE_{i}",
            "name": f"parte{i}.pdf",
            "size": size,
            "mime": "application/pdf",
        }
        files.append(
            {
                "id": str(i),
                "file": copy.deepcopy(file),
                "original_file": file,
                "optimization": {"status": "queued", "requested_at": now.isoformat()},
            }
        )
    session = {
        "stage": "optimizing",
        "nonce": "a" * 32,
        "batch_id": "batch",
        "files": files,
        "key": "10:1",
        "connected_at": state["users"]["111"]["connected_at"],
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "name": "Tarea",
        "course_name": "Asignatura",
        "due_at": None,
    }
    state["users"]["111"]["delivery"] = session
    return session


class Transport:
    def __init__(self):
        self.downloads = self.uploads = 0

    def download(self, file):
        self.downloads += 1
        return b"x" * file["size"]

    def upload(self, uid, file, data):
        self.uploads += 1
        return {**file, "file_id": "OPT_" + file["file_id"], "size": len(data)}


def test_every_pdf_is_prepared_and_only_full_list_is_confirmable(state, store, now):
    batch(state, now)
    store.save(state)
    transport = Transport()
    assert run_once(store, transport, lambda f, b: (b[:2000], "improved"), now) == 2
    result = store.load()
    session = result["users"]["111"]["delivery"]
    assert session["stage"] == "ready"
    assert [item["file"]["file_id"] for item in session["files"]] == [
        "OPT_SOURCE_0",
        "OPT_SOURCE_1",
    ]
    assert all("optimization" not in item for item in session["files"])
    previews = [event for key, event in result["outbox"].items() if ":prepare:" in key]
    assert len(previews) == 1
    assert "parte0.pdf" in previews[0]["text"] and "parte1.pdf" in previews[0]["text"]
    assert (
        previews[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        == "confirm:" + session["nonce"]
    )
    assert transport.uploads == 2
    assert run_once(store, transport, now=now) == 0


def test_new_document_during_preparation_is_preserved_and_prepared_on_next_run(state, store, now):
    batch(state, now, (100000,))
    store.save(state)
    t = Transport()

    def adding(file, data):
        current = store.load()
        session = current["users"]["111"]["delivery"]
        entry = copy.deepcopy(session["files"][0])
        entry.update(id="second", file={**entry["file"], "file_id": "SECOND", "name": "anexo.pdf"})
        entry["original_file"] = copy.deepcopy(entry["file"])
        entry["optimization"] = {"status": "queued", "requested_at": now.isoformat()}
        session["files"].append(entry)
        session["nonce"] = "b" * 32
        store.save(current)
        return data[:2000], "improved"

    assert run_once(store, t, adding, now) == 1
    result = store.load()
    assert len(result["users"]["111"]["delivery"]["files"]) == 2
    assert result["users"]["111"]["delivery"]["stage"] == "optimizing"
    assert not result["outbox"]
    assert run_once(store, t, lambda f, b: (b[:2000], "improved"), now) == 1
    assert store.load()["users"]["111"]["delivery"]["stage"] == "ready"
    assert t.uploads == 2


@pytest.mark.parametrize(
    "change", ["remove", "originals", "disconnect", "new_selection", "new_account"]
)
def test_changed_selection_or_account_is_never_overwritten(state, store, now, change):
    batch(state, now, (100000,))
    store.save(state)
    t = Transport()

    def changed(file, data):
        current = store.load()
        user = current["users"]["111"]
        session = user["delivery"]
        if change == "remove":
            session["files"] = []
            session["stage"] = "await_file"
        elif change == "originals":
            session["files"][0].pop("optimization")
            session["stage"] = "ready"
        elif change == "disconnect":
            del current["users"]["111"]
        elif change == "new_selection":
            session["batch_id"] = "new"
        else:
            user["connected_at"] = (now + timedelta(seconds=1)).isoformat()
        store.save(current)
        return data[:2000], "improved"

    assert run_once(store, t, changed, now) == 0
    assert t.uploads == 0
    assert not store.load()["outbox"]


def test_overlarge_file_is_retained_in_list_for_removal_without_partial_confirmation(
    state, store, now
):
    batch(state, now, (11 * 1024 * 1024, 100000))
    store.save(state)
    run_once(store, Transport(), lambda f, b: (b, "unchanged"), now)
    result = store.load()
    session = result["users"]["111"]["delivery"]
    assert len(session["files"]) == 2
    event = next(iter(result["outbox"].values()))
    buttons = [b for row in event["reply_markup"]["inline_keyboard"] for b in row]
    assert not any(b["callback_data"].startswith("confirm:") for b in buttons)
    assert sum(b["callback_data"].startswith("remove:") for b in buttons) == 2


def test_ambiguous_copy_and_interrupted_jobs_use_original_without_duplicate_upload(
    state, store, now
):
    session = batch(state, now)
    session["files"][0]["optimization"].update(
        status="running", started_at=(now - timedelta(minutes=11)).isoformat(), upload_started=True
    )
    store.save(state)
    t = Transport()

    def ambiguous(uid, file, data):
        t.uploads += 1
        raise BotError("Ambiguous")

    t.upload = ambiguous
    assert run_once(store, t, lambda f, b: (b[:2000], "improved"), now) == 2
    assert t.downloads == t.uploads == 1
    assert run_once(store, t, now=now) == 0
    assert [e["file"]["file_id"] for e in store.load()["users"]["111"]["delivery"]["files"]] == [
        "SOURCE_0",
        "SOURCE_1",
    ]


def test_batch_preview_invalidates_when_another_document_is_added(state, store, now, telegram):
    batch(state, now)
    store.save(state)
    run_once(store, Transport(), lambda f, b: (b, "unchanged"), now)
    current = store.load()
    current["users"]["111"]["delivery"]["nonce"] = "b" * 32
    store.save(current)
    dispatch(current, store, telegram, now, event_contains=":prepare:")
    assert not telegram.sent
    assert all(e["status"] == "cancelled" for e in current["outbox"].values())
