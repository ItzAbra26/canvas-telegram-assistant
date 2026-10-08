import copy
from dataclasses import replace
from datetime import timedelta

import pytest

from src.demo import demo_update
from src.errors import APIError
from src.models import Snapshot
from src.notifier import reconcile
from src.service import BotService


def factory_with(snapshot, error=None, profile_error=None):
    class FakeCanvas:
        calls = []

        def __init__(self, url, token):
            self.token = token
            self.calls.append(token)

        def profile(self):
            if profile_error:
                raise profile_error
            return 123

        def snapshot(self, previous):
            if error:
                raise error
            return copy.deepcopy(snapshot)

    return FakeCanvas


def test_start_token_register_and_private_response(config, store, telegram, snapshot, now):
    token = "PERSONAL_TEST_TOKEN_NOT_REAL"
    factory = factory_with(snapshot)
    telegram.updates = [
        demo_update(1, 111, "/start"),
        demo_update(2, 111, token),
        demo_update(3, 111, "/resumen"),
    ]
    assert BotService(config, store, telegram, factory).cycle(now)
    user = store.load()["users"]["111"]
    assert user["token"] == token and user["canvas_user_id"] == 123
    assert not user["onboarding"] and user["initialized"]
    assert telegram.deleted == [(111, 2)]
    text = "\n".join(m for _, m in telegram.sent)
    assert "Cuenta → Configuración" in text and "Cuenta conectada" in text
    assert "Total pendientes: 1" in text and token not in text
    assert token.encode() not in config.state_file.read_bytes()
    assert store.load()["offset"] == 4
    count = len(telegram.sent)
    telegram.updates = [demo_update(3, 111, "/resumen")]
    BotService(config, store, telegram, factory).cycle(now + timedelta(minutes=15))
    assert len(telegram.sent) == count


def test_two_students_have_different_submission_status(
    config, store, telegram, task, snapshot, now
):
    class FakeCanvas:
        def __init__(self, url, token):
            self.token = token

        def profile(self):
            return 123 if self.token.endswith("A") else 456

        def snapshot(self, previous):
            return Snapshot(
                snapshot.courses, [replace(task, submitted=self.token.endswith("B"))], set()
            )

    telegram.updates = [
        demo_update(1, 111, "/start"),
        demo_update(2, 111, "TEST_CANVAS_TOKEN_A"),
        demo_update(3, 222, "/start"),
        demo_update(4, 222, "TEST_CANVAS_TOKEN_B"),
        demo_update(5, 111, "/resumen"),
        demo_update(6, 222, "/resumen"),
    ]
    assert BotService(config, store, telegram, FakeCanvas).cycle(now)
    a = next(m for chat, m in telegram.sent if chat == 111 and "RESUMEN" in m)
    b = next(m for chat, m in telegram.sent if chat == 222 and "RESUMEN" in m)
    assert "Total pendientes: 1" in a and "Total pendientes: 0" in b


def test_groups_and_spoofed_chat_ignored(config, store, telegram, snapshot, now):
    group = demo_update(1, 111, "/start")
    group["message"]["chat"] = {"id": -99, "type": "group"}
    spoofed = demo_update(2, 111, "/resumen")
    spoofed["message"]["chat"]["id"] = 222
    telegram.updates = [group, spoofed]
    BotService(config, store, telegram, factory_with(snapshot)).cycle(now)
    assert not telegram.sent and not store.load()["users"]


def test_allowlist_and_invite(config, store, telegram, snapshot, now):
    config = replace(config, allowed_users=frozenset({111}), invite_code="clase123")
    telegram.updates = [
        demo_update(1, 222, "/start clase123"),
        demo_update(2, 111, "/start"),
        demo_update(3, 111, "/start clase123"),
    ]
    BotService(config, store, telegram, factory_with(snapshot)).cycle(now)
    assert set(store.load()["users"]) == {"111"}
    assert all(chat == 111 for chat, _ in telegram.sent)


def test_bad_token_not_persisted(config, store, telegram, snapshot, now):
    token = "INVALID_CANVAS_TEST_TOKEN"
    telegram.updates = [demo_update(1, 111, "/start"), demo_update(2, 111, token)]
    factory = factory_with(snapshot, profile_error=APIError("Canvas", 401))
    BotService(config, store, telegram, factory).cycle(now)
    assert "token" not in store.load()["users"]["111"]
    assert all(token not in text for _, text in telegram.sent)
    assert telegram.deleted == [(111, 2)]


def test_failed_delete_warns_but_connects(config, store, telegram, snapshot, now):
    telegram.delete_message = lambda chat, message: False
    telegram.updates = [
        demo_update(1, 111, "/start"),
        demo_update(2, 111, "VALID_CANVAS_TEST_TOKEN"),
    ]
    BotService(config, store, telegram, factory_with(snapshot)).cycle(now)
    assert store.load()["users"]["111"]["token"]
    assert any("No pude borrar" in text for _, text in telegram.sent)


def test_short_token_and_cancel(config, store, telegram, snapshot, now):
    telegram.updates = [
        demo_update(1, 111, "/start"),
        demo_update(2, 111, "short"),
        demo_update(3, 111, "/cancelar"),
    ]
    BotService(config, store, telegram, factory_with(snapshot)).cycle(now)
    assert not store.load()["users"]


def test_test_mode_false_disables_manual_registration(config, store, telegram, snapshot, now):
    config = replace(config, test_mode=False)
    telegram.updates = [demo_update(1, 111, "/start"), demo_update(2, 111, "VALID_TEST_TOKEN_000")]
    BotService(config, store, telegram, factory_with(snapshot)).cycle(now)
    assert not store.load()["users"]
    assert any("desactivado" in text for _, text in telegram.sent)


@pytest.mark.parametrize("status", [None, 403, 429, 503])
def test_canvas_failure_preserves_previous_state(
    status, config, store, telegram, state, snapshot, now
):
    reconcile(state, "111", snapshot, config, now)
    store.save(state)
    previous = copy.deepcopy(state["users"]["111"]["tasks"])
    telegram.updates = [demo_update(1, 111, "/pendientes")]
    healthy = BotService(
        config, store, telegram, factory_with(snapshot, APIError("Canvas", status))
    ).cycle(now + timedelta(minutes=15))
    assert not healthy
    assert store.load()["users"]["111"]["tasks"] == previous
    assert any("últimos datos" in text for _, text in telegram.sent)


def test_expired_token_disabled_once(config, store, telegram, state, snapshot, now):
    store.save(state)
    service = BotService(config, store, telegram, factory_with(snapshot, APIError("Canvas", 401)))
    assert not service.cycle(now)
    assert store.load()["users"]["111"]["disabled"]
    count = len(telegram.sent)
    assert service.cycle(now + timedelta(minutes=15))
    assert len(telegram.sent) == count


def test_disconnect_forgets_credentials_and_old_outbox(
    config, store, telegram, state, snapshot, now
):
    reconcile(state, "111", snapshot, config, now)
    store.save(state)
    telegram.updates = [demo_update(1, 111, "/resumen"), demo_update(2, 111, "/desconectar")]
    BotService(config, store, telegram, factory_with(snapshot)).cycle(now)
    saved = store.load()
    assert not saved["users"]
    assert list(saved["outbox"]) == ["update:2"]
    assert all("RESUMEN" not in text for _, text in telegram.sent)


def test_one_bad_account_does_not_block_others(config, store, telegram, state, snapshot, now):
    state["users"]["222"] = copy.deepcopy(state["users"]["111"])
    state["users"]["222"]["token"] = "WORKING_TEST_TOKEN"
    store.save(state)

    class FakeCanvas:
        def __init__(self, url, token):
            self.token = token

        def snapshot(self, previous):
            if self.token != "WORKING_TEST_TOKEN":
                raise APIError("Canvas", 503)
            return snapshot

    assert not BotService(config, store, telegram, FakeCanvas).cycle(now)
    assert store.load()["users"]["222"]["initialized"]
    assert any(chat == 222 for chat, _ in telegram.sent)


def test_forwarded_token_rejected(config, store, telegram, snapshot, now):
    forwarded = demo_update(2, 111, "FORWARDED_TEST_TOKEN")
    forwarded["message"]["forward_origin"] = {"type": "user"}
    telegram.updates = [demo_update(1, 111, "/start"), forwarded]
    BotService(config, store, telegram, factory_with(snapshot)).cycle(now)
    assert "token" not in store.load()["users"]["111"]
    assert telegram.deleted == [(111, 2)]


def test_capacity_limit(config, store, telegram, snapshot, now):
    config = replace(config, max_users=1)
    telegram.updates = [demo_update(1, 111, "/start"), demo_update(2, 222, "/start")]
    BotService(config, store, telegram, factory_with(snapshot)).cycle(now)
    assert set(store.load()["users"]) == {"111"}
    assert any("límite" in text for chat, text in telegram.sent if chat == 222)


def test_unicode_invite_does_not_crash_or_bypass(config, store, telegram, snapshot, now):
    config = replace(config, invite_code="Diseño-2026")
    telegram.updates = [
        demo_update(1, 111, "/start incorrecto🔒"),
        demo_update(2, 111, "/start Diseño-2026"),
    ]
    assert BotService(config, store, telegram, factory_with(snapshot)).cycle(now)
    assert store.load()["users"]["111"]["onboarding"]
