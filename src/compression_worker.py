"""Prepare queued Telegram documents on a free GitHub runner, never submit to Canvas."""

import copy
import logging
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from src.batch_preparation import run_batch
from src.config import Config
from src.errors import BotError, StorageConflict
from src.file_optimizer import MAX_INPUT
from src.notifier import dispatch, enqueue
from src.remote_store import RemoteStore
from src.storage import State, Store
from src.telegram_bot import TelegramBot
from src.utils import e, parse_date

MAX_OUTPUT = 10 * 1024 * 1024
logger = logging.getLogger(__name__)


class DocumentTransport:
    def __init__(self, token: str, session: requests.Session | None = None):
        self.root = "https://api.telegram.org/bot" + token + "/"
        self.http = session or requests.Session()

    def download(self, file: dict[str, Any]) -> bytes:
        try:
            response = self.http.post(
                self.root + "getFile",
                json={"file_id": file["file_id"]},
                timeout=(10, 30),
                allow_redirects=False,
            )
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError
            info = body.get("result", {})
            if not isinstance(info, dict):
                raise ValueError
            path = info.get("file_path", "")
            if (
                not response.ok
                or body.get("ok") is not True
                or not re.fullmatch(r"[A-Za-z0-9_/-]+\.[A-Za-z0-9]+", path)
                or ".." in path
                or path.startswith("/")
                or info.get("file_size", file["size"]) != file["size"]
            ):
                raise ValueError
            with self.http.get(
                self.root.replace("/bot", "/file/bot") + path,
                stream=True,
                timeout=(10, 45),
                allow_redirects=False,
            ) as download:
                if not download.ok or int(download.headers.get("content-length", 0)) > file["size"]:
                    raise ValueError
                data = bytearray()
                for chunk in download.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > min(file["size"], MAX_INPUT):
                        raise ValueError
            if len(data) != file["size"]:
                raise ValueError
            return bytes(data)
        except (requests.RequestException, ValueError, KeyError, TypeError):
            raise BotError("No se pudo descargar el documento; no se ha entregado.") from None

    def upload(self, uid: str, file: dict[str, Any], data: bytes) -> dict[str, Any]:
        try:
            response = self.http.post(
                self.root + "sendDocument",
                data={
                    "chat_id": uid,
                    "disable_notification": "true",
                    "caption": "🗜️ Copia optimizada sin pérdida. Todavía no se ha entregado en Canvas; revisa el mensaje de confirmación.",
                },
                files={"document": (file["name"], data, file["mime"])},
                timeout=(10, 60),
                allow_redirects=False,
            )
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("result"), dict):
                raise ValueError
            result = body.get("result", {})
            document = result.get("document", {})
            if not isinstance(document, dict) or not isinstance(result.get("chat"), dict):
                raise ValueError
            if (
                not response.ok
                or body.get("ok") is not True
                or result.get("chat", {}).get("id") != int(uid)
                or not isinstance(document.get("file_id"), str)
                or not 1 <= len(document["file_id"]) <= 256
                or document.get("file_size") != len(data)
            ):
                raise ValueError
            return {**file, "file_id": document["file_id"], "size": len(data)}
        except (requests.RequestException, ValueError, TypeError):
            # sendDocument may have succeeded. It is never automatically repeated.
            raise BotError("Telegram no confirmó la copia optimizada.") from None


def sandbox_optimize(file: dict[str, Any], data: bytes) -> tuple[bytes, str]:
    extension = file["name"].rsplit(".", 1)[-1].lower()
    if extension not in {"pdf", "zip"} or not 0 < len(data) <= MAX_INPUT:
        return data, "unsupported"
    with tempfile.TemporaryDirectory(prefix="canvas-prepare-") as directory:
        source, output = Path(directory) / "input.bin", Path(directory) / "output.bin"
        source.write_bytes(data)
        try:
            result = subprocess.run(  # noqa: S603 — fixed interpreter/module, private paths, no shell
                [
                    sys.executable,
                    "-m",
                    "src.file_optimizer",
                    str(source),
                    str(output),
                    extension,
                ],
                capture_output=True,
                timeout=30,
                check=True,
            )
            if not 0 < output.stat().st_size <= MAX_INPUT:
                raise ValueError
            candidate, reason = output.read_bytes(), result.stdout.decode().strip()
            if reason not in {
                "improved",
                "unchanged",
                "limited",
                "invalid",
                "unsupported",
                "protected",
            }:
                raise ValueError
            return candidate, reason
        except (subprocess.SubprocessError, OSError, ValueError):
            return data, "limited"


def current_job(state: State, uid: str, identity: tuple, nonce: str) -> dict | None:
    user = state["users"].get(uid)
    if (
        not user
        or user.get("disabled")
        or user.get("onboarding")
        or (user.get("token"), user.get("connected_at")) != identity
    ):
        return None
    session = user.get("delivery", {})
    if session.get("nonce") != nonce or session.get("stage") != "optimizing":
        return None
    return session


def finish(
    state: State,
    uid: str,
    session: dict,
    original: dict,
    selected: dict,
    reason: str,
    now: datetime,
) -> None:
    session.pop("optimization", None)
    session["nonce"] = uuid.uuid4().hex
    session["expires_at"] = (now + timedelta(minutes=15)).isoformat()
    base = f"📚 {e(session['course_name'], 120)}\n📝 {e(session['name'], 180)}"
    prefix = f"u{uid}:prepare:{session['nonce']}"
    if selected["size"] > MAX_OUTPUT:
        session["stage"] = "await_file"
        session.pop("file", None)
        session.pop("original_file", None)
        enqueue(
            state,
            prefix,
            int(uid),
            f"⚠️ El archivo sigue superando los 10 MB después de intentar optimizarlo.\n{base}\nEnvía una versión más pequeña o entrégalo directamente desde Canvas. No he registrado ninguna entrega.",
        )
        return
    session["stage"], session["file"] = "ready", selected
    saved = original["size"] - selected["size"]
    summary = (
        f"🗜️ Optimizado sin pérdida: {original['size'] / 1048576:.2f} → {selected['size'] / 1048576:.2f} MB ({saved / original['size']:.0%} menos)."
        if reason == "improved"
        else "📎 Conservo el original: no pude reducirlo de forma segura o no mejoraba su tamaño."
    )
    if reason != "improved":
        session.pop("original_file", None)
    warnings = ""
    if session.get("submitted_at") or session.get("attempt"):
        warnings += "\n⚠️ Ya existe una entrega: crearás un nuevo intento."
    if session.get("group"):
        warnings += "\n👥 Es una tarea de grupo: la entrega puede afectar a sus miembros."
    due = parse_date(session.get("due_at"))
    if due and due < now:
        warnings += "\n⏰ El plazo ha vencido: Canvas puede registrarla como entrega tardía."
    buttons = [
        [
            {"text": "✅ Entregar ahora", "callback_data": "confirm:" + session["nonce"]},
            {"text": "❌ Cancelar", "callback_data": "cancel:" + session["nonce"]},
        ]
    ]
    if reason == "improved" and original["size"] <= MAX_OUTPUT:
        buttons.append(
            [{"text": "📎 Usar original", "callback_data": "original:" + session["nonce"]}]
        )
    enqueue(
        state,
        prefix,
        int(uid),
        f"📤 <b>Revisa antes de entregar</b>\n{base}\n📎 {e(selected['name'], 220)} · {selected['size'] / 1048576:.2f} MB\n{summary}{warnings}\n\nPuedes enviar más documentos para añadirlos a la misma tarea antes de confirmar. Al pulsar <b>Entregar ahora</b>, autorizas a subir la lista de archivos y registrar una única entrega en Canvas con tu cuenta. Todavía no se ha entregado.",
        reply_markup={"inline_keyboard": buttons},
        kind="file_preview",
        uid=uid,
        nonce=session["nonce"],
    )


def run_once(
    store: Store,
    transport: DocumentTransport,
    optimizer=sandbox_optimize,
    now: datetime | None = None,
) -> int:
    fixed_time = now
    now = now or datetime.now(UTC)
    state = store.load()
    processed = 0
    for uid in list(state["users"]):
        state = store.load()
        user = state["users"].get(uid, {})
        session = user.get("delivery", {})
        if (
            session.get("stage") != "optimizing"
            or not user.get("token")
            or user.get("disabled")
            or user.get("onboarding")
        ):
            continue
        if "files" in session:
            processed += run_batch(
                store, uid, transport, optimizer, fixed_time or datetime.now(UTC)
            )
            continue
        identity, nonce = (user["token"], user.get("connected_at")), session["nonce"]
        original = copy.deepcopy(session["original_file"])
        job = session["optimization"]
        expiry, started = parse_date(session.get("expires_at")), parse_date(job.get("started_at"))
        if expiry and expiry <= now or job.get("status") == "running":
            if job.get("status") == "running" and started and now - started < timedelta(minutes=10):
                continue
            finish(state, uid, session, original, original, "limited", now)
            store.save(state)
            processed += 1
            continue
        if job.get("status") != "queued":
            continue
        claim = uuid.uuid4().hex
        job.update(status="running", started_at=now.isoformat(), claim=claim)
        store.save(state)  # CAS claim before any file/network processing.
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
                session = current_job(state, uid, identity, nonce)
                if not session or session["optimization"].get("claim") != claim:
                    continue
                session["optimization"]["upload_started"] = True
                store.save(state)  # Persist before the single Telegram POST.
                selected = transport.upload(uid, original, candidate)
            else:
                reason = "unchanged" if reason == "improved" else reason
        except BotError:
            selected, reason = original, "limited"
        state = store.load()
        session = current_job(state, uid, identity, nonce)
        if not session or session["optimization"].get("claim") != claim:
            continue
        finish(state, uid, session, original, selected, reason, fixed_time or datetime.now(UTC))
        store.save(state)
        processed += 1
    return processed


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        config = Config.from_env()
        if not config.test_mode or config.backend != "remote":
            raise BotError("La preparación requiere modo de prueba y estado remoto.")
        store = RemoteStore(config.state_api_url, config.state_api_key, config.encryption_key)
        count = run_once(store, DocumentTransport(config.telegram_token))
        state = store.load()
        # Only send this worker's previews. Never disturb an active webhook send.
        dispatch(
            state,
            store,
            TelegramBot(config.telegram_token),
            datetime.now(UTC),
            event_contains=":prepare:",
        )
        logger.info(
            "Preparación terminada: %s archivos revisados; sin entregas automáticas.", count
        )
        return 0
    except StorageConflict:
        logger.warning("Estado actualizado por Telegram; se conserva y se recargará.")
        return 75
    except (BotError, OSError, ValueError):
        logger.error("No se completó la preparación. Los originales se conservan.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
