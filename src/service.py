import hmac
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from src.canvas_client import CanvasClient
from src.commands import PRIVACY, help_text, render_command, start_text
from src.config import Config
from src.errors import APIError
from src.notifier import dispatch, enqueue, queue_pages, reconcile, recover_inflight
from src.storage import State, Store
from src.telegram_bot import COMMANDS, TelegramBot

logger = logging.getLogger(__name__)
CanvasFactory = Callable[[str, str], CanvasClient]


class BotService:
    def __init__(
        self,
        config: Config,
        store: Store,
        telegram: TelegramBot,
        canvas_factory: CanvasFactory = CanvasClient,
    ):
        self.config = config
        self.store = store
        self.telegram = telegram
        self.canvas_factory = canvas_factory

    def _allowed(self, uid: int) -> bool:
        return not self.config.allowed_users or uid in self.config.allowed_users

    @staticmethod
    def _forget(state: State, uid: str) -> None:
        state["users"].pop(uid, None)
        # Elimina también textos históricos del usuario del estado activo.
        for key in list(state["outbox"]):
            if state["outbox"][key]["chat_id"] == int(uid):
                del state["outbox"][key]

    def _update(
        self,
        state: State,
        update: dict[str, Any],
        now: datetime,
        commands: list[tuple[str, str, int]],
    ) -> None:
        message = update.get("message", {})
        chat = message.get("chat", {})
        sender = message.get("from", {})
        uid_int = sender.get("id")
        if (
            chat.get("type") != "private"
            or type(uid_int) is not int
            or chat.get("id") != uid_int
            or sender.get("is_bot")
            or not self._allowed(uid_int)
        ):
            # No responder con información personal en grupos, forwards o usuarios no permitidos.
            return
        uid = str(uid_int)
        text = message.get("text", "").strip()
        user = state["users"].get(uid, {})
        prefix = f"update:{update['update_id']}"
        if message.get("forward_origin"):
            self.telegram.delete_message(uid_int, message["message_id"])
            enqueue(
                state,
                prefix,
                uid_int,
                "No acepto tokens reenviados. Usa /start y envía tu propio token directamente.",
            )
            return
        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text.startswith("/") else ""
        if command == "/start":
            if not self.config.test_mode:
                enqueue(
                    state,
                    prefix,
                    uid_int,
                    "El registro con tokens manuales está desactivado. Esta versión solo implementa el modo de prueba.",
                )
                return
            if self.config.invite_code:
                supplied = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
                if not hmac.compare_digest(
                    supplied.encode("utf-8"), self.config.invite_code.encode("utf-8")
                ):
                    enqueue(
                        state,
                        prefix,
                        uid_int,
                        "Para entrar en la práctica usa /start seguido del código de clase que te ha dado el profesor.",
                    )
                    return
            if uid not in state["users"] and len(state["users"]) >= self.config.max_users:
                enqueue(
                    state,
                    prefix,
                    uid_int,
                    "La práctica ha alcanzado el límite de usuarios. Consulta al profesor.",
                )
                return
            user = state["users"].setdefault(uid, {"onboarding": False, "tasks": {}, "courses": []})
            user.update(onboarding=True, onboarding_at=now.isoformat())
            enqueue(state, prefix, uid_int, start_text(self.config))
        elif command == "/cancelar":
            if uid in state["users"]:
                user["onboarding"] = False
                if not user.get("token"):
                    state["users"].pop(uid)
            enqueue(state, prefix, uid_int, "Registro cancelado. Puedes volver con /start.")
        elif command == "/desconectar":
            self._forget(state, uid)
            enqueue(
                state,
                prefix,
                uid_int,
                "🔌 Conexión y datos activos borrados. Revoca tu token también en Canvas. Las versiones cifradas anteriores de GitHub pueden seguir en su historial; consulta /privacidad.",
            )
        elif command == "/privacidad":
            enqueue(state, prefix, uid_int, PRIVACY)
        elif command == "/ayuda":
            enqueue(state, prefix, uid_int, help_text())
        elif command == "/id":
            enqueue(
                state,
                prefix,
                uid_int,
                f"Tu identificador de Telegram es <code>{uid_int}</code>. Solo se muestra en este chat privado.",
            )
        elif command and command.removeprefix("/") in COMMANDS:
            commands.append((uid, command.removeprefix("/"), update["update_id"]))
        elif command:
            enqueue(state, prefix, uid_int, "No reconozco ese comando. Usa /ayuda.")
        elif text:
            # Cualquier texto durante el registro se trata como secreto y nunca se persiste como update.
            deleted = self.telegram.delete_message(uid_int, message["message_id"])
            if not user.get("onboarding") or not self.config.test_mode:
                enqueue(
                    state,
                    prefix,
                    uid_int,
                    "Para conectar o cambiar tu cuenta usa /start. No envíes credenciales fuera del registro.",
                )
                return
            if not 16 <= len(text) <= 512 or not text.isascii() or any(c.isspace() for c in text):
                enqueue(
                    state,
                    prefix,
                    uid_int,
                    "No parece un token. Copia solo el token de Canvas, sin espacios ni contraseña, o usa /cancelar.",
                )
                return
            try:
                canvas_id = self.canvas_factory(self.config.canvas_base_url, text).profile()
            except APIError as exc:
                reason = (
                    "Canvas ha rechazado el token (caducado, revocado o de otro dominio)."
                    if exc.status in {401, 403}
                    else "Canvas no está disponible o no ha devuelto un perfil válido."
                )
                enqueue(
                    state,
                    prefix,
                    uid_int,
                    reason + " Vuelve a enviar el token para reintentarlo, o /cancelar.",
                )
                if not deleted:
                    enqueue(
                        state,
                        prefix + ":delete",
                        uid_int,
                        "⚠️ Telegram no me permitió borrar tu mensaje. Bórralo tú y revoca el token si pudo quedar expuesto.",
                    )
                return
            if user.get("canvas_user_id") not in {None, canvas_id}:
                self._forget(state, uid)
                user = {"onboarding": False, "tasks": {}, "courses": []}
                state["users"][uid] = user
            user.update(
                token=text,
                canvas_user_id=canvas_id,
                onboarding=False,
                disabled=False,
                sync_error=None,
                connected_at=now.isoformat(),
            )
            notice = (
                "🔒 He borrado el mensaje que contenía el token."
                if deleted
                else "⚠️ No pude borrar el mensaje con tu token: bórralo tú desde Telegram."
            )
            enqueue(
                state,
                prefix,
                uid_int,
                "✅ <b>Cuenta conectada</b>\n"
                + notice
                + "\nTus tareas y avisos llegarán solo a este chat. Usa /resumen, /hoy o /pendientes.",
            )
        else:
            enqueue(
                state,
                prefix,
                uid_int,
                "Envía el token como texto durante /start, o consulta /ayuda.",
            )

    def cycle(self, now: datetime, receive_updates: bool = True) -> bool:
        state = self.store.load()
        recover_inflight(state, now if not receive_updates else None)
        self.store.save(state)
        healthy = True
        if not state["commands_registered"]:
            try:
                self.telegram.register_commands()
                state["commands_registered"] = True
                self.store.save(state)
            except APIError:
                logger.warning("No se pudo registrar el menú de Telegram; se volverá a intentar.")
                healthy = False
        commands: list[tuple[str, str, int]] = []
        try:
            # Un lote por ejecución: el siguiente procesa el resto; no exceder el tiempo de Actions.
            updates = self.telegram.get_updates(state["offset"]) if receive_updates else []
        except APIError as exc:
            logger.error("No se pudieron leer comandos: %s", exc)
            updates = []
            healthy = False
        for update in updates:
            if update["update_id"] < state["offset"]:
                continue
            self._update(state, update, now, commands)
            state["offset"] = update["update_id"] + 1
            # Persistir offset y token juntos. No guardar nunca el mensaje entrante completo.
            # Los comandos se materializan inmediatamente con caché y se reemplazan tras sincronizar.
            for uid, command, update_id in commands[-1:]:
                if update_id == update["update_id"]:
                    queue_pages(
                        state,
                        f"update:{update_id}",
                        int(uid),
                        render_command(command, state["users"].get(uid, {}), self.config, now),
                    )
            self.store.save(state)
        connected = 0
        for uid, user in list(state["users"].items()):
            if not user.get("token") or not self._allowed(int(uid)) or not self.config.test_mode:
                continue
            if user.get("disabled"):
                continue
            connected += 1
            try:
                snapshot = self.canvas_factory(self.config.canvas_base_url, user["token"]).snapshot(
                    user.get("tasks", {})
                )
            except APIError as exc:
                healthy = False
                user["sync_error"] = exc.status or "network"
                if exc.status == 401:
                    user["disabled"] = True
                    enqueue(
                        state,
                        f"u{uid}:auth:{user['connected_at']}",
                        int(uid),
                        "⚠️ Canvas ha rechazado tu token. Tus datos anteriores se conservan. Renueva tu token y vuelve a conectar con /start.",
                    )
                logger.warning("Falló una sincronización: %s. Se conserva su estado.", exc)
            else:
                reconcile(state, uid, snapshot, self.config, now)
            self.store.save(state)
        for uid, command, update_id in commands:
            # No recrear respuestas borradas por /desconectar posterior en el mismo lote.
            prefix = f"update:{update_id}:"
            if not any(k.startswith(prefix) for k in state["outbox"]):
                continue
            for key in list(state["outbox"]):
                if key.startswith(prefix) and state["outbox"][key]["status"] == "pending":
                    del state["outbox"][key]
            user = state["users"].get(uid, {})
            user["uncertain_count"] = sum(
                event["chat_id"] == int(uid) and event["status"] == "uncertain"
                for event in state["outbox"].values()
            )
            queue_pages(
                state,
                f"update:{update_id}",
                int(uid),
                render_command(command, user, self.config, now),
            )
        self.store.save(state)
        delivered = dispatch(state, self.store, self.telegram, now)
        uncertain = sum(event["status"] == "uncertain" for event in state["outbox"].values())
        logger.info(
            "Revisión terminada: %s cuentas consultadas; %s envíos inciertos.", connected, uncertain
        )
        return healthy and delivered
