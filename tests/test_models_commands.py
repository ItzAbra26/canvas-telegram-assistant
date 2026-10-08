from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from src.commands import render_command
from src.models import Assignment, Course, Snapshot
from src.notifier import reconcile, task_block
from src.utils import clean_html, date_label, e, pack_messages, parse_date, safe_link, time_left


def test_html_cleaning_and_escaping():
    assert (
        clean_html("<p>A &amp; B</p><style>hide</style><script>alert(1)</script><p>C</p>")
        == "A & B C"
    )
    assert e('<b>&"') == "&lt;b&gt;&amp;&quot;"
    assert len(e("&" * 1000, 200)) <= 200


def test_dates_madrid_dst_and_labels():
    assert "Domingo 11 de octubre de 2026 · 23:59" == date_label("2026-10-11T21:59:00Z")
    assert date_label("2026-10-25T01:30:00Z").endswith("02:30")
    assert date_label("2026-03-29T01:30:00Z").endswith("03:30")
    with pytest.raises(ValueError):
        parse_date("2026-10-08T12:00:00")


def test_missing_dates_and_relative(now):
    assert date_label(None) == "Sin fecha límite"
    assert time_left(None, now) == "Sin fecha límite"
    assert (
        time_left((now + timedelta(days=2, hours=4)).isoformat(), now) == "Quedan 2 días y 4 horas"
    )
    assert time_left((now - timedelta(seconds=1)).isoformat(), now) == "Plazo vencido"


@pytest.mark.parametrize(
    "state,attempt,submitted",
    [
        ("unsubmitted", 0, False),
        ("submitted", 0, True),
        ("pending_review", 0, True),
        ("graded", 0, False),
        ("graded", 1, True),
    ],
)
def test_submission_states(raw_task, state, attempt, submitted):
    raw_task["submission"].update(workflow_state=state, attempt=attempt)
    a = Assignment.from_api(raw_task, Course(10, "Curso"), "https://canvas.example.edu")
    assert a.submitted is submitted
    assert a.description == "Hola clase"
    assert a.pending is not submitted


def test_exempt_and_no_submission(raw_task):
    raw_task["submission"]["excused"] = True
    assert not Assignment.from_api(
        raw_task, Course(10, "Curso"), "https://canvas.example.edu"
    ).pending
    raw_task["submission"]["excused"] = False
    raw_task["submission_types"] = ["none"]
    assert not Assignment.from_api(
        raw_task, Course(10, "Curso"), "https://canvas.example.edu"
    ).pending


def test_incomplete_assignment_rejected(raw_task):
    del raw_task["due_at"]
    with pytest.raises(ValueError):
        Assignment.from_api(raw_task, Course(10, "Curso"), "https://canvas.example.edu")


@pytest.mark.parametrize(
    "command",
    [
        "hoy",
        "manana",
        "semana",
        "pendientes",
        "atrasadas",
        "ultimas",
        "asignaturas",
        "resumen",
        "estado",
        "ayuda",
        "privacidad",
    ],
)
def test_all_commands(command, state, snapshot, config, now):
    reconcile(state, "111", snapshot, config, now)
    pages = pack_messages(render_command(command, state["users"]["111"], config, now))
    assert pages and all(len(p) <= 3500 for p in pages)


def test_midnight_local_filter_includes_delivered(state, task, config):
    now = datetime(2026, 10, 8, 22, 30, tzinfo=UTC)  # 9 octubre, 00:30 Madrid
    a = replace(task, due_at="2026-10-09T21:59:00Z", submitted=True)
    reconcile(state, "111", Snapshot([Course(10, "Curso")], [a], set()), config, now)
    text = "\n".join(render_command("hoy", state["users"]["111"], config, now))
    assert a.name in text and "✅ Entregada" in text
    tomorrow = "\n".join(render_command("manana", state["users"]["111"], config, now))
    assert "ENTREGAS DE MAÑANA</b> · 0" in tomorrow and a.name not in tomorrow


def test_summary_disjoint_categories(state, task, config, now):
    tasks = [
        replace(task, id=i, due_at=(now + timedelta(hours=h)).isoformat())
        for i, h in enumerate([-2, 20, 48, 200], 1)
    ]
    tasks.append(replace(task, id=5, due_at=None))
    reconcile(state, "111", Snapshot([Course(10, "Curso")], tasks, set()), config, now)
    text = "\n".join(render_command("resumen", state["users"]["111"], config, now))
    assert "Urgentes: 2" in text and "Esta semana: 1" in text and "Más adelante: 1" in text
    assert "Sin fecha: 1" in text and "Total pendientes: 5" in text


def test_pagination_and_extreme_html_entities(task, config, now):
    worst = replace(task, description="&" * 10000, name="&" * 10000, course_name="&" * 10000)
    block = task_block(worst, config, now, True)
    assert len(block) < 3500
    pages = pack_messages([block] * 50)
    assert len(pages) > 1 and all(len(page) <= 3500 for page in pages)
    assert sum(page.count("Abrir en Canvas") for page in pages) == 50


def test_links_only_canvas_https(config):
    assert safe_link("https://evil.example/x", config.canvas_base_url) == ""
    assert safe_link("javascript:alert(1)", config.canvas_base_url) == ""
    assert (
        safe_link("https://canvas.example.edu/x?access_token=secret", config.canvas_base_url) == ""
    )
    assert "Abrir en Canvas" in safe_link(
        "https://canvas.example.edu/courses/1", config.canvas_base_url
    )
