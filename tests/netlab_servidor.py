"""Arranque de NetLab para las pruebas y utilidades HTTP crudas.

Solo libreria estandar. Las pruebas levantan ``app/netlab.py`` como subproceso
en un puerto libre, con ``NETLAB_DB=off`` salvo que la prueba pida otra cosa.

Con ``NETLAB_APP=<ruta a netlab.py>`` se prueba otro archivo; asi se demuestra
que las mismas pruebas fallan contra la copia sin corregir.
"""

import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

RAIZ_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAVE_FABRICA = "netlab-admin-2026"
CLAVE_LECTURA_FABRICA = "netlab-lectura-2026"
TOPE_EQUIPOS = 50


def ruta_app():
    """El netlab.py bajo prueba: NETLAB_APP o el del repositorio de estas pruebas."""
    ruta = os.environ.get("NETLAB_APP") or os.path.join(RAIZ_REPO, "app", "netlab.py")
    return os.path.abspath(ruta)


def raiz_de_la_app():
    """Raiz del repositorio al que pertenece el netlab.py bajo prueba."""
    return os.path.dirname(os.path.dirname(ruta_app()))


def puerto_libre():
    """Un puerto libre en IPv4 e IPv6, porque el servidor escucha en pila dual."""
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        s.bind(("::", 0))
    except OSError:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
    with s:
        return s.getsockname()[1]


class Respuesta:
    def __init__(self, status, cabeceras, cuerpo, cabeceras_lista=None):
        self.status = status
        self.cabeceras = cabeceras  # nombre en minusculas -> valor (gana la ultima)
        self.cabeceras_lista = cabeceras_lista or list(cabeceras.items())
        self.cuerpo = cuerpo

    def cab(self, nombre):
        return self.cabeceras.get(nombre.lower())

    @property
    def json(self):
        return json.loads(self.cuerpo.decode("utf-8"))

    @property
    def texto(self):
        return self.cuerpo.decode("utf-8", "replace")

    def __repr__(self):
        return f"<Respuesta {self.status} {self.cabeceras} {self.cuerpo[:200]!r}>"


def metodos_de_allow(valor):
    return {m.strip().upper() for m in (valor or "").split(",") if m.strip()}


class Servidor:
    """Un proceso de NetLab en un puerto libre.

    ``entorno`` se suma al entorno del proceso. Las claves NETLAB_* que traiga el
    entorno de quien corre las pruebas se descartan para que no cambien el
    resultado.
    """

    def __init__(self, **entorno):
        self.entorno = entorno
        self.proceso = None
        self.puerto = None
        self._dir = None
        self._log = None

    def arrancar(self, espera=20.0):
        self.puerto = puerto_libre()
        env = {k: v for k, v in os.environ.items() if not k.startswith("NETLAB_")}
        env.update(PORT=str(self.puerto), NETLAB_DB="off", PYTHONUNBUFFERED="1")
        env.update(self.entorno)
        self._dir = tempfile.mkdtemp(prefix="netlab-prueba-")
        self._log_path = os.path.join(self._dir, "servidor.log")
        self._log = open(self._log_path, "wb")
        self.proceso = subprocess.Popen(
            [sys.executable, "-u", ruta_app()], env=env, cwd=self._dir,
            stdout=self._log, stderr=subprocess.STDOUT)
        limite = time.time() + espera
        while time.time() < limite:
            if self.proceso.poll() is not None:
                salida = self.salida()
                self.detener()
                raise RuntimeError(f"NetLab termino al arrancar:\n{salida}")
            try:
                if self.pedir("GET", "/api/v1/health", timeout=2).status == 200:
                    return self
            except OSError:
                pass
            time.sleep(0.1)
        salida = self.salida()
        self.detener()
        raise RuntimeError(f"NetLab no respondio /api/v1/health en {espera} s:\n{salida}")

    def detener(self):
        if self.proceso is not None and self.proceso.poll() is None:
            self.proceso.terminate()
            try:
                self.proceso.wait(5)
            except subprocess.TimeoutExpired:
                self.proceso.kill()
                self.proceso.wait(5)
        if self._log is not None:
            self._log.close()
            self._log = None
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None

    def interrumpir(self):
        """Ctrl+C: el servidor guarda el estado antes de salir."""
        import signal
        if self.proceso is not None and self.proceso.poll() is None:
            self.proceso.send_signal(signal.SIGINT)
            try:
                self.proceso.wait(10)
            except subprocess.TimeoutExpired:
                pass
        self.detener()

    def salida(self):
        try:
            if self._log is not None:
                self._log.flush()
            with open(self._log_path, "rb") as f:
                return f.read().decode("utf-8", "replace")
        except (OSError, AttributeError):
            return ""

    @property
    def pid(self):
        return self.proceso.pid

    @property
    def base(self):
        return f"http://127.0.0.1:{self.puerto}"

    def pedir(self, metodo, ruta, cuerpo=None, cabeceras=None, timeout=10):
        cabeceras = dict(cabeceras or {})
        if isinstance(cuerpo, (dict, list)):
            cuerpo = json.dumps(cuerpo)
            cabeceras.setdefault("Content-Type", "application/json")
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=timeout)
        try:
            c.request(metodo, ruta, body=cuerpo, headers=cabeceras)
            r = c.getresponse()
            datos = r.read()
            lista = [(k.lower(), v) for k, v in r.getheaders()]
            return Respuesta(r.status, dict(lista), datos, lista)
        finally:
            c.close()

    def equipos_activos(self):
        r = self.pedir("GET", "/api/v1/topology?silent=1")
        if r.status != 200:
            raise AssertionError(f"GET /api/v1/topology fallo: {r}")
        return r.json["equipos_activos"]

    def memoria_mb(self):
        """RSS del proceso del servidor en MB."""
        estado = f"/proc/{self.pid}/status"
        if os.path.exists(estado):
            with open(estado) as f:
                for linea in f:
                    if linea.startswith("VmRSS:"):
                        return int(linea.split()[1]) / 1024
        salida = subprocess.run(["ps", "-o", "rss=", "-p", str(self.pid)],
                                capture_output=True, text=True).stdout
        return int(salida.strip()) / 1024


# ---------------------------------------------------------------------------
# HTTP crudo: urllib y http.client esconden la desincronizacion de B4
# ---------------------------------------------------------------------------
def conectar(puerto, timeout=5.0):
    return socket.create_connection(("127.0.0.1", puerto), timeout=timeout)


def peticion_cruda(metodo, ruta, cabeceras=None, cuerpo=b""):
    """Arma una peticion HTTP/1.1. Content-Length va solo si el llamador lo pone
    o si hay cuerpo y no lo puso."""
    cabeceras = dict(cabeceras or {})
    if isinstance(cuerpo, str):
        cuerpo = cuerpo.encode()
    if cuerpo and not any(k.lower() == "content-length" for k in cabeceras):
        cabeceras["Content-Length"] = str(len(cuerpo))
    lineas = [f"{metodo} {ruta} HTTP/1.1", "Host: netlab"]
    lineas += [f"{k}: {v}" for k, v in cabeceras.items()]
    return ("\r\n".join(lineas) + "\r\n\r\n").encode() + cuerpo


def leer_hasta_cierre(sock, silencio=1.5, maximo=10.0):
    """Lee hasta que el servidor cierre o pasen ``silencio`` s sin datos.

    Devuelve (bytes, cerrado). ``cerrado`` es True si el servidor cerro la
    conexion (EOF o reset)."""
    sock.settimeout(silencio)
    datos = b""
    limite = time.time() + maximo
    while time.time() < limite:
        try:
            trozo = sock.recv(65536)
        except socket.timeout:
            return datos, False
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            return datos, True
        if not trozo:
            return datos, True
        datos += trozo
    return datos, False


_LINEA_ESTADO = re.compile(rb"HTTP/\d\.\d (\d{3})")


def separar_respuestas(datos, metodos=()):
    """Parte un flujo de bytes en respuestas HTTP.

    ``metodos`` dice, en orden, el metodo de la peticion de cada respuesta; solo
    importa para HEAD, que trae Content-Length sin cuerpo."""
    respuestas = []
    i = 0
    while i < len(datos):
        fin_cab = datos.find(b"\r\n\r\n", i)
        if fin_cab < 0:
            break
        lineas = datos[i:fin_cab].split(b"\r\n")
        m = _LINEA_ESTADO.match(lineas[0])
        if not m:
            respuestas.append(Respuesta(-1, {}, datos[i:]))
            break
        status = int(m.group(1))
        lista = []
        for linea in lineas[1:]:
            k, _, v = linea.decode("latin-1").partition(":")
            lista.append((k.strip().lower(), v.strip()))
        cab = dict(lista)
        j = fin_cab + 4
        metodo = metodos[len(respuestas)] if len(respuestas) < len(metodos) else "GET"
        if status in (204, 304) or 100 <= status < 200 or metodo == "HEAD":
            n = 0
        elif "content-length" in cab:
            n = int(cab["content-length"])
        else:
            n = len(datos) - j
        respuestas.append(Respuesta(status, cab, datos[j:j + n], lista))
        i = j + n
    return respuestas


def leer_una_respuesta(sock, metodo="GET", maximo=10.0):
    """Lee exactamente una respuesta. Devuelve (Respuesta o None, cerrado)."""
    sock.settimeout(maximo)
    datos = b""
    limite = time.time() + maximo
    while time.time() < limite:
        resp = separar_respuestas(datos, (metodo,))
        if resp and resp[0].status != -1:
            r = resp[0]
            n = int(r.cab("content-length") or 0)
            if r.status in (204, 304) or metodo == "HEAD" or len(r.cuerpo) >= n:
                return r, False
        try:
            trozo = sock.recv(65536)
        except socket.timeout:
            break
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            return (separar_respuestas(datos, (metodo,)) or [None])[0], True
        if not trozo:
            return (separar_respuestas(datos, (metodo,)) or [None])[0], True
        datos += trozo
    return (separar_respuestas(datos, (metodo,)) or [None])[0], False


def es_ascii(datos):
    try:
        datos.decode("ascii")
        return True
    except UnicodeDecodeError:
        return False
