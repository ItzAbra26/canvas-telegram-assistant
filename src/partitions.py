"""Particiones cifradas: los comandos solo leen la vista breve de su alumno."""

import copy
import hashlib

from src.errors import StorageError
from src.storage import State, empty_state, validate_state


def scope_id(uid: str) -> str:
    return hashlib.sha256(uid.encode()).hexdigest()


def partition_state(state: State) -> dict[str, State]:
    validate_state(state)
    meta = copy.deepcopy(state)
    meta["users"] = {uid: {"onboarding": False} for uid in state["users"]}
    meta["outbox"] = {}
    parts = {"meta": meta}
    uids = set(state["users"]) | {str(e["chat_id"]) for e in state["outbox"].values()}
    for uid in uids:
        prefix = scope_id(uid)
        control = empty_state()
        control["outbox"] = {
            key: copy.deepcopy(e) for key, e in state["outbox"].items() if str(e["chat_id"]) == uid
        }
        if uid in state["users"]:
            user = copy.deepcopy(state["users"][uid])
            tasks = user.pop("tasks", {})
            courses = user.pop("courses", [])
            control["users"][uid] = user
            full = empty_state()
            full["users"][uid] = {"onboarding": False, "tasks": tasks, "courses": courses}
            parts[prefix + ":full"] = full
            view = copy.deepcopy(full)
            for record in view["users"][uid]["tasks"].values():
                # Python conserva el enunciado completo en :full para detectar cambios.
                record["data"]["description"] = record["data"]["description"][:400]
            parts[prefix + ":view"] = view
        parts[prefix + ":control"] = control
    return parts


def merge_parts(parts: dict[str, State]) -> State:
    if "meta" not in parts:
        raise StorageError("Falta la partición principal; no se reinicia el estado.")
    state = copy.deepcopy(parts["meta"])
    registry = set(state["users"])
    state["users"] = {}
    state["outbox"] = {}
    for name, part in parts.items():
        if name.endswith(":control"):
            state["users"].update(copy.deepcopy(part["users"]))
            state["outbox"].update(copy.deepcopy(part["outbox"]))
    if set(state["users"]) != registry:
        raise StorageError("Particiones incompletas; se conserva el estado anterior.")
    for uid, user in state["users"].items():
        full = parts.get(scope_id(uid) + ":full")
        if not full or uid not in full["users"]:
            raise StorageError("Falta el historial de un alumno; no se sobrescribe.")
        user.update(copy.deepcopy(full["users"][uid]))
        user["onboarding"] = parts[scope_id(uid) + ":control"]["users"][uid]["onboarding"]
    return validate_state(state)
