"""Configura Cloudflare sin mostrar credenciales. Datos generados en data/ (ignorado)."""

import argparse
import gzip
import hashlib
import json
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

import requests
from cryptography.fernet import Fernet
from dotenv import dotenv_values, set_key

# Permite python scripts/cloud_admin.py desde la raíz del repositorio.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import Config  # noqa: E402
from src.errors import BotError  # noqa: E402
from src.partitions import partition_state  # noqa: E402
from src.remote_store import RemoteStore  # noqa: E402
from src.storage import EncryptedCodec, empty_state  # noqa: E402


def bootstrap(
    url: str, output: Path, state_file: Path | None, resolved_ip: bool, runtime_only: bool = False
) -> None:
    config = Config.from_env()
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise BotError("La URL del Worker debe ser HTTPS, sin credenciales ni parámetros.")
    if not Path(".env").is_file():
        raise BotError("Crea .env siguiendo .env.example antes de configurar Cloudflare.")
    values = dotenv_values(".env")
    for name in ("STATE_API_KEY", "TELEGRAM_WEBHOOK_SECRET"):
        if not values.get(name):
            set_key(".env", name, Fernet.generate_key().decode().rstrip("="))
    set_key(".env", "STATE_API_URL", url)
    values = dotenv_values(".env")
    runtime = {
        "telegram_token": config.telegram_token,
        "encryption_key": config.encryption_key,
        "canvas_base_url": config.canvas_base_url,
        "timezone": config.timezone,
        "test_mode": config.test_mode,
        "max_users": config.max_users,
        "recent_days": config.recent_days,
        "allowed_users": sorted(config.allowed_users),
        "invite_code": config.invite_code,
        "webhook_url": url,
        "webhook_secret": values["TELEGRAM_WEBHOOK_SECRET"],
    }
    if resolved_ip:
        runtime["webhook_ip"] = socket.gethostbyname(parsed.hostname)
    codec = EncryptedCodec(config.encryption_key)
    state = codec.decode(state_file.read_bytes()) if state_file else empty_state()
    if runtime_only:
        runtime["polling_cutoff"] = int(values.get("TELEGRAM_POLLING_CUTOFF") or "0")
    else:
        runtime["polling_cutoff"] = state["offset"]
        set_key(".env", "TELEGRAM_POLLING_CUTOFF", str(state["offset"]))
    fields = []
    for name in ("TELEGRAM_WEBHOOK_SECRET", "STATE_API_KEY"):
        secret = values[name]
        fields.extend(
            (
                hashlib.sha256(secret.encode()).hexdigest(),
                Fernet((secret + "=").encode())
                .encrypt(gzip.compress(json.dumps(runtime).encode(), mtime=0))
                .decode(),
            )
        )
    sql = (
        "INSERT INTO bot_configuration (id,webhook_hash,webhook_config,state_hash,state_config) VALUES (1,'"  # noqa: S608 -- Solo hashes y ciphertext base64 generados internamente.
        + "','".join(fields)
        + "');\n"
    )
    if runtime_only:
        sql = (
            "UPDATE bot_configuration SET webhook_hash='"  # noqa: S608 -- Solo hashes y ciphertext base64 generados internamente.
            + fields[0]
            + "',webhook_config='"
            + fields[1]
            + "',state_hash='"
            + fields[2]
            + "',state_config='"
            + fields[3]
            + "' WHERE id=1;"
        )
    for name, part in ({} if runtime_only else partition_state(state)).items():
        sql += (
            "INSERT INTO bot_parts (id,ciphertext) VALUES ('"  # noqa: S608 -- ID SHA-256 y ciphertext base64; nunca texto de entrada.
            + name
            + "','"
            + codec.encode(part).decode()
            + "');\n"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(sql, encoding="utf-8")
    print(f"Configuración y estado cifrados preparados en {output}. No publiques ese archivo.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Administración del webhook de Canvas")
    parser.add_argument(
        "action", choices=["bootstrap", "configure", "setup", "status", "probe", "backup"]
    )
    parser.add_argument("--url")
    parser.add_argument("--output", type=Path, default=Path("data/cloudflare-bootstrap.sql"))
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--use-resolved-ip", action="store_true")
    args = parser.parse_args()
    try:
        if args.action in {"bootstrap", "configure"}:
            if args.action == "configure" and not args.url:
                args.url = dotenv_values(".env").get("STATE_API_URL")
            if not args.url:
                raise BotError("bootstrap requiere --url con la dirección HTTPS del Worker.")
            bootstrap(
                args.url,
                args.output,
                args.state_file,
                args.use_resolved_ip,
                args.action == "configure",
            )
        else:
            config = Config.from_env()
            if not config.state_api_url or not config.state_api_key:
                raise BotError("Faltan STATE_API_URL o STATE_API_KEY en .env.")
            if args.action == "backup":
                state = RemoteStore(
                    config.state_api_url, config.state_api_key, config.encryption_key
                ).load()
                target = Path("data/cloud-backup.enc")
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(EncryptedCodec(config.encryption_key).encode(state))
                print(f"Copia cifrada guardada en {target}.")
            else:
                response = requests.post(
                    config.state_api_url,
                    headers={"X-Canvas-State-Key": config.state_api_key},
                    json={"action": args.action},
                    timeout=45,
                    allow_redirects=False,
                )
                if not response.ok:
                    raise BotError(
                        f"Servicio remoto: HTTP {response.status_code}. Se conserva el estado."
                    )
                print(json.dumps(response.json(), ensure_ascii=False))
        return 0
    except (BotError, requests.RequestException, OSError, ValueError):
        print(
            "No se completó la operación. Comprueba configuración y disponibilidad; el estado remoto no se reinicia.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
