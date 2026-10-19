# Seguridad y uso responsable

## Alcance

Este repositorio es una práctica docente de Canvas LMS y Telegram para una clase de hasta 15 alumnos. La revisión del código y las pruebas reducen riesgos; no garantizan ausencia de vulnerabilidades ni equivalen a una auditoría independiente de la instalación.

La implementación utiliza tokens manuales de Canvas en modo de prueba. Para pasar a una aplicación multiusuario de producción hay que implementar OAuth con autorización del centro. [Documentación oficial de Canvas](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth).

## Controles incluidos

- Webhook autenticado mediante un secreto independiente del acceso administrativo al estado.
- Chats privados y comprobación de que el remitente coincide con el propietario del chat.
- Lista de alumnos y/o invitación de clase; las altas nuevas se cierran si faltan ambas.
- Estado cifrado y autenticado con Fernet, compatible entre Python y JavaScript.
- Datos separados por alumno y escrituras condicionadas a la versión del estado.
- Validación del destino de las consultas a Canvas y bloqueo de redirecciones con credenciales.
- Confirmación explícita para entregar, validación de la tarea y comprobación del resultado.
- Comprobación de tamaños y nombres de documentos; límites de descompresión y procesamiento.
- Secretos disponibles únicamente en los pasos de ejecución que los necesitan, no durante la instalación de dependencias.
- Subproceso de documentos con entorno mínimo, Python aislado y directorio temporal. En Linux se añaden límites de CPU, memoria y archivo.
- Acciones de CI fijadas por commit, Dependabot y pruebas sin secretos en pull requests.

## Qué sigue siendo responsabilidad del administrador

1. **Restringir la clase.** Configura `ALLOWED_TELEGRAM_USER_IDS` y/o `CLASS_INVITE_CODE`. Para una clase fija, la lista de IDs es más restrictiva que una invitación que puede reenviarse. La configuración del Worker está cifrada en D1: cambiar un Secret de GitHub no actualiza esa configuración. Sigue `configure` en la [guía](docs/GUIA.md) sin reinicializar el estado.
2. **Desplegar el Worker actualizado.** Publicar cambios en GitHub no actualiza Cloudflare automáticamente. El flujo horario sí instala las dependencias del repositorio en su siguiente ejecución.
3. **Proteger las cuentas.** Usa segundo factor y limita colaboradores con permiso de escritura: pueden cambiar código que tiene acceso a secretos en ejecuciones posteriores.
4. **Guardar las claves fuera de Git.** Nunca publiques `.env`, SQL de configuración, copias de estado, tokens ni la clave de cifrado. Un identificador D1 no es una credencial de acceso.
5. **Tratar las copias antiguas como datos sensibles.** La rama pública histórica `bot-state` contiene copias cifradas. `/desconectar` no las elimina del historial. Si la clave se filtra, esas copias pueden descifrarse; revoca los tokens afectados y planifica el cambio de clave y la gestión del historial.
6. **Mantener dependencias y cuotas.** Dependabot propone actualizaciones; hay que revisarlas. El repositorio público no elimina los límites ni los riesgos de saturación de Telegram, Canvas, Workers y Actions.

## Límites importantes

- El administrador con las claves puede descifrar las credenciales y los datos guardados.
- Telegram y Canvas conservan los mensajes y archivos conforme a sus políticas. Intentar borrar un token del chat no garantiza que nunca se haya copiado.
- El proceso de documentos no es una sandbox completa del sistema operativo: comparte el usuario y el equipo con el proceso padre. El entorno mínimo reduce la exposición, pero una vulnerabilidad nativa de un parser requiere aislamiento adicional, por ejemplo un contenedor sin red y sin secretos montados, antes de aceptar documentos de desconocidos.
- No hay un limitador global persistente por alumno. La actualización manual tiene espera mínima; otros comandos pueden consumir cuotas si alguien autorizado abusa de ellos. El acceso debe mantenerse restringido a la práctica.
- Las pruebas se realizan con datos sintéticos. No certifican permisos, reglas de red, configuración efectiva ni sesiones activas en las cuentas de GitHub, Cloudflare, Canvas o Telegram.

## Comunicar una incidencia

Escribe en privado a **[abrahamamar26@gmail.com](mailto:abrahamamar26@gmail.com)** indicando el componente y los pasos mínimos para reproducirla con datos ficticios. No publiques tokens, datos de alumnos, documentos reales ni pruebas contra la instalación de clase en una issue pública.

Si hay una filtración real, revoca primero la credencial afectada, conserva únicamente evidencia sin secretos y revisa las copias e instalaciones donde se utilizó. Eliminar una línea de Git no revoca un token.

## Referencias

- [Seguridad de GitHub Actions](https://docs.github.com/en/actions/reference/security/secure-use).
- [Secretos del webhook de Telegram](https://core.telegram.org/bots/api#setwebhook).
- [Autorización OAuth de Canvas](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth).
