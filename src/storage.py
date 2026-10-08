import base64
import gzip
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

from src.errors import APIError, StorageError
from src.http_client import HTTPClient
from src.models import Assignment
from src.utils import parse_date

State = dict[str, Any]


def empty_state() -> State:
    return {"version": 1, "users": {}, "offset": 0, "outbox": {}, "commands_registered": False}


def validate_state(state: Any) -> State:
    """Un archivo corrupto NO se sustituye por una base vacía."""
    try:
        if (
            not isinstance(state, dict)
            or state["version"] != 1
            or not isinstance(state["users"], dict)
            or not isinstance(state["outbox"], dict)
            or type(state["offset"]) is not int
            or state["offset"] < 0
            or type(state["commands_registered"]) is not bool
        ):
            raise ValueError
        for uid, user in state["users"].items():
            if (
                not uid.isdigit()
                or not isinstance(user, dict)
                or type(user["onboarding"]) is not bool
            ):
                raise ValueError
            if user.get("token"):
                if not isinstance(user["token"], str) or type(user["canvas_user_id"]) is not int:
                    raise ValueError
            for record in user.get("tasks", {}).values():
                assignment = Assignment(**record["data"])
                parse_date(assignment.due_at)
                if not isinstance(record["reminders"], dict) or type(record["revision"]) is not int:
                    raise ValueError
        for event in state["outbox"].values():
            if event["status"] not in {"pending", "sending", "sent", "uncertain", "cancelled"}:
                raise ValueError
            if not isinstance(event["chat_id"], int) or not isinstance(event.get("text", ""), str):
                raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        raise StorageError("Estado incompatible o corrupto; se conserva sin modificar.") from None
    return state


class Store(Protocol):
    def load(self) -> State: ...
    def save(self, state: State) -> None: ...


class EncryptedCodec:
    def __init__(self, key: str):
        try:
            self.cipher = Fernet(key.encode())
        except ValueError:
            raise StorageError("STATE_ENCRYPTION_KEY inválida.") from None

    def encode(self, state: State) -> bytes:
        validate_state(state)
        payload = json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        # Los mismos enunciados se repiten entre alumnos: comprimir antes de cifrar
        # permite guardar una clase completa sin superar el límite de GitHub.
        return self.cipher.encrypt(gzip.compress(payload.encode("utf-8"), mtime=0))

    def decode(self, content: bytes) -> State:
        try:
            payload = self.cipher.decrypt(content)
            # Compatibilidad con los checkpoints anteriores de JSON sin comprimir.
            if payload.startswith(b"\x1f\x8b"):
                payload = gzip.decompress(payload)
            result = json.loads(payload.decode("utf-8"))
        except (InvalidToken, ValueError, UnicodeDecodeError, OSError, EOFError):
            raise StorageError(
                "No se puede descifrar el estado. No cambies la clave ni borres el archivo."
            ) from None
        return validate_state(result)


class LocalStore:
    def __init__(self, path: Path, key: str):
        self.path = path
        self.codec = EncryptedCodec(key)

    def load(self) -> State:
        try:
            return (
                self.codec.decode(self.path.read_bytes()) if self.path.exists() else empty_state()
            )
        except OSError:
            raise StorageError("No se puede leer el archivo de estado.") from None

    def save(self, state: State) -> None:
        content = self.codec.encode(state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp: str | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as output:
                temp = output.name
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, self.path)
        except OSError:
            raise StorageError("No se puede guardar el estado; se detienen los envíos.") from None
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)


class GitHubStore:
    """Checkpoint remoto antes/después de cada envío. SHA protege de escritores concurrentes."""

    def __init__(
        self,
        repository: str,
        token: str,
        key: str,
        branch: str = "bot-state",
        http: HTTPClient | None = None,
    ):
        self.root = f"https://api.github.com/repos/{repository}"
        self.branch = branch
        self.codec = EncryptedCodec(key)
        self.http = http or HTTPClient("GitHub", token)
        self.http.session.headers.update(
            {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        )
        self.sha: str | None = None
        self.last_plaintext: str | None = None

    def _data(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            return self.http.json(method, self.root + path, **kwargs)
        except APIError as exc:
            raise StorageError(f"No se pudo acceder al estado remoto: {exc}") from None

    def load(self) -> State:
        try:
            branch_response = self.http.request(
                "GET", self.root + f"/git/ref/heads/{quote(self.branch)}"
            )
            if branch_response.status_code == 404:
                repo = self._data("GET", "")
                default = quote(repo["default_branch"], safe="")
                head = self._data("GET", f"/git/ref/heads/{default}")
                self._data(
                    "POST",
                    "/git/refs",
                    json={"ref": f"refs/heads/{self.branch}", "sha": head["object"]["sha"]},
                )
            elif branch_response.status_code != 200:
                raise StorageError(f"GitHub: HTTP {branch_response.status_code}")
            response = self.http.request(
                "GET", self.root + "/contents/state.enc", params={"ref": self.branch}
            )
            if response.status_code == 404:
                state = empty_state()
            elif response.status_code == 200:
                raw = response.json()
                if raw.get("encoding") != "base64" or not raw.get("sha"):
                    raise StorageError("Formato de estado remoto inesperado.")
                self.sha = raw["sha"]
                state = self.codec.decode(base64.b64decode(raw["content"]))
            else:
                raise StorageError(f"GitHub: HTTP {response.status_code}")
        except (APIError, ValueError, KeyError, TypeError):
            raise StorageError("No se pudo cargar el estado remoto; no se sobrescribe.") from None
        self.last_plaintext = json.dumps(state, sort_keys=True)
        return state

    def save(self, state: State) -> None:
        plaintext = json.dumps(state, sort_keys=True)
        if plaintext == self.last_plaintext:
            return
        encrypted = self.codec.encode(state)
        if len(encrypted) > 900_000:
            raise StorageError(
                "El estado supera 900 KB. Exporta y archiva datos antes de continuar."
            )
        payload = {
            "message": "chore: checkpoint encrypted bot state [skip ci]",
            "content": base64.b64encode(encrypted).decode("ascii"),
            "branch": self.branch,
        }
        if self.sha:
            payload["sha"] = self.sha
        result = self._data("PUT", "/contents/state.enc", json=payload)
        try:
            self.sha = result["content"]["sha"]
        except (KeyError, TypeError):
            raise StorageError(
                "GitHub no confirmó el checkpoint; se detienen los envíos."
            ) from None
        self.last_plaintext = plaintext
