from dataclasses import replace

import pytest

from src.ai import PlainTaskAssistant
from src.demo import run_demo
from src.errors import APIError
from src.main import main


def test_end_to_end_demo(capsys):
    assert run_demo() == 0
    assert "DEMO OK" in capsys.readouterr().out


def test_ai_extension_needs_no_api(task):
    extension = PlainTaskAssistant()
    assert extension.explain(task) == task.description
    assert extension.summarize(task) == task.description
    assert extension.estimate_minutes(task) is None


def test_generate_key_main(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["bot", "--generate-key"])
    assert main() == 0
    assert len(capsys.readouterr().out.strip()) == 44


def test_demo_main(monkeypatch):
    monkeypatch.setattr("sys.argv", ["bot", "--demo"])
    assert main() == 0


def test_real_main_once_with_mocked_telegram(monkeypatch, config, telegram):
    monkeypatch.setattr("sys.argv", ["bot", "--once"])
    monkeypatch.setattr("src.main.Config.from_env", lambda: config)
    telegram.webhook_info = lambda: {"url": ""}
    monkeypatch.setattr("src.main.TelegramBot", lambda token: telegram)
    assert main() == 0
    assert config.state_file.exists()


def test_real_main_fails_when_webhook_active(monkeypatch, config, telegram):
    monkeypatch.setattr("sys.argv", ["bot", "--once"])
    monkeypatch.setattr("src.main.Config.from_env", lambda: config)
    telegram.webhook_info = lambda: {"url": "https://example.com/hook"}
    monkeypatch.setattr("src.main.TelegramBot", lambda token: telegram)
    assert main() == 1


def test_real_main_remove_webhook(monkeypatch, config, telegram):
    monkeypatch.setattr("sys.argv", ["bot", "--remove-webhook"])
    monkeypatch.setattr("src.main.Config.from_env", lambda: config)
    calls = []
    telegram.remove_webhook = lambda: calls.append(True)
    monkeypatch.setattr("src.main.TelegramBot", lambda token: telegram)
    assert main() == 0 and calls == [True]


def test_real_main_import_personal(monkeypatch, config, telegram):
    config = replace(config, canvas_token="TEST_IMPORT_PERSONAL_TOKEN", personal_chat_id="111")
    monkeypatch.setattr("sys.argv", ["bot", "--import-personal", "--once"])
    monkeypatch.setattr("src.main.Config.from_env", lambda: config)
    telegram.webhook_info = lambda: {"url": ""}
    monkeypatch.setattr("src.main.TelegramBot", lambda token: telegram)

    class Canvas:
        def __init__(self, base, token):
            pass

        def profile(self):
            return 123

    monkeypatch.setattr("src.main.CanvasClient", Canvas)
    monkeypatch.setattr("src.main.BotService.cycle", lambda self, now: True)
    assert main() == 0


def test_real_main_api_error_safe(monkeypatch, config, telegram, caplog):
    monkeypatch.setattr("sys.argv", ["bot", "--once"])
    monkeypatch.setattr("src.main.Config.from_env", lambda: config)

    def broken():
        raise APIError("Telegram", 401)

    telegram.webhook_info = broken
    monkeypatch.setattr("src.main.TelegramBot", lambda token: telegram)
    assert main() == 1
    assert config.telegram_token not in caplog.text


@pytest.mark.parametrize("args", [["--once"], ["--import-personal"]])
def test_invalid_environment_does_not_start(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["bot", *args])
    monkeypatch.setattr("src.config.load_dotenv", lambda **kwargs: None)
    monkeypatch.delenv("CANVAS_BASE_URL", raising=False)
    assert main() == 1
