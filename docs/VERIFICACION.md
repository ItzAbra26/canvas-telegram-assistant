# Verificación de esta entrega

Comprobación realizada el 8 de octubre de 2026 en la carpeta del proyecto, con Python 3.14.2 en Windows. El workflow configura Python 3.12; no se ha ejecutado en GitHub al no haberse creado el repositorio ni configurado sus Secrets.

| Comprobación | Resultado |
| --- | --- |
| Instalación de dependencias del bot y desarrollo | Correcta |
| `python -m pytest` | 103 pruebas correctas |
| Cobertura de líneas medida por pytest-cov | 90 % |
| `python -m ruff check .` | Correcto |
| `python -m ruff format --check .` | Correcto |
| `python -m pip check` | Sin incompatibilidades declaradas |
| Compilación/imports y arranque de la entrada | Correctos, incluidos en tests |
| `python -m src.main --demo` desde PowerShell | Correcto; sin red ni credenciales reales |
| Workflows YAML y expresiones de Actions | Correctos con actionlint 1.7.12 |
| Paquete de actionlint descargado del repositorio oficial | SHA-256 comprobado contra su archivo de checksums |

Las pruebas cubren el registro privado con token, dos alumnos con estados de entrega distintos, renovación y baja, rechazo de grupos/reenviados, invitación/lista de clase, fechas de Madrid y horario de verano, paginación, HTML, respuestas incompletas, fallback de entregas, cambios, nuevas tareas, los cuatro umbrales, borrados confirmados, persistencia cifrada, conflictos de escritura, rate limits e interrupciones durante envíos.

La prueba de arranque real sustituye los clientes externos por simuladores. No se ha validado todavía una cuenta real de MEDAC ni un bot real de Telegram porque sus credenciales no se han proporcionado. La autorización institucional para OAuth y el cumplimiento de las condiciones de la práctica tampoco se verifican mediante estos tests.

Para repetir la comprobación, utiliza los comandos del README. Para activar la prueba real, crea el bot con BotFather, sube el código a GitHub y configura `CANVAS_BASE_URL`, `TELEGRAM_BOT_TOKEN` y `STATE_ENCRYPTION_KEY`. Después cada alumno se registra en su chat privado.
