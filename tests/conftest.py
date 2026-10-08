from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

from src.config import Config
from src.demo import DemoTelegram
from src.models import Assignment, Course, Snapshot
from src.storage import LocalStore, empty_state


@pytest.fixture
def now():
    return datetime(2026, 10, 8, 12, tzinfo=UTC)


@pytest.fixture
def config(tmp_path):
    return Config(
        "https://canvas.example.edu",
        "test-telegram-secret",
        Fernet.generate_key().decode(),
        state_file=tmp_path / "state.enc",
    )


@pytest.fixture
def task(now):
    return Assignment(
        1,
        10,
        "Diseño de Interfaces",
        "Práctica 3",
        "Entrega un diseño.",
        now.isoformat(),
        None,
        (now + timedelta(days=8)).isoformat(),
        10,
        "https://canvas.example.edu/courses/10/assignments/1",
        "unsubmitted",
        False,
        False,
        True,
    )


@pytest.fixture
def state():
    result = empty_state()
    result["users"]["111"] = {
        "onboarding": False,
        "token": "CANVAS_TEST_TOKEN_NOT_REAL",
        "canvas_user_id": 123,
        "tasks": {},
        "courses": [],
        "connected_at": "2026-10-08T12:00:00+00:00",
    }
    return result


@pytest.fixture
def store(config):
    return LocalStore(config.state_file, config.encryption_key)


@pytest.fixture
def telegram():
    return DemoTelegram()


@pytest.fixture
def snapshot(task):
    return Snapshot([Course(10, task.course_name)], [task], set())


@pytest.fixture
def raw_task(now):
    return {
        "id": 1,
        "name": "Práctica",
        "description": "<p>Hola <b>clase</b></p><script>secreto</script>",
        "due_at": (now + timedelta(days=1)).isoformat(),
        "created_at": now.isoformat(),
        "unlock_at": None,
        "points_possible": 10,
        "submission_types": ["online_upload"],
        "published": True,
        "submission": {"workflow_state": "unsubmitted", "submitted_at": None, "attempt": 0},
    }


def changed(task, **kwargs):
    return replace(task, **kwargs)
