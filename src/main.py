import argparse
import logging
import sys
import time

from cryptography.fernet import Fernet
from filelock import FileLock, Timeout

from src.canvas_client import CanvasClient
from src.config import Config
from src.errors import BotError, StorageConflict
from src.remote_store import RemoteStore
from src.service import BotService
from src.storage import GitHubStore, LocalStore
from src.telegram_bot import TelegramBot
from src.utils import utcnow


def main() -> int:
    # PowerShell puede iniciar Python con cp1252: la demo y los logs incluyen emojis.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Canvas Telegram Assistant · modo de prueba multiusuario"
    )
    parser.add_argument(
        "--generate-key",
        action="store_true",
        help="Genera una clave de cifrado; guárdala como secreto",
    )
    parser.add_argument(
        "--demo", action="store_true", help="Prueba completa simulada; no usa credenciales ni red"
    )
    parser.add_argument(
        "--once", action="store_true", help="Una revisión (predeterminado, para Actions)"
    )
    parser.add_argument(
        "--sync-only",
        action="store_true",
        help="Consulta Canvas y envía avisos; el webhook atiende los comandos",
    )
    parser.add_argument(
        "--listen", action="store_true", help="Modo continuo mientras esta máquina esté encendida"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Segundos entre revisiones en modo --listen (mínimo 15)",
    )
    parser.add_argument(
        "--remove-webhook",
        action="store_true",
        help="Quita un webhook anterior para permitir getUpdates",
    )
    parser.add_argument(
        "--import-personal",
        action="store_true",
        help="Importa CANVAS_TOKEN y TELEGRAM_CHAT_ID desde el entorno",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # Evitar que logs de librerías muestren la URL del Bot API, que contiene su token.
    logging.getLogger("urllib3").setLevel(logging.CRITICAL)
    if args.generate_key:
        print(Fernet.generate_key().decode())
        return 0
    if args.demo:
        from src.demo import run_demo

        return run_demo()
    try:
        config = Config.from_env()
        store = (
            RemoteStore(config.state_api_url, config.state_api_key, config.encryption_key)
            if config.backend == "remote"
            else GitHubStore(
                config.github_repository,
                config.github_token,
                config.encryption_key,
                config.state_branch,
            )
            if config.backend == "github"
            else LocalStore(config.state_file, config.encryption_key)
        )
        telegram = TelegramBot(config.telegram_token)
        if args.remove_webhook:
            telegram.remove_webhook()
            logging.info("Webhook eliminado; los mensajes pendientes se conservan.")
            return 0
        sync_only = args.sync_only or config.delivery_mode == "webhook"
        if telegram.webhook_info().get("url") and not sync_only:
            raise BotError("Hay un webhook activo. Para usar este bot ejecuta --remove-webhook.")
        logging.info("Bot verificado: https://t.me/%s", telegram.identity())
        config.state_file.parent.mkdir(parents=True, exist_ok=True)
        # Evita procesos locales simultáneos. GitHub añade concurrency y SHA remoto.
        with FileLock(str(config.state_file) + ".lock", timeout=0):
            if args.import_personal:
                if (
                    not config.test_mode
                    or not config.canvas_token
                    or not config.personal_chat_id.isdigit()
                ):
                    raise BotError(
                        "La importación requiere TEST_MODE=true, CANVAS_TOKEN y TELEGRAM_CHAT_ID privado positivo."
                    )
                uid = config.personal_chat_id
                if config.allowed_users and int(uid) not in config.allowed_users:
                    raise BotError("TELEGRAM_CHAT_ID no está en la lista de usuarios permitidos.")
                canvas_id = CanvasClient(config.canvas_base_url, config.canvas_token).profile()
                state = store.load()
                if uid not in state["users"] and len(state["users"]) >= config.max_users:
                    raise BotError("Límite de usuarios alcanzado.")
                if state["users"].get(uid, {}).get("canvas_user_id") not in {None, canvas_id}:
                    BotService._forget(state, uid)
                user = state["users"].setdefault(
                    uid, {"onboarding": False, "tasks": {}, "courses": []}
                )
                user.update(
                    token=config.canvas_token,
                    canvas_user_id=canvas_id,
                    disabled=False,
                    connected_at=utcnow().isoformat(),
                )
                store.save(state)
                logging.info("Cuenta personal importada y cifrada.")
            service = BotService(config, store, telegram)
            while True:
                try:
                    healthy = (
                        service.cycle(utcnow(), receive_updates=False)
                        if sync_only
                        else service.cycle(utcnow())
                    )
                except StorageConflict as exc:
                    logging.warning("%s", exc)
                    if not args.listen:
                        return 75
                    healthy = False
                except BotError as exc:
                    logging.error("%s", exc)
                    if not args.listen:
                        return 1
                    healthy = False
                if not args.listen:
                    return 0 if healthy else 1
                time.sleep(max(15, args.interval))
    except Timeout:
        logging.error("Ya hay otro proceso del bot activo en esta máquina.")
        return 1
    except BotError as exc:
        logging.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logging.info("Bot detenido.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
