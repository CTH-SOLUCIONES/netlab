# Laboratorio: gestión de dispositivos de red por API REST

## Por qué este laboratorio

Un administrador de red que configura 200 switches a mano invierte semanas en un
cambio y comete errores que nadie puede auditar. El mismo cambio hecho por API se
aplica en minutos, queda registrado y se puede revertir. El obstáculo real no es
el equipo Cisco: es que el ingeniero de redes no domina HTTP, y sin eso no puede
automatizar nada ni entender por qué falla un script de Ansible o de Python.

Este laboratorio ataca ese vacío. Usted va a apagar puertos, crear VLANs y
reiniciar dispositivos usando únicamente HTTP, y va a ver el efecto de cada
comando en una topología proyectada en tiempo real.

**Audiencia:** ingenieros y técnicos de redes con conocimiento de Cisco IOS
(`shutdown`, `vlan`, `switchport`) y manejo básico de terminal. No se requiere
experiencia previa en programación.

**Duración estimada:** 90 minutos para los bloques 0 al 9; los retos finales
quedan como práctica.

**Al terminar usted será capaz de:**

- Leer y escribir cualquier comando `curl` identificando sus partes.
- Elegir el verbo HTTP correcto (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`).
- Diagnosticar un fallo de integración leyendo el código de estado y las cabeceras.
- Explicar la diferencia entre `401` y `403`, entre `PUT` y `PATCH`, y entre
  `400` y `422`, que son las confusiones que más tiempo cuestan en producción.

---

## El laboratorio

| | |
|---|---|
| Interfaz visual | <http://localhost:8080/> |
| Base de la API | `http://localhost:8080/api/v1` |
| Clave de escritura | `netlab-admin-2026` |
| Clave de solo lectura | `netlab-lectura-2026` (para el ejercicio de `403`) |

Esta guía asume que el laboratorio corre en su propio computador (vea el
`README.md`: `python3 app/netlab.py` o `docker compose up`). Si su instructor
lo publicó en internet, use la dirección que le hayan dado en lugar de
`http://localhost:8080`; todos los comandos son iguales.

Mantenga la interfaz visual abierta (o mírela en el proyector). Cada comando que
usted ejecute aparecerá en el panel **Registro HTTP de la clase** y su efecto se
verá en la topología.

### Dispositivos simulados

| Identificador | Modelo | Rol | Notas |
|---|---|---|---|
| `rtr-wan-01` | ISR 4331 | Router de borde | No maneja VLANs de capa 2 |
| `csr-core-01` | Catalyst 9300-48P | Core | Troncales hacia los dos switches de acceso |
| `sw-access-01` | Catalyst 2960X-24TS | Acceso piso 3 | Puertos de usuario |
| `sw-access-02` | Catalyst 9200L-24P | Acceso piso 4 | Puertos de usuario |

Los enlaces están conectados de verdad en el simulador: si usted apaga
`Te1/1/1` en el core, el puerto `Gi0/1` de `sw-access-01` se cae también. Eso es
lo que hace visible el impacto de un cambio.

### Requisitos

Solo necesita `curl`, que ya viene instalado en macOS, en Linux y en Windows 10
(actualización 1803, de 2018) o superior. Verifíquelo:

```
curl --version
```

> **Nota para PowerShell:** en PowerShell, `curl` es un alias de
> `Invoke-WebRequest`, que usa otra sintaxis. Escriba `curl.exe` (con el `.exe`)
> y siga las instrucciones de Windows CMD de esta guía, o simplemente abra CMD
> (`cmd` en el menú inicio), que es lo que esta guía usa para Windows.

### Cómo leer esta guía en dos sistemas

Cada ejercicio aparece dos veces: primero para **Linux / macOS** (también sirve
en WSL y Git Bash) y luego para **Windows CMD**. La petición HTTP que viaja por
la red es idéntica; lo único que cambia es cómo cada terminal trata las
comillas y las variables:

| | Linux / macOS (bash) | Windows (CMD) |
|---|---|---|
| Definir variable | `B=http://localhost:8080/api/v1` | `set "B=http://localhost:8080/api/v1"` |
| Usar variable | `$B` | `%B%` |
| Cuerpo JSON | `-d '{"id": 99}'` | `-d "{\"id\": 99}"` |
| Descartar salida | `-o /dev/null` | `-o NUL` |
| Comando largo en varias líneas | termina la línea con `\` | termina la línea con `^` (o escríbalo en una sola línea) |

> **Si escribe un archivo `.bat`:** duplique los signos de porcentaje que no
> sean variables suyas (`%%{http_code}`, y `%%i` en los bucles `for`). En la
> ventana interactiva de CMD van sencillos, como aparecen aquí.

---

## Anatomía de un comando curl

Todo comando `curl` de este laboratorio se compone de cinco partes. Entender
estas cinco partes es el objetivo central de la clase, porque son las mismas
cinco partes de cualquier petición HTTP, sin importar si la escribe usted a mano,
la genera Python o la envía Ansible.

```
curl  -i   -X POST   http://localhost:8080/api/v1/devices/csr-core-01/vlans   -H "X-API-Key: netlab-admin-2026"   -H "Content-Type: application/json"   -d '{"id": 99, "name": "LABORATORIO"}'
 │    │        │                          │                                    │                             │
 1    2        3                          4                                    5                             6
```

| # | Parte | Qué es | Para qué sirve |
|---|---|---|---|
| 1 | `curl` | El programa cliente | Abre la conexión y envía la petición |
| 2 | `-i` | Opción de curl | Muestra las cabeceras de **respuesta** además del cuerpo |
| 3 | `-X POST` | **Método** o verbo | Declara la *intención*: leer, crear, reemplazar, modificar o borrar |
| 4 | La URL | **Recurso** | Dice *sobre qué* actúa. Aquí: las VLANs del dispositivo `csr-core-01` |
| 5 | `-H "..."` | **Cabeceras** | Metadatos: quién soy, qué formato envío, condiciones |
| 6 | `-d '...'` | **Cuerpo** | Los datos que se envían. Solo en `POST`, `PUT` y `PATCH` |

### Cómo se lee la URL

```
http :// localhost:8080 /api/v1 /devices/csr-core-01/vlans ?limit=5
  │            │             │              │                  │
esquema      host        versión         ruta al           parámetros
          (qué servidor) de la API       recurso           de consulta
```

Cuando el laboratorio está publicado en internet, el esquema es `https`
(cifrado) y el host es un dominio, por ejemplo `netlab.mi-academia.edu`; la
estructura no cambia.

La ruta se lee de lo general a lo particular, como un árbol de carpetas:
`/devices` (todos los dispositivos) → `/devices/csr-core-01` (uno concreto) →
`/devices/csr-core-01/vlans` (las VLANs de ese dispositivo) →
`/devices/csr-core-01/vlans/99` (una VLAN concreta).

> **Regla práctica:** la URL identifica **el sustantivo** (el recurso), el método
> HTTP expresa **el verbo** (la acción). Si usted se descubre escribiendo
> `/crearVlan` en la URL, está poniendo el verbo en el lugar equivocado.

### Las opciones de curl que se usan en la clase

| Opción | Efecto | Cuándo usarla |
|---|---|---|
| `-i` | Muestra cabeceras de respuesta y cuerpo | Casi siempre en este laboratorio |
| `-I` | Muestra **solo** cabeceras (envía `HEAD`) | Para consultar sin descargar |
| `-X <VERBO>` | Fija el método | Con todo lo que no sea `GET` |
| `-H "clave: valor"` | Agrega una cabecera | Autenticación, tipo de contenido |
| `-d '<json>'` | Envía un cuerpo | Al crear o modificar |
| `-s` | Modo silencioso, sin barra de progreso | Al encadenar con otros programas |
| `-v` | Muestra todo el diálogo, incluida la petición enviada | Para depurar |
| `-L` | Sigue las redirecciones | Cuando la respuesta es `301` o `302` |
| `--max-time <s>` | Aborta si tarda demasiado | Para practicar tiempos de espera |
| `-w "%{http_code}"` | Imprime un dato concreto al final | Para scripts |

### Sobre las comillas

Es el error más frecuente de la clase, y la regla depende de su terminal. El
JSON exige **comillas dobles** por especificación; el problema es lograr que la
terminal se las entregue intactas a curl.

**Linux / macOS:** envuelva el JSON en comillas **simples**; dentro de ellas la
terminal no toca nada.

```bash
-d '{"id": 99, "name": "LABORATORIO"}'    # correcto
-d "{"id": 99}"                           # incorrecto: la terminal rompe el JSON
-d '{id: 99}'                             # incorrecto: JSON exige comillas en las claves
```

**Windows CMD:** las comillas simples no existen para CMD. Envuelva el JSON en
comillas **dobles** y anteponga `\` a cada comilla doble interna.

```bat
-d "{\"id\": 99, \"name\": \"LABORATORIO\"}"    &:: correcto
-d '{"id": 99}'                                 &:: incorrecto: CMD no entiende comillas simples
-d "{"id": 99}"                                 &:: incorrecto: sin escapar, el JSON llega roto
```

---

## Bloque 0. Verificar que hay señal

**Linux / macOS:**

```bash
curl -i http://localhost:8080/api/v1/health
```

**Windows (CMD):**

```bat
curl -i http://localhost:8080/api/v1/health
```

Debe ver `HTTP/1.1 200` en la primera línea (`HTTP/2 200` si el laboratorio
está publicado detrás de un proxy). Ese `200` es el acuse de recibo de HTTP:
*entendí y aquí está lo que pediste*.

Ahora pida el índice de la API. Una API bien hecha se explica a sí misma:

```
curl -s http://localhost:8080/api/v1
```

> **Sugerencia:** guarde la base en una variable y ahorre escritura durante toda
> la sesión. Los ejemplos siguientes usan la variable.
>
> **Linux / macOS:**
> ```bash
> B=http://localhost:8080/api/v1
> curl -s $B/health
> ```
>
> **Windows (CMD):**
> ```bat
> set "B=http://localhost:8080/api/v1"
> curl -s %B%/health
> ```
>
> La variable dura mientras la ventana de la terminal esté abierta. Si abre
> otra ventana, defínala de nuevo.

---

## Bloque 1. GET: leer el estado de la red

`GET` pide información y **no modifica nada**. Es seguro repetirlo; por eso no
necesita clave en este laboratorio.

**1.1 Inventario completo**

**Linux / macOS:**

```bash
curl -s $B/devices
```

**Windows (CMD):**

```bat
curl -s %B%/devices
```

**1.2 Un dispositivo concreto**

**Linux / macOS:**

```bash
curl -s $B/devices/csr-core-01
```

**Windows (CMD):**

```bat
curl -s %B%/devices/csr-core-01
```

**1.3 Sus interfaces**

**Linux / macOS:**

```bash
curl -s $B/devices/csr-core-01/interfaces
```

**Windows (CMD):**

```bat
curl -s %B%/devices/csr-core-01/interfaces
```

**1.4 Filtrar con parámetros de consulta**

Los parámetros van después de `?` y se unen con `&`. Sirven para *afinar* una
consulta, nunca para cambiar datos.

**Linux / macOS:**

```bash
curl -s "$B/devices/csr-core-01/interfaces?status=down"
curl -s "$B/devices/sw-access-01/interfaces?mode=access&vlan=10"
curl -s "$B/devices?role=access"
curl -s "$B/devices?fields=id,hostname,status&limit=2"
```

**Windows (CMD):**

```bat
curl -s "%B%/devices/csr-core-01/interfaces?status=down"
curl -s "%B%/devices/sw-access-01/interfaces?mode=access&vlan=10"
curl -s "%B%/devices?role=access"
curl -s "%B%/devices?fields=id,hostname,status&limit=2"
```

> **Importante en ambos sistemas:** encierre la URL en comillas dobles cuando
> lleve `?` o `&`. Sin comillas, bash interpreta el `&` como "ejecuta en segundo
> plano" y CMD lo interpreta como separador de comandos; en los dos casos la
> petición llega mutilada.

**1.5 Dos formas de nombrar la misma interfaz**

Ambas funcionan, igual que en IOS:

**Linux / macOS:**

```bash
curl -s $B/devices/csr-core-01/interfaces/GigabitEthernet1/0/10
curl -s $B/devices/csr-core-01/interfaces/Gi1/0/10
```

**Windows (CMD):**

```bat
curl -s %B%/devices/csr-core-01/interfaces/GigabitEthernet1/0/10
curl -s %B%/devices/csr-core-01/interfaces/Gi1/0/10
```

**Pregunta para el grupo:** ¿cuántas interfaces caídas hay en `sw-access-01` y
por qué el uplink `Gi0/1` depende de un puerto que está en otro dispositivo?

---

## Bloque 2. Cabeceras: el mismo recurso en dos formatos

La cabecera `Accept` le dice al servidor **en qué formato** quiere la respuesta.
El recurso es el mismo; cambia la representación.

**Linux / macOS:**

```bash
# Como la vería un ingeniero: texto plano, igual que en la consola del switch
curl -s $B/devices/csr-core-01/config

# Como la consumiría un programa
curl -s -H "Accept: application/json" $B/devices/csr-core-01/config
```

**Windows (CMD):**

```bat
curl -s %B%/devices/csr-core-01/config
curl -s -H "Accept: application/json" %B%/devices/csr-core-01/config
```

Compare las cabeceras de respuesta de ambos y observe el `Content-Type`:

**Linux / macOS:**

```bash
curl -s -o /dev/null -w "%{content_type}\n" $B/devices/csr-core-01/config
curl -s -o /dev/null -w "%{content_type}\n" -H "Accept: application/json" $B/devices/csr-core-01/config
```

**Windows (CMD):**

```bat
curl -s -o NUL -w "%{content_type}\n" %B%/devices/csr-core-01/config
curl -s -o NUL -w "%{content_type}\n" -H "Accept: application/json" %B%/devices/csr-core-01/config
```

---

## Bloque 3. Autenticación: 401 y 403 no son lo mismo

Confundir estos dos códigos hace perder horas de diagnóstico. La diferencia es
precisa:

- **401 Unauthorized** — *no sé quién es usted*. Falta la credencial o no se envió.
- **403 Forbidden** — *sé quién es usted, y no le alcanza*. La credencial es
  válida pero el permiso no.

**3.1 Sin credencial: 401**

**Linux / macOS:**

```bash
curl -i -X POST $B/devices/csr-core-01/interfaces/Te1/1/1/shutdown
```

**Windows (CMD):**

```bat
curl -i -X POST %B%/devices/csr-core-01/interfaces/Te1/1/1/shutdown
```

Observe la cabecera `WWW-Authenticate` en la respuesta: el servidor le está
indicando *cómo* debe autenticarse.

**3.2 Con credencial válida pero sin permiso: 403**

**Linux / macOS:**

```bash
curl -i -X POST -H "X-API-Key: netlab-lectura-2026" \
  $B/devices/csr-core-01/interfaces/Te1/1/1/shutdown
```

**Windows (CMD):**

```bat
curl -i -X POST -H "X-API-Key: netlab-lectura-2026" %B%/devices/csr-core-01/interfaces/Te1/1/1/shutdown
```

**3.3 Con credencial de escritura: 200**

**Linux / macOS:**

```bash
curl -i -X POST -H "X-API-Key: netlab-admin-2026" \
  $B/devices/csr-core-01/interfaces/Te1/1/1/shutdown
```

**Windows (CMD):**

```bat
curl -i -X POST -H "X-API-Key: netlab-admin-2026" %B%/devices/csr-core-01/interfaces/Te1/1/1/shutdown
```

**Mire la topología.** El enlace hacia `sw-access-01` acaba de caer, y en el
panel de eventos aparecen dos entradas: el `shutdown` en el core y la caída del
puerto vecino. Un solo comando HTTP, dos dispositivos afectados.

Vuelva a levantarlo:

**Linux / macOS:**

```bash
curl -i -X POST -H "X-API-Key: netlab-admin-2026" \
  $B/devices/csr-core-01/interfaces/Te1/1/1/no-shutdown
```

**Windows (CMD):**

```bat
curl -i -X POST -H "X-API-Key: netlab-admin-2026" %B%/devices/csr-core-01/interfaces/Te1/1/1/no-shutdown
```

> Para no repetir la clave en cada comando, guárdela también. Los ejemplos
> siguientes usan esta variable.
>
> **Linux / macOS:**
> ```bash
> K="X-API-Key: netlab-admin-2026"
> curl -i -X POST -H "$K" $B/devices/csr-core-01/interfaces/Te1/1/1/shutdown
> ```
>
> **Windows (CMD):**
> ```bat
> set "K=X-API-Key: netlab-admin-2026"
> curl -i -X POST -H "%K%" %B%/devices/csr-core-01/interfaces/Te1/1/1/shutdown
> ```

---

## Bloque 4. POST: crear una VLAN

`POST` crea un recurso nuevo. La respuesta correcta no es `200` sino **`201
Created`**, acompañada de una cabecera `Location` con la dirección de lo que
acaba de nacer.

**Linux / macOS:**

```bash
curl -i -X POST -H "$K" -H "Content-Type: application/json" \
  -d '{"id": 99, "name": "LABORATORIO"}' \
  $B/devices/csr-core-01/vlans
```

**Windows (CMD):**

```bat
curl -i -X POST -H "%K%" -H "Content-Type: application/json" -d "{\"id\": 99, \"name\": \"LABORATORIO\"}" %B%/devices/csr-core-01/vlans
```

Fíjese en tres cosas de la respuesta:

1. El código `201`, no `200`.
2. La cabecera `Location: /api/v1/devices/csr-core-01/vlans/99`.
3. En la topología, la VLAN 99 aparece con una animación en la tarjeta del core.

Consulte lo que creó, siguiendo la dirección que le dio el servidor:

**Linux / macOS:**

```bash
curl -s $B/devices/csr-core-01/vlans/99
```

**Windows (CMD):**

```bat
curl -s %B%/devices/csr-core-01/vlans/99
```

**4.1 Por qué `Content-Type` es obligatorio**

Omítalo a propósito:

**Linux / macOS:**

```bash
curl -i -X POST -H "$K" -d '{"id": 88, "name": "PRUEBA"}' $B/devices/csr-core-01/vlans
```

**Windows (CMD):**

```bat
curl -i -X POST -H "%K%" -d "{\"id\": 88, \"name\": \"PRUEBA\"}" %B%/devices/csr-core-01/vlans
```

Obtiene **`415 Unsupported Media Type`**. Cuando usa `-d`, curl envía por
defecto `application/x-www-form-urlencoded` (formato de formulario web), no JSON.
El servidor no adivina: exige que usted declare el formato. Este es el error
número uno al integrar una API por primera vez.

**4.2 Los errores que va a cometer en producción**

| Cuerpo enviado | Código | Por qué |
|---|---|---|
| `{"id": 10, "name": "REPETIDA"}` | `409` | La VLAN 10 ya existe: conflicto con el estado actual |
| `{"id": "diez"}` | `422` | JSON válido, pero `id` debe ser número |
| `{"id": 97, "name": "con espacio"}` | `422` | IOS no admite espacios en el nombre |
| `{"id": 4095}` | `422` | Fuera del rango 1–4094 |
| `{roto` | `400` | Ni siquiera es JSON |
| VLAN sobre `rtr-wan-01` | `409` | Un router no administra VLANs de capa 2 |

Pruebe al menos tres:

**Linux / macOS:**

```bash
curl -i -X POST -H "$K" -H "Content-Type: application/json" \
  -d '{"id": 10, "name": "REPETIDA"}' $B/devices/csr-core-01/vlans

curl -i -X POST -H "$K" -H "Content-Type: application/json" \
  -d '{"id": "diez"}' $B/devices/csr-core-01/vlans

curl -i -X POST -H "$K" -H "Content-Type: application/json" \
  -d '{roto' $B/devices/csr-core-01/vlans
```

**Windows (CMD):**

```bat
curl -i -X POST -H "%K%" -H "Content-Type: application/json" -d "{\"id\": 10, \"name\": \"REPETIDA\"}" %B%/devices/csr-core-01/vlans

curl -i -X POST -H "%K%" -H "Content-Type: application/json" -d "{\"id\": \"diez\"}" %B%/devices/csr-core-01/vlans

curl -i -X POST -H "%K%" -H "Content-Type: application/json" -d "{roto" %B%/devices/csr-core-01/vlans
```

> **`400` frente a `422`:** `400` significa *no entendí lo que me enviaste* (el
> texto no es JSON válido). `422` significa *lo entendí perfectamente, pero los
> datos no tienen sentido*. Lea el campo `detalle.errors` de la respuesta `422`:
> le dice exactamente qué campo falló y por qué.

---

## Bloque 5. PUT frente a PATCH

Esta distinción es la que más se equivoca, y tiene consecuencias reales: un `PUT`
mal usado borra configuración que usted no quería tocar.

- **`PATCH`** modifica **solo los campos que usted envía**. Lo demás queda igual.
- **`PUT`** **reemplaza el recurso completo**. Lo que no envía, vuelve a su valor
  por defecto.

**5.1 PATCH: un cambio quirúrgico**

**Linux / macOS:**

```bash
curl -i -X PATCH -H "$K" -H "Content-Type: application/json" \
  -d '{"description": "PC-CONTABILIDAD-NUEVA"}' \
  $B/devices/sw-access-01/interfaces/Gi0/3
```

**Windows (CMD):**

```bat
curl -i -X PATCH -H "%K%" -H "Content-Type: application/json" -d "{\"description\": \"PC-CONTABILIDAD-NUEVA\"}" %B%/devices/sw-access-01/interfaces/Gi0/3
```

Solo cambió la descripción. La VLAN, el modo y el estado siguen intactos.

**5.2 PUT incompleto: el servidor lo protege**

**Linux / macOS:**

```bash
curl -i -X PUT -H "$K" -H "Content-Type: application/json" \
  -d '{"description": "solo esto"}' \
  $B/devices/sw-access-01/interfaces/Gi0/3
```

**Windows (CMD):**

```bat
curl -i -X PUT -H "%K%" -H "Content-Type: application/json" -d "{\"description\": \"solo esto\"}" %B%/devices/sw-access-01/interfaces/Gi0/3
```

Devuelve `422`: como `PUT` reemplaza todo, el servidor exige la representación
completa y le avisa que faltan campos obligatorios.

**5.3 PUT completo**

**Linux / macOS:**

```bash
curl -i -X PUT -H "$K" -H "Content-Type: application/json" \
  -d '{"enabled": true, "mode": "access", "vlan": 20, "description": "PUESTO-REEMPLAZADO", "mtu": 1500}' \
  $B/devices/sw-access-01/interfaces/Gi0/3
```

**Windows (CMD):**

```bat
curl -i -X PUT -H "%K%" -H "Content-Type: application/json" -d "{\"enabled\": true, \"mode\": \"access\", \"vlan\": 20, \"description\": \"PUESTO-REEMPLAZADO\", \"mtu\": 1500}" %B%/devices/sw-access-01/interfaces/Gi0/3
```

**5.4 Apagar un puerto con PATCH en lugar de la acción**

Hay dos caminos para el mismo resultado. Este cambia el campo de configuración:

**Linux / macOS:**

```bash
curl -i -X PATCH -H "$K" -H "Content-Type: application/json" \
  -d '{"enabled": false}' \
  $B/devices/sw-access-01/interfaces/Gi0/5
```

**Windows (CMD):**

```bat
curl -i -X PATCH -H "%K%" -H "Content-Type: application/json" -d "{\"enabled\": false}" %B%/devices/sw-access-01/interfaces/Gi0/5
```

Y este invoca la operación con nombre, que es más legible en un guion de trabajo:

**Linux / macOS:**

```bash
curl -i -X POST -H "$K" $B/devices/sw-access-01/interfaces/Gi0/5/shutdown
```

**Windows (CMD):**

```bat
curl -i -X POST -H "%K%" %B%/devices/sw-access-01/interfaces/Gi0/5/shutdown
```

Ambos producen `admin-down`. La respuesta del segundo incluye el campo
`comando_ios` con el equivalente en la consola del switch: útil para explicarle
a un colega qué hizo el script.

**Ejercicio:** mueva el puerto `Gi0/6` de `sw-access-01` a la VLAN 30 y cámbiele
la descripción, en un solo comando. Luego intente moverlo a la VLAN 999 y
explique el `422`.

---

## Bloque 6. DELETE: borrar y sus salvaguardas

**Linux / macOS:**

```bash
curl -i -X DELETE -H "$K" $B/devices/csr-core-01/vlans/99
```

**Windows (CMD):**

```bat
curl -i -X DELETE -H "%K%" %B%/devices/csr-core-01/vlans/99
```

Devuelve **`204 No Content`**: la operación tuvo éxito y no hay nada que
devolver. Un `204` sin cuerpo es correcto y esperado, no un error.

Ahora intente borrar algo que está en uso:

**Linux / macOS:**

```bash
curl -i -X DELETE -H "$K" $B/devices/csr-core-01/vlans/30
```

**Windows (CMD):**

```bat
curl -i -X DELETE -H "%K%" %B%/devices/csr-core-01/vlans/30
```

`409 Conflict`, con la lista de puertos que aún usan esa VLAN. Una API bien
diseñada no le deja dispararse en el pie; le dice qué debe resolver primero.

**Linux / macOS:**

```bash
curl -i -X DELETE -H "$K" $B/devices/csr-core-01/vlans/1     # 409, VLAN 1 protegida
curl -i -X DELETE -H "$K" $B/devices/csr-core-01/vlans/777   # 404, no existe
```

**Windows (CMD):**

```bat
curl -i -X DELETE -H "%K%" %B%/devices/csr-core-01/vlans/1
curl -i -X DELETE -H "%K%" %B%/devices/csr-core-01/vlans/777
```

(El primero da `409`, la VLAN 1 está protegida; el segundo `404`, no existe.)

**Idempotencia:** repita el `DELETE` de la VLAN 99. Ahora da `404`, porque ya no
existe. `DELETE` es idempotente en su efecto (la VLAN sigue borrada), aunque el
código cambie.

---

## Bloque 7. Acciones que no son crear ni borrar

Reiniciar un dispositivo no encaja en crear, leer, modificar ni borrar. Para eso
se modelan operaciones con nombre bajo `/actions/`.

**7.1 Protección contra el descuido**

**Linux / macOS:**

```bash
curl -i -X POST -H "$K" -H "Content-Type: application/json" -d '{}' \
  $B/devices/sw-access-02/actions/reload
```

**Windows (CMD):**

```bat
curl -i -X POST -H "%K%" -H "Content-Type: application/json" -d "{}" %B%/devices/sw-access-02/actions/reload
```

`422`: la operación interrumpe el servicio y exige confirmación explícita.

**7.2 Con confirmación**

**Linux / macOS:**

```bash
curl -i -X POST -H "$K" -H "Content-Type: application/json" -d '{"confirm": true}' \
  $B/devices/sw-access-02/actions/reload
```

**Windows (CMD):**

```bat
curl -i -X POST -H "%K%" -H "Content-Type: application/json" -d "{\"confirm\": true}" %B%/devices/sw-access-02/actions/reload
```

Devuelve **`202 Accepted`**: *recibí la orden, es válida, pero todavía no
terminó*. Distinto de `200`, que significa *ya está hecho*. Mire la topología: el
dispositivo pulsa en ámbar unos nueve segundos y vuelve con el contador de
actividad en cero.

Mientras está reiniciando, intente configurarlo:

**Linux / macOS:**

```bash
curl -i -X PATCH -H "$K" -H "Content-Type: application/json" \
  -d '{"description": "X"}' $B/devices/sw-access-02/interfaces/Gi0/2
```

**Windows (CMD):**

```bat
curl -i -X PATCH -H "%K%" -H "Content-Type: application/json" -d "{\"description\": \"X\"}" %B%/devices/sw-access-02/interfaces/Gi0/2
```

`409`: el dispositivo no acepta cambios en ese estado.

**7.3 Guardar la configuración**

En la tarjeta de cada dispositivo aparece la etiqueta **sin guardar** cuando hay
cambios en memoria volátil, igual que en un equipo real.

**Linux / macOS:**

```bash
curl -i -X POST -H "$K" $B/devices/csr-core-01/actions/save
```

**Windows (CMD):**

```bat
curl -i -X POST -H "%K%" %B%/devices/csr-core-01/actions/save
```

---

## Bloque 8. El catálogo de códigos de estado

Este endpoint devuelve el código que usted le pida, con su significado. Sirve
para fijar el mapa mental:

**Linux / macOS:**

```bash
curl -i $B/demo/status/200
curl -i $B/demo/status/201
curl -i $B/demo/status/301
curl -i $B/demo/status/404
curl -i $B/demo/status/418
curl -i $B/demo/status/500
curl -i $B/demo/status/503
```

**Windows (CMD):**

```bat
curl -i %B%/demo/status/200
curl -i %B%/demo/status/201
curl -i %B%/demo/status/301
curl -i %B%/demo/status/404
curl -i %B%/demo/status/418
curl -i %B%/demo/status/500
curl -i %B%/demo/status/503
```

**Las cinco familias:**

| Familia | Significado | Quién debe actuar |
|---|---|---|
| `1xx` | Informativo, la cosa sigue en marcha | Nadie |
| `2xx` | Éxito | Nadie, siga adelante |
| `3xx` | Redirección, está en otro lado | El cliente, siguiendo `Location` |
| `4xx` | Error **del cliente**: su petición está mal | **Usted**: corrija el comando |
| `5xx` | Error **del servidor** | El dueño de la API |

> La distinción `4xx` / `5xx` decide a quién le escribe usted cuando algo falla.
> Un `422` no se arregla abriendo un ticket: se arregla corrigiendo el JSON.

**Redirecciones:**

**Linux / macOS:**

```bash
curl -i $B/demo/redirect        # 302 y una cabecera Location
curl -i -L $B/demo/redirect     # -L la sigue y termina en 200
```

**Windows (CMD):**

```bat
curl -i %B%/demo/redirect
curl -i -L %B%/demo/redirect
```

**Eco de su propia petición.** Excelente para entender qué está enviando de
verdad:

**Linux / macOS:**

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -H "X-Mi-Cabecera: hola" \
  -d '{"prueba": true}' "$B/demo/echo?param=valor"
```

**Windows (CMD):**

```bat
curl -s -X POST -H "Content-Type: application/json" -H "X-Mi-Cabecera: hola" -d "{\"prueba\": true}" "%B%/demo/echo?param=valor"
```

La respuesta le muestra el método, la ruta, los parámetros, **todas** las
cabeceras que su cliente envió (incluidas las que curl agrega solo) y el cuerpo
interpretado.

---

## Bloque 9. Caché y concurrencia: ETag

Este bloque explica cómo dos ingenieros evitan sobrescribirse el trabajo.

**9.1 Obtener el ETag**

El `ETag` es una huella de la configuración del recurso.

**Linux / macOS:**

```bash
curl -i $B/devices/csr-core-01/interfaces/Te1/1/1 | grep -i etag
```

**Windows (CMD):**

```bat
curl -i %B%/devices/csr-core-01/interfaces/Te1/1/1 | findstr /i etag
```

**9.2 Ahorrar ancho de banda: 304**

Pregunte "¿cambió desde esta versión?". Copie el valor del paso anterior
(incluidas las comillas internas) donde dice `PEGUE_AQUI_SU_ETAG`:

**Linux / macOS:**

```bash
curl -i -H 'If-None-Match: "PEGUE_AQUI_SU_ETAG"' \
  $B/devices/csr-core-01/interfaces/Te1/1/1
```

**Windows (CMD):**

```bat
curl -i -H "If-None-Match: \"PEGUE_AQUI_SU_ETAG\"" %B%/devices/csr-core-01/interfaces/Te1/1/1
```

Responde **`304 Not Modified`** sin cuerpo: *no ha cambiado, use su copia*. Así
funciona la caché de todo internet.

**9.3 Evitar pisar el trabajo de otro: 412**

Guarde el ETag, deje que otra persona (o usted mismo desde la interfaz web)
modifique ese puerto, e intente escribir con el ETag viejo.

**Linux / macOS** (captura el ETag automáticamente):

```bash
ET=$(curl -s -i $B/devices/csr-core-01/interfaces/Te1/1/1 | grep -i '^etag' | tr -d '\r' | cut -d' ' -f2)

# Alguien más cambia la interfaz entre estos dos comandos
curl -s -o /dev/null -X PATCH -H "$K" -H "Content-Type: application/json" \
  -d '{"description": "CAMBIO-DE-OTRO"}' $B/devices/csr-core-01/interfaces/Te1/1/1

# Su escritura con el ETag viejo es rechazada
curl -i -X PATCH -H "$K" -H "Content-Type: application/json" \
  -H "If-Match: $ET" -d '{"description": "MI-CAMBIO"}' \
  $B/devices/csr-core-01/interfaces/Te1/1/1
```

**Windows (CMD)** (copie el ETag a mano con el paso 9.1, es el mismo ejercicio):

```bat
curl -i %B%/devices/csr-core-01/interfaces/Te1/1/1 | findstr /i etag

curl -s -o NUL -X PATCH -H "%K%" -H "Content-Type: application/json" -d "{\"description\": \"CAMBIO-DE-OTRO\"}" %B%/devices/csr-core-01/interfaces/Te1/1/1

curl -i -X PATCH -H "%K%" -H "Content-Type: application/json" -H "If-Match: \"PEGUE_AQUI_SU_ETAG\"" -d "{\"description\": \"MI-CAMBIO\"}" %B%/devices/csr-core-01/interfaces/Te1/1/1
```

**`412 Precondition Failed`**: el recurso cambió desde que usted lo leyó. Sin
este mecanismo, su cambio habría borrado silenciosamente el del compañero. Esto
se llama concurrencia optimista y es exactamente el problema que sufre cualquier
equipo con dos personas automatizando la misma red.

---

## Bloque 10. HEAD y OPTIONS: preguntar antes de actuar

**`HEAD`** trae solo las cabeceras, sin el cuerpo. Sirve para verificar
existencia o tamaño sin descargar nada:

**Linux / macOS:**

```bash
curl -I $B/devices/csr-core-01
```

**Windows (CMD):**

```bat
curl -I %B%/devices/csr-core-01
```

**`OPTIONS`** pregunta qué se puede hacer sobre un recurso:

**Linux / macOS:**

```bash
curl -i -X OPTIONS $B/devices/csr-core-01/vlans
```

**Windows (CMD):**

```bat
curl -i -X OPTIONS %B%/devices/csr-core-01/vlans
```

Lea la cabecera `Allow`. Y ahora provoque un `405` usando un verbo que ese
recurso no admite:

**Linux / macOS:**

```bash
curl -i -X POST -H "$K" -H "Content-Type: application/json" -d '{}' \
  $B/devices/csr-core-01/interfaces/Gi1/0/10
```

**Windows (CMD):**

```bat
curl -i -X POST -H "%K%" -H "Content-Type: application/json" -d "{}" %B%/devices/csr-core-01/interfaces/Gi1/0/10
```

`405 Method Not Allowed`, y la cabecera `Allow` le indica cuáles sí puede usar.
Esta es la misma petición `OPTIONS` que su navegador envía solo antes de un
`POST` entre dominios distintos: si alguna vez vio un error de CORS, era esto.

---

## Bloque 11. Límites, esperas y tiempos de espera

**Límite de peticiones.** Ejecute seis veces seguidas:

**Linux / macOS:**

```bash
for i in 1 2 3 4 5 6; do curl -s -o /dev/null -w "%{http_code} " $B/demo/rate-limit; done; echo
```

**Windows (CMD):**

```bat
for /L %i in (1,1,6) do @curl -s -o NUL -w "%{http_code} " %B%/demo/rate-limit
```

(Recuerde: dentro de un archivo `.bat` sería `%%i` y `%%{http_code}`.)

Las primeras cinco dan `200`; la sexta, **`429 Too Many Requests`** con la
cabecera `Retry-After`, que indica cuántos segundos esperar. Un script que
ignora el `429` y reintenta de inmediato empeora la situación.

**Tiempos de espera.** El servidor tarda tres segundos a propósito:

**Linux / macOS:**

```bash
curl -i --max-time 1 "$B/demo/slow?seconds=3"    # curl aborta: código de salida 28
curl -i --max-time 10 "$B/demo/slow?seconds=3"   # alcanza a responder 200
```

**Windows (CMD):**

```bat
curl -i --max-time 1 "%B%/demo/slow?seconds=3"
curl -i --max-time 10 "%B%/demo/slow?seconds=3"
```

(El primero aborta por tiempo; el segundo alcanza a responder `200`.)

Un `--max-time` demasiado corto produce fallos que parecen del servidor pero son
de su cliente.

---

## Retos finales

Resuélvalos usando solo lo aprendido. Cada uno requiere combinar varios pasos.

1. **Segmento de invitados.** Cree la VLAN `120` llamada `INVITADOS` en
   `sw-access-02`, mueva los puertos `Gi0/8` y `Gi0/9` a esa VLAN, y verifique
   con una sola consulta filtrada que ambos quedaron ahí.

2. **Auditoría.** Liste todas las interfaces caídas de los cuatro dispositivos y
   determine cuáles están apagadas administrativamente y cuáles cayeron por un
   enlace vecino. Pista: compare `admin_status` con `oper_status`.

3. **Ventana de mantenimiento.** Apague el uplink `Te1/1/2` del core, compruebe
   en la topología qué dispositivo quedó aislado, confirme por API que su uplink
   está caído, y restaure el servicio.

4. **Limpieza obligada.** Intente borrar la VLAN 20 de `sw-access-01`. Recibirá
   `409`. Resuelva el conflicto y logre borrarla.

5. **Diagnóstico a ciegas.** Un compañero le dice: "mi script recibe `415` y no
   entiendo por qué". Reproduzca ese error y explique en una frase la corrección.

6. **Trabajo en equipo aislado.** Agregue `-H "X-Lab-Team: rojo"` a sus comandos.
   Tendrá una copia privada de la red completa, independiente de la del resto del
   curso. Verifique que una VLAN creada con ese encabezado no aparece sin él, y
   seleccione su equipo en la interfaz visual para verla.

---

## Tabla de referencia

### Verbos

| Verbo | Intención | ¿Modifica? | ¿Repetirlo es seguro? | Código típico |
|---|---|---|---|---|
| `GET` | Leer | No | Sí | `200` |
| `HEAD` | Leer solo cabeceras | No | Sí | `200` |
| `OPTIONS` | Consultar qué se permite | No | Sí | `204` |
| `POST` | Crear o ejecutar una acción | Sí | **No** | `201`, `202`, `200` |
| `PUT` | Reemplazar por completo | Sí | Sí | `200` |
| `PATCH` | Modificar parcialmente | Sí | Depende | `200` |
| `DELETE` | Eliminar | Sí | Sí (en su efecto) | `204` |

### Códigos que verá en este laboratorio

| Código | Nombre | Causa en este lab |
|---|---|---|
| `200` | OK | Lectura o modificación exitosa |
| `201` | Created | VLAN creada, con `Location` |
| `202` | Accepted | Reinicio aceptado, aún en curso |
| `204` | No Content | VLAN borrada, o respuesta a `OPTIONS` |
| `302` | Found | Redirección de demostración |
| `304` | Not Modified | Su `If-None-Match` coincide |
| `400` | Bad Request | JSON malformado o parámetro no numérico |
| `401` | Unauthorized | Falta `X-API-Key` |
| `403` | Forbidden | Clave de solo lectura, o clave inválida |
| `404` | Not Found | Dispositivo, interfaz o VLAN inexistente |
| `405` | Method Not Allowed | Verbo incorrecto; lea `Allow` |
| `409` | Conflict | VLAN duplicada, en uso, protegida, o equipo reiniciando |
| `412` | Precondition Failed | `If-Match` con ETag viejo |
| `415` | Unsupported Media Type | Falta `Content-Type: application/json` |
| `422` | Unprocessable Content | Datos inválidos; lea `detalle.errors` |
| `429` | Too Many Requests | Superó el límite; lea `Retry-After` |

### Cabeceras usadas

| Cabecera | Dirección | Función |
|---|---|---|
| `X-API-Key` | Envío | Credencial de escritura |
| `Content-Type` | Envío | Formato del cuerpo que usted manda |
| `Accept` | Envío | Formato que desea recibir |
| `If-None-Match` | Envío | Condición de caché, produce `304` |
| `If-Match` | Envío | Condición de escritura, produce `412` |
| `X-Lab-Team` | Envío | Aísla su copia del laboratorio |
| `Location` | Respuesta | Dirección del recurso creado |
| `ETag` | Respuesta | Huella de la configuración |
| `Allow` | Respuesta | Verbos permitidos |
| `Retry-After` | Respuesta | Segundos de espera tras un `429` |
| `WWW-Authenticate` | Respuesta | Cómo autenticarse tras un `401` |

---

## Errores comunes y su corrección

| Síntoma | Causa | Corrección |
|---|---|---|
| `415` al crear | curl envió formato de formulario | Agregue `-H "Content-Type: application/json"` |
| `400 json_malformado` en Linux/macOS | Comillas dobles alrededor del JSON | Use comillas simples: `-d '{"a":1}'` |
| `400 json_malformado` en Windows | Comillas internas sin escapar | Escápelas: `-d "{\"a\":1}"` |
| CMD dice "'curl' no se reconoce" | Windows anterior a 10 (1803) o PATH roto | Actualice Windows o instale curl desde curl.se |
| PowerShell responde algo rarísimo | `curl` es alias de `Invoke-WebRequest` | Escriba `curl.exe`, o use CMD |
| El filtro no se aplica | La terminal se comió el `?` o el `&` | Ponga la URL entre comillas dobles |
| `401` con la clave puesta | Falta el espacio o el nombre está mal escrito | `-H "X-API-Key: netlab-admin-2026"` |
| `404` en una interfaz | Nombre mal escrito | Liste primero `/interfaces` y copie el nombre |
| `422` al hacer `PUT` | Envió campos parciales | Use `PATCH`, o envíe la representación completa |
| Todo da `000` o "Connection refused" | El laboratorio no está corriendo | Arranque `python3 app/netlab.py` o `docker compose up` |

## Si necesita empezar de cero

Devuelve toda la red al estado inicial:

**Linux / macOS:**

```bash
curl -i -X POST -H "$K" $B/reset
```

**Windows (CMD):**

```bat
curl -i -X POST -H "%K%" %B%/reset
```

Con `-H "X-Lab-Team: rojo"` reinicia solo la copia de ese equipo.
