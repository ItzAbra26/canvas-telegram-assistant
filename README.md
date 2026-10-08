# Canvas Telegram Assistant

Bot de **prácticas de clase, multiusuario**, en Python. Cada alumno abre el mismo bot por privado, envía `/start`, sigue las instrucciones y registra **su propio token de Canvas**. El bot valida el token, intenta borrar el mensaje y conserva credenciales y estado **cifrados**. Cada cuenta tiene sus propias tareas, entregas, cambios y recordatorios.

El centro de esta configuración es **https://medac.instructure.com**. No hay contraseñas ni tokens reales en el código. El bot utiliza la API oficial y solo realiza consultas a Canvas: no entrega tareas ni modifica calificaciones.

### Esta instalación ya está activa

Abre **[SuperDelegado2B en Telegram](https://t.me/SuperDelegado2B_bot)** y envía `/start` por privado. Sigue la guía y envía tu propio token de Canvas cuando el bot te lo pida. Hay espacio para **15 alumnos**. No necesitas instalar Python, configurar GitHub ni mantener el ordenador encendido para utilizar esta instalación.

El código está publicado en [ItzAbra26/canvas-telegram-assistant](https://github.com/ItzAbra26/canvas-telegram-assistant). Cloudflare atiende Telegram, D1 conserva el estado cifrado y GitHub Actions revisa Canvas cada hora. Los cinco Secrets obligatorios ya están configurados. Se ha migrado una cuenta real de MEDAC con 148 tareas, conservando sus avisos anteriores. Puedes consultar las [ejecuciones automáticas](https://github.com/ItzAbra26/canvas-telegram-assistant/actions/workflows/canvas-bot.yml).

**Los comandos responden al escribirlos, normalmente en segundos.** La prueba real del resumen, incluyendo su persistencia y envío a Telegram, tardó 0,7 segundos. Las respuestas usan la última revisión de Canvas; `/estado` muestra su fecha. Las tareas se actualizan aproximadamente cada hora. Los demás alumnos solo tienen que abrir el bot y completar `/start`. Las secciones siguientes explican cómo mantener esta instalación o crear otra desde cero.

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

Para que las listas sean rápidas y legibles, el webhook muestra **8 tareas por página**. Si hay más, indica cómo continuar: por ejemplo, `/pendientes 2`, `/atrasadas 2` o `/ultimas 2`. Puedes consultar todas las páginas; no se descartan tareas.

## 2. Ejecución sin tener tu PC encendido y coste

**Cloudflare Workers recibe cada mensaje de Telegram mediante un webhook HTTPS** y responde con los datos guardados. **GitHub Actions consulta Canvas una vez por hora**, al minuto 17. Ambos siguen funcionando cuando cierres el ordenador. La primera carga tras registrar un token se intenta inmediatamente en segundo plano; si no se completa, la revisión horaria vuelve a intentarla.

El comprobador horario consulta cuentas registradas, calcula cambios y recordatorios y envía avisos. No recibe comandos con `getUpdates`: Telegram los entrega directamente a Cloudflare. Por ello un retraso de Actions no retrasa `/start` o `/resumen`, aunque sus datos podrían estar desactualizados. Cada respuesta muestra la última revisión; los fallos temporales conservan el estado anterior. Los recordatorios llegan en la primera revisión después de cruzar su umbral, con una precisión aproximada de una hora.

Esta instalación utiliza **Workers Free, D1 gratuito y un repositorio público con runners estándar de Linux**, sin contratar planes de pago ni IA. Workers Free permite 100.000 solicitudes diarias y tiene un límite de CPU de 10 ms por solicitud; D1 Free incluye bases de hasta 500 MB y cuotas diarias de consultas. El estado se separa por alumno y los comandos leen vistas breves para reducir el trabajo. Si se superan cuotas gratuitas, el servicio puede rechazar operaciones; no hemos activado facturación de Workers Paid. [Cloudflare Workers](https://developers.cloudflare.com/workers/platform/pricing/), [D1](https://developers.cloudflare.com/d1/platform/pricing/), [GitHub Actions](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

No es una garantía de disponibilidad 24/7: los cron de Actions pueden retrasarse o descartarse, y los workflows públicos se desactivan tras 60 días sin actividad. Revisa la pestaña Actions durante la práctica y reactívalos si procede. El workflow debe estar en la rama predeterminada. [Límites de los eventos programados](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## 3. Arquitectura y persistencia

```text
Telegram privado → Cloudflare Worker → respuesta inmediata
                         ↕
                 Cloudflare D1 cifrado
                         ↕
GitHub Actions cada hora → API oficial Canvas → cambios y avisos
```

El estado contiene usuarios, tokens, cursos activos, versiones anteriores de cada tarea, primera detección, recordatorios, cola de envíos y último `update_id` procesado. Está indexado por el ID del remitente de Telegram, comprobado contra su chat privado. No se aceptan registros desde grupos ni mensajes reenviados.

**Elección:** D1, una base persistente gratuita compartida por el webhook y Actions. Guarda JSON comprimido y cifrado con Fernet en particiones: metadatos, datos y cola de cada alumno, historial completo y vista abreviada para comandos. Los identificadores de partición son hashes; tokens, IDs privados, cursos y tareas están dentro del cifrado. El comprobador Python reconstruye el historial completo; una respuesta rápida nunca reemplaza una descripción completa por su versión abreviada. La configuración de ejecución también está cifrada y cada entrada necesita su secreto de autenticación.

La rama `bot-state` conserva la copia cifrada anterior a esta migración; ya no es el estado activo ni recibe los checkpoints del bot. `GitHubStore` sigue disponible como alternativa de ejecución periódica. D1 no depende de la caché ni de los archivos temporales del runner. Sus copias Time Travel gratuitas permiten recuperación de los últimos 7 días. [Límites de D1](https://developers.cloudflare.com/d1/platform/limits/).

El estado se guarda **tras cada registro/comando y sincronización, y antes y después de cada envío**. No depende de un paso final del workflow. D1 utiliza una transacción con versión y nonce: una escritura con versión antigua no modifica ninguna partición. El webhook recarga y reintenta conflictos; Actions recarga hasta tres veces cuando detecta concurrencia. Una caída no crea una base vacía. Los envíos del webhook que siguen en curso se respetan durante la recuperación horaria. En local se conserva la alternativa de archivo cifrado, escritura atómica y bloqueo.

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
src/remote_store.py        Estado compartido con el webhook
src/partitions.py          División y reconstrucción del estado
cloud/worker.mjs           Entrada del webhook de Cloudflare
cloud/bot.mjs              Registro y respuestas inmediatas
cloud/d1-db.mjs            Transacciones y vistas por alumno
cloud/d1-schema.sql        Tablas persistentes
scripts/cloud_admin.py     Configuración cifrada, diagnóstico y copias
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

El despliegue recomendado utiliza un webhook alojado en el dominio gratuito `workers.dev`. No necesitas comprar dominio ni mantener un servidor. La herramienta de configuración registra los comandos y el webhook en Telegram. Para probar la variante local con `getUpdates`, utiliza otro bot de pruebas; un bot con webhook activo no puede recibir también por polling.

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
4. Para probar un bot local diferente, usa `CANVAS_BASE_URL=https://medac.instructure.com`, `TEST_MODE=true`, `STORAGE_BACKEND=local`, `DELIVERY_MODE=polling` y `TIMEZONE=Europe/Madrid`. El bot de esta clase ya tiene un webhook: no lo quites para hacer pruebas locales. La demo y los tests funcionan sin tocarlo.
5. Ejecuta el bot en modo continuo, abre su chat privado y envía `/start`.

```powershell
# Solo si necesitas generar tu propia clave; no la compartas.
.\.venv\Scripts\python.exe -m src.main --generate-key

# Bot activo mientras esta máquina siga encendida.
.\.venv\Scripts\python.exe -m src.main --listen

# Alternativa: una única revisión.
.\.venv\Scripts\python.exe -m src.main --once
```

Pulsa Ctrl+C para detener el modo continuo. Usa un bot de pruebas distinto para `--listen`; el bot compartido funciona mediante webhook. Para migrar un estado cifrado existente, la herramienta de Cloudflare admite `bootstrap --state-file data/state.enc`, conservando la misma clave. No importes dos fuentes de estado ni ejecutes una inicialización sobre la base activa.

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
| `STATE_API_URL` | URL HTTPS del Worker, generada al desplegarlo | Sí |
| `STATE_API_KEY` | Secreto generado por `cloud_admin.py bootstrap`, guardado en `.env` | Sí |
| `CLASS_INVITE_CODE` | Código de clase elegido por el profesor | Opcional, recomendable si el enlace del bot se comparte ampliamente |
| `ALLOWED_TELEGRAM_USER_IDS` | IDs numéricos separados por comas, sin nombres de usuario | Opcional |

**No necesitas `CANVAS_TOKEN` ni `TELEGRAM_CHAT_ID` como Secrets globales**: cada alumno registra su token en el chat y el bot obtiene su ID del remitente. Los nombres originales siguen en `.env.example` para la importación personal opcional. Actions usa permiso `contents: read`; no necesita un PAT ni acceso de administrador a Cloudflare. `TELEGRAM_WEBHOOK_SECRET` se genera en `.env` y queda dentro de la configuración cifrada del webhook; no se añade como Secret de Actions.

Con `CLASS_INVITE_CODE`, se entra con `/start CODIGO_DE_CLASE`; sin él basta `/start`. La lista de IDs permite limitar la práctica. Estos valores deben coincidir en GitHub Secrets y en la configuración de Cloudflare. Para cambiar los ajustes después del despliegue, actualiza `.env`, genera la configuración cifrada sin alterar los alumnos e impórtala:

```powershell
.\.venv\Scripts\python.exe scripts/cloud_admin.py configure --output data/cloudflare-runtime.sql --use-resolved-ip
cd cloud
npx wrangler d1 execute canvas-telegram-assistant --remote --file ../data/cloudflare-runtime.sql
cd ..
.\.venv\Scripts\python.exe scripts/cloud_admin.py setup
```

`configure` conserva tokens, tareas, cola y claves existentes. No uses `bootstrap` para modificar una base que ya tiene usuarios.

### Obtener `TELEGRAM_CHAT_ID` o los IDs de la clase

Cuando el bot esté funcionando, cada alumno envía `/id` en privado y ve su ID numérico. En chats privados ese ID coincide con su `chat_id`. El profesor puede reunirlos para `ALLOWED_TELEGRAM_USER_IDS`; no los publiques en el repositorio. Si ya activaste una lista y falta alguien, añádelo antes para que `/id` responda.

Para importar solo tu cuenta desde variables de entorno, rellena los dos valores opcionales en `.env` y ejecuta:

```powershell
.\.venv\Scripts\python.exe -m src.main --import-personal --once
```

## 9. Desplegar desde cero y activar Actions

**Esta instalación ya está desplegada. No repitas su inicialización.** Los pasos siguientes sirven para crear otra instalación con otro bot y una base nueva.

1. Crea el repositorio según el apartado 7, un bot con BotFather y `.env` según el apartado 6. Instala también Node.js 24 y crea una cuenta gratuita en [Cloudflare](https://dash.cloudflare.com/). No contrates Workers Paid.
2. Desde la carpeta del proyecto, instala la parte de Cloudflare e inicia sesión. Wrangler es la herramienta oficial; autoriza lectura de tu cuenta, publicación de Workers/Scripts y gestión de D1.

```powershell
cd cloud
npm ci
npx wrangler login
npx wrangler d1 create canvas-telegram-assistant --location weur
```

3. Copia el `database_id` que devuelve el último comando en `cloud/wrangler.jsonc`, sustituyendo el ID de esta instalación. Puedes cambiar también `name` y `database_name` si elegiste otros nombres. El binding debe seguir llamándose `BOT_DB`.
4. Crea las tablas y publica el Worker. Anota la URL HTTPS que termina en `workers.dev`.

```powershell
npx wrangler d1 execute canvas-telegram-assistant --remote --file d1-schema.sql
npm run deploy
cd ..
```

5. Sustituye `URL_DEL_WORKER` por esa URL y genera la configuración cifrada. Este paso crea `STATE_API_KEY` y `TELEGRAM_WEBHOOK_SECRET` en `.env`; no los imprime ni los sube a Git. El parámetro `--use-resolved-ip` utiliza la IP pública verificada para evitar la caché DNS inicial de Telegram.

```powershell
.\.venv\Scripts\python.exe scripts/cloud_admin.py bootstrap --url https://URL_DEL_WORKER/ --use-resolved-ip
cd cloud
npx wrangler d1 execute canvas-telegram-assistant --remote --file ../data/cloudflare-bootstrap.sql
cd ..
.\.venv\Scripts\python.exe scripts/cloud_admin.py setup
.\.venv\Scripts\python.exe scripts/cloud_admin.py status
```

6. `setup` debe indicar `webhook_installed: true`; `status`, `webhook_active: true`. Si la URL acaba de crearse, espera a que resuelva. Si hay fallos de entrega tras un cambio de IP, vuelve a ejecutar `configure --use-resolved-ip`, importa su SQL y ejecuta `setup`. No compartas los archivos generados de `data/`.
7. Añade los **cinco Secrets obligatorios** del apartado 8 en GitHub. Los valores `STATE_API_URL` y `STATE_API_KEY` están en tu `.env`. El workflow ya tiene `STORAGE_BACKEND=remote`, `DELIVERY_MODE=webhook`, `MAX_USERS=15` y revisión horaria.
8. Comprueba que `main` es la rama predeterminada. En **Actions → Canvas Telegram Bot → Run workflow**, ejecuta una revisión. Debe finalizar en verde. No necesita permiso para escribir en el repositorio: los checkpoints van a D1.
9. Abre tu bot en privado, envía `/start`, espera las instrucciones y envía tu token. `/resumen` y `/estado` responden directamente; no ejecutes Actions para atender comandos.
10. Cuando termine la primera carga, compara el estado de una entrega con Canvas. Si la carga inmediata falla, lanza manualmente la revisión horaria para reintentarlo sin esperar.
11. Puedes comprobar un envío real del resumen desde la nube con `python scripts/cloud_admin.py probe`. Envía al primer usuario registrado una respuesta de prueba, con deduplicación, e indica el tiempo medido.
12. Ya puedes apagar el PC. Actions queda programado al minuto 17 de cada hora. Mantén activado el workflow y consulta `/estado` para saber cuándo revisó Canvas por última vez.

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
| Falta una variable al iniciar | Revisa `.env` o los cinco Secrets obligatorios |
| 401 de Canvas | Canvas no acepta la credencial: copia el valor completo del token del dominio correcto; también puede estar caducado/revocado. Una cuenta ya conectada se pausa y conserva sus datos |
| 403 de Canvas | Puede ser un permiso o un bloqueo de la conexión del servidor, sin implicar caducidad. El cliente del webhook envía `Accept: application/json` y un `User-Agent` propio; conserva los datos anteriores |
| 429 / caída temporal de Canvas | Lecturas con reintentos limitados; conserva el estado y vuelve en otra revisión |
| Canvas entrega datos incompletos | Se rechaza la sincronización, sin borrar tareas |
| Telegram no responde enseguida | Ejecuta `cloud_admin.py status`; comprueba el webhook, cuotas y disponibilidad de Cloudflare |
| Telegram 401 | Revisa el token de BotFather |
| Telegram 403 al enviar | Usuario ha bloqueado el bot; debe abrirlo de nuevo y usar un comando para recibir respuesta |
| Telegram 429 | Se respeta `retry_after` y la cola se conserva para otra ejecución |
| «Hay un webhook activo» | Para el bot compartido usa `DELIVERY_MODE=webhook` y `--sync-only`; no retires su webhook |
| Dos procesos reciben mensajes del mismo bot | Detén el modo local al activar Actions; no uses el mismo bot en dos repositorios |
| HTTP 409 al guardar en D1 | Otro proceso avanzó la versión; se recarga, sin forzar ni sobrescribir su cambio |
| Telegram rechaza el webhook por DNS | Regenera la configuración con `--use-resolved-ip` y ejecuta `setup` |
| No se puede descifrar el estado | Restaura la clave original. El bot se detiene y no sustituye el estado por uno vacío |
| Actions se ha desactivado | Revisa cuota/actividad y habilita el workflow de nuevo |
| Envíos inciertos | Revisa `/estado`, comprueba Telegram y usa `/resumen`; no se reenvían automáticamente |

Los tokens de alumno caducan según la configuración y políticas de Canvas/centro. La guía vigente indica caducidad de los tokens de estudiante; comprueba la fecha que muestra tu instancia. [Gestión oficial de tokens](https://community.instructure.com/en/kb/articles/662901-unknown).

Si un token recién creado falla solo al registrarlo, el administrador puede ejecutar `python scripts/cloud_admin.py canvas-health`. Consulta dos endpoints oficiales con una cuenta ya conectada y muestra únicamente códigos HTTP y categorías de error, sin nombres, IDs ni tokens. En MEDAC se verificó un 403 con HTML desde el webhook que desapareció al añadir las cabeceras JSON e identificación de la aplicación; no era una caducidad del token. [API oficial de usuarios y perfil](https://developerdocs.instructure.com/services/canvas/resources/users).

El límite inicial es `MAX_USERS=15`; para esta práctica conserva ese valor en `.env`, el workflow y la configuración cifrada. El comprobador tiene un máximo de 10 minutos y envía como máximo 80 mensajes por revisión, dejando el resto en cola. Cada partición cifrada tiene un límite preventivo de 1,8 MB frente al máximo de 2 MB por fila de D1. Si se supera, el bot detiene las escrituras y conserva el estado anterior. Los tests incluyen 15 cuentas y enunciados extensos, pero no garantizan capacidad ilimitada. La primera carga del webhook también está sujeta al tiempo de segundo plano de Workers; si no termina, Actions la realiza después. [Límites oficiales de D1](https://developers.cloudflare.com/d1/platform/limits/).

## 13. Seguridad, bajas y copias

- `.env`, archivos locales cifrados, logs y el entorno Python se ignoran en Git. `.env.example` contiene solo nombres y valores de configuración no secretos.
- Nunca compartas `STATE_ENCRYPTION_KEY`: permite descifrar los tokens y datos de todos. Conserva una copia segura, pues perderla impide recuperar el estado.
- D1 almacena los datos cifrados. La copia anterior de `bot-state` también está cifrada. El administrador que tiene la clave puede acceder al contenido; los colaboradores que modifican el workflow son administradores de confianza.
- `/desconectar` borra el estado activo y la cola anterior de ese alumno. No purga las copias de recuperación de D1 ni el historial Git anterior a la migración. Revocar el token en Canvas anula su uso aunque exista una copia cifrada antigua.
- Para una copia manual cifrada, ejecuta `python scripts/cloud_admin.py backup`. Se guarda en `data/cloud-backup.enc`, ignorado por Git; conserva también la clave en un lugar seguro. D1 Free dispone de Time Travel de 7 días en su panel.
- Telegram conserva/procesa los mensajes conforme a su servicio. El borrado automático es una reducción de exposición, no una garantía de que el token nunca haya sido copiado.
- El bot no almacena los updates completos ni registra el texto del mensaje que contiene el token. Rechaza enlaces de paginación a otros dominios y no sigue redirecciones de Canvas con credenciales.
- Para terminar la práctica, desactiva el workflow, pide que se revoquen los tokens, elimina los Secrets y gestiona las copias de estado según lo acordado con la clase.

## 14. IA futura y migración a OAuth

`src/ai.py` define una interfaz para resumir, explicar y estimar duración. La implementación base devuelve texto de la tarea y no inventa dificultad ni tiempo. El bot no llama a un proveedor de IA, no necesita una clave de IA y no envía tareas a uno.

Para pasar del modo de prueba al uso multiusuario habitual, será necesario implementar autorización OAuth con una Developer Key del centro, callback HTTPS, vinculación segura con Telegram y renovación de access tokens mediante refresh tokens. La capa de Canvas acepta un bearer token; el almacenamiento, la lógica de tareas y los avisos pueden reutilizarse. `TEST_MODE=false` desactiva tanto el registro manual como la consulta de estas cuentas de prueba; **no activa una implementación OAuth inexistente**.
