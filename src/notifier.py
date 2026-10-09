import hashlib
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from src.config import Config
from src.errors import AmbiguousDelivery, RejectedDelivery
from src.models import Assignment, Snapshot, ignored_assignment
from src.storage import State, Store
from src.telegram_bot import TelegramBot
from src.utils import date_label, e, pack_messages, parse_date, safe_link, time_left

logger = logging.getLogger(__name__)
THRESHOLDS = (("7d", 7 * 86400), ("3d", 3 * 86400), ("24h", 86400), ("3h", 3 * 3600))


def enqueue(state: State, event_id: str, chat_id: int, text: str, **metadata: Any) -> None:
    if event_id not in state["outbox"]:
        state["outbox"][event_id] = {
            "status": "pending",
            "chat_id": chat_id,
            "text": text,
            **metadata,
        }


def queue_pages(state: State, prefix: str, chat_id: int, blocks: list[str]) -> None:
    for index, text in enumerate(pack_messages(blocks)):
        enqueue(state, f"{prefix}:{index}", chat_id, text)


def task_block(a: Assignment, config: Config, now: datetime, description: bool = False) -> str:
    status = (
        "✅ Entregada"
        if a.submitted
        else "⚪ Exenta"
        if a.excused
        else "ℹ️ Sin entrega requerida"
        if not a.requires_submission
        else "🔴 Perdida"
        if a.overdue(now)
        else "🟠 Pendiente"
    )
    result = (
        f"📚 <b>{e(a.course_name, 120)}</b>\n📝 {e(a.name, 180)}\n"
        f"📅 {date_label(a.due_at, config.timezone)}\n⏳ {time_left(a.due_at, now)}\n{status}"
    )
    if description:
        result += f"\n💯 Puntos: {a.points if a.points is not None else 'No indicados'}\n{e(a.description, 400)}"
    link = safe_link(a.url, config.canvas_base_url)
    return result + (f"\n{link}" if link else "")


def reconcile(state: State, uid: str, snapshot: Snapshot, config: Config, now: datetime) -> None:
    user = state["users"][uid]
    baseline = not user.get("initialized", False)
    previous = user.setdefault("tasks", {})
    excluded = {
        **snapshot.ignored,
        **{a.key: a.name for a in snapshot.assignments if ignored_assignment(a.name)},
    }
    for key, name in excluded.items():
        if key in previous:
            previous[key]["ignored"] = True
            previous[key]["data"]["name"] = name
    current = {a.key: a for a in snapshot.assignments if not ignored_assignment(a.name)}
    for key, a in current.items():
        old = previous.get(key)
        if old:
            old.pop("ignored", None)
        remaining = (parse_date(a.due_at) - now).total_seconds() if a.due_at else None
        if old is None or old.pop("manual_unnotified", False):
            record = {
                "data": a.to_dict(),
                "revision": 0,
                "first_seen_at": now.isoformat(),
                "reminders": {},
                "remaining": remaining,
                "deleted": False,
                "missing_count": 0,
            }
            previous[key] = record
            if not baseline or config.initial_notifications:
                enqueue(
                    state,
                    f"u{uid}:task:{key}:new",
                    int(uid),
                    "🚨 <b>NUEVA TAREA</b>\n\n" + task_block(a, config, now, True),
                )
            # La primera alerta ya contiene el plazo. No enviar 4 recordatorios a la vez.
            for name, seconds in THRESHOLDS:
                if remaining is not None and remaining <= seconds:
                    record["reminders"][name] = "skipped_initial"
            continue
        old_data = Assignment(**old.pop("hourly_data", old["data"]))
        changes = []
        due_changed = old_data.due_at != a.due_at
        if due_changed:
            changes.append(
                f"📅 Fecha anterior:\n{date_label(old_data.due_at, config.timezone)}\n\nNueva fecha:\n{date_label(a.due_at, config.timezone)}"
            )
        if old_data.description != a.description:
            changes.append(f"📄 Descripción actualizada:\n{e(a.description, 450)}")
        if old_data.points != a.points:
            changes.append(f"💯 Puntuación: {old_data.points} → {a.points}")
        if old_data.name != a.name:
            changes.append(f"📝 Nombre actualizado: {e(a.name, 180)}")
        if not old_data.submitted and a.submitted:
            changes.append("✅ Canvas indica que has entregado la tarea.")
        elif old_data.submitted and not a.submitted:
            changes.append("🟠 Canvas vuelve a indicar que no hay entrega registrada.")
        if old_data.excused != a.excused:
            changes.append("⚪ Tarea exenta." if a.excused else "🟠 La tarea ya no está exenta.")
        if old.get("deleted"):
            changes.append("👀 La tarea vuelve a estar disponible.")
        if changes:
            old["revision"] += 1
            queue_pages(
                state,
                f"u{uid}:task:{key}:change:{old['revision']}",
                int(uid),
                [
                    f"⚠️ <b>CAMBIO EN UNA TAREA</b>\n\n📝 {e(a.name, 180)}\n📚 {e(a.course_name, 120)}",
                    *changes,
                    safe_link(a.url, config.canvas_base_url)
                    or "Consulta Canvas para más detalles.",
                ],
            )
        if due_changed:
            old["reminders"] = {}
            # Fecha cambiada: la alerta de cambio cubre los umbrales ya vencidos.
            for name, seconds in THRESHOLDS:
                if remaining is not None and remaining <= seconds:
                    old["reminders"][name] = "skipped_changed"
        elif a.pending and remaining is not None and remaining > 0:
            prev_remaining = old.get("remaining")
            crossed = [
                (name, seconds)
                for name, seconds in THRESHOLDS
                if name not in old["reminders"]
                and prev_remaining is not None
                and prev_remaining > seconds >= remaining
            ]
            if crossed:
                # Si hubo una caída prolongada, enviar solo el recordatorio más urgente.
                chosen = min(crossed, key=lambda item: item[1])[0]
                for name, _ in crossed:
                    old["reminders"][name] = "queued" if name == chosen else "skipped_outage"
                enqueue(
                    state,
                    f"u{uid}:task:{key}:reminder:{old['revision']}:{chosen}",
                    int(uid),
                    f"⏰ <b>RECORDATORIO · {chosen}</b>\n\n" + task_block(a, config, now),
                    kind="reminder",
                    uid=uid,
                    task_key=key,
                    due_at=a.due_at,
                )
        old.update(data=a.to_dict(), remaining=remaining, deleted=False, missing_count=0)
    for key in snapshot.missing:
        record = previous[key]
        if record.get("ignored") or ignored_assignment(record["data"]["name"]):
            continue
        record["missing_count"] = record.get("missing_count", 0) + 1
        if record["missing_count"] >= 2 and not record.get("deleted"):
            record["deleted"] = True
            record["revision"] += 1
            old_task = Assignment(**record["data"])
            enqueue(
                state,
                f"u{uid}:task:{key}:removed:{record['revision']}",
                int(uid),
                "🗑️ <b>TAREA ELIMINADA O YA NO VISIBLE</b>\n\n"
                f"📝 {e(old_task.name, 180)}\n📚 {e(old_task.course_name, 120)}\n"
                "Canvas devolvió 404 en dos revisiones completas. Puede ser eliminación o pérdida de visibilidad.",
            )
    user.update(
        courses=[{"id": c.id, "name": c.name} for c in snapshot.courses],
        initialized=True,
        last_sync=now.isoformat(),
        sync_error=None,
    )
    if baseline and not config.initial_notifications:
        count = sum(a.pending_at(now) for a in snapshot.assignments)
        lost = sum(a.overdue(now) for a in snapshot.assignments)
        enqueue(
            state,
            f"u{uid}:baseline",
            int(uid),
            f"📚 <b>Primera sincronización completada</b>\n{len(snapshot.courses)} asignaturas · {count} tareas pendientes · {lost} perdidas.\n"
            "He guardado las tareas existentes. Desde ahora avisaré de las nuevas y sus cambios. Usa /resumen.",
        )


def recover_inflight(state: State, now: datetime | None = None) -> None:
    if now:
        for uid, user in state["users"].items():
            delivery = user.get("delivery", {})
            started = parse_date(delivery.get("started_at"))
            if (
                delivery.get("stage") == "processing"
                and started
                and (now - started).total_seconds() >= 120
            ):
                delivery["stage"] = "uncertain"
                delivery.pop("file", None)
                enqueue(
                    state,
                    f"u{uid}:delivery:{delivery['nonce']}:recovery",
                    int(uid),
                    "⚠️ No se confirmó el resultado de tu entrega. No la reenviaré automáticamente. "
                    "Comprueba la tarea en Canvas antes de reintentar.",
                )
    for event in state["outbox"].values():
        if event["status"] == "sending":
            started = parse_date(event.get("started_at"))
            if now and started and (now - started).total_seconds() < 120:
                continue  # El webhook puede seguir terminando un envío activo.
            event["status"] = "uncertain"
            event.pop("text", None)


def dispatch(
    state: State, store: Store, telegram: TelegramBot, now: datetime, max_messages: int = 80
) -> bool:
    """Devuelve False si Telegram necesita revisión/reintento. Persiste ANTES de enviar."""
    healthy = True
    sent = 0
    for event_id, event in list(state["outbox"].items()):
        if event["status"] != "pending" or sent >= max_messages:
            continue
        not_before = parse_date(event.get("not_before"))
        if not_before and now < not_before:
            continue
        task_event = re.match(r"^u(\d+):task:(\d+:\d+):", event_id)
        if task_event:
            record = state["users"].get(task_event[1], {}).get("tasks", {}).get(task_event[2])
            if record and (record.get("ignored") or ignored_assignment(record["data"]["name"])):
                event["status"] = "cancelled"
                event.pop("text", None)
                store.save(state)
                continue
        if event.get("kind") == "reminder":
            record = state["users"].get(event["uid"], {}).get("tasks", {}).get(event["task_key"])
            active_ids = {c["id"] for c in state["users"].get(event["uid"], {}).get("courses", [])}
            if (
                not record
                or record.get("deleted")
                or record["data"]["course_id"] not in active_ids
                or record["data"]["due_at"] != event["due_at"]
                or not Assignment(**record["data"]).pending
                or parse_date(event["due_at"]) <= now
            ):
                event["status"] = "cancelled"
                event.pop("text", None)
                store.save(state)
                continue
        event["status"] = "sending"
        event["started_at"] = now.isoformat()
        store.save(state)  # Si falla, NO se hace la llamada a Telegram.
        try:
            message_id = (
                telegram.send_message(event["chat_id"], event["text"], event["reply_markup"])
                if event.get("reply_markup")
                else telegram.send_message(event["chat_id"], event["text"])
            )
        except RejectedDelivery as exc:
            healthy = False
            event["status"] = "pending"
            event["not_before"] = (now + timedelta(seconds=max(60, exc.retry_after))).isoformat()
            if exc.status in {400, 403}:
                event["status"] = "cancelled"
                event.pop("text", None)
                logger.warning(
                    "Telegram rechazó un mensaje; revisar chat/permisos (HTTP %s).", exc.status
                )
            store.save(state)
            if exc.status not in {400, 403}:
                break
        except AmbiguousDelivery:
            healthy = False
            event["status"] = "uncertain"
            event.pop("text", None)
            store.save(state)
            logger.error(
                "Envío incierto %s: no se reenviará automáticamente.",
                hashlib.sha256(event_id.encode()).hexdigest()[:12],
            )
            break
        else:
            event.update(status="sent", message_id=message_id, sent_at=now.isoformat())
            event.pop("text", None)
            store.save(state)
            sent += 1
    return healthy
