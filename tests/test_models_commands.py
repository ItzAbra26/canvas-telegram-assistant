from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from src.commands import help_text, render_command, start_text
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
        "perdidas",
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
    assert "Urgentes: 1" in text and "Esta semana: 1" in text and "Más adelante: 1" in text
    assert "Sin fecha: 1" in text and "Total pendientes: 4" in text and "Perdidas: 1" in text


@pytest.mark.parametrize("seconds,expected", [(-1, False), (0, True), (1, True), (None, True)])
def test_pending_deadline_boundary(task, now, seconds, expected):
    a = replace(
        task, due_at=(now + timedelta(seconds=seconds)).isoformat() if seconds is not None else None
    )
    assert a.pending_at(now) is expected
    assert a.overdue(now) is (not expected)


def test_lost_tasks_separate_from_pending_and_reclassify_without_sync(state, task, config, now):
    tasks = [
        replace(
            task, id=1, name="Perdida sin entrega", due_at=(now - timedelta(seconds=1)).isoformat()
        ),
        replace(task, id=2, name="Vence ahora", due_at=now.isoformat()),
        replace(task, id=3, name="Sin fecha", due_at=None),
        replace(
            task,
            id=4,
            name="Ya entregada",
            due_at=(now - timedelta(days=1)).isoformat(),
            submitted=True,
        ),
        replace(
            task, id=5, name="Exenta", due_at=(now - timedelta(days=1)).isoformat(), excused=True
        ),
        replace(
            task,
            id=6,
            name="Sin entrega requerida",
            due_at=(now - timedelta(days=1)).isoformat(),
            requires_submission=False,
        ),
    ]
    reconcile(state, "111", Snapshot([Course(10, "Curso")], tasks, set()), config, now)
    user = state["users"]["111"]
    pending_text = "\n".join(render_command("pendientes", user, config, now))
    lost_text = "\n".join(render_command("perdidas", user, config, now))
    assert "Perdida sin entrega" not in pending_text
    assert "Vence ahora" in pending_text and "Sin fecha" in pending_text
    assert "TAREAS PERDIDAS</b> · 1" in lost_text and "🔴 Perdida" in lost_text
    for name in ["Vence ahora", "Sin fecha", "Ya entregada", "Exenta", "Sin entrega requerida"]:
        assert name not in lost_text
    assert render_command("atrasadas", user, config, now) == render_command(
        "perdidas", user, config, now
    )
    courses = "\n".join(render_command("asignaturas", user, config, now))
    assert "Pendientes: 2" in courses and "Perdidas: 1" in courses
    later = "\n".join(render_command("resumen", user, config, now + timedelta(seconds=1)))
    assert "Total pendientes: 1" in later and "Perdidas: 2" in later
    # A deadline extension in the cache restores the task without changing submission status.
    user["tasks"]["10:1"]["data"]["due_at"] = (now + timedelta(days=1)).isoformat()
    assert "Perdida sin entrega" in "\n".join(render_command("pendientes", user, config, now))


def test_branding_and_initial_counts(state, task, config, now):
    tasks = [task, replace(task, id=2, due_at=(now - timedelta(days=1)).isoformat())]
    reconcile(state, "111", Snapshot([Course(10, "Curso")], tasks, set()), config, now)
    baseline = state["outbox"]["u111:baseline"]["text"]
    assert "1 tareas pendientes · 1 perdidas" in baseline
    for text in [
        start_text(config),
        help_text(),
        "\n".join(render_command("resumen", state["users"]["111"], config, now)),
    ]:
        assert "Made by; AB Solutions" in text


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


def test_scorm_excluded_from_every_task_list_and_summary(state, task, config, now):
    from dataclasses import replace

    from src.commands import render_command
    from src.models import Course, Snapshot
    from src.notifier import reconcile

    scorm = replace(task, name="Tema sCoRm")
    reconcile(state, "111", Snapshot([Course(10, "Interfaces")], [scorm], set()), config, now)
    # Include a legacy cached row too: exclusion must not depend on the next API call.
    state["users"]["111"]["tasks"][scorm.key] = {
        "data": scorm.to_dict(),
        "revision": 0,
        "reminders": {},
        "first_seen_at": now.isoformat(),
    }
    for command in [
        "hoy",
        "manana",
        "semana",
        "pendientes",
        "perdidas",
        "atrasadas",
        "ultimas",
        "resumen",
        "asignaturas",
    ]:
        text = "\n".join(render_command(command, state["users"]["111"], config, now))
        assert "sCoRm" not in text
    assert "Total pendientes: 0" in "\n".join(
        render_command("resumen", state["users"]["111"], config, now)
    )
