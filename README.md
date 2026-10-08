# Canvas Telegram Assistant

Bot de **prácticas de clase, multiusuario**, en Python. Cada alumno abre el mismo bot por privado, envía `/start`, sigue las instrucciones y registra **su propio token de Canvas**. El bot valida el token, intenta borrar el mensaje y conserva credenciales y estado **cifrados**. Cada cuenta tiene sus propias tareas, entregas, cambios y recordatorios.

El centro de esta configuración es **https://medac.instructure.com**. No hay contraseñas ni tokens reales en el código. El bot utiliza la API oficial y solo realiza consultas a Canvas: no entrega tareas ni modifica calificaciones.

### Esta instalación ya está activa

Abre **[SuperDelegado2B en Telegram](https://t.me/SuperDelegado2B_bot)** y envía `/start` por privado. Sigue la guía y envía tu propio token de Canvas cuando el bot te lo pida. Hay espacio para **15 alumnos**. No necesitas instalar Python, configurar GitHub ni mantener el ordenador encendido para utilizar esta instalación.

El código está publicado en [ItzAbra26/canvas-telegram-assistant](https://github.com/ItzAbra26/canvas-telegram-assistant). Los tres Secrets obligatorios ya están configurados; las 111 pruebas y varias ejecuciones reales del bot han terminado correctamente en GitHub. También se ha comprobado una respuesta real a `/start`. El estado cifrado se conserva en `bot-state`. Puedes consultar las [ejecuciones automáticas](https://github.com/ItzAbra26/canvas-telegram-assistant/actions/workflows/canvas-bot.yml).

**Las respuestas llegan en la siguiente revisión, aproximadamente cada 15 minutos**, con posibles retrasos de GitHub. Espera a recibir las instrucciones de `/start` antes de enviar el token. Cada alumno debe registrar su cuenta; todavía no se ha validado una cuenta real de MEDAC. Las siguientes secciones explican cómo mantener esta instalación o crear otra desde cero.

> **Alcance docente:** el registro manual solicitado está disponible con `TEST_MODE=true`. La documentación de Canvas reserva los tokens manuales para pruebas previas a OAuth y exige OAuth para aplicaciones con varios usuarios. El consentimiento de la clase no sustituye ese requisito. Esta versión no implementa OAuth ni se presenta como un despliegue multiusuario de producción. [Fuente oficial](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth).

## 1. Qué puedes usar

| Comando | Resultado, siempre en tu chat privado |
| --- | --- |
| `/start` | Instrucciones y registro/renovación de tu token |
| `/hoy` | Tareas con vencimiento hoy y su estado, incluidas las entregadas |
| `/manana` | Tareas con vencimiento mañana y su estado |
| `/semana` | Vencimientos de hoy y los seis días siguientes |
| `/pendientes` | Todas las pendientes, ordenadas; las que no tienen fecha aparecen al final |
| `/atrasadas` | Pendientes cuyo plazo ya pasó |
| `/ultimas` | Tareas creadas o detectadas en los últimos siete días |
| `/asignaturas` | Cursos activos y número de pendientes por curso |
| `/resumen` | Urgentes, esta semana, más adelante, sin fecha y próxima entrega |
| `/estado` | Conexión, última sincronización y envíos inciertos |
| `/id` | Tu ID de Telegram, útil para restringir la práctica a la clase |
| `/ayuda` | Lista de comandos |
| `/cancelar` | Cancela el registro; conserva una conexión anterior si la había |
| `/desconectar` | Elimina tu conexión y datos del estado activo |
| `/privacidad` | Datos utilizados, cifrado, administrador e historial |

También envía avisos de tareas nuevas; cambios de fecha, descripción, puntos, nombre, entrega o exención; y tareas eliminadas o que dejan de ser visibles. Los recordatorios se programan al cruzar **7 días, 3 días, 24 horas y 3 horas** antes de la entrega.

Los textos usan HTML de Telegram, escapan títulos/descripciones y se reparten en mensajes de hasta 3.500 caracteres sin cortar etiquetas. La descripción HTML de Canvas se limpia y se abrevia en los avisos. La descripción completa permanece en el estado cifrado y en Canvas.

## 2. Ejecución sin tener tu PC encendido y coste

**GitHub Actions ejecuta una revisión aproximadamente cada 15 minutos**, a los minutos 7, 22, 37 y 52 de cada hora. Puede seguir funcionando cuando cierres el ordenador.

Cada ejecución recibe los comandos pendientes, consulta las cuentas registradas, calcula cambios/recordatorios y envía las respuestas. **El bot no permanece conectado entre ejecuciones**: `/start`, el registro y los comandos pueden tardar hasta la siguiente revisión, y más si GitHub retrasa el trabajo. Los mensajes de Telegram pendientes no se conservan más de 24 horas. Para respuestas rápidas existe `--listen`, pero necesita una máquina encendida y no debe ejecutarse simultáneamente con Actions usando el mismo bot.

Para la opción de coste cero, usa un repositorio **público con runners estándar de Linux**. El código es público; las credenciales están en Secrets y el estado está cifrado. GitHub documenta que esos runners son gratuitos en repositorios públicos; los privados consumen una cuota (2.000 minutos/mes con GitHub Free), y cada 15 minutos puede superarla. No se requiere una API de IA ni un servicio de base de datos de pago. [Facturación oficial](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

No es una garantía de disponibilidad 24/7: los cron de Actions pueden retrasarse o descartarse, y los workflows públicos se desactivan tras 60 días sin actividad. Revisa la pestaña Actions durante la práctica y reactívalos si procede. El workflow debe estar en la rama predeterminada. [Límites de los eventos programados](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## 3. Arquitectura y persistencia

```text
Telegram privado → registro y comandos → cuenta Canvas del alumno
                                         ↓
                                  API oficial Canvas
                                         ↓
                       tareas / entregas / comparación de cambios
                                         ↓
                   estado + cola de avisos cifrados con Fernet
                                         ↓
                   GitHub: rama bot-state, archivo state.enc
                                         ↓
                            respuestas privadas en Telegram
```

El estado contiene usuarios, tokens, cursos activos, versiones anteriores de cada tarea, primera detección, recordatorios, cola de envíos y último `update_id` procesado. Está indexado por el ID del remitente de Telegram, comprobado contra su chat privado. No se aceptan registros desde grupos ni mensajes reenviados.

**Elección:** JSON comprimido, cifrado y versionado en una rama dedicada de GitHub. Es sencillo, gratuito, no caduca como una caché y no añade un proveedor. La compresión se aplica antes del cifrado; permite guardar los enunciados que comparten los alumnos sin repetir su tamaño. Se ha probado con 15 alumnos y 1.800 tareas con descripciones extensas. Los estados de la versión anterior siguen siendo compatibles. `GitHubStore` usa la API de contenidos y el `GITHUB_TOKEN` temporal de Actions para guardar checkpoints; no necesita un token personal de GitHub. La rama se crea automáticamente a partir de la rama predeterminada y no recibe cambios de código posteriores. No la combines con `main`.

El estado se guarda **tras cada registro/comando y sincronización, y antes y después de cada envío**. No depende de que llegue a ejecutarse un paso final del workflow. Una escritura fallida detiene los envíos. `concurrency` evita solapamientos del workflow; el SHA de cada archivo rechaza escrituras de un proceso con estado desactualizado. En local se utiliza un archivo cifrado, escritura atómica y bloqueo de proceso.

**Avisos repetidos:** cada evento tiene una identidad estable y un estado persistido. Si Telegram rechaza explícitamente un mensaje por límite de frecuencia, se reintenta en otra ejecución. Si la conexión se pierde cuando Telegram podría haberlo aceptado, se marca como `uncertain` y **no se reenvía automáticamente**. Telegram no ofrece una clave de idempotencia para `sendMessage`: no puede garantizarse a la vez cero pérdidas y cero duplicados ante cualquier interrupción. Aquí se prioriza no duplicar; podría faltar un aviso incierto. `/estado` muestra el contador y `/resumen` permite consultar la información actual.

Por defecto la primera sincronización manda un único resumen, sin anunciar todas las tareas antiguas. Los umbrales ya pasados al descubrir una tarea se consideran cubiertos por su aviso inicial; cuando cambia la fecha, por el aviso de cambio. Tras una caída que cruza varios umbrales se envía solo el más urgente que todavía resulte útil. Se cancelan los recordatorios pendientes si la tarea se entrega, desaparece o cambia de fecha.

```text
README.md                  Guía de configuración
requirements.txt           Dependencias del bot
requirements-dev.txt       Tests y herramientas de calidad
.env.example               Plantilla sin credenciales reales
src/main.py                Entrada y modos de ejecución
src/config.py              Variables de entorno y validaciones
src/canvas_client.py       API Canvas, paginación y fallback oficial
src/telegram_bot.py        Bot API, menú, mensajes y borrado
src/service.py             Registro y coordinación de cuentas
src/commands.py            Respuestas a comandos
src/notifier.py            Cambios, recordatorios y cola persistida
src/storage.py             Cifrado, archivo local y persistencia GitHub
src/models.py              Cursos, tareas y estado de entrega
src/utils.py               Fechas, HTML y formato de mensajes
src/ai.py                  Interfaz para futuras funciones de IA
src/demo.py                Demostración completa sin red
tests/                     Pruebas de lógica e integración simulada
.github/workflows/         Revisión automática y pruebas
```

## 4. Crear el bot de Telegram con BotFather

Solo el administrador/profesor necesita hacer esto una vez:

1. Abre Telegram y busca **@BotFather**, el bot oficial verificado.
2. Envía `/newbot`.
3. Elige un nombre visible, por ejemplo `Asistente Canvas de clase`.
4. Elige un nombre de usuario disponible terminado en `bot`.
5. BotFather entrega un token. Guárdalo como `TELEGRAM_BOT_TOKEN` en `.env` o GitHub Secrets, nunca en código ni en capturas compartidas.
6. Abre el enlace del nuevo bot y pulsa Iniciar. El programa registra automáticamente el menú de comandos en su primera ejecución.

El bot utiliza recepción periódica mediante `getUpdates`. No necesita una web pública, webhook ni dominio propio. Si reutilizas uno que ya tenga webhook, consulta la solución del apartado de problemas.

## 5. Preparar y probar en Windows

Desde PowerShell, dentro de la carpeta del proyecto:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m src.main --demo
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

No hace falta activar el entorno ni cambiar la política de ejecución de PowerShell. Se recomienda Python 3.12 o superior; Actions usa 3.12.

La demo no usa red ni credenciales reales: registra dos alumnos, asigna la misma tarea entregada a uno y pendiente al otro, prueba recordatorios/cambios y comprueba el cifrado.

Para usar el bot real:

1. Si todavía no tienes `.env`, copia `.env.example` a `.env`.
2. Genera una clave con el comando siguiente y copia su resultado a `STATE_ENCRYPTION_KEY`. **En esta entrega local ya se ha generado una clave en `.env`; puedes conservarla.**
3. Introduce el token de BotFather en `TELEGRAM_BOT_TOKEN` dentro de `.env`.
4. Deja `CANVAS_BASE_URL=https://medac.instructure.com`, `TEST_MODE=true`, `STORAGE_BACKEND=local` y `TIMEZONE=Europe/Madrid`.
5. Ejecuta el bot en modo continuo, abre su chat privado y envía `/start`.

```powershell
# Solo si necesitas generar tu propia clave; no la compartas.
.\.venv\Scripts\python.exe -m src.main --generate-key

# Bot activo mientras esta máquina siga encendida.
.\.venv\Scripts\python.exe -m src.main --listen

# Alternativa: una única revisión.
.\.venv\Scripts\python.exe -m src.main --once
```

Pulsa Ctrl+C para detener el modo continuo. No tengas dos procesos del mismo bot ni mantengas el modo local escuchando una vez activado Actions: competirían por los mensajes entrantes. Al pasar de local a GitHub el estado no se migra automáticamente; los alumnos deben registrarse allí de nuevo. No subas el archivo local sin cifrarlo ni mezcles dos fuentes de estado.

En Linux/macOS usa `python3 -m venv .venv` y `.venv/bin/python` en lugar de `.\.venv\Scripts\python.exe`.

## 6. Registro de cada alumno: `/start` → token → cuenta conectada

1. Abre el mismo bot **en un chat privado** y envía `/start` (sin punto delante).
2. Sigue el mensaje: en MEDAC abre **Cuenta → Configuración → Integraciones aprobadas → Nuevo token de acceso**. El nombre de la opción puede variar por idioma.
3. Pon un nombre para la práctica y una caducidad. Copia el token cuando Canvas lo muestre.
4. Envíalo al chat privado como un único mensaje que contenga **solo el token**.
5. El bot intenta borrar el mensaje antes de validarlo. Si no puede borrarlo, lo indicará: bórralo tú manualmente.
6. Canvas debe devolver un perfil válido. Solo entonces se guarda el token cifrado, vinculado a tu usuario de Telegram.
7. Recibirás `Cuenta conectada` y el resultado de la primera sincronización. Prueba `/resumen`, `/hoy` y `/pendientes`.

Si estás usando Actions, espera a la siguiente ejecución después de cada paso. El token queda en Telegram hasta que esa ejecución lo procese e intente borrarlo. No pegues tokens en grupos, mensajes reenviados, GitHub Issues o el chat con el asistente de programación. Enviar el token autoriza las consultas descritas en `/start` y `/privacidad`; la clave de cifrado está bajo control del administrador.

Cuando el token caduque, usa `/start` de nuevo y envía uno nuevo. Si corresponde a la misma cuenta, se conserva el historial de tareas para evitar repetir avisos. Si conectas una cuenta Canvas diferente, se eliminan los datos activos anteriores de ese usuario y se inicia otra sincronización.

Si no aparece la opción de generar tokens o la API da 403, debe revisarlo el profesor/administrador de MEDAC. No hay un fallback que eluda permisos con scraping. Para el uso habitual multiusuario, el siguiente paso es OAuth con una Developer Key autorizada por el centro.

## 7. Crear y subir el repositorio GitHub

Nombre sugerido: **canvas-telegram-assistant**.

1. Entra en [Crear repositorio](https://github.com/new) con tu cuenta.
2. Pon ese nombre. Para usar los runners públicos gratuitos, elige **Public**. El código generado no contiene datos reales; el estado se cifra. Si prefieres Private, revisa la cuota de Actions y considera una frecuencia menor.
3. Crea el repositorio vacío, sin añadir otro README ni `.gitignore`.
4. Dentro de esta carpeta ejecuta lo siguiente, sustituyendo `TU_USUARIO`:

```powershell
git init -b main
git add .
git commit -m "Build Canvas Telegram classroom test assistant"
git remote add origin https://github.com/TU_USUARIO/canvas-telegram-assistant.git
git push -u origin main
```

Si esta entrega ya tiene un commit local, omite `git init`, `git add` y `git commit`. No vuelvas a ejecutar `git remote add` si ya hay un remoto: compruébalo con `git remote -v`. Git puede pedirte iniciar sesión en GitHub.

Antes de subir, `git status --short --ignored` debe indicar que `.env`, `.venv` y `data` están ignorados. Subir el código y los workflows activa las pruebas, pero las revisiones reales necesitan los Secrets siguientes.

## 8. Secrets exactos para la versión multiusuario

En el repositorio abre **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Valor | Necesario |
| --- | --- | --- |
| `CANVAS_BASE_URL` | `https://medac.instructure.com` | Sí |
| `TELEGRAM_BOT_TOKEN` | Token del bot creado con BotFather | Sí |
| `STATE_ENCRYPTION_KEY` | La clave Fernet de tu `.env`, o una recién generada antes del primer uso | Sí |
| `CLASS_INVITE_CODE` | Código de clase elegido por el profesor | Opcional, recomendable si el enlace del bot se comparte ampliamente |
| `ALLOWED_TELEGRAM_USER_IDS` | IDs numéricos separados por comas, sin nombres de usuario | Opcional |

**No necesitas `CANVAS_TOKEN` ni `TELEGRAM_CHAT_ID` como Secrets globales**: cada alumno registra su token en el chat y el bot obtiene su ID del remitente. Los nombres originales siguen en `.env.example` únicamente para la importación personal opcional. `GITHUB_TOKEN` y `GITHUB_REPOSITORY` se proporcionan automáticamente por Actions; no crees un PAT para el workflow.

Con `CLASS_INVITE_CODE`, se entra con `/start CODIGO_DE_CLASE`; sin él basta `/start`. La lista de IDs permite limitar de forma más estricta la práctica. Si se configura, el bot ignora a los demás y deja de sincronizar sus cuentas ya registradas. No se borra su estado automáticamente.

### Obtener `TELEGRAM_CHAT_ID` o los IDs de la clase

Cuando el bot esté funcionando, cada alumno envía `/id` en privado y ve su ID numérico. En chats privados ese ID coincide con su `chat_id`. El profesor puede reunirlos para `ALLOWED_TELEGRAM_USER_IDS`; no los publiques en el repositorio. Si ya activaste una lista y falta alguien, añádelo antes para que `/id` responda.

Para importar solo tu cuenta desde variables de entorno, rellena los dos valores opcionales en `.env` y ejecuta:

```powershell
.\.venv\Scripts\python.exe -m src.main --import-personal --once
```

## 9. Activar Actions y comprobar el despliegue

1. Comprueba que `.github/workflows/canvas-bot.yml` está en `main` y que `main` es la rama predeterminada.
2. Añade los tres Secrets obligatorios.
3. En **Actions → Canvas Telegram Bot → Run workflow**, selecciona la rama predeterminada y ejecuta.
4. Comprueba que se instalan las dependencias y finaliza la revisión. El workflow solicita `contents: write` para guardar el estado. Si el centro/organización impide permisos de escritura, su administrador tendrá que habilitarlos.
5. Envía `/start` al bot. Ejecuta manualmente otra revisión para recibir las instrucciones sin esperar al cron.
6. Envía tu token privado. Ejecuta otra revisión para validarlo y cargar tus tareas.
7. Envía `/resumen` y lanza una revisión más. Comprueba tu estado de entrega con el de Canvas.
8. En GitHub aparecerán la rama `bot-state` y `state.enc`, con contenido cifrado. No debería aparecer ninguna descripción de tarea ni token en texto claro.
9. Las siguientes revisiones se programan cada 15 minutos. Ya puedes apagar el PC.

El workflow no publica artefactos con datos de alumnos. Los logs muestran conteos y errores de servicio/HTTP, no tokens ni cuerpos de respuesta. El workflow `Tests` corre por cambios de código y pull requests; no utiliza tus Secrets ni responde al alumnado.

## 10. Probar las alertas sin esperar días

Para la comprobación completa sin credenciales, ejecuta `--demo` y los tests. Para la prueba real, usa un curso de pruebas y coordina estas acciones con el profesor:

1. Conecta la cuenta y espera la sincronización inicial.
2. El profesor publica una tarea nueva visible para ese alumno. Tras una revisión debe llegar `NUEVA TAREA`.
3. Cambia su fecha, descripción o puntos. Debe llegar el aviso de cambio con los valores correspondientes.
4. El alumno entrega la tarea. La revisión siguiente debe detectar la entrega; `/pendientes` debe excluirla.
5. Para comprobar el recordatorio de 3 horas, publica una tarea con vencimiento algo más de 3 horas después de la primera revisión. Cuando una revisión cruce el umbral, llegará una sola vez. La tarea debe continuar pendiente.
6. Repite una revisión sin cambiar nada: no debe repetir los avisos.
7. Al desaparecer una tarea, el bot requiere dos revisiones completas con un 404 individual antes de avisar. Si se cierra un curso, sus tareas se ocultan de consultas actuales, pero se conservan en el historial.

El bot no crea ni cambia tareas para hacer estas pruebas; todo lo anterior debe hacerse desde Canvas por una persona con los permisos correspondientes.

## 11. Qué campos ofrece Canvas y límites importantes

Endpoints utilizados, con paginación `Link` y `per_page=100`:

| Endpoint | Uso |
| --- | --- |
| `GET /api/v1/users/self/profile` | Valida el token y obtiene el ID de su propietario |
| `GET /api/v1/courses?enrollment_state=active&enrollment_type=student&state[]=available` | Cursos activos de estudiante |
| `GET /api/v1/courses/{id}/assignments?include[]=submission&override_assignment_dates=true` | Tareas y entregas del usuario autenticado |
| `GET /api/v1/courses/{id}/assignments/{assignment}/submissions/self` | Fallback oficial si falta la entrega incluida |
| `GET /api/v1/courses/{id}/assignments/{assignment}` | Comprueba una tarea que ha desaparecido de la lista |

Referencias: [Courses](https://developerdocs.instructure.com/services/canvas/resources/courses), [Assignments](https://developerdocs.instructure.com/services/canvas/resources/assignments), [Submissions](https://developerdocs.instructure.com/services/canvas/resources/submissions), [Pagination](https://developerdocs.instructure.com/services/canvas/basics/file.pagination), [Telegram Bot API](https://core.telegram.org/bots/api).

La tarea conserva ID, curso, nombre, descripción limpia, creación, apertura, vencimiento efectivo para ese alumno, puntos, URL y estado de entrega. **Canvas no expone una fecha exacta de publicación en el objeto Assignment**: se guarda `created_at`, `unlock_at` y primera detección, sin inventar `published_at`. `/ultimas` usa creación o detección reciente; en la primera carga también pueden aparecer tareas antiguas recién detectadas.

Las fechas de Canvas se interpretan con su zona/UTC y se muestran en **Europe/Madrid**, incluidos los cambios de horario de verano. Las tareas sin fecha siguen en `/pendientes`, pero no generan recordatorios por plazo. Las tareas exentas o sin entrega requerida no se cuentan como pendientes.

`submitted` y `pending_review`, o una entrega/intento registrado, indican entrega. **No se utiliza `has_submitted_submissions`**, porque significa que alguien del curso ha entregado, no necesariamente este alumno. Una calificación sin intento ni fecha de entrega puede ser un cero automático; no basta para marcarla como entregada. Las entregas en papel o mediante herramientas externas dependen de lo que el profesor/herramienta refleje en Canvas.

Un 404 no permite distinguir con certeza una eliminación de una tarea que ya no es visible. Por eso el mensaje dice «eliminada o ya no visible». Si faltan campos esenciales o falla una página/curso, no se aplica esa sincronización al alumno. Los demás alumnos siguen procesándose. No se comparten snapshots entre alumnos aunque tengan el mismo curso: pueden tener fechas y permisos diferentes.

## 12. Problemas frecuentes

| Problema | Qué hacer |
| --- | --- |
| Falta una variable al iniciar | Revisa `.env` o los tres Secrets obligatorios |
| 401 de Canvas | Token caducado/revocado; el bot pausa esa cuenta y pide renovar con `/start` |
| 403 de Canvas | Revisa permisos con el administrador; el bot conserva los datos anteriores |
| 429 / caída temporal de Canvas | Lecturas con reintentos limitados; conserva el estado y vuelve en otra revisión |
| Canvas entrega datos incompletos | Se rechaza la sincronización, sin borrar tareas |
| Telegram no responde enseguida | Con Actions espera la siguiente revisión o ejecuta `Run workflow` |
| Telegram 401 | Revisa el token de BotFather |
| Telegram 403 al enviar | Usuario ha bloqueado el bot; debe abrirlo de nuevo y usar un comando para recibir respuesta |
| Telegram 429 | Se respeta `retry_after` y la cola se conserva para otra ejecución |
| «Hay un webhook activo» | Detén la integración anterior y ejecuta `python -m src.main --remove-webhook` con el entorno configurado |
| Dos procesos reciben mensajes del mismo bot | Detén el modo local al activar Actions; no uses el mismo bot en dos repositorios |
| GitHub 403 al guardar | Revisa permisos de Actions, reglas de la rama y límites de la API |
| GitHub 409 al guardar | Hay otro escritor o un cambio de estado externo; espera una ejecución nueva, sin forzar la escritura |
| No se puede descifrar el estado | Restaura la clave original. El bot se detiene y no sustituye el estado por uno vacío |
| Actions se ha desactivado | Revisa cuota/actividad y habilita el workflow de nuevo |
| Envíos inciertos | Revisa `/estado`, comprueba Telegram y usa `/resumen`; no se reenvían automáticamente |

Los tokens de alumno caducan según la configuración y políticas de Canvas/centro. La guía vigente indica caducidad de los tokens de estudiante; comprueba la fecha que muestra tu instancia. [Gestión oficial de tokens](https://community.instructure.com/en/kb/articles/662901-unknown).

El límite inicial es `MAX_USERS=15`. Ajusta ese valor en `.env` y en el workflow si lo necesitas. Más cuentas y tareas implican más consultas, commits y tiempo; el workflow tiene un máximo de 10 minutos. El bot envía como máximo 80 mensajes por ejecución y deja el resto en cola. El archivo remoto comprimido y cifrado se limita a 900 KB para evitar la limitación de lectura de la API de contenidos. La prueba de 1.800 tareas supera 5 MB de JSON original y cabe tras comprimir; el tamaño real depende de tus enunciados. Si se alcanza el límite, el programa detiene los envíos y conserva el estado anterior; para una clase grande o uso prolongado conviene migrar a una base de datos. [Límites oficiales del almacenamiento](https://docs.github.com/en/rest/repos/contents#get-repository-content).

## 13. Seguridad, bajas y copias

- `.env`, archivos locales cifrados, logs y el entorno Python se ignoran en Git. `.env.example` contiene solo nombres y valores de configuración no secretos.
- Nunca compartas `STATE_ENCRYPTION_KEY`: permite descifrar los tokens y datos de todos. Conserva una copia segura, pues perderla impide recuperar el estado.
- El cifrado protege el contenido publicado en GitHub, pero los lectores del repositorio pueden ver que existen archivos/commits y sus fechas. El administrador que tiene la clave puede acceder al contenido. Los colaboradores con capacidad de modificar el workflow son administradores de confianza.
- `/desconectar` borra el estado activo y la cola de ese alumno. **No purga automáticamente las versiones cifradas del historial Git.** Revocar su token en Canvas anula su uso incluso si existiera una versión anterior. Para una retirada completa de copias históricas, el administrador debe planificar una purga del historial y de sus copias; no basta borrar el archivo actual.
- Telegram conserva/procesa los mensajes conforme a su servicio. El borrado automático es una reducción de exposición, no una garantía de que el token nunca haya sido copiado.
- El bot no almacena los updates completos ni registra el texto del mensaje que contiene el token. Rechaza enlaces de paginación a otros dominios y no sigue redirecciones de Canvas con credenciales.
- Para terminar la práctica, desactiva el workflow, pide que se revoquen los tokens, elimina los Secrets y gestiona las copias de estado según lo acordado con la clase.

## 14. IA futura y migración a OAuth

`src/ai.py` define una interfaz para resumir, explicar y estimar duración. La implementación base devuelve texto de la tarea y no inventa dificultad ni tiempo. El bot no llama a un proveedor de IA, no necesita una clave de IA y no envía tareas a uno.

Para pasar del modo de prueba al uso multiusuario habitual, será necesario implementar autorización OAuth con una Developer Key del centro, callback HTTPS, vinculación segura con Telegram y renovación de access tokens mediante refresh tokens. La capa de Canvas acepta un bearer token; el almacenamiento, la lógica de tareas y los avisos pueden reutilizarse. `TEST_MODE=false` desactiva tanto el registro manual como la consulta de estas cuentas de prueba; **no activa una implementación OAuth inexistente**.
