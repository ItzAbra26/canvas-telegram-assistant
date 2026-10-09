from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from src.config import Config
from src.models import Assignment
from src.notifier import task_block
from src.telegram_bot import COMMANDS
from src.utils import date_label, e, parse_date

PRIVACY = (
    "🔐 <b>Privacidad · modo de prueba</b>\n\n"
    "Enviar tu token autoriza a esta práctica a consultar tus cursos, tareas y estado de entrega "
    "y enviarlos a este chat privado. El bot en la nube también permite subir un archivo y "
    "registrar una entrega, solo después de que selecciones la tarea y confirmes expresamente. "
    "El archivo pasa por Telegram y Canvas; el bot conserva sus metadatos cifrados, no su contenido. "
    "Un token puede conceder más permisos que los que utiliza el bot.\n\n"
    "El bot intentará borrar el mensaje con el token y guardará credenciales y datos cifrados. "
    "El administrador que dispone de la clave puede descifrarlos. Telegram procesa los mensajes; "
    "borrarlos no garantiza que nunca hayan existido copias.\n\n"
    "/desconectar elimina tu conexión y datos del estado activo. Las versiones cifradas anteriores "
    "pueden seguir en las copias de Cloudflare y, si se utilizó, en el historial de GitHub. "
    "Revoca también el token en Canvas para anular su acceso.\n\n"
    "Este registro manual es un modo de prueba docente. Para una aplicación multiusuario "
    "de uso habitual Canvas exige OAuth."
)


def start_text(config: Config) -> str:
    return (
        "👋 <b>Canvas Assistant · práctica de clase</b>\n\n"
        "Conecta tu propia cuenta para recibir solo tus tareas y avisos.\n\n"
        f"1️⃣ Abre el Canvas del centro: {e(config.canvas_base_url)}\n"
        "2️⃣ Entra en <b>Cuenta → Configuración → Integraciones aprobadas</b>.\n"
        "3️⃣ Pulsa <b>Nuevo token de acceso</b>, pon un nombre para esta práctica y una caducidad. "
        "Si no aparece, consulta al profesor o administrador.\n"
        "4️⃣ Copia el token y envíalo aquí, <b>solo el token y en un único mensaje privado</b>.\n\n"
        "Al enviarlo autorizas la consulta de tus cursos, tareas y entregas para esta prueba. "
        "Intentaré borrar ese mensaje y guardaré el token cifrado. El administrador del bot puede "
        "acceder a los datos con la clave; consulta /privacidad.\n\n"
        "🔒 No envíes tu contraseña ni publiques el token en un grupo.\n"
        "🧪 Los tokens manuales son para esta prueba; el uso multiusuario habitual requiere OAuth.\n"
        + (
            "⚡ Los comandos responden con los últimos datos guardados. Canvas se revisa cada hora.\n\n"
            if config.delivery_mode == "webhook"
            else "⏱️ En GitHub Actions el registro y los comandos se procesan en la siguiente revisión "
            "(aproximadamente 15 minutos; puede haber retrasos).\n\n"
        )
        + "Envía tu token para continuar, o /cancelar para salir."
    )


def help_text() -> str:
    return "🤖 <b>Comandos</b>\n\n" + "\n".join(
        f"/{command} — {text}" for command, text in COMMANDS.items()
    )


def assignments_for(user: dict[str, Any]) -> list[Assignment]:
    active = {course["id"] for course in user.get("courses", [])}
    return [
        Assignment(**record["data"])
        for record in user.get("tasks", {}).values()
        if not record.get("deleted") and record["data"]["course_id"] in active
    ]


def sorted_tasks(tasks: list[Assignment]) -> list[Assignment]:
    return sorted(
        tasks,
        key=lambda a: (
            a.due_at is None,
            parse_date(a.due_at) or datetime.max.replace(tzinfo=ZoneInfo("UTC")),
            a.course_name.casefold(),
            a.name.casefold(),
        ),
    )


def render_command(command: str, user: dict[str, Any], config: Config, now: datetime) -> list[str]:
    if command == "ayuda":
        return [help_text()]
    if command == "privacidad":
        return [PRIVACY]
    if command == "estado":
        connection = (
            "Token caducado/revocado: vuelve a conectar con /start."
            if user.get("disabled")
            else "Cuenta conectada."
            if user.get("token")
            else "Sin cuenta conectada. Usa /start."
        )
        uncertain = user.get("uncertain_count", 0)
        return [
            f"🔌 <b>Estado</b>\n{connection}\nÚltima sincronización: {date_label(user.get('last_sync'), config.timezone)}\nEnvíos inciertos: {uncertain}. Si los hay, consulta al administrador."
        ]
    if not user.get("token"):
        return ["Primero conecta tu propia cuenta con /start."]
    tasks = assignments_for(user)
    pending = sorted_tasks([a for a in tasks if a.pending])
    blocks = []
    if user.get("last_sync"):
        blocks.append(f"🕒 Última revisión: {date_label(user['last_sync'], config.timezone)}")
    if user.get("sync_error") or user.get("disabled"):
        blocks.append(
            "⚠️ No he podido actualizar Canvas. Estos son los últimos datos guardados; el estado puede haber cambiado."
        )
    if not user.get("initialized"):
        return blocks + [
            "Todavía no hay una sincronización completa. Lo volveré a intentar en la siguiente revisión."
        ]
    if command == "resumen":
        urgent = sum(
            bool(a.due_at and parse_date(a.due_at) <= now + timedelta(hours=24)) for a in pending
        )
        week = sum(
            bool(
                a.due_at
                and now + timedelta(hours=24) < parse_date(a.due_at) <= now + timedelta(days=7)
            )
            for a in pending
        )
        later = sum(
            bool(a.due_at and parse_date(a.due_at) > now + timedelta(days=7)) for a in pending
        )
        undated = sum(a.due_at is None for a in pending)
        text = f"📚 <b>RESUMEN</b>\n\n🔴 Urgentes: {urgent}\n🟠 Esta semana: {week}\n🟢 Más adelante: {later}\n⚪ Sin fecha: {undated}\n\n<b>Total pendientes: {len(pending)}</b>"
        upcoming = next((a for a in pending if a.due_at and parse_date(a.due_at) >= now), None)
        if upcoming:
            local_due = parse_date(upcoming.due_at).astimezone(ZoneInfo(config.timezone))
            delta = (local_due.date() - now.astimezone(ZoneInfo(config.timezone)).date()).days
            label = (
                "Hoy"
                if delta == 0
                else "Mañana"
                if delta == 1
                else date_label(upcoming.due_at, config.timezone)
            )
            when = f"{label} · {local_due:%H:%M}" if delta in {0, 1} else label
            text += f"\n\n<b>Próxima entrega:</b>\n{e(upcoming.course_name, 120)}\n{e(upcoming.name, 180)}\n{when}"
        elif pending:
            text += "\n\nNo hay próximas entregas con fecha futura. Revisa /atrasadas y las tareas sin fecha."
        else:
            text += "\n\n✅ No tienes tareas pendientes."
        return blocks + [text]
    if command == "asignaturas":
        blocks.append("📚 <b>ASIGNATURAS ACTIVAS</b>")
        for course in user.get("courses", []):
            count = sum(a.course_id == course["id"] for a in pending)
            blocks.append(f"📚 {e(course['name'], 180)}\n📝 Pendientes: {count}")
        if not user.get("courses"):
            blocks.append("No hay cursos activos visibles en Canvas.")
        return blocks
    local_today = now.astimezone(ZoneInfo(config.timezone)).date()
    if command in {"hoy", "manana", "semana"}:
        target = local_today + timedelta(days=1 if command == "manana" else 0)
        selected = []
        for a in tasks:
            due = parse_date(a.due_at)
            if due:
                date = due.astimezone(ZoneInfo(config.timezone)).date()
                if (command != "semana" and date == target) or (
                    command == "semana" and local_today <= date < local_today + timedelta(days=7)
                ):
                    selected.append(a)
        selected = sorted_tasks(selected)
    elif command == "pendientes":
        selected = pending
    elif command == "atrasadas":
        selected = [a for a in pending if a.overdue(now)]
    elif command == "ultimas":

        def recent_date(a: Assignment) -> datetime:
            first_seen = user["tasks"][a.key]["first_seen_at"]
            return max(parse_date(a.created_at) or parse_date(first_seen), parse_date(first_seen))

        selected = sorted(
            [a for a in tasks if recent_date(a) >= now - timedelta(days=config.recent_days)],
            key=recent_date,
            reverse=True,
        )
    else:
        return ["No reconozco ese comando. Consulta /ayuda."]
    title = {
        "hoy": "ENTREGAS DE HOY",
        "manana": "ENTREGAS DE MAÑANA",
        "semana": "PRÓXIMOS 7 DÍAS",
        "pendientes": "TAREAS PENDIENTES",
        "atrasadas": "TAREAS ATRASADAS",
        "ultimas": "TAREAS RECIENTES",
    }[command]
    blocks.append(f"📋 <b>{title}</b> · {len(selected)}")
    if command == "ultimas":
        blocks.append(
            "Canvas no expone una fecha exacta de publicación. Uso creación o primera detección."
        )
    for a in selected:
        blocks.append(task_block(a, config, now, description=command == "ultimas"))
    if not selected:
        blocks.append("No hay tareas en esta categoría.")
    return blocks
