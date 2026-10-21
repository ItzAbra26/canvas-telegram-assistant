import copy
import json

import pytest
import requests
import responses

from src.canvas_client import CanvasClient
from src.errors import AmbiguousDelivery, APIError, RejectedDelivery
from src.models import Assignment, Course
from src.telegram_bot import TelegramBot

BASE = "https://canvas.example.edu/api/v1"
TG = "https://api.telegram.org/bottest-bot-token"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.http_client.time.sleep", lambda seconds: None)
    monkeypatch.setattr("src.telegram_bot.time.sleep", lambda seconds: None)


@responses.activate
def test_canvas_full_pagination_and_auth(raw_task):
    responses.get(
        BASE + "/courses",
        json=[{"id": 10, "name": "Curso"}],
        headers={"Link": f'<{BASE}/courses?page=2>; rel="next"'},
    )
    responses.get(BASE + "/courses?page=2", json=[{"id": 20, "name": "Segundo curso"}])
    responses.get(
        BASE + "/courses/10/assignments",
        json=[raw_task],
        headers={"Link": f'<{BASE}/courses/10/assignments?page=2>; rel="next"'},
    )
    second = copy.deepcopy(raw_task)
    second["id"] = 2
    responses.get(BASE + "/courses/10/assignments?page=2", json=[second])
    responses.get(BASE + "/courses/20/assignments", json=[])
    snapshot = CanvasClient("https://canvas.example.edu", "SECRET_TEST_TOKEN").snapshot({})
    assert len(snapshot.courses) == 2 and len(snapshot.assignments) == 2
    assert all(
        call.request.headers["Authorization"] == "Bearer SECRET_TEST_TOKEN"
        for call in responses.calls
    )
    assert all(call.request.headers["Accept"] == "application/json" for call in responses.calls)
    assert all(
        call.request.headers["User-Agent"].startswith("CanvasTelegramAssistant/1.0 ")
        for call in responses.calls
    )
    assert "override_assignment_dates=true" in responses.calls[2].request.url


@responses.activate
def test_canvas_profile_identifies_the_integration_for_server_access_filters():
    def require_headers(request):
        if request.headers.get("Accept") != "application/json" or not request.headers.get(
            "User-Agent", ""
        ).startswith("CanvasTelegramAssistant/"):
            return 403, {"Content-Type": "text/html"}, "Forbidden"
        return 200, {}, json.dumps({"id": 42})

    responses.add_callback(responses.GET, BASE + "/users/self/profile", callback=require_headers)
    assert CanvasClient("https://canvas.example.edu", "test").profile() == 42


@responses.activate
def test_missing_include_submission_fallback(raw_task):
    submission = raw_task.pop("submission")
    responses.get(BASE + "/courses", json=[{"id": 10, "name": "Curso"}])
    responses.get(BASE + "/courses/10/assignments", json=[raw_task])
    responses.get(BASE + "/courses/10/assignments/1/submissions/self", json=submission)
    result = CanvasClient("https://canvas.example.edu", "test").snapshot({})
    assert len(result.assignments) == 1 and result.assignments[0].pending


@responses.activate
def test_scorm_is_ignored_without_requesting_its_submission(raw_task):
    responses.get(BASE + "/courses", json=[{"id": 10, "name": "Curso"}])
    responses.get(
        BASE + "/courses/10/assignments", json=[raw_task, {"id": 2, "name": "Tema 3 SCORM"}]
    )
    result = CanvasClient("https://canvas.example.edu", "test").snapshot({})
    assert len(result.assignments) == 1 and result.ignored == {"10:2": "Tema 3 SCORM"}
    assert len(responses.calls) == 2


@responses.activate
def test_missing_assignment_confirmed_individually(raw_task):
    previous = {
        "10:1": {
            "data": Assignment.from_api(
                raw_task, Course(10, "Curso"), "https://canvas.example.edu"
            ).to_dict(),
            "deleted": False,
        }
    }
    responses.get(BASE + "/courses", json=[{"id": 10, "name": "Curso"}])
    responses.get(BASE + "/courses/10/assignments", json=[])
    responses.get(BASE + "/courses/10/assignments/1", status=404)
    result = CanvasClient("https://canvas.example.edu", "test").snapshot(previous)
    assert result.missing == {"10:1"}


@responses.activate
def test_hidden_old_assignment_403_does_not_abort_visible_tasks(raw_task):
    previous = {
        "10:1": {
            "data": Assignment.from_api(
                raw_task, Course(10, "Curso"), "https://canvas.example.edu"
            ).to_dict()
        }
    }
    visible = {**raw_task, "id": 2, "name": "Tarea visible"}
    responses.get(BASE + "/courses", json=[{"id": 10, "name": "Curso"}])
    responses.get(BASE + "/courses/10/assignments", json=[visible])
    responses.get(
        BASE + "/courses/10/assignments/1",
        status=403,
        json={"errors": [{"message": "unauthorized"}]},
    )
    client = CanvasClient("https://canvas.example.edu", "test")
    for _ in range(2):
        result = client.snapshot(previous)
        assert result.unavailable == {"10:1"}
        assert not result.missing
        assert [a.id for a in result.assignments] == [2]
    assert (
        len(responses.calls) == 6
    )  # Recheck visibility rather than permanently ignoring the task.


@responses.activate
@pytest.mark.parametrize("path", ["courses", "courses/10/assignments"])
def test_forbidden_full_list_still_fails_instead_of_committing_empty_snapshot(path):
    if path != "courses":
        responses.get(BASE + "/courses", json=[{"id": 10, "name": "Curso"}])
    responses.get(BASE + "/" + path, status=403)
    with pytest.raises(APIError) as error:
        CanvasClient("https://canvas.example.edu", "test").snapshot({})
    assert error.value.status == 403


@responses.activate
@pytest.mark.parametrize("status", [401, 429, 503])
def test_other_errors_on_old_assignment_are_not_treated_as_loss_of_visibility(raw_task, status):
    previous = {
        "10:1": {
            "data": Assignment.from_api(
                raw_task, Course(10, "Curso"), "https://canvas.example.edu"
            ).to_dict()
        }
    }
    responses.get(BASE + "/courses", json=[{"id": 10, "name": "Curso"}])
    responses.get(BASE + "/courses/10/assignments", json=[])
    responses.get(BASE + "/courses/10/assignments/1", status=status)
    with pytest.raises(APIError) as error:
        CanvasClient("https://canvas.example.edu", "test").snapshot(previous)
    assert error.value.status == status


@responses.activate
@pytest.mark.parametrize(
    "body,headers",
    [
        ("Access denied", {"Content-Type": "text/html"}),
        ('{"errors":[{"message":"insufficient_scope"}]}', {"Content-Type": "application/json"}),
        (
            '{"status":"unauthorized"}',
            {"Content-Type": "application/json", "cf-mitigated": "challenge"},
        ),
        ("incomplete", {"Content-Type": "application/json"}),
    ],
)
def test_server_filters_and_scope_errors_do_not_hide_old_assignments(raw_task, body, headers):
    previous = {
        "10:1": {
            "data": Assignment.from_api(
                raw_task, Course(10, "Curso"), "https://canvas.example.edu"
            ).to_dict()
        }
    }
    responses.get(BASE + "/courses", json=[{"id": 10, "name": "Curso"}])
    responses.get(BASE + "/courses/10/assignments", json=[])
    responses.get(BASE + "/courses/10/assignments/1", status=403, body=body, headers=headers)
    with pytest.raises(APIError) as error:
        CanvasClient("https://canvas.example.edu", "test").snapshot(previous)
    assert error.value.status == 403


@responses.activate
def test_truncated_list_restores_task_from_individual_get(raw_task):
    previous = {
        "10:1": {
            "data": Assignment.from_api(
                raw_task, Course(10, "Curso"), "https://canvas.example.edu"
            ).to_dict()
        }
    }
    responses.get(BASE + "/courses", json=[{"id": 10, "name": "Curso"}])
    responses.get(BASE + "/courses/10/assignments", json=[])
    responses.get(BASE + "/courses/10/assignments/1", json=raw_task)
    result = CanvasClient("https://canvas.example.edu", "test").snapshot(previous)
    assert not result.missing and len(result.assignments) == 1


@responses.activate
def test_off_origin_pagination_rejected_before_sending_token():
    responses.get(
        BASE + "/courses",
        json=[],
        headers={"Link": '<https://evil.example/api/v1/courses>; rel="next"'},
    )
    with pytest.raises(APIError):
        CanvasClient("https://canvas.example.edu", "test").snapshot({})
    assert len(responses.calls) == 1


@responses.activate
def test_redirect_not_followed():
    responses.get(
        BASE + "/users/self/profile", status=302, headers={"Location": "https://evil.example/"}
    )
    with pytest.raises(APIError):
        CanvasClient("https://canvas.example.edu", "test").profile()
    assert len(responses.calls) == 1


@responses.activate
def test_canvas_429_retry_success():
    responses.get(BASE + "/users/self/profile", status=429, headers={"Retry-After": "1"})
    responses.get(BASE + "/users/self/profile", json={"id": 42})
    assert CanvasClient("https://canvas.example.edu", "test").profile() == 42
    assert len(responses.calls) == 2


@responses.activate
def test_canvas_long_rate_limit_defers():
    responses.get(BASE + "/users/self/profile", status=429, headers={"Retry-After": "120"})
    with pytest.raises(APIError) as error:
        CanvasClient("https://canvas.example.edu", "test").profile()
    assert error.value.status == 429 and len(responses.calls) == 1


@responses.activate
@pytest.mark.parametrize("body", [{"unexpected": True}, [{"id": 1}], "invalid"])
def test_canvas_incomplete_courses_fail(body):
    responses.get(BASE + "/courses", json=body)
    with pytest.raises(APIError):
        CanvasClient("https://canvas.example.edu", "test").snapshot({})


@responses.activate
def test_telegram_http_payloads_and_deletion():
    responses.post(
        TG + "/getUpdates", json={"ok": True, "result": [{"update_id": 99, "message": {}}]}
    )
    responses.post(TG + "/sendMessage", json={"ok": True, "result": {"message_id": 5}})
    responses.post(TG + "/deleteMessage", json={"ok": True, "result": True})
    bot = TelegramBot("test-bot-token")
    assert bot.get_updates(42)[0]["update_id"] == 99
    assert bot.send_message(111, "<b>Hola</b>") == 5
    assert bot.delete_message(111, 5)
    sent = json.loads(responses.calls[1].request.body)
    assert sent["chat_id"] == 111 and sent["parse_mode"] == "HTML"


@responses.activate
def test_telegram_429_confirmed_rejection():
    responses.post(
        TG + "/sendMessage",
        status=429,
        json={"ok": False, "error_code": 429, "parameters": {"retry_after": 10}},
    )
    with pytest.raises(RejectedDelivery) as error:
        TelegramBot("test-bot-token").send_message(111, "Hola")
    assert error.value.retry_after == 10


@responses.activate
@pytest.mark.parametrize(
    "failure", [requests.Timeout("private URL token"), requests.ConnectionError("secret")]
)
def test_telegram_network_failure_ambiguous_and_redacted(failure):
    responses.post(TG + "/sendMessage", body=failure)
    with pytest.raises(AmbiguousDelivery) as error:
        TelegramBot("test-bot-token").send_message(111, "Hola")
    assert "test-bot-token" not in str(error.value) and "private URL" not in str(error.value)
    assert len(responses.calls) == 1


@responses.activate
def test_telegram_invalid_response_ambiguous():
    responses.post(TG + "/sendMessage", status=502, body="bad gateway")
    with pytest.raises(AmbiguousDelivery):
        TelegramBot("test-bot-token").send_message(111, "Hola")


@responses.activate
def test_delete_failure_and_webhook():
    responses.post(TG + "/deleteMessage", status=400, json={"ok": False, "error_code": 400})
    responses.post(
        TG + "/getWebhookInfo", json={"ok": True, "result": {"url": "https://example.com/hook"}}
    )
    responses.post(TG + "/deleteWebhook", json={"ok": True, "result": True})
    bot = TelegramBot("test-bot-token")
    assert not bot.delete_message(111, 9)
    assert bot.webhook_info()["url"]
    bot.remove_webhook()


@responses.activate
def test_telegram_identity_returns_only_public_username():
    responses.post(
        TG + "/getMe", json={"ok": True, "result": {"is_bot": True, "username": "class_bot"}}
    )
    assert TelegramBot("test-bot-token").identity() == "class_bot"


@responses.activate
@pytest.mark.parametrize("result", [{}, {"is_bot": False, "username": "user"}, []])
def test_telegram_incomplete_identity_is_rejected(result):
    responses.post(TG + "/getMe", json={"ok": True, "result": result})
    with pytest.raises(APIError):
        TelegramBot("test-bot-token").identity()
