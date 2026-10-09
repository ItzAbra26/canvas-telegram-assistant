"""Estado cifrado compartido entre el webhook y el comprobador horario."""

import copy
import json
from typing import Any

from src.errors import APIError, StorageConflict, StorageError
from src.http_client import HTTPClient
from src.partitions import merge_parts, partition_state
from src.storage import EncryptedCodec, State

_MISSING = object()


def _merge(base: Any, local: Any, remote: Any) -> Any:
    def clone(value: Any) -> Any:
        return value if value is _MISSING else copy.deepcopy(value)

    if local == base:
        return clone(remote)
    if remote == base or local == remote:
        return clone(local)
    if all(isinstance(value, dict) for value in (base, local, remote)):
        merged = {}
        for key in base.keys() | local.keys() | remote.keys():
            value = _merge(
                base.get(key, _MISSING), local.get(key, _MISSING), remote.get(key, _MISSING)
            )
            if value is not _MISSING:
                merged[key] = value
        return merged
    raise StorageConflict("Cambios simultáneos en el mismo dato; hay que recargarlo.")


def _rebase(base: State, local: State, remote: State) -> State:
    # Account switches/disconnects invalidate snapshots and notifications for that user.
    touched = {
        uid
        for uid in base["users"].keys() | local["users"].keys()
        if base["users"].get(uid) != local["users"].get(uid)
    }
    for key in base["outbox"].keys() | local["outbox"].keys():
        old, wanted = base["outbox"].get(key), local["outbox"].get(key)
        if old != wanted:
            touched.add(str((wanted or old)["chat_id"]))
            # Even an identical sending status is not proof that WE claimed the send.
            if remote["outbox"].get(key) != old:
                raise StorageConflict("Otro proceso cambió el mismo envío; no se repite.")
    for uid in touched:
        old, current = base["users"].get(uid), remote["users"].get(uid)
        if old and (
            not current
            or any(
                old.get(field) != current.get(field)
                for field in ("token", "canvas_user_id", "connected_at")
            )
        ):
            raise StorageConflict("La conexión cambió; no se aplican datos de la cuenta anterior.")
    return _merge(base, local, remote)


def _replace_in_place(target: dict, source: dict) -> None:
    # Keep references held by the notifier to its claimed event and current user.
    for key in target.keys() - source.keys():
        del target[key]
    for key, value in source.items():
        if isinstance(target.get(key), dict) and isinstance(value, dict):
            _replace_in_place(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


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
                row = (
                    {"ciphertext": result["ciphertexts"].get(name)}
                    if isinstance(result.get("ciphertexts"), dict)
                    else self._call({"action": "read", "version": result["version"], "id": name})
                )
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
        if not self.partitioned or not self.last_plaintext:
            self._save_once(state)
            return
        base = json.loads(self.last_plaintext)
        desired = copy.deepcopy(state)
        for _ in range(5):
            try:
                self._save_once(desired)
            except StorageConflict:
                current = self.load()
                desired = _rebase(base, desired, current)
                base = current
                continue
            _replace_in_place(state, desired)
            return
        raise StorageConflict("Demasiadas actualizaciones simultáneas; hay que recargarlo.")

    def _save_once(self, state: State) -> None:
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
