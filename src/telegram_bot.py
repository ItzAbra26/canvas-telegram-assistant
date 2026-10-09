import time
from typing import Any

import requests

from src.errors import AmbiguousDelivery, APIError, RejectedDelivery

COMMANDS = {
    "start": "Conectar tu cuenta de Canvas (modo de prueba)",
    "hoy": "Tareas que vencen hoy y su estado de entrega",
    "manana": "Tareas que vencen mañana y su estado de entrega",
    "semana": "Tareas de los próximos 7 días y su estado",
    "pendientes": "Todas tus tareas pendientes",
    "atrasadas": "Tareas vencidas sin entregar",
    "ultimas": "Tareas creadas o detectadas recientemente",
    "asignaturas": "Cursos activos y tareas pendientes",
    "resumen": "Resumen y próxima entrega",
    "actualizar": "Consultar Canvas ahora (bot en la nube)",
    "entregar": "Seleccionar tarea y entregar un archivo (bot en la nube)",
    "ayuda": "Lista de comandos",
    "estado": "Estado de tu conexión",
    "id": "Tu identificador de Telegram para la lista de clase",
    "cancelar": "Cancelar el registro",
    "desconectar": "Borrar tu conexión y datos activos",
    "privacidad": "Cómo se manejan tus datos",
}


class TelegramBot:
    def __init__(self, token: str, session: requests.Session | None = None):
        self._root = f"https://api.telegram.org/bot{token}"
        self.session = session or requests.Session()

    def _call(self, method: str, data: dict[str, Any]) -> Any:
        try:
            response = self.session.post(
                self._root + "/" + method, json=data, timeout=(10, 35), allow_redirects=False
            )
        except requests.RequestException:
            if method == "sendMessage":
                raise AmbiguousDelivery("Telegram no confirmó el envío.") from None
            raise APIError("Telegram") from None
        try:
            raw = response.json()
        except ValueError:
            if method == "sendMessage":
                raise AmbiguousDelivery("Telegram devolvió una respuesta inválida.") from None
            raise APIError("Telegram", response.status_code) from None
        if not isinstance(raw, dict) or raw.get("ok") is not True:
            code = (
                raw.get("error_code", response.status_code)
                if isinstance(raw, dict)
                else response.status_code
            )
            if method == "sendMessage":
                if code >= 500 or response.status_code >= 500 or 300 <= response.status_code < 400:
                    raise AmbiguousDelivery("El servidor no confirmó el envío.")
                wait = (
                    raw.get("parameters", {}).get("retry_after", 0) if isinstance(raw, dict) else 0
                )
                raise RejectedDelivery(int(code), int(wait))
            raise APIError("Telegram", int(code))
        if "result" not in raw:
            if method == "sendMessage":
                raise AmbiguousDelivery("Telegram no incluyó confirmación.")
            raise APIError("Telegram")
        return raw["result"]

    def get_updates(self, offset: int, timeout: int = 0) -> list[dict[str, Any]]:
        result = self._call(
            "getUpdates",
            {"offset": offset, "limit": 100, "timeout": timeout, "allowed_updates": ["message"]},
        )
        if not isinstance(result, list) or any(
            not isinstance(u, dict) or type(u.get("update_id")) is not int for u in result
        ):
            raise APIError("Telegram")
        return sorted(result, key=lambda update: update["update_id"])

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> int:
        result = self._call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
                **({"reply_markup": reply_markup} if reply_markup is not None else {}),
            },
        )
        if not isinstance(result, dict) or type(result.get("message_id")) is not int:
            raise AmbiguousDelivery("Telegram no devolvió un identificador de mensaje.")
        # Límite conservador: no saturar un mismo chat con ráfagas de mensajes.
        time.sleep(1.05)
        return result["message_id"]

    def delete_message(self, chat_id: int, message_id: int) -> bool:
        try:
            return (
                self._call("deleteMessage", {"chat_id": chat_id, "message_id": message_id}) is True
            )
        except APIError:
            return False

    def register_commands(self) -> None:
        self._call(
            "setMyCommands",
            {
                "commands": [
                    {"command": key, "description": value} for key, value in COMMANDS.items()
                ]
            },
        )

    def identity(self) -> str:
        """Comprueba el token y devuelve únicamente el nombre público del bot."""
        result = self._call("getMe", {})
        if (
            not isinstance(result, dict)
            or result.get("is_bot") is not True
            or not isinstance(result.get("username"), str)
            or not result["username"]
        ):
            raise APIError("Telegram")
        return result["username"]

    def webhook_info(self) -> dict[str, Any]:
        result = self._call("getWebhookInfo", {})
        if not isinstance(result, dict):
            raise APIError("Telegram")
        return result

    def remove_webhook(self) -> None:
        self._call("deleteWebhook", {"drop_pending_updates": False})
