#!/usr/bin/env python3
"""
Cliente interactivo del laboratorio NetLab.

Sirve para practicar la gestion de dispositivos de red por API sin escribir
comandos curl a mano. Cada operacion muestra el comando curl equivalente y la
peticion HTTP cruda, para que quede claro que Python no hace magia: solo arma
la misma peticion que usted escribiria en la terminal.

Uso:
    python3 netlab_cli.py                  # menu interactivo contra el lab local
    python3 netlab_cli.py --equipo rojo    # usa una copia privada del lab
    python3 netlab_cli.py --demo           # recorrido automatico para la clase
    python3 netlab_cli.py --url https://netlab.mi-academia.edu   # lab remoto

En Windows funciona igual: use "python" en lugar de "python3" si es necesario.

La direccion y las claves tambien se pueden fijar con variables de entorno:
NETLAB_URL, NETLAB_API_KEY y NETLAB_READONLY_KEY.

Sin dependencias externas: solo libreria estandar de Python 3.
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("NETLAB_URL", "http://localhost:8080")
CLAVE_ADMIN = os.environ.get("NETLAB_API_KEY", "netlab-admin-2026")
CLAVE_LECTURA = os.environ.get("NETLAB_READONLY_KEY", "netlab-lectura-2026")


# ---------------------------------------------------------------------------
# Colores para la terminal
# ---------------------------------------------------------------------------
class C:
    R = "\033[0m"; N = "\033[1m"; DIM = "\033[2m"
    VERDE = "\033[92m"; ROJO = "\033[91m"; AMAR = "\033[93m"
    AZUL = "\033[96m"; MAG = "\033[95m"; GRIS = "\033[90m"

    @classmethod
    def apagar(cls):
        for a in ("R", "N", "DIM", "VERDE", "ROJO", "AMAR", "AZUL", "MAG", "GRIS"):
            setattr(cls, a, "")


# En Windows, la consola clasica (cmd.exe) necesita activar el procesamiento
# de secuencias ANSI; llamar a system("") con una cadena vacia lo consigue en
# Windows 10 y posteriores sin dependencias externas.
if os.name == "nt":
    os.system("")

if not sys.stdout.isatty():
    C.apagar()

COLOR_METODO = {"GET": C.AZUL, "POST": C.VERDE, "PUT": C.AMAR,
                "PATCH": C.MAG, "DELETE": C.ROJO, "HEAD": C.GRIS, "OPTIONS": C.GRIS}


def color_estado(codigo):
    if codigo < 300:
        return C.VERDE
    if codigo < 400:
        return C.AZUL
    if codigo < 500:
        return C.AMAR
    return C.ROJO


def titulo(txt):
    print(f"\n{C.N}{C.AZUL}{'─' * 72}{C.R}")
    print(f"{C.N}{C.AZUL}  {txt}{C.R}")
    print(f"{C.N}{C.AZUL}{'─' * 72}{C.R}")


# ---------------------------------------------------------------------------
# Cliente HTTP
# ---------------------------------------------------------------------------
class NetLab:
    """Envoltorio delgado sobre urllib que ademas explica lo que envia."""

    def __init__(self, base=BASE, clave=CLAVE_ADMIN, equipo="shared",
                 mostrar=True, inseguro=False):
        self.base = base.rstrip("/")
        self.clave = clave
        self.equipo = equipo
        self.mostrar = mostrar
        self.ctx = None
        if inseguro:
            self.ctx = ssl.create_default_context()
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE

    # -- construccion de la peticion ---------------------------------------
    def pedir(self, metodo, ruta, cuerpo=None, cabeceras=None, mostrar=None):
        """Envia una peticion y devuelve (codigo, cabeceras, datos)."""
        mostrar = self.mostrar if mostrar is None else mostrar
        url = self.base + ruta
        h = {"Accept": "application/json", "User-Agent": "netlab-cli/1.0"}
        if self.equipo != "shared":
            h["X-Lab-Team"] = self.equipo
        if metodo in ("POST", "PUT", "PATCH", "DELETE"):
            h["X-API-Key"] = self.clave
        datos = None
        if cuerpo is not None:
            datos = json.dumps(cuerpo).encode("utf-8")
            # Sin esta cabecera la API responde 415: es el error mas comun.
            h["Content-Type"] = "application/json"
        h.update(cabeceras or {})

        if mostrar:
            self._explicar(metodo, url, h, cuerpo)

        pet = urllib.request.Request(url, data=datos, headers=h, method=metodo)
        inicio = time.time()
        try:
            with urllib.request.urlopen(pet, timeout=30, context=self.ctx) as r:
                crudo = r.read()
                codigo, cab = r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            # Un codigo 4xx o 5xx no es una excepcion en este dominio: es una
            # respuesta valida que el estudiante necesita poder leer.
            crudo = e.read()
            codigo, cab = e.code, dict(e.headers)
        except urllib.error.URLError as e:
            print(f"{C.ROJO}  No se pudo conectar con {url}{C.R}")
            print(f"{C.GRIS}  Motivo: {e.reason}{C.R}")
            return None, {}, None

        ms = (time.time() - inicio) * 1000
        try:
            datos_resp = json.loads(crudo) if crudo else None
        except json.JSONDecodeError:
            datos_resp = crudo.decode("utf-8", "replace")

        if mostrar:
            self._mostrar_respuesta(codigo, cab, datos_resp, ms, len(crudo))
        return codigo, cab, datos_resp

    def _explicar(self, metodo, url, cabeceras, cuerpo):
        col = COLOR_METODO.get(metodo, "")
        print(f"\n{C.DIM}  ── Peticion enviada ─────────────────────────────{C.R}")
        print(f"  {col}{C.N}{metodo}{C.R} {url}")
        for k, v in cabeceras.items():
            if k in ("Accept", "User-Agent"):
                continue
            valor = v if k != "X-API-Key" else v[:12] + "…"
            print(f"  {C.GRIS}{k}: {valor}{C.R}")
        if cuerpo is not None:
            print(f"  {C.GRIS}{json.dumps(cuerpo, ensure_ascii=False)}{C.R}")
        print(f"  {C.DIM}equivalente en curl:{C.R}")
        print(f"  {C.AZUL}{self.curl(metodo, url, cabeceras, cuerpo)}{C.R}")

    def curl(self, metodo, url, cabeceras, cuerpo):
        partes = [f"curl -i -X {metodo} {url}"]
        for k, v in cabeceras.items():
            if k in ("Accept", "User-Agent"):
                continue
            partes.append(f'-H "{k}: {v}"')
        if cuerpo is not None:
            partes.append(f"-d '{json.dumps(cuerpo, ensure_ascii=False)}'")
        return " \\\n    ".join(partes)

    def _mostrar_respuesta(self, codigo, cab, datos, ms, bytes_):
        col = color_estado(codigo)
        print(f"  {C.DIM}── Respuesta ────────────────────────────────────{C.R}")
        print(f"  {col}{C.N}HTTP {codigo}{C.R}  {C.GRIS}{ms:.0f} ms · {bytes_} bytes{C.R}")
        for k in ("Location", "ETag", "Allow", "Retry-After", "Content-Type",
                  "WWW-Authenticate"):
            if k in cab:
                print(f"  {C.GRIS}{k}: {cab[k]}{C.R}")
        if isinstance(datos, dict) and "error" in datos:
            e = datos["error"]
            print(f"  {C.ROJO}{e.get('codigo')}: {e.get('mensaje')}{C.R}")
            det = e.get("detalle") or {}
            for err in det.get("errors", []):
                print(f"    {C.AMAR}· campo '{err.get('field')}': {err.get('issue')}{C.R}")
            for k, v in det.items():
                if k != "errors":
                    print(f"    {C.GRIS}{k}: {v}{C.R}")
        elif datos is not None:
            texto = json.dumps(datos, ensure_ascii=False, indent=2) \
                if not isinstance(datos, str) else datos
            lineas = texto.split("\n")
            for ln in lineas[:16]:
                print(f"  {C.GRIS}{ln[:110]}{C.R}")
            if len(lineas) > 16:
                print(f"  {C.DIM}… {len(lineas) - 16} lineas mas{C.R}")
        else:
            print(f"  {C.DIM}(sin cuerpo, que es lo correcto para este codigo){C.R}")

    # -- atajos de alto nivel ---------------------------------------------
    def dispositivos(self, mostrar=False):
        _, _, d = self.pedir("GET", "/api/v1/devices", mostrar=mostrar)
        return (d or {}).get("devices", [])

    def interfaces(self, dev, mostrar=False):
        _, _, d = self.pedir("GET", f"/api/v1/devices/{dev}/interfaces", mostrar=mostrar)
        return (d or {}).get("interfaces", [])

    def vlans(self, dev, mostrar=False):
        _, _, d = self.pedir("GET", f"/api/v1/devices/{dev}/vlans", mostrar=mostrar)
        return (d or {}).get("vlans", [])


# ---------------------------------------------------------------------------
# Presentacion de datos
# ---------------------------------------------------------------------------
def tabla_dispositivos(devs):
    print(f"\n  {C.N}{'ID':<15} {'MODELO':<26} {'ROL':<8} {'IP':<14} "
          f"{'UP/DOWN':<9} {'ESTADO'}{C.R}")
    print(f"  {C.GRIS}{'─' * 88}{C.R}")
    for d in devs:
        col = C.VERDE if d["status"] == "online" else C.AMAR
        aviso = "" if d["config_saved"] else f" {C.AMAR}(sin guardar){C.R}"
        print(f"  {d['id']:<15} {d['model'].replace('Cisco ', ''):<26} "
              f"{d['role']:<8} {d['mgmt_ip']:<14} "
              f"{C.VERDE}{d['interfaces_up']:>3}{C.R}/{C.ROJO}{d['interfaces_down']:<4}{C.R} "
              f"{col}{d['status']}{C.R}{aviso}")


def tabla_interfaces(ifaces):
    print(f"\n  {C.N}{'INTERFAZ':<24} {'ESTADO':<16} {'MODO':<8} {'VLAN':<6} "
          f"{'USO':<7} {'DESCRIPCION'}{C.R}")
    print(f"  {C.GRIS}{'─' * 92}{C.R}")
    for i in ifaces:
        if i["oper_status"] == "up":
            col, marca = C.VERDE, "●"
        elif i["admin_status"] == "up":
            col, marca = C.ROJO, "●"
        else:
            col, marca = C.GRIS, "○"
        vlan = str(i["vlan"]) if i["vlan"] else (i["ip_address"] or "-")
        uso = f"{i['utilization_pct']:.1f}%" if i["oper_status"] == "up" else "-"
        print(f"  {col}{marca}{C.R} {i['name']:<22} {col}{i['status_text']:<16}{C.R} "
              f"{i['mode']:<8} {vlan:<6} {uso:<7} {C.GRIS}{i['description'][:28]}{C.R}")


def tabla_vlans(vlans):
    print(f"\n  {C.N}{'VLAN':<7} {'NOMBRE':<22} {'ESTADO':<11} {'PUERTOS'}{C.R}")
    print(f"  {C.GRIS}{'─' * 72}{C.R}")
    for v in vlans:
        col = C.VERDE if v["status"] == "active" else C.AMAR
        puertos = ", ".join(v.get("ports", [])) or "-"
        print(f"  {v['id']:<7} {v['name']:<22} {col}{v['status']:<11}{C.R} "
              f"{C.GRIS}{puertos[:40]}{C.R}")


# ---------------------------------------------------------------------------
# Utilidades de entrada
# ---------------------------------------------------------------------------
def preguntar(texto, defecto=""):
    suf = f" {C.GRIS}[{defecto}]{C.R}" if defecto else ""
    try:
        r = input(f"  {C.AZUL}{texto}{suf}: {C.R}").strip()
    except EOFError:
        return defecto
    return r or defecto


def elegir_dispositivo(api, solo_switches=False):
    devs = api.dispositivos()
    if not devs:
        return None
    if solo_switches:
        devs = [d for d in devs if d["role"] != "router"]
    print()
    for n, d in enumerate(devs, 1):
        print(f"  {C.N}{n}{C.R}) {d['id']:<15} {C.GRIS}{d['model']}{C.R}")
    r = preguntar("Numero del dispositivo", "1")
    try:
        return devs[int(r) - 1]["id"]
    except (ValueError, IndexError):
        print(f"  {C.ROJO}Opcion no valida{C.R}")
        return None


def elegir_interfaz(api, dev):
    ifaces = api.interfaces(dev)
    if not ifaces:
        return None
    print()
    for n, i in enumerate(ifaces, 1):
        col = C.VERDE if i["oper_status"] == "up" else (
            C.ROJO if i["admin_status"] == "up" else C.GRIS)
        print(f"  {C.N}{n:>2}{C.R}) {i['name']:<24} {col}{i['status_text']:<16}{C.R} "
              f"{C.GRIS}{i['description'][:26]}{C.R}")
    r = preguntar("Numero de la interfaz", "1")
    try:
        return ifaces[int(r) - 1]["name"]
    except (ValueError, IndexError):
        print(f"  {C.ROJO}Opcion no valida{C.R}")
        return None


# ---------------------------------------------------------------------------
# Opciones del menu
# ---------------------------------------------------------------------------
def op_inventario(api):
    titulo("Inventario de dispositivos")
    devs = api.dispositivos(mostrar=True)
    tabla_dispositivos(devs)


def op_interfaces(api):
    titulo("Interfaces de un dispositivo")
    dev = elegir_dispositivo(api)
    if dev:
        tabla_interfaces(api.interfaces(dev, mostrar=True))


def op_conmutar(api, apagar):
    accion = "Apagar" if apagar else "Encender"
    titulo(f"{accion} una interfaz")
    dev = elegir_dispositivo(api)
    if not dev:
        return
    iface = elegir_interfaz(api, dev)
    if not iface:
        return
    ruta = f"/api/v1/devices/{dev}/interfaces/{iface}/" \
           f"{'shutdown' if apagar else 'no-shutdown'}"
    codigo, _, d = api.pedir("POST", ruta)
    if codigo == 200 and isinstance(d, dict):
        print(f"\n  {C.N}Comandos IOS equivalentes:{C.R}")
        for c in d.get("comando_ios", []):
            print(f"    {C.VERDE}{c}{C.R}")
        i = d["interface"]
        print(f"  Estado ahora: {i['status_text']}")
        if i.get("link_to"):
            v = i["link_to"]
            print(f"  {C.AMAR}Revise el vecino {v['device']} {v['interface']}: "
                  f"tambien se afecto.{C.R}")


def op_vlans(api):
    titulo("VLANs de un dispositivo")
    dev = elegir_dispositivo(api)
    if dev:
        tabla_vlans(api.vlans(dev, mostrar=True))


def op_crear_vlan(api):
    titulo("Crear una VLAN")
    dev = elegir_dispositivo(api, solo_switches=True)
    if not dev:
        return
    vid = preguntar("Identificador de la VLAN (1-4094)", "99")
    nombre = preguntar("Nombre (sin espacios)", "LABORATORIO")
    try:
        cuerpo = {"id": int(vid), "name": nombre}
    except ValueError:
        # Se envia tal cual a proposito: el 422 resultante es la leccion.
        print(f"  {C.AMAR}'{vid}' no es un numero. Se enviara asi para que vea "
              f"la respuesta del servidor.{C.R}")
        cuerpo = {"id": vid, "name": nombre}
    codigo, cab, _ = api.pedir("POST", f"/api/v1/devices/{dev}/vlans", cuerpo)
    if codigo == 201:
        print(f"\n  {C.VERDE}Creada. El servidor indica donde quedo: "
              f"{cab.get('Location')}{C.R}")
        print(f"  {C.GRIS}Equivalente en IOS: vlan {vid} / name {nombre}{C.R}")


def op_borrar_vlan(api):
    titulo("Eliminar una VLAN")
    dev = elegir_dispositivo(api, solo_switches=True)
    if not dev:
        return
    tabla_vlans(api.vlans(dev))
    vid = preguntar("Identificador de la VLAN a eliminar")
    if not vid:
        return
    codigo, _, _ = api.pedir("DELETE", f"/api/v1/devices/{dev}/vlans/{vid}")
    if codigo == 204:
        print(f"\n  {C.VERDE}Eliminada. El codigo 204 no trae cuerpo: eso es "
              f"correcto, no un error.{C.R}")
    elif codigo == 409:
        print(f"\n  {C.AMAR}Conflicto: la API protege la red. Libere los puertos "
              f"y reintente.{C.R}")


def op_configurar(api):
    titulo("Configurar una interfaz con PATCH")
    dev = elegir_dispositivo(api)
    if not dev:
        return
    iface = elegir_interfaz(api, dev)
    if not iface:
        return
    print(f"\n  {C.GRIS}Deje en blanco lo que no quiera cambiar. PATCH solo "
          f"modifica lo que se envia.{C.R}")
    cuerpo = {}
    desc = preguntar("Nueva descripcion")
    if desc:
        cuerpo["description"] = desc
    vlan = preguntar("Nueva VLAN de acceso")
    if vlan:
        try:
            cuerpo["vlan"] = int(vlan)
        except ValueError:
            cuerpo["vlan"] = vlan
    mtu = preguntar("Nuevo MTU")
    if mtu:
        try:
            cuerpo["mtu"] = int(mtu)
        except ValueError:
            cuerpo["mtu"] = mtu
    if not cuerpo:
        print(f"  {C.GRIS}No indico ningun cambio.{C.R}")
        return
    api.pedir("PATCH", f"/api/v1/devices/{dev}/interfaces/{iface}", cuerpo)


def op_config(api):
    titulo("running-config del dispositivo")
    dev = elegir_dispositivo(api)
    if not dev:
        return
    _, _, d = api.pedir("GET", f"/api/v1/devices/{dev}/config",
                        cabeceras={"Accept": "application/json"}, mostrar=False)
    if isinstance(d, dict):
        print()
        for ln in d.get("lines", []):
            if ln.startswith("!"):
                print(f"  {C.GRIS}{ln}{C.R}")
            elif ln.startswith(" "):
                print(f"  {C.AZUL}{ln}{C.R}")
            else:
                print(f"  {C.N}{ln}{C.R}")


def op_reload(api):
    titulo("Reiniciar un dispositivo")
    dev = elegir_dispositivo(api)
    if not dev:
        return
    print(f"\n  {C.AMAR}Esto interrumpe el servicio del equipo unos 9 segundos.{C.R}")
    if preguntar("Escriba 'si' para confirmar", "no").lower() not in ("si", "sí", "s"):
        print(f"  {C.GRIS}Cancelado.{C.R}")
        return
    codigo, _, _ = api.pedir("POST", f"/api/v1/devices/{dev}/actions/reload",
                             {"confirm": True})
    if codigo == 202:
        print(f"\n  {C.AZUL}202 Accepted: la orden se acepto pero aun no termina."
              f" Observe el proyector.{C.R}")


def op_auditoria(api):
    titulo("Auditoria: interfaces caidas en toda la red")
    devs = api.dispositivos()
    admin, enlace = [], []
    for d in devs:
        for i in api.interfaces(d["id"]):
            if i["oper_status"] == "up":
                continue
            fila = (d["id"], i["name"], i["description"] or "sin descripcion")
            (admin if i["admin_status"] != "up" else enlace).append(fila)

    print(f"\n  {C.GRIS}Apagadas a proposito (admin-down): configuracion, no falla{C.R}")
    for d, i, desc in admin:
        print(f"    {C.GRIS}○ {d:<15} {i:<24} {desc}{C.R}")
    if not admin:
        print(f"    {C.GRIS}ninguna{C.R}")

    print(f"\n  {C.ROJO}Caidas con el puerto habilitado: esto si es una falla{C.R}")
    for d, i, desc in enlace:
        print(f"    {C.ROJO}● {d:<15} {i:<24} {desc}{C.R}")
    if not enlace:
        print(f"    {C.VERDE}ninguna: la red esta sana{C.R}")

    print(f"\n  {C.N}Total: {len(admin)} apagadas por configuracion, "
          f"{len(enlace)} caidas por falla o por su vecino.{C.R}")


def op_codigos(api):
    titulo("Los codigos de estado en vivo")
    casos = [
        ("GET",    "/api/v1/devices",                    None, "200 lectura correcta"),
        ("GET",    "/api/v1/devices/no-existe",          None, "404 el recurso no existe"),
        ("POST",   "/api/v1/devices/csr-core-01/vlans",  {"id": 10, "name": "REPETIDA"},
         "409 conflicto: la VLAN 10 ya existe"),
        ("POST",   "/api/v1/devices/csr-core-01/vlans",  {"id": "diez"},
         "422 el dato es invalido aunque el JSON sea correcto"),
        ("PUT",    "/api/v1/devices/sw-access-01/interfaces/Gi0/7",
         {"description": "parcial"}, "422 PUT exige el objeto completo"),
        ("DELETE", "/api/v1/devices/csr-core-01/vlans/30", None,
         "409 no se borra una VLAN con puertos asignados"),
    ]
    for metodo, ruta, cuerpo, explicacion in casos:
        print(f"\n  {C.N}{explicacion}{C.R}")
        api.pedir(metodo, ruta, cuerpo)
        time.sleep(0.4)

    print(f"\n  {C.N}Ahora la diferencia entre 401 y 403{C.R}")
    ruta = "/api/v1/devices/csr-core-01/interfaces/Te1/1/1/shutdown"
    guardada, api.clave = api.clave, ""
    print(f"\n  {C.GRIS}Sin clave: el servidor no sabe quien es usted{C.R}")
    api.pedir("POST", ruta)
    api.clave = CLAVE_LECTURA
    print(f"\n  {C.GRIS}Con clave de solo lectura: sabe quien es, pero no le alcanza{C.R}")
    api.pedir("POST", ruta)
    api.clave = guardada
    print(f"\n  {C.VERDE}401 = no se identifico.  403 = se identifico y no tiene "
          f"permiso.{C.R}")


def op_etag(api):
    titulo("ETag: cache y escrituras seguras")
    ruta = "/api/v1/devices/csr-core-01/interfaces/Te1/1/1"
    print(f"\n  {C.N}1) Leo el recurso y guardo su ETag{C.R}")
    _, cab, _ = api.pedir("GET", ruta, mostrar=False)
    etag = cab.get("ETag")
    print(f"     ETag = {C.AZUL}{etag}{C.R}")

    print(f"\n  {C.N}2) Pregunto si cambio, enviando ese ETag{C.R}")
    codigo, _, _ = api.pedir("GET", ruta, cabeceras={"If-None-Match": etag})
    if codigo == 304:
        print(f"     {C.VERDE}304 Not Modified: no cambio, ahorre la descarga.{C.R}")

    print(f"\n  {C.N}3) Otra persona modifica la interfaz{C.R}")
    api.pedir("PATCH", ruta, {"description": "CAMBIO-DE-OTRO-INGENIERO"},
              mostrar=False)
    print(f"     {C.GRIS}hecho{C.R}")

    print(f"\n  {C.N}4) Intento escribir con mi ETag viejo{C.R}")
    codigo, _, _ = api.pedir("PATCH", ruta, {"description": "MI-CAMBIO"},
                             cabeceras={"If-Match": etag})
    if codigo == 412:
        print(f"     {C.VERDE}412: la API evito que usted borrara el cambio "
              f"del otro.{C.R}")

    api.pedir("PATCH", ruta, {"description": "TRUNK-ACCESO-01"}, mostrar=False)


def op_libre(api):
    titulo("Peticion libre")
    print(f"  {C.GRIS}Escriba la ruta empezando por /api/v1{C.R}")
    metodo = preguntar("Metodo", "GET").upper()
    ruta = preguntar("Ruta", "/api/v1/devices")
    cuerpo = None
    if metodo in ("POST", "PUT", "PATCH"):
        crudo = preguntar("Cuerpo JSON (vacio para ninguno)")
        if crudo:
            try:
                cuerpo = json.loads(crudo)
            except json.JSONDecodeError as e:
                print(f"  {C.ROJO}Ese texto no es JSON valido: {e}{C.R}")
                print(f"  {C.GRIS}Ejemplo correcto: "
                      f'{{"id": 99, "name": "PRUEBA"}}{C.R}')
                return
    api.pedir(metodo, ruta, cuerpo)


def op_reset(api):
    titulo("Reiniciar el laboratorio")
    print(f"  {C.AMAR}Devuelve la red del equipo '{api.equipo}' al estado "
          f"inicial.{C.R}")
    if preguntar("Escriba 'si' para confirmar", "no").lower() in ("si", "sí", "s"):
        api.pedir("POST", "/api/v1/reset")


# ---------------------------------------------------------------------------
# Recorrido automatico para proyectar en clase
# ---------------------------------------------------------------------------
def demo(api):
    pasos = [
        ("El inventario: una sola peticion GET trae toda la red",
         lambda: tabla_dispositivos(api.dispositivos(mostrar=True))),
        ("Las interfaces del switch de core",
         lambda: tabla_interfaces(api.interfaces("csr-core-01", mostrar=True))),
        ("Apagamos el troncal hacia el piso 3. Mire la topologia",
         lambda: api.pedir("POST", "/api/v1/devices/csr-core-01/interfaces/"
                                   "Te1/1/1/shutdown")),
        ("El puerto vecino en sw-access-01 cayo sin que lo tocaramos",
         lambda: tabla_interfaces(api.interfaces("sw-access-01")[:1])),
        ("Lo volvemos a encender",
         lambda: api.pedir("POST", "/api/v1/devices/csr-core-01/interfaces/"
                                   "Te1/1/1/no-shutdown")),
        ("Creamos una VLAN. Observe el 201 y la cabecera Location",
         lambda: api.pedir("POST", "/api/v1/devices/csr-core-01/vlans",
                           {"id": 99, "name": "DEMO-EN-VIVO"})),
        ("Asignamos un puerto a esa VLAN con PATCH",
         lambda: api.pedir("PATCH", "/api/v1/devices/csr-core-01/interfaces/"
                                    "Gi1/0/12", {"vlan": 99, "enabled": True,
                                                 "description": "PUESTO-DEMO"})),
        ("La configuracion resultante, como se veria en la consola del switch",
         lambda: op_config_demo(api, "csr-core-01")),
        ("Intentamos crear la misma VLAN otra vez: 409",
         lambda: api.pedir("POST", "/api/v1/devices/csr-core-01/vlans",
                           {"id": 99, "name": "DUPLICADA"})),
        ("Un dato invalido produce 422 con el detalle del campo",
         lambda: api.pedir("POST", "/api/v1/devices/csr-core-01/vlans",
                           {"id": "noventa"})),
        ("Liberamos el puerto y borramos la VLAN: 204 sin cuerpo",
         lambda: (api.pedir("PATCH", "/api/v1/devices/csr-core-01/interfaces/"
                                     "Gi1/0/12", {"vlan": 1}, mostrar=False),
                  api.pedir("DELETE", "/api/v1/devices/csr-core-01/vlans/99"))),
        ("Auditoria completa de la red", lambda: op_auditoria(api)),
    ]
    for n, (texto, accion) in enumerate(pasos, 1):
        titulo(f"Paso {n} de {len(pasos)}. {texto}")
        accion()
        try:
            input(f"\n  {C.DIM}[Enter para continuar, Ctrl+C para salir]{C.R}")
        except (EOFError, KeyboardInterrupt):
            print()
            return
    print(f"\n  {C.VERDE}{C.N}Fin del recorrido.{C.R}\n")


def op_config_demo(api, dev):
    _, _, d = api.pedir("GET", f"/api/v1/devices/{dev}/config",
                        cabeceras={"Accept": "application/json"}, mostrar=False)
    if isinstance(d, dict):
        for ln in d.get("lines", [])[:26]:
            print(f"  {C.AZUL if ln.startswith(' ') else C.GRIS}{ln}{C.R}")


# ---------------------------------------------------------------------------
# Menu principal
# ---------------------------------------------------------------------------
OPCIONES = [
    ("Ver inventario de dispositivos",          op_inventario),
    ("Ver interfaces de un dispositivo",        op_interfaces),
    ("Apagar una interfaz (shutdown)",          lambda a: op_conmutar(a, True)),
    ("Encender una interfaz (no shutdown)",     lambda a: op_conmutar(a, False)),
    ("Ver VLANs",                               op_vlans),
    ("Crear una VLAN",                          op_crear_vlan),
    ("Eliminar una VLAN",                       op_borrar_vlan),
    ("Configurar una interfaz (PATCH)",         op_configurar),
    ("Ver running-config",                      op_config),
    ("Reiniciar un dispositivo",                op_reload),
    ("Auditoria de interfaces caidas",          op_auditoria),
    ("Recorrido por los codigos de estado",     op_codigos),
    ("Demostracion de ETag, 304 y 412",         op_etag),
    ("Enviar una peticion libre",               op_libre),
    ("Reiniciar el laboratorio",                op_reset),
]


def menu(api):
    print(f"\n{C.N}{C.VERDE}  NetLab · cliente interactivo{C.R}")
    print(f"  {C.GRIS}{api.base}   equipo: {api.equipo}{C.R}")
    print(f"  {C.GRIS}Cada opcion muestra la peticion HTTP y su equivalente "
          f"en curl.{C.R}")
    while True:
        print(f"\n{C.N}  Menu{C.R}")
        for n, (texto, _) in enumerate(OPCIONES, 1):
            print(f"   {C.AZUL}{n:>2}{C.R}) {texto}")
        print(f"    {C.AZUL}0{C.R}) Salir")
        try:
            r = input(f"\n  {C.N}Opcion: {C.R}").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n  {C.GRIS}Hasta luego.{C.R}")
            return
        if r in ("0", "q", "salir", ""):
            print(f"  {C.GRIS}Hasta luego.{C.R}")
            return
        try:
            idx = int(r) - 1
            if not 0 <= idx < len(OPCIONES):
                raise IndexError
        except (ValueError, IndexError):
            print(f"  {C.ROJO}Opcion no valida.{C.R}")
            continue
        try:
            OPCIONES[idx][1](api)
        except KeyboardInterrupt:
            print(f"\n  {C.GRIS}Operacion interrumpida.{C.R}")


def main():
    ap = argparse.ArgumentParser(
        description="Cliente interactivo del laboratorio NetLab.")
    ap.add_argument("--url", default=BASE, help="direccion base de la API")
    ap.add_argument("--equipo", default="shared",
                    help="nombre de equipo para trabajar en una copia aislada")
    ap.add_argument("--clave", default=CLAVE_ADMIN, help="valor de X-API-Key")
    ap.add_argument("--demo", action="store_true",
                    help="recorrido guiado para proyectar en clase")
    ap.add_argument("--inseguro", action="store_true",
                    help="no verificar el certificado TLS")
    a = ap.parse_args()

    api = NetLab(a.url, a.clave, a.equipo, inseguro=a.inseguro)
    codigo, _, _ = api.pedir("GET", "/api/v1/health", mostrar=False)
    if codigo != 200:
        print(f"{C.ROJO}  El laboratorio no responde en {a.url}{C.R}")
        print(f"{C.GRIS}  Si es local, arranquelo primero: python3 app/netlab.py "
              f"(o docker compose up). Si es remoto, revise la URL con --url.{C.R}")
        return 1

    if a.demo:
        demo(api)
    else:
        menu(api)
    return 0


if __name__ == "__main__":
    sys.exit(main())
