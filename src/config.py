import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from dotenv import load_dotenv

from src.errors import BotError


@dataclass(frozen=True)
class Config:
    canvas_base_url: str
    telegram_token: str = field(repr=False)
    encryption_key: str = field(repr=False)
    test_mode: bool = True
    backend: str = "local"
    state_file: Path = Path("data/state.enc")
    timezone: str = "Europe/Madrid"
    allowed_users: frozenset[int] = frozenset()
    invite_code: str = field(default="", repr=False)
    max_users: int = 15
    recent_days: int = 7
    initial_notifications: bool = False
    github_token: str = field(default="", repr=False)
    github_repository: str = ""
    state_branch: str = "bot-state"
    canvas_token: str = field(default="", repr=False)
    personal_chat_id: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv(override=False)

        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise BotError(f"Falta {name}. Consulta .env.example / README.")
            return value

        def boolean(name: str, default: str) -> bool:
            value = os.getenv(name, default).lower()
            if value not in {"true", "false"}:
                raise BotError(f"{name} debe ser true o false.")
            return value == "true"

        base = required("CANVAS_BASE_URL").rstrip("/")
        url = urlsplit(base)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path
        ):
            raise BotError("CANVAS_BASE_URL debe ser un dominio HTTPS sin rutas ni credenciales.")
        key = required("STATE_ENCRYPTION_KEY")
        try:
            Fernet(key.encode())
            ZoneInfo(os.getenv("TIMEZONE", "Europe/Madrid"))
            allowed = frozenset(
                int(x) for x in os.getenv("ALLOWED_TELEGRAM_USER_IDS", "").split(",") if x.strip()
            )
            max_users = int(os.getenv("MAX_USERS", "15"))
            recent = int(os.getenv("RECENT_DAYS", "7"))
        except (ValueError, KeyError) as exc:
            raise BotError("Clave, zona horaria o límites de configuración incorrectos.") from exc
        if not 1 <= max_users <= 100 or not 1 <= recent <= 365:
            raise BotError("MAX_USERS: 1–100; RECENT_DAYS: 1–365.")
        backend = os.getenv("STORAGE_BACKEND", "local")
        if backend not in {"local", "github"}:
            raise BotError("STORAGE_BACKEND debe ser local o github.")
        repo = os.getenv("GITHUB_REPOSITORY", "")
        gh_token = os.getenv("GITHUB_TOKEN", "")
        branch = os.getenv("STATE_BRANCH", "bot-state")
        if backend == "github" and (not gh_token or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo)):
            raise BotError("El almacenamiento GitHub requiere GITHUB_TOKEN y GITHUB_REPOSITORY.")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", branch):
            raise BotError("STATE_BRANCH contiene caracteres no permitidos.")
        return cls(
            canvas_base_url=base,
            telegram_token=required("TELEGRAM_BOT_TOKEN"),
            encryption_key=key,
            test_mode=boolean("TEST_MODE", "true"),
            backend=backend,
            state_file=Path(os.getenv("STATE_FILE", "data/state.enc")),
            timezone=os.getenv("TIMEZONE", "Europe/Madrid"),
            allowed_users=allowed,
            invite_code=os.getenv("CLASS_INVITE_CODE", ""),
            max_users=max_users,
            recent_days=recent,
            initial_notifications=boolean("INITIAL_NOTIFICATIONS", "false"),
            github_token=gh_token,
            github_repository=repo,
            state_branch=branch,
            canvas_token=os.getenv("CANVAS_TOKEN", ""),
            personal_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        )
