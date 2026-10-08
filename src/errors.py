"""Errores seguros: nunca incluyen URLs con tokens ni respuestas privadas."""


class BotError(Exception):
    pass


class APIError(BotError):
    def __init__(self, service: str, status: int | None = None):
        self.service = service
        self.status = status
        super().__init__(
            f"{service}: HTTP {status}" if status else f"{service}: fallo de red/datos"
        )


class StorageError(BotError):
    pass


class AmbiguousDelivery(BotError):
    """Telegram pudo aceptar el mensaje: no se reenvía automáticamente."""


class RejectedDelivery(APIError):
    """Telegram confirmó que NO aceptó el mensaje; es seguro reintentarlo."""

    def __init__(self, status: int, retry_after: int = 0):
        super().__init__("Telegram", status)
        self.retry_after = retry_after
