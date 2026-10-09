from dataclasses import asdict, dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from typing import Any

from src.utils import clean_html, parse_date


def ignored_assignment(name: str) -> bool:
    return "scorm" in name.casefold()


@dataclass(frozen=True)
class Course:
    id: int
    name: str


@dataclass(frozen=True)
class Assignment:
    id: int
    course_id: int
    course_name: str
    name: str
    description: str
    created_at: str | None
    unlock_at: str | None
    due_at: str | None
    points: float | None
    url: str
    submission_state: str
    submitted: bool
    excused: bool
    requires_submission: bool

    @property
    def key(self) -> str:
        return f"{self.course_id}:{self.id}"

    @property
    def pending(self) -> bool:
        return (
            not ignored_assignment(self.name)
            and self.requires_submission
            and not self.submitted
            and not self.excused
        )

    def overdue(self, now: datetime) -> bool:
        due = parse_date(self.due_at)
        return bool(self.pending and due and due < now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_api(cls, raw: dict[str, Any], course: Course, base_url: str) -> "Assignment":
        submission = raw.get("submission")
        if not isinstance(submission, dict) or "workflow_state" not in submission:
            raise ValueError("Estado de entrega ausente")
        state = submission["workflow_state"]
        if state not in {"unsubmitted", "submitted", "pending_review", "graded"}:
            raise ValueError("Estado de entrega desconocido")
        for field in ("name", "due_at", "description", "points_possible", "submission_types"):
            if field not in raw:
                raise ValueError("Respuesta incompleta")
        if not isinstance(raw["name"], str) or not isinstance(raw["submission_types"], list):
            raise ValueError("Respuesta incompleta")
        dates = {}
        for field in ("created_at", "unlock_at", "due_at"):
            value = parse_date(raw.get(field))
            dates[field] = value.isoformat() if value else None
        # graded sin submitted_at/attempt puede ser un cero automático, no una entrega.
        submitted = bool(
            submission.get("submitted_at")
            or submission.get("attempt", 0)
            or state in {"submitted", "pending_review"}
        )
        types = set(raw["submission_types"])
        return cls(
            id=int(raw["id"]),
            course_id=course.id,
            course_name=course.name,
            name=raw["name"],
            description=clean_html(raw["description"]),
            created_at=dates["created_at"],
            unlock_at=dates["unlock_at"],
            due_at=dates["due_at"],
            points=float(raw["points_possible"]) if raw["points_possible"] is not None else None,
            url=raw.get("html_url") or f"{base_url}/courses/{course.id}/assignments/{raw['id']}",
            submission_state=state,
            submitted=submitted,
            excused=bool(submission.get("excused")),
            requires_submission=bool(types - {"none", "not_graded"}),
        )


@dataclass
class Snapshot:
    courses: list[Course]
    assignments: list[Assignment]
    missing: set[str]
    ignored: dict[str, str] = dataclass_field(default_factory=dict)
