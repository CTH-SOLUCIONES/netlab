# Registro de cambios

Todos los cambios notables de NetLab se anotan aquí. El formato sigue
*Keep a Changelog* y el versionado sigue *Versionado Semántico*. Las fechas van
en DD/MM/AAAA.

## [1.0.0] - 17/11/2026

Primera versión numerada. Antes, la imagen se publicaba solo con las etiquetas
`latest` (que sigue la rama `main`) y `sha-<commit>`.

### Cambiado

Estos cambios afectan a quien ya tenía NetLab funcionando.

- **Una clave inválida ahora da `401`, no `403`.** `401` (con `WWW-Authenticate`)
  significa que la clave falta o no es válida; `403` queda para una clave válida
  que no alcanza, es decir, la de solo lectura en un método de escritura. Un
  script que trataba el `403` como «clave inválida» debe revisarse.
- **Tope de 50 equipos (`X-Lab-Team`) por proceso.** `shared` cuenta. Un equipo
  nuevo por encima del tope recibe `403` con el código `limite_de_equipos`.
  Además, una petición que falla (404, 405, 401…) ya no crea el mundo de su
  equipo, y al restaurar desde SQLite se cargan como máximo 50.
- **La clave de administrador ya no va dentro de la interfaz, salvo que sea la de
  fábrica.** Con una clave propia (`NETLAB_ADMIN_KEY`), la interfaz la pide la
  primera vez que hace falta y la guarda en `sessionStorage`; el `curl` de
  ejemplo muestra `$K`. Con la clave de fábrica, la interfaz funciona como antes.
- **Aviso al arrancar** si alguna de las dos claves es la de fábrica.
- **Kubernetes:** `k8s/desplegar.sh` crea el secreto `netlab-claves` con claves
  aleatorias si no existe y las imprime una sola vez; `k8s/netlab.yaml` lo usa.
- **Imagen versionada.** Además de `latest`, al publicar la etiqueta `v1.0.0`
  existen `ghcr.io/cth-soluciones/netlab:1.0.0` y `:1.0`. Para una clase, fije
  la versión.
- **Mensajes de la API:** «superó» pasa a «supero», para que todos los mensajes
  sigan sin tildes.
- **Conexiones:** cada conexión tiene un tiempo límite de 30 segundos sin
  actividad y el servidor atiende como máximo 200 a la vez; por encima, cierra
  la conexión.

### Corregido

- **`204` y `304` sin `Content-Length`**, como exige HTTP. El `304` lleva `ETag`
  y el mismo `Cache-Control` que llevaría el `200`.
- **`Allow` coherente:** `HEAD` se anuncia solo si `GET` está permitido, y las
  rutas de acción (`…/shutdown`, `…/no-shutdown`) ya no se confunden con una
  interfaz.
- **Cuerpo sin leer en una conexión persistente.** Si el servidor responde sin
  haber leído el cuerpo de la petición, cierra la conexión (`Connection: close`);
  antes el resto del cuerpo podía leerse como una petición nueva.
- **Memoria del límite de peticiones:** la dirección de `X-Forwarded-For` se
  recorta a 45 caracteres y el registro del límite descarta las entradas que ya
  salieron de la ventana.
- **Interfaz:** el registro de la clase ya no parpadea y el sondeo de topología
  no usa caché.

### Seguridad

- **XSS almacenado en la interfaz.** La función `esc` no escapaba la comilla
  simple, y una descripción de interfaz con una comilla podía inyectar atributos
  HTML. Ahora escapa `& < > " '`.

### Añadido

- Pruebas de CI con `unittest` de la librería estándar, que corren antes de
  publicar la imagen. Los guiones de la interfaz con Playwright quedan como
  pruebas locales.
- Captura de la interfaz y aviso de marcas en el `README`, y etiquetas Open Graph
  en la página de GitHub Pages.

### Documentación

- La guía enseña el `ETag` guardándolo en una variable (`ET`), en lugar de pedir
  copiar las comillas internas, y muestra solo la línea `ETag`.
- La guía aclara que los bloques de Windows son de CMD y que en PowerShell hay
  que abrir CMD; corrige la tabla de errores, el reto 2 y varios detalles de
  redacción.
- El `README` explica el tope de equipos, el comportamiento de la clave en la
  interfaz, por qué la API responde sin tildes y cómo fijar la versión de la
  imagen.
