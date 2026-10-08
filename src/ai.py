from typing import Protocol

from src.models import Assignment


class TaskAssistant(Protocol):
    """Extensión futura, desconectada del bot base. No requiere ninguna API de pago."""

    def summarize(self, assignment: Assignment) -> str: ...
    def explain(self, assignment: Assignment) -> str: ...
    def estimate_minutes(self, assignment: Assignment) -> int | None: ...


class PlainTaskAssistant:
    def summarize(self, assignment: Assignment) -> str:
        return assignment.description[:500]

    def explain(self, assignment: Assignment) -> str:
        return assignment.description

    def estimate_minutes(self, assignment: Assignment) -> int | None:
        return None
