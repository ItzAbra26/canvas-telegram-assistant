# Verificación de esta entrega

Comprobado el 9 de octubre de 2026. [Repositorio](https://github.com/ItzAbra26/canvas-telegram-assistant), [bot compartido](https://t.me/SuperDelegado2B_bot) y [servicio Cloudflare](https://canvas-telegram-assistant.canvas-telegram-immediate.workers.dev/).

| Comprobación | Resultado |
| --- | --- |
| Dependencias Python y Node.js | Instaladas y verificadas |
| Python local, 3.14.2 | 149 tests correctos; cobertura 90 % |
| Webhook, Node.js 24 | 55 tests correctos |
| Ruff: lint y formato | Correctos |
| pip check | Sin incompatibilidades |
| npm audit --audit-level=high | 0 vulnerabilidades |
| Demo sin red, imports y arranque | Correctos |
| Workflows | Validados con actionlint 1.7.12 |
| Tests en GitHub, Python 3.12 y Node.js 24 | [Ejecución correcta](https://github.com/ItzAbra26/canvas-telegram-assistant/actions/workflows/tests.yml) |
| Comprobador horario real | [Ejecución correcta: seis cuentas, cero envíos inciertos](https://github.com/ItzAbra26/canvas-telegram-assistant/actions/runs/37922314245) |
| Webhook de Telegram | Instalado; sin mensajes pendientes ni errores de recepción al comprobarlo |
| Respuestas tras la migración | Confirmadas por Telegram y conservadas en D1 |
| Envío real desde Cloudflare | Confirmado en 325 ms |
| Resumen mediante caché, cola y transacciones | Una respuesta confirmada en 741 ms; prueba deduplicada |
| Resumen con botones | Respuesta real confirmada en 718 ms |
| Actualización manual real | 10 cursos, 165 tareas, resumen enviado en 14.530 ms |
| Estado migrado | Una cuenta Canvas registrada, 148 tareas y avisos anteriores conservados |
| Persistencia D1 | Descifrado y copia manual comprobados |
| GitHub Secrets | Los cinco obligatorios configurados; valores no publicados |
| Cuenta Cloudflare | Worker y base exclusivos; ningún proyecto de Supabase modificado |

Los tests cubren 15 usuarios independientes, rechazo del alumno 16, registro privado, rechazo de tokens reenviados y grupos, altas y bajas, conservación del historial al renovar credenciales, fechas de Madrid y cambios de horario, paginación oficial, HTML, entregas, cambios y recordatorios, errores de red, límites de frecuencia y envíos inciertos.

Las pruebas de D1 ejecutan el SQL real en SQLite y comprueban que una transacción con versión antigua no escribe ninguna partición. También comprueban que la vista breve de un comando conserva las descripciones completas en el historial Python, que los alumnos permanecen aislados y que la configuración cifrada se puede actualizar sin tocar el estado. Python y JavaScript descifran mutuamente sus checkpoints Fernet comprimidos. Las consultas largas ofrecen todas las tareas en páginas pequeñas.

Además de los mocks, se han consultado tareas de una cuenta real de MEDAC desde GitHub Actions y se han enviado mensajes reales mediante la Telegram Bot API desde Cloudflare. El comprobador horario omite getUpdates, respeta el webhook activo y conserva el estado mediante el endpoint autenticado de D1. La copia previa de GitHub permanece cifrada en bot-state y ya no es la base activa.

La revisión se programa una vez por hora, al minuto 17; los cron de GitHub pueden retrasarse. Los comandos usan la última sincronización y se atienden directamente desde el webhook. Los tiempos medidos corresponden a pruebas concretas, no a una garantía para cualquier red o volumen. La práctica conserva el modo de prueba con tokens manuales solicitado; OAuth institucional no está implementado.

Para repetir las pruebas locales, sigue el README. python scripts/cloud_admin.py status comprueba el webhook; probe comprueba el resumen con envío deduplicado; backup guarda una copia cifrada ignorada por Git. No se han generado tareas, cambios de notas ni entregas en Canvas para realizar las comprobaciones.

Se corrigió además el registro de alumnos: una cuenta válida obtenía 200 desde la conexión local y 403 con HTML desde Cloudflare. Tras enviar `Accept: application/json` y un `User-Agent` propio, ambos endpoints oficiales (`users/self/profile` y `users/self`) devolvieron 200 desde el servicio desplegado. Los avisos distinguen 401, 403 y bloqueo del servidor; no atribuyen automáticamente un 403 a caducidad. La comprobación autenticada `canvas-health` no devuelve información personal ni credenciales. Las pruebas nuevas verifican las cabeceras, el rechazo HTML, el reintento del registro y la privacidad del diagnóstico.

El 9 de octubre se añadieron botones, /actualizar y /entregar al webhook. Telegram acepta mensajes y callback_query. Las pruebas cubren selección y autorización por alumno, tarea cerrada o con herramienta externa, tamaño y extensión, caducidad, cambio de archivo/tarea, entrega tardía/de grupo/repetida, confirmación obligatoria, desconexión durante la subida, transferencia multipart oficial, ausencia de credenciales en hosts externos y resultado incierto sin reenvío. Los tests de D1 preservan las descripciones completas y el historial que necesita el comprobador horario tras actualizar manualmente.

El flujo de entrega de archivos se verificó con documentos y APIs simuladas. No se ha entregado ningún trabajo académico real para comprobarlo. La primera prueba real debe hacerse con un archivo del alumno en una tarea de práctica que admita archivos, revisando la pantalla de confirmación y el resultado en Canvas. Las restricciones del centro, intentos disponibles y herramientas externas siguen sujetos a Canvas.

Se excluyen las tareas cuyo nombre contenga SCORM, tanto en datos nuevos como en caché e historial. Las pruebas comprueban listas, contadores, avisos pendientes y selector de entregas. También se ha corregido la concurrencia observada durante la prueba real: una lectura SQL atómica evita interrupciones entre particiones; Python combina cambios independientes sin reemplazar los mensajes o datos del webhook. Se rechazan cambios de cuenta, desconexiones, actualizaciones incompatibles y reclamaciones simultáneas del mismo envío.

Las tareas vencidas sin entregar ahora se muestran como **Perdidas**, con botón y comando `/perdidas`. Las 204 pruebas verifican que quedan fuera de pendientes y urgentes, que las entregadas o exentas no se confunden con perdidas, el límite exacto del vencimiento, la reclasificación sin sincronización y tras ampliar la fecha, y la separación de contadores por asignatura. Python y JavaScript producen las mismas listas y resúmenes. La firma **Made by; AB Solutions** aparece en inicio, ayuda y resumen. Actualización publicada en Cloudflare con versión `710ab573-6e64-456a-aa64-0fd7fd1205a4`; menú de comandos registrado de nuevo en Telegram.
