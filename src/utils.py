from datetime import UTC, datetime
from html import escape
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

WEEKDAYS = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Fecha sin zona horaria")
    return result.astimezone(UTC)


def clean_html(value: str | None) -> str:
    soup = BeautifulSoup(value or "", "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def e(value: object, limit: int = 240) -> str:
    # Limitar DESPUÉS de escapar sin cortar entidades (&amp; etc.).
    parts = []
    size = 0
    text = str(value)
    for index, char in enumerate(text):
        encoded = escape(char)
        if size + len(encoded) > limit - 1:
            return "".join(parts) + "…"
        parts.append(encoded)
        size += len(encoded)
        if index >= limit:
            break
    return "".join(parts)


def date_label(value: str | None, timezone: str = "Europe/Madrid") -> str:
    date = parse_date(value)
    if date is None:
        return "Sin fecha límite"
    date = date.astimezone(ZoneInfo(timezone))
    return f"{WEEKDAYS[date.weekday()]} {date.day} de {MONTHS[date.month - 1]} de {date.year} · {date:%H:%M}"


def time_left(value: str | None, now: datetime) -> str:
    due = parse_date(value)
    if not due:
        return "Sin fecha límite"
    seconds = int((due - now).total_seconds())
    if seconds <= 0:
        return "Plazo vencido"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    if days:
        return f"Quedan {days} días y {hours} horas"
    if hours:
        return f"Quedan {hours} horas y {rest // 60} minutos"
    return f"Quedan {max(1, rest // 60)} minutos"


def safe_link(url: str, base_url: str) -> str:
    if not isinstance(url, str) or len(url) > 1000:
        return ""
    parsed = urlsplit(url)
    base = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != base.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return ""
    return f'<a href="{escape(url, quote=True)}">🔗 Abrir en Canvas</a>'


def pack_messages(blocks: list[str], limit: int = 3500) -> list[str]:
    """Agrupa bloques completos; nunca corta una etiqueta HTML."""
    pages: list[str] = []
    current = ""
    for block in blocks:
        if len(block) > limit:
            raise ValueError("Bloque demasiado largo")
        joined = f"{current}\n\n{block}" if current else block
        if len(joined) > limit:
            pages.append(current)
            current = block
        else:
            current = joined
    if current:
        pages.append(current)
    return pages
