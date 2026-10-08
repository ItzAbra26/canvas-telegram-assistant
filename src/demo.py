"""Escenario completo sin red, sin tokens reales y sin modificar el estado del bot."""

import copy
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from cryptography.fernet import Fernet

from src.config import Config
from src.models import Assignment, Course, Snapshot
from src.service import BotService
from src.storage import LocalStore


class DemoTelegram:
    def __init__(self):
        self.updates: list[dict[str, Any]] = []
        self.sent: list[tuple[int, str]] = []
        self.deleted: list[tuple[int, int]] = []

    def get_updates(self, offset: int, timeout: int = 0) -> list[dict[str, Any]]:
        result = [u for u in self.updates if u["update_id"] >= offset]
        self.updates = []
        return result

    def send_message(self, chat_id: int, text: str) -> int:
        self.sent.append((chat_id, text))
        return len(self.sent)

    def delete_message(self, chat_id: int, message_id: int) -> bool:
        self.deleted.append((chat_id, message_id))
        return True

    def register_commands(self) -> None:
        pass


def demo_update(update_id: int, uid: int, text: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": uid, "is_bot": False},
            "chat": {"id": uid, "type": "private"},
            "text": text,
        },
    }


def run_demo() -> int:
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    # Identificadores sintéticos del simulador; no son credenciales.
    first_token = "DEMO_ACCOUNT_A_NOT_A_REAL_TOKEN"  # noqa: S105
    second_token = "DEMO_ACCOUNT_B_NOT_A_REAL_TOKEN"  # noqa: S105
    course = Course(10, "Diseño de Interfaces")
    assignment = Assignment(
        id=1,
        course_id=10,
        course_name=course.name,
        name="Práctica 3",
        description="Diseña y entrega una interfaz.",
        created_at=now.isoformat(),
        unlock_at=None,
        due_at=(now + timedelta(hours=25)).isoformat(),
        points=10,
        url="https://canvas.example.edu/courses/10/assignments/1",
        submission_state="unsubmitted",
        submitted=False,
        excused=False,
        requires_submission=True,
    )
    snapshots = {
        first_token: Snapshot([course], [assignment], set()),
        second_token: Snapshot(
            [course], [replace(assignment, submitted=True, submission_state="submitted")], set()
        ),
    }

    class DemoCanvas:
        def __init__(self, base: str, token: str):
            self.token = token

        def profile(self) -> int:
            return 101 if self.token == first_token else 202

        def snapshot(self, previous: dict[str, Any]) -> Snapshot:
            return copy.deepcopy(snapshots[self.token])

    with TemporaryDirectory() as folder:
        config = Config(
            "https://canvas.example.edu",
            "DEMO_BOT",
            Fernet.generate_key().decode(),
            state_file=Path(folder) / "state.enc",
        )
        store = LocalStore(config.state_file, config.encryption_key)
        telegram = DemoTelegram()
        service = BotService(config, store, telegram, DemoCanvas)
        telegram.updates = [
            demo_update(1, 111, "/start"),
            demo_update(2, 111, first_token),
            demo_update(3, 222, "/start"),
            demo_update(4, 222, second_token),
        ]
        if not service.cycle(now):
            return 1
        initial_count = len(telegram.sent)
        service.cycle(now + timedelta(minutes=15))
        if len(telegram.sent) != initial_count:
            raise RuntimeError("La demo detectó un aviso duplicado.")
        telegram.updates = [demo_update(5, 111, "/resumen"), demo_update(6, 222, "/resumen")]
        service.cycle(now + timedelta(hours=1, minutes=1))
        user_a = next(
            text for chat, text in reversed(telegram.sent) if chat == 111 and "RESUMEN" in text
        )
        user_b = next(
            text for chat, text in reversed(telegram.sent) if chat == 222 and "RESUMEN" in text
        )
        if "Total pendientes: 1" not in user_a or "Total pendientes: 0" not in user_b:
            raise RuntimeError("La demo detectó mezcla de datos entre usuarios.")
        if first_token.encode() in config.state_file.read_bytes():
            raise RuntimeError("La demo detectó una credencial sin cifrar.")
        new = replace(assignment, id=2, name="Proyecto nuevo", due_at=None)
        snapshots[first_token].assignments.append(new)
        service.cycle(now + timedelta(hours=2))
        snapshots[first_token].assignments[0] = replace(
            assignment, due_at=(now + timedelta(days=3)).isoformat(), submitted=True
        )
        service.cycle(now + timedelta(hours=3))
        print(
            "DEMO OK: registro de 2 usuarios, borrado de mensajes, estado cifrado, aislamiento, recordatorio de 24h, tarea nueva y cambios."
        )
        print("\nEjemplo de resumen del alumno A:\n" + user_a)
        print("\nEjemplo de resumen del alumno B:\n" + user_b)
        logging.info("La demo no ha usado la red ni credenciales reales.")
    return 0
