# NetLab · laboratorio de automatización de redes por API

Un ingeniero de redes que no domina HTTP no puede automatizar nada: no entiende
por qué falla un script de Ansible, no sabe leer un `401` ni distinguirlo de un
`403`, y no puede consumir la API de un controlador real. NetLab ataca ese
vacío: simula cuatro dispositivos Cisco y los expone por una API REST completa,
con una interfaz visual animada, para que los estudiantes apaguen puertos,
creen VLANs y reinicien equipos usando únicamente `curl` y Python, viendo el
efecto de cada comando proyectado en tiempo real.

**Audiencia:** docentes y estudiantes de redes (nivel CCNA o similar) con manejo
básico de terminal. No se requiere experiencia en programación.

Todo el laboratorio es **un solo archivo de Python sin dependencias**: no hay
`pip install`, no hay base de datos, no hay framework. Corre en cualquier
computador con Python 3.8 o superior, en Windows, macOS o Linux.

## Cómo correrlo

### Opción 1: solo Python (lo más simple)

Windows (CMD o PowerShell):

```bat
python app\netlab.py
```

Linux / macOS:

```bash
python3 app/netlab.py
```

Abra <http://localhost:8080/> — ahí están la interfaz visual y la API
(`http://localhost:8080/api/v1`). Para usar otro puerto, defina la variable de
entorno `PORT` antes de arrancar.

### Opción 2: Docker

```bash
docker compose up
```

Igual que la opción 1, pero sin necesidad de tener Python instalado. También
sirve `docker build -t netlab . && docker run -p 8080:8080 netlab`.

### Opción 3: expuesto a internet (para academias)

Si quiere que todo el curso trabaje contra una sola instancia publicada en una
URL, tiene dos caminos:

- **Un servidor con Docker:** ejecute `docker compose up -d` en cualquier VPS
  y ponga delante su proxy inverso habitual (Caddy, nginx, Traefik) con TLS.
  No hay más piezas: la aplicación es un único contenedor sin volúmenes.
- **Kubernetes:** los manifiestos están en `k8s/` y el script
  `k8s/desplegar.sh` los aplica con su dominio:

  ```bash
  NETLAB_DOMINIO=netlab.mi-academia.edu ./k8s/desplegar.sh
  ```

**Antes de exponerlo, cambie las claves.** Las claves de los ejemplos
(`netlab-admin-2026` y `netlab-lectura-2026`) son públicas porque están en la
guía. Defina las suyas con variables de entorno:

```bash
NETLAB_ADMIN_KEY=mi-clave-secreta NETLAB_READONLY_KEY=mi-clave-lectura python3 app/netlab.py
```

Tenga en cuenta que es un laboratorio didáctico, no un servicio productivo: el
estado vive en memoria (se pierde al reiniciar, lo cual aquí es una ventaja),
las lecturas GET son abiertas a propósito y no hay registro de auditoría.

## Contenido del repositorio

| Ruta | Qué es |
|---|---|
| `guia/GUIA-LAB-CURL.md` | Guía del laboratorio: anatomía de un comando `curl`, 11 bloques de ejercicios con comandos para Windows (CMD) y Linux/macOS, retos y tablas de referencia |
| `cliente/netlab_cli.py` | Cliente interactivo en Python; 15 opciones de menú y un recorrido automático para proyectar |
| `app/netlab.py` | La API y el simulador de dispositivos (un solo archivo, librería estándar) |
| `app/ui.html` | La interfaz visual: topología, panel de puertos, registro HTTP en vivo |
| `material/` | Diapositivas y material de apoyo de la sesión de formatos de datos (JSON, XML, YAML) |
| `Dockerfile`, `docker-compose.yml` | Ejecución en contenedor |
| `k8s/` | Manifiestos y script para publicarlo en un cluster (opcional) |

## Cómo dar la clase

1. Arranque el laboratorio y proyecte <http://localhost:8080/> (o la URL
   pública si lo desplegó en internet). Cada comando de los estudiantes aparece
   en **Registro HTTP de la clase** y su efecto se ve en la topología.
2. Abra el recorrido guiado para la introducción:
   ```bash
   python3 cliente/netlab_cli.py --demo
   ```
   Doce pasos con pausa entre cada uno, desde el inventario hasta la auditoría.
3. Reparta `guia/GUIA-LAB-CURL.md`. Los bloques 0 a 9 toman unos 90 minutos.
4. Si el laboratorio queda desordenado, reinícielo desde el botón de la
   interfaz o con:
   ```bash
   curl -X POST -H "X-API-Key: netlab-admin-2026" http://localhost:8080/api/v1/reset
   ```

### Trabajo por equipos

Con 30 estudiantes sobre la misma red los cambios se pisan. La cabecera
`X-Lab-Team` da a cada equipo una copia privada y completa de los cuatro
dispositivos:

```bash
curl -H "X-Lab-Team: rojo" http://localhost:8080/api/v1/devices
python3 cliente/netlab_cli.py --equipo rojo
```

En la interfaz visual, el selector **Equipo** de la cabecera cambia lo que se
muestra; también acepta `?team=rojo` en la URL. El desorden compartido resulta
didáctico al principio, así que conviene empezar en `shared` y separar por
equipos cuando lleguen a los retos finales.

## Qué enseña la API

Cubre deliberadamente todo el temario de HTTP con casos reales de red:

- **Verbos:** `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`.
- **Códigos:** `200`, `201`, `202`, `204`, `302`, `304`, `400`, `401`, `403`,
  `404`, `405`, `409`, `412`, `415`, `422`, `429`.
- **Cabeceras:** `X-API-Key`, `Content-Type`, `Accept`, `Location`, `ETag`,
  `If-Match`, `If-None-Match`, `Allow`, `Retry-After`, `WWW-Authenticate`.
- **Conceptos:** idempotencia, negociación de contenido, caché condicional,
  concurrencia optimista, límite de peticiones, CORS y preflight.

Los enlaces entre dispositivos están simulados de verdad: apagar `Te1/1/1` en el
core tumba `Gi0/1` en `sw-access-01`, lo que hace visible el impacto de un cambio.
Hay endpoints didácticos bajo `/api/v1/demo/` (`echo`, `status/{código}`,
`slow`, `rate-limit`, `redirect`) para provocar cualquier comportamiento a
voluntad.

## Arquitectura y decisiones

- **Solo librería estándar de Python.** Sin `pip install` ni imágenes propias
  que construir, el laboratorio es inmune a fallos de red o de registro de
  contenedores el día de la clase, al precio de renunciar a un framework y su
  documentación automática. Para un aula, esa robustez vale más.
- **Estado en memoria y una sola réplica.** Con dos réplicas cada petición
  caería en un mundo distinto y la clase vería datos incoherentes. Reiniciar el
  proceso devuelve el laboratorio al estado inicial, que es justo lo que un
  docente necesita entre sesiones.
- **Sondeo silencioso.** La interfaz refresca con
  `GET /api/v1/topology?silent=1` cada 1,5 segundos. El parámetro `silent`
  evita que ese sondeo, y las sondas de salud de Docker o Kubernetes, ensucien
  el registro que se proyecta a los estudiantes.

## Operación en Kubernetes (solo si usó la opción 3)

```bash
kubectl logs -n netlab -l app=netlab-api -f              # ver registros
kubectl rollout restart deploy/netlab-api -n netlab      # reiniciar y limpiar estado
kubectl delete namespace netlab                          # borrar todo al terminar
```

Para editar el código y volver a publicarlo basta modificar `app/netlab.py` o
`app/ui.html` y ejecutar de nuevo `desplegar.sh`: el script regenera el
ConfigMap y reinicia el pod por cambio de hash. Al terminar el curso, borre
también el registro DNS que haya creado.

## Licencia

[MIT](LICENSE). Úselo, modifíquelo y adáptelo a su curso libremente.
