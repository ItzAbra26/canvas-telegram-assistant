# Verificación de esta entrega

Comprobación realizada el 8 de octubre de 2026, con Python 3.14.2 en Windows y Python 3.12 en GitHub Actions. Repositorio desplegado: [ItzAbra26/canvas-telegram-assistant](https://github.com/ItzAbra26/canvas-telegram-assistant). Bot conectado: [SuperDelegado2B](https://t.me/SuperDelegado2B_bot).

| Comprobación | Resultado |
| --- | --- |
| Instalación de dependencias del bot y desarrollo | Correcta |
| `python -m pytest` | 108 pruebas correctas |
| Cobertura de líneas medida por pytest-cov | 90 % |
| `python -m ruff check .` | Correcto |
| `python -m ruff format --check .` | Correcto |
| `python -m pip check` | Sin incompatibilidades declaradas |
| Compilación/imports y arranque de la entrada | Correctos, incluidos en tests |
| `python -m src.main --demo` desde PowerShell | Correcto; sin red ni credenciales reales |
| Workflows YAML y expresiones de Actions | Correctos con actionlint 1.7.12 |
| Paquete de actionlint descargado del repositorio oficial | SHA-256 comprobado contra su archivo de checksums |
| Tests, lint, formato y demo en GitHub con Python 3.12 | [Ejecución correcta](https://github.com/ItzAbra26/canvas-telegram-assistant/actions/runs/37756269189) |
| Arranque real, autenticación Telegram y menú de comandos | [Ejecución correcta](https://github.com/ItzAbra26/canvas-telegram-assistant/actions/runs/37756269567) |
| Persistencia real de GitHub | Rama `bot-state`, archivo `state.enc`; descifrado comprobado sin imprimir datos |
| Secrets obligatorios | Los tres configurados y sus nombres comprobados; sin credenciales en el código |

Las pruebas cubren el registro privado con token, 15 alumnos con cuentas y entregas independientes, rechazo del alumno 16, renovación y baja, rechazo de grupos/reenviados, invitación/lista de clase, fechas de Madrid y horario de verano, paginación, HTML, respuestas incompletas, fallback de entregas, cambios, nuevas tareas, los cuatro umbrales, borrados confirmados, persistencia cifrada, conflictos de escritura, rate limits e interrupciones durante envíos.

Los tests sustituyen los clientes externos por simuladores. Además se han ejecutado dos revisiones reales desde GitHub: Telegram ha validado el token y devuelto el nombre público del bot mediante `getMe`; el menú de comandos está registrado y el estado cifrado se ha leído y guardado correctamente entre ejecuciones. La API real de MEDAC responde HTTP 401 sin credenciales, como corresponde. La red local interrumpe la conexión con api.telegram.org, pero el runner de GitHub sí conecta correctamente.

Todavía no se ha registrado ni consultado una cuenta real de MEDAC: cada alumno debe completar `/start` y aportar su propio token. Las entregas y los avisos se han comprobado con datos simulados, incluidos 15 usuarios con estados independientes. La autorización institucional para OAuth y el cumplimiento de las condiciones de la práctica no se verifican mediante estos tests.

Para repetir la comprobación, utiliza los comandos del README. La instalación de esta entrega ya tiene `CANVAS_BASE_URL`, `TELEGRAM_BOT_TOKEN` y `STATE_ENCRYPTION_KEY` configurados en GitHub Secrets. El único paso que requiere cada alumno es abrir el bot y registrarse en su chat privado. El calendario configurado revisa aproximadamente cada 15 minutos; GitHub puede retrasar las ejecuciones.
