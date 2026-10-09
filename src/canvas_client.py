from typing import Any
from urllib.parse import urljoin, urlsplit

from src.errors import APIError
from src.http_client import HTTPClient
from src.models import Assignment, Course, Snapshot, ignored_assignment


class CanvasClient:
    def __init__(self, base_url: str, token: str, http: HTTPClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.http = http or HTTPClient("Canvas", token)

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v1/{path}"

    def profile(self) -> int:
        data = self.http.json("GET", self._url("users/self/profile"))
        try:
            return int(data["id"])
        except (KeyError, TypeError, ValueError):
            raise APIError("Canvas") from None

    def _pages(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        url = self._url(path)
        seen = set()
        items = []
        while url:
            parsed = urlsplit(url)
            base = urlsplit(self.base_url)
            # No enviar el bearer token a dominios de enlaces externos ni redirecciones.
            if (
                parsed.scheme != "https"
                or parsed.netloc != base.netloc
                or parsed.username
                or parsed.password
                or not parsed.path.startswith("/api/v1/")
                or url in seen
                or len(seen) >= 1000
            ):
                raise APIError("Canvas")
            seen.add(url)
            response = self.http.request("GET", url, params=params if len(seen) == 1 else None)
            if response.status_code != 200:
                raise APIError("Canvas", response.status_code)
            try:
                page = response.json()
                if not isinstance(page, list) or any(
                    not isinstance(x, dict) or "id" not in x for x in page
                ):
                    raise ValueError
            except ValueError:
                raise APIError("Canvas") from None
            items.extend(page)
            next_url = response.links.get("next", {}).get("url")
            url = urljoin(url, next_url) if next_url else ""
        return items

    def snapshot(self, previous: dict[str, Any]) -> Snapshot:
        raw_courses = self._pages(
            "courses",
            {
                "enrollment_state": "active",
                "enrollment_type": "student",
                "state[]": "available",
                "per_page": 100,
            },
        )
        try:
            courses = [Course(int(c["id"]), c["name"]) for c in raw_courses]
            if any(not isinstance(c.name, str) for c in courses):
                raise ValueError
            if len({c.id for c in courses}) != len(courses):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise APIError("Canvas") from None
        assignments: list[Assignment] = []
        missing: set[str] = set()
        ignored: dict[str, str] = {}
        for course in courses:
            raw_tasks = self._pages(
                f"courses/{course.id}/assignments",
                {
                    "include[]": "submission",
                    "override_assignment_dates": "true",
                    "per_page": 100,
                },
            )
            course_keys = set()
            for raw in raw_tasks:
                if raw.get("published") is False:
                    continue
                if isinstance(raw.get("name"), str) and ignored_assignment(raw["name"]):
                    key = f"{course.id}:{raw['id']}"
                    ignored[key] = raw["name"]
                    course_keys.add(key)
                    continue
                if (
                    not isinstance(raw.get("submission"), dict)
                    or "workflow_state" not in raw["submission"]
                ):
                    # Fallback oficial si la instancia omite include[]=submission.
                    raw["submission"] = self.http.json(
                        "GET",
                        self._url(f"courses/{course.id}/assignments/{raw['id']}/submissions/self"),
                    )
                try:
                    assignment = Assignment.from_api(raw, course, self.base_url)
                except (ValueError, KeyError, TypeError):
                    raise APIError("Canvas") from None
                if assignment.key in course_keys:
                    raise APIError("Canvas")
                assignments.append(assignment)
                course_keys.add(assignment.key)
            for key, record in previous.items():
                if (
                    record["data"]["course_id"] != course.id
                    or key in course_keys
                    or record.get("deleted")
                    or record.get("ignored")
                    or ignored_assignment(record["data"]["name"])
                ):
                    continue
                # Confirmación individual: una lista truncada no basta para borrar una tarea.
                response = self.http.request(
                    "GET",
                    self._url(f"courses/{course.id}/assignments/{record['data']['id']}"),
                    params={"include[]": "submission"},
                )
                if response.status_code == 404:
                    missing.add(key)
                elif response.status_code == 200:
                    try:
                        raw = response.json()
                        if isinstance(raw.get("name"), str) and ignored_assignment(raw["name"]):
                            ignored[key] = raw["name"]
                            continue
                        if not isinstance(raw.get("submission"), dict):
                            raw["submission"] = self.http.json(
                                "GET",
                                self._url(
                                    f"courses/{course.id}/assignments/{record['data']['id']}/submissions/self"
                                ),
                            )
                        assignments.append(Assignment.from_api(raw, course, self.base_url))
                    except (ValueError, KeyError, TypeError, AttributeError):
                        raise APIError("Canvas") from None
                else:
                    raise APIError("Canvas", response.status_code)
        return Snapshot(courses, assignments, missing, ignored)
