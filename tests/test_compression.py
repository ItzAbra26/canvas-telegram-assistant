import copy
import io
import zipfile
from datetime import timedelta

import pikepdf
import pytest
import responses

from src.compression_worker import (
    MAX_OUTPUT,
    DocumentTransport,
    run_once,
    sandbox_optimize,
)
from src.errors import BotError
from src.file_optimizer import optimize
from src.notifier import dispatch, enqueue


def archive(content=b"Contenido de la tarea\n" * 12000):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as z:
        z.comment = b"Comentario original"
        z.writestr("carpeta/", b"")
        z.writestr("carpeta/diseño.txt", content)
    return output.getvalue()


def test_zip_recompression_preserves_content_paths_and_comment():
    data = archive()
    candidate, reason = optimize("Trabajo.ZIP", data)
    assert reason == "improved" and len(candidate) < len(data)
    with (
        zipfile.ZipFile(io.BytesIO(data)) as original,
        zipfile.ZipFile(io.BytesIO(candidate)) as result,
    ):
        assert result.namelist() == original.namelist()
        assert result.comment == original.comment
        for name in result.namelist():
            assert result.read(name) == original.read(name)


def test_pdf_lossless_streams_and_page_count():
    stream = b"% Comentario de la tarea\n" * 12000
    output = io.BytesIO()
    with pikepdf.Pdf.new() as pdf:
        pdf.add_blank_page()
        pdf.pages[0].Contents = pdf.make_stream(stream)
        pdf.save(output, compress_streams=False)
    original = output.getvalue()
    candidate, reason = optimize("trabajo.PDF", original)
    assert reason == "improved" and candidate.startswith(b"%PDF-")
    with pikepdf.Pdf.open(io.BytesIO(candidate)) as result:
        assert len(result.pages) == 1
        assert result.pages[0].Contents.read_bytes() == stream


@pytest.mark.parametrize(
    "name,data",
    [
        ("signed.pdf", b"%PDF /ByteRange [0 1 2 3]"),
        ("invalid.pdf", b"invalid"),
        ("test.docx", b"unknown"),
        ("empty.zip", b""),
    ],
)
def test_protected_invalid_or_unsupported_preserve_original(name, data):
    assert optimize(name, data)[0] == data


def test_pdf_forms_and_encryption_preserved():
    with pikepdf.Pdf.new() as pdf:
        pdf.add_blank_page()
        pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([]))
        output = io.BytesIO()
        pdf.save(output)
        data = output.getvalue()
        assert optimize("form.pdf", data) == (data, "protected")
        encrypted = io.BytesIO()
        pdf.save(encrypted, encryption=pikepdf.Encryption(owner="private", user="private", R=6))
        assert optimize("encrypted.pdf", encrypted.getvalue())[0] == encrypted.getvalue()


def test_zip_limits_and_path_traversal_preserve_original(monkeypatch):
    import src.file_optimizer as module

    monkeypatch.setattr(module, "MAX_EXPANDED", 100)
    data = archive()
    assert optimize("large.zip", data) == (data, "limited")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        z.writestr("../../secret.txt", b"test")
    data = output.getvalue()
    assert optimize("unsafe.zip", data) == (data, "limited")


def test_sandbox_prepares_real_zip_in_isolated_process():
    data = archive()
    result, reason = sandbox_optimize({"name": "task.zip"}, data)
    assert reason == "improved" and len(result) < len(data)


def test_parser_does_not_inherit_credentials_or_python_injection(monkeypatch, tmp_path):
    import subprocess

    import src.compression_worker as worker

    actual = subprocess.run
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "SYNTHETIC_SECRET")
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "SYNTHETIC_KEY")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    (tmp_path / "sitecustomize.py").write_text("raise RuntimeError('injected')", encoding="utf-8")

    def guarded_run(args, **kwargs):
        assert "TELEGRAM_BOT_TOKEN" not in kwargs["env"]
        assert "STATE_ENCRYPTION_KEY" not in kwargs["env"]
        assert "PYTHONPATH" not in kwargs["env"]
        assert args[1] == "-I"
        assert str(kwargs["cwd"]) != str(tmp_path)
        return actual(args, **kwargs)

    monkeypatch.setattr(worker.subprocess, "run", guarded_run)
    candidate, reason = sandbox_optimize({"name": "task.zip"}, archive())
    assert reason == "improved" and candidate.startswith(b"PK")


def queued(state, now, size=100000):
    file = {
        "file_id": "SYNTHETIC_SOURCE",
        "name": "trabajo.zip",
        "mime": "application/zip",
        "size": size,
    }
    state["users"]["111"]["delivery"] = {
        "stage": "optimizing",
        "nonce": "a" * 32,
        "key": "10:1",
        "connected_at": state["users"]["111"]["connected_at"],
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "name": "Tarea",
        "course_name": "Asignatura",
        "due_at": None,
        "file": copy.deepcopy(file),
        "original_file": file,
        "optimization": {"status": "queued", "requested_at": now.isoformat()},
    }


class Transport:
    def __init__(self):
        self.downloads = self.uploads = 0

    def download(self, file):
        self.downloads += 1
        return b"x" * file["size"]

    def upload(self, uid, file, data):
        self.uploads += 1
        assert uid == "111"
        return {**file, "file_id": "SYNTHETIC_OPTIMIZED", "size": len(data)}


def test_preparation_preview_requires_new_confirmation_and_never_submits(
    state, store, now, telegram
):
    queued(state, now)
    enqueue(state, "unrelated", 111, "Webhook still pending")
    store.save(state)
    transport = Transport()
    assert run_once(store, transport, lambda f, data: (data[:2000], "improved"), now) == 1
    result = store.load()
    session = result["users"]["111"]["delivery"]
    assert session["stage"] == "ready" and session["nonce"] != "a" * 32
    assert session["file"]["file_id"] == "SYNTHETIC_OPTIMIZED"
    assert "submission_started" not in session
    preview = next(event for key, event in result["outbox"].items() if ":prepare:" in key)
    assert "Optimizado sin pérdida" in preview["text"]
    assert "98% menos" in preview["text"]
    buttons = preview["reply_markup"]["inline_keyboard"]
    assert buttons[0][0]["callback_data"] == "confirm:" + session["nonce"]
    assert buttons[1][0]["callback_data"] == "original:" + session["nonce"]
    dispatch(result, store, telegram, now, event_contains=":prepare:")
    assert store.load()["outbox"]["unrelated"]["status"] == "pending"
    assert run_once(store, transport, now=now) == 0
    assert transport.uploads == 1


def test_no_improvement_uses_original_and_overlarge_file_cannot_be_confirmed(state, store, now):
    for size in [100000, MAX_OUTPUT + 1]:
        queued(state, now, size)
        store.save(state)
        t = Transport()
        run_once(store, t, lambda f, data: (data, "unchanged"), now)
        s = store.load()["users"]["111"]["delivery"]
        assert t.uploads == 0
        assert s["stage"] == ("ready" if size <= MAX_OUTPUT else "await_file")
        if size <= MAX_OUTPUT:
            assert s["file"]["file_id"] == "SYNTHETIC_SOURCE"
        else:
            assert "file" not in s


def test_cancel_disconnect_and_replaced_file_cannot_be_restored(state, store, now):
    for change in ["cancel", "disconnect", "replace"]:
        queued(state, now)
        store.save(state)
        t = Transport()

        def optimize_after_change(file, data, change=change):
            current = store.load()
            if change == "cancel":
                current["users"]["111"].pop("delivery")
            elif change == "disconnect":
                current["users"].pop("111")
            else:
                current["users"]["111"]["delivery"]["nonce"] = "b" * 32
            store.save(current)
            return data[:2000], "improved"

        run_once(store, t, optimize_after_change, now)
        assert t.uploads == 0
        assert not any(":prepare:" in key for key in store.load()["outbox"])


def test_ambiguous_telegram_copy_is_never_uploaded_twice(state, store, now):
    queued(state, now)
    store.save(state)
    t = Transport()

    def ambiguous(uid, file, data):
        t.uploads += 1
        raise BotError("Ambiguous test")

    t.upload = ambiguous
    run_once(store, t, lambda f, data: (data[:2000], "improved"), now)
    run_once(store, t, now=now)
    assert t.uploads == 1
    assert store.load()["users"]["111"]["delivery"]["file"]["file_id"] == "SYNTHETIC_SOURCE"


def test_interrupted_preparation_recovers_original_without_upload(state, store, now):
    queued(state, now)
    session = state["users"]["111"]["delivery"]
    session["optimization"].update(
        status="running", started_at=(now - timedelta(minutes=11)).isoformat(), upload_started=True
    )
    store.save(state)
    t = Transport()
    run_once(store, t, now=now)
    assert t.downloads == t.uploads == 0
    assert store.load()["users"]["111"]["delivery"]["stage"] == "ready"


def test_cancelled_preview_is_not_sent_later(state, store, now, telegram):
    queued(state, now)
    store.save(state)
    run_once(store, Transport(), lambda f, data: (data, "unchanged"), now)
    current = store.load()
    current["users"]["111"].pop("delivery")
    store.save(current)
    dispatch(current, store, telegram, now, event_contains=":prepare:")
    assert not telegram.sent
    assert all(e["status"] == "cancelled" for e in current["outbox"].values())


@responses.activate
def test_document_transport_checks_size_and_private_receipt():
    t = DocumentTransport("SYNTHETIC_TOKEN")
    file = {"file_id": "SYNTHETIC", "size": 4, "name": "work.pdf", "mime": "application/pdf"}
    responses.post(
        t.root + "getFile",
        json={"ok": True, "result": {"file_path": "documents/file.pdf", "file_size": 4}},
    )
    responses.get(t.root.replace("/bot", "/file/bot") + "documents/file.pdf", body=b"DATA")
    assert t.download(file) == b"DATA"
    responses.post(
        t.root + "sendDocument",
        json={
            "ok": True,
            "result": {"chat": {"id": 111}, "document": {"file_id": "OPTIMIZED", "file_size": 4}},
        },
    )
    assert t.upload("111", file, b"DATA")["file_id"] == "OPTIMIZED"
    responses.post(
        t.root + "getFile",
        json={"ok": True, "result": {"file_path": "../secret.pdf", "file_size": 4}},
    )
    with pytest.raises(BotError):
        t.download(file)
