"""Estado cifrado compartido entre el webhook y el comprobador horario."""

import json
from typing import Any

from src.errors import APIError, StorageConflict, StorageError
from src.http_client import HTTPClient
from src.partitions import merge_parts, partition_state
from src.storage import EncryptedCodec, State


class RemoteStore:
    def __init__(
        self, url: str, access_key: str, encryption_key: str, http: HTTPClient | None = None
    ):
        self.url = url
        self.codec = EncryptedCodec(encryption_key)
        self.http = http or HTTPClient("Estado", "")
        self.http.session.headers.pop("Authorization", None)
        self.http.session.headers["X-Canvas-State-Key"] = access_key
        self.version: int | None = None
        self.last_plaintext: str | None = None
        self.partitioned = False
        self.parts_plaintext: dict[str, str] = {}

    def _call(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self.http.json("POST", self.url, json=body)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except APIError as exc:
            if exc.status == 409:
                raise StorageConflict(
                    "El webhook actualizó el estado; hay que recargarlo."
                ) from None
            raise StorageError(
                "No se pudo guardar/leer el estado compartido; se detienen los envíos."
            ) from None
        except ValueError:
            raise StorageError(
                "No se pudo guardar/leer el estado compartido; se detienen los envíos."
            ) from None

    def load(self) -> State:
        result = self._call({"action": "load"})
        if result.get("format") == "partitioned":
            if type(result.get("version")) is not int or not isinstance(result.get("parts"), list):
                raise StorageError("Índice de particiones incompleto.")
            parts = {}
            for name in result["parts"]:
                if not isinstance(name, str) or name.endswith(":view"):
                    raise StorageError("Índice de particiones inválido.")
                row = self._call({"action": "read", "version": result["version"], "id": name})
                if not isinstance(row.get("ciphertext"), str):
                    raise StorageError("Partición remota incompleta.")
                parts[name] = self.codec.decode(row["ciphertext"].encode("ascii"))
            state = merge_parts(parts)
            self.partitioned = True
            self.version = result["version"]
            self.last_plaintext = json.dumps(state, sort_keys=True)
            self.parts_plaintext = {
                name: json.dumps(part, sort_keys=True)
                for name, part in partition_state(state).items()
            }
            return state
        if type(result.get("version")) is not int or not isinstance(result.get("ciphertext"), str):
            raise StorageError("Respuesta incompleta del estado compartido; no se sobrescribe.")
        state = self.codec.decode(result["ciphertext"].encode("ascii"))
        self.version = result["version"]
        self.last_plaintext = json.dumps(state, sort_keys=True)
        return state

    def save(self, state: State) -> None:
        plaintext = json.dumps(state, sort_keys=True)
        if plaintext == self.last_plaintext:
            return
        if self.version is None:
            raise StorageError("Carga el estado compartido antes de modificarlo.")
        if self.partitioned:
            parts = partition_state(state)
            plaintext_parts = {
                name: json.dumps(part, sort_keys=True) for name, part in parts.items()
            }
            changed = {
                name: self.codec.encode(part).decode("ascii")
                for name, part in parts.items()
                if plaintext_parts[name] != self.parts_plaintext.get(name)
            }
            if any(len(value) > 1_800_000 for value in changed.values()):
                raise StorageError("Partición demasiado grande; se conserva el estado anterior.")
            result = self._call(
                {
                    "action": "save",
                    "version": self.version,
                    "parts": changed,
                    "deleted": sorted(set(self.parts_plaintext) - set(parts)),
                }
            )
            if result.get("version") != self.version + 1:
                raise StorageError("No se confirmó el checkpoint; se detienen los envíos.")
            self.version = result["version"]
            self.parts_plaintext = plaintext_parts
            self.last_plaintext = plaintext
            return
        ciphertext = self.codec.encode(state).decode("ascii")
        if len(ciphertext) > 10_000_000:
            raise StorageError(
                "Estado compartido demasiado grande; se conserva el checkpoint anterior."
            )
        result = self._call({"action": "save", "version": self.version, "ciphertext": ciphertext})
        if result.get("version") != self.version + 1:
            raise StorageError("No se confirmó la versión del checkpoint; se detienen los envíos.")
        self.version = result["version"]
        self.last_plaintext = plaintext
