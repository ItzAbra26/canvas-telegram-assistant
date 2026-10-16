"""Prepare each attachment independently without replacing the other documents."""

import copy
import uuid
from datetime import datetime, timedelta

from src.errors import BotError
from src.notifier import enqueue
from src.utils import e, parse_date

MAX_OUTPUT = 10 * 1024 * 1024
MAX_BATCH = 20 * 1024 * 1024


def current(state, uid, identity, batch_id, entry_id):
    user = state["users"].get(uid, {})
    session = user.get("delivery", {})
    if (
        user.get("disabled")
        or user.get("onboarding")
        or (user.get("token"), user.get("connected_at")) != identity
        or session.get("batch_id") != batch_id
        or session.get("stage") != "optimizing"
    ):
        return None, None
    entry = next((item for item in session.get("files", []) if item["id"] == entry_id), None)
    return session, entry


def finish(state, uid, session, entry, selected, reason, now):
    original = entry["original_file"]
    entry["file"] = selected
    entry.pop("optimization", None)
    if reason != "improved":
        entry.pop("original_file", None)
    session["nonce"] = uuid.uuid4().hex
    pending = any(item.get("optimization") for item in session["files"])
    session["stage"] = "optimizing" if pending else "ready"
    session["expires_at"] = (now + timedelta(minutes=60 if pending else 15)).isoformat()
    if pending:
        return
    files = [item["file"] for item in session["files"]]
    allowed = (
        0 < len(files) <= 8
        and all(0 < f["size"] <= MAX_OUTPUT for f in files)
        and sum(f["size"] for f in files) <= MAX_BATCH
    )
    lines = "\n".join(
        f"{i}. 📎 {e(f['name'], 100)} · {f['size'] / 1048576:.2f} MB"
        for i, f in enumerate(files, 1)
    )
    summary = (
        f"🗜️ Último archivo optimizado sin pérdida: {original['size'] / 1048576:.2f} → {selected['size'] / 1048576:.2f} MB."
        if reason == "improved"
        else "📎 Preparación terminada. Se conservan los originales cuando no se pueden reducir de forma segura."
    )
    warnings = ""
    if session.get("submitted_at") or session.get("attempt"):
        warnings += "\n⚠️ Ya existe una entrega: crearás un nuevo intento."
    if session.get("group"):
        warnings += "\n👥 Es una tarea de grupo: la entrega puede afectar a sus miembros."
    due = parse_date(session.get("due_at"))
    if due and due < now:
        warnings += "\n⏰ Canvas puede registrarla como entrega tardía."
    buttons = []
    if allowed:
        buttons.append(
            [
                {
                    "text": f"✅ Entregar ahora ({len(files)})",
                    "callback_data": "confirm:" + session["nonce"],
                }
            ]
        )
    originals = [item.get("original_file", item["file"]) for item in session["files"]]
    if (
        any(item.get("original_file") for item in session["files"])
        and all(f["size"] <= MAX_OUTPUT for f in originals)
        and sum(f["size"] for f in originals) <= MAX_BATCH
    ):
        buttons.append(
            [{"text": "📎 Usar originales", "callback_data": "original:" + session["nonce"]}]
        )
    buttons.extend(
        [
            {
                "text": f"🗑️ Quitar {i + 1}: {f['name']}"[:60],
                "callback_data": f"remove:{session['nonce']}:{i}",
            }
        ]
        for i, f in enumerate(files)
    )
    buttons.append([{"text": "❌ Cancelar", "callback_data": "cancel:" + session["nonce"]}])
    instruction = (
        "Al pulsar Entregar ahora, autorizas a subir TODOS los archivos de esta lista en una única entrega de Canvas."
        if allowed
        else "⚠️ Hay archivos que superan 10 MB o el conjunto supera 20 MB. Quita archivos y envía versiones más pequeñas antes de confirmar."
    )
    enqueue(
        state,
        f"u{uid}:prepare:{session['nonce']}",
        int(uid),
        f"📤 <b>Revisa antes de entregar</b>\n📚 {e(session['course_name'], 120)}\n📝 {e(session['name'], 180)}\n\n<b>Archivos añadidos: {len(files)}/8</b>\n{lines}\n{summary}{warnings}\n\nPuedes seguir enviando más documentos. {instruction}\n<b>Todavía no se ha entregado en Canvas.</b>",
        reply_markup={"inline_keyboard": buttons},
        kind="file_preview",
        uid=uid,
        nonce=session["nonce"],
    )


def run_batch(store, uid, transport, optimizer, now: datetime) -> int:
    state = store.load()
    user = state["users"].get(uid, {})
    session = user.get("delivery", {})
    identity = (user.get("token"), user.get("connected_at"))
    batch_id = session.get("batch_id")
    if not batch_id:
        return 0
    entry_ids = [item["id"] for item in session.get("files", [])]
    processed = 0
    for entry_id in entry_ids:
        state = store.load()
        session, entry = current(state, uid, identity, batch_id, entry_id)
        if not entry or not entry.get("optimization"):
            continue
        original = copy.deepcopy(entry["original_file"])
        job = entry["optimization"]
        expiry, started = parse_date(session.get("expires_at")), parse_date(job.get("started_at"))
        if expiry and expiry <= now or job.get("status") == "running":
            if job.get("status") == "running" and started and now - started < timedelta(minutes=10):
                continue
            finish(state, uid, session, entry, original, "limited", now)
            store.save(state)
            processed += 1
            continue
        if job.get("status") != "queued":
            continue
        claim = uuid.uuid4().hex
        job.update(status="running", started_at=now.isoformat(), claim=claim)
        store.save(state)
        selected, reason = original, "limited"
        try:
            data = transport.download(original)
            candidate, reason = optimizer(original, data)
            if (
                reason == "improved"
                and 0 < len(candidate) < len(data)
                and len(candidate) <= MAX_OUTPUT
            ):
                state = store.load()
                session, entry = current(state, uid, identity, batch_id, entry_id)
                if not entry or entry.get("optimization", {}).get("claim") != claim:
                    continue
                entry["optimization"]["upload_started"] = True
                store.save(state)
                selected = transport.upload(uid, original, candidate)
            elif reason == "improved":
                reason = "unchanged"
        except BotError:
            selected, reason = original, "limited"
        state = store.load()
        session, entry = current(state, uid, identity, batch_id, entry_id)
        if not entry or entry.get("optimization", {}).get("claim") != claim:
            continue
        finish(state, uid, session, entry, selected, reason, now)
        store.save(state)
        processed += 1
    return processed
