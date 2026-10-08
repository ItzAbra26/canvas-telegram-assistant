import base64
import json
from pathlib import Path

import pytest
import responses
import yaml
from cryptography.fernet import Fernet

from src.config import Config
from src.errors import BotError, StorageError
from src.storage import GitHubStore, LocalStore, empty_state, validate_state

ROOT = "https://api.github.com/repos/example/classbot"


def test_encrypted_roundtrip_no_plaintext(store, config, state):
    store.save(state)
    assert store.load() == state
    content = config.state_file.read_bytes()
    assert b"CANVAS_TEST_TOKEN" not in content and b'"users"' not in content
    with pytest.raises(StorageError):
        LocalStore(config.state_file, Fernet.generate_key().decode()).load()
    assert config.state_file.read_bytes() == content


def test_corrupt_file_preserved(store, config):
    config.state_file.write_bytes(b"broken")
    with pytest.raises(StorageError):
        store.load()
    assert config.state_file.read_bytes() == b"broken"


def test_invalid_schema_does_not_save(store, state):
    state["offset"] = "bad"
    with pytest.raises(StorageError):
        store.save(state)


@responses.activate
def test_github_state_creation_encrypted_checkpoint(config, state):
    responses.get(ROOT + "/git/ref/heads/bot-state", status=404)
    responses.get(ROOT, json={"default_branch": "main"})
    responses.get(ROOT + "/git/ref/heads/main", json={"object": {"sha": "initial"}})
    responses.post(ROOT + "/git/refs", json={"ref": "refs/heads/bot-state"}, status=201)
    responses.get(ROOT + "/contents/state.enc", status=404)
    responses.put(ROOT + "/contents/state.enc", json={"content": {"sha": "newsha"}})
    store = GitHubStore("example/classbot", "fake-github-token", config.encryption_key)
    assert store.load() == empty_state()
    store.save(state)
    payload = json.loads(responses.calls[-1].request.body)
    encrypted = base64.b64decode(payload["content"])
    assert b"CANVAS_TEST_TOKEN" not in encrypted and payload["branch"] == "bot-state"
    assert store.codec.decode(encrypted) == state
    count = len(responses.calls)
    store.save(state)
    assert len(responses.calls) == count


@responses.activate
def test_github_restore_and_compare_and_swap(config, state):
    store = GitHubStore("example/classbot", "fake", config.encryption_key)
    responses.get(ROOT + "/git/ref/heads/bot-state", json={"object": {"sha": "branchsha"}})
    encrypted = base64.b64encode(store.codec.encode(state)).decode()
    responses.get(
        ROOT + "/contents/state.enc",
        json={"encoding": "base64", "content": encrypted, "sha": "oldsha"},
    )
    responses.put(ROOT + "/contents/state.enc", status=409, json={"message": "Conflict"})
    loaded = store.load()
    assert loaded == state
    loaded["offset"] = 123
    with pytest.raises(StorageError):
        store.save(loaded)
    payload = json.loads(responses.calls[-1].request.body)
    assert payload["sha"] == "oldsha" and store.sha == "oldsha"


@responses.activate
def test_github_bad_ciphertext_never_overwrites(config):
    responses.get(ROOT + "/git/ref/heads/bot-state", json={"object": {"sha": "branch"}})
    responses.get(
        ROOT + "/contents/state.enc",
        json={"encoding": "base64", "content": base64.b64encode(b"corrupt").decode(), "sha": "old"},
    )
    with pytest.raises(StorageError):
        GitHubStore("example/classbot", "fake", config.encryption_key).load()
    assert all(call.request.method == "GET" for call in responses.calls)


def set_env(monkeypatch, config):
    monkeypatch.setattr("src.config.load_dotenv", lambda **kwargs: None)
    for variable in (
        "CANVAS_BASE_URL",
        "TELEGRAM_BOT_TOKEN",
        "STATE_ENCRYPTION_KEY",
        "STORAGE_BACKEND",
        "TEST_MODE",
        "MAX_USERS",
        "TIMEZONE",
        "ALLOWED_TELEGRAM_USER_IDS",
        "INITIAL_NOTIFICATIONS",
        "RECENT_DAYS",
        "STATE_BRANCH",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("CANVAS_BASE_URL", config.canvas_base_url)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", config.telegram_token)
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", config.encryption_key)


def test_environment_config_and_redacted_repr(monkeypatch, config):
    set_env(monkeypatch, config)
    result = Config.from_env()
    assert result.timezone == "Europe/Madrid" and result.test_mode
    assert result.max_users == 15
    assert config.encryption_key not in repr(result) and config.telegram_token not in repr(result)


@pytest.mark.parametrize(
    "name,value",
    [
        ("CANVAS_BASE_URL", "http://canvas.example.edu"),
        ("CANVAS_BASE_URL", "https://canvas.example.edu/api/v1"),
        ("STATE_ENCRYPTION_KEY", "bad"),
        ("MAX_USERS", "0"),
        ("TEST_MODE", "yes"),
        ("TIMEZONE", "Invalid/Zone"),
        ("STORAGE_BACKEND", "bad"),
    ],
)
def test_bad_config_fails_clearly(monkeypatch, config, name, value):
    set_env(monkeypatch, config)
    monkeypatch.setenv(name, value)
    with pytest.raises(BotError):
        Config.from_env()


def test_missing_credential_fails(monkeypatch, config):
    set_env(monkeypatch, config)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    with pytest.raises(BotError, match="TELEGRAM_BOT_TOKEN"):
        Config.from_env()


def test_workflow_structure_and_persistence():
    # BaseLoader evita que YAML 1.1 convierta la clave 'on' en booleano.
    workflow = yaml.load(
        Path(".github/workflows/canvas-bot.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,  # noqa: S506 -- BaseLoader solo construye cadenas, no objetos Python.
    )
    assert workflow["on"]["schedule"][0]["cron"] == "7,22,37,52 * * * *"
    assert "workflow_dispatch" in workflow["on"]
    assert workflow["permissions"]["contents"] == "write"
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    job = workflow["jobs"]["check"]
    assert job["env"]["STORAGE_BACKEND"] == "github"
    assert job["env"]["MAX_USERS"] == "15"
    assert job["env"]["STATE_ENCRYPTION_KEY"] == "${{ secrets.STATE_ENCRYPTION_KEY }}"
    assert job["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    assert any(step.get("run") == "python -m src.main --once" for step in job["steps"])
    assert "default_branch" in job["if"]


def test_state_version_rejected():
    state = empty_state()
    state["version"] = 99
    with pytest.raises(StorageError):
        validate_state(state)
