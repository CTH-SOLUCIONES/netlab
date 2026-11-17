"""Pruebas adversariales de la API de NetLab: ronda de correccion v1.0.0.

Salen del contrato de la ronda (criterios B1-B4 e I1-I5 y la restriccion de
mensajes en ASCII), no de la implementacion. Cada prueba nombra en su docstring
el criterio que verifica. Las marcadas "Regresion" protegen un comportamiento
que ya era correcto en main (02d6026): no pueden fallar ahi y estan para que la
correccion no lo rompa.

Correr:
    python3 -m unittest discover -s tests -v
Contra otra copia (por ejemplo main sin corregir):
    NETLAB_APP=/ruta/a/netlab/app/netlab.py python3 -m unittest discover -s tests -v
Sin las lentas (I1, unos 31 s):
    NETLAB_SALTAR_LENTAS=1 python3 -m unittest discover -s tests -v
"""

import http.client
import importlib.util
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from netlab_servidor import (  # noqa: E402
    CLAVE_FABRICA, CLAVE_LECTURA_FABRICA, TOPE_EQUIPOS, Servidor, conectar,
    es_ascii, leer_hasta_cierre, leer_una_respuesta, metodos_de_allow,
    peticion_cruda, ruta_app, separar_respuestas)

ADMIN = {"X-API-Key": CLAVE_FABRICA}
JSON = {"Content-Type": "application/json"}
TE111 = "/api/v1/devices/csr-core-01/interfaces/Te1%2F1%2F1"
SHUTDOWN_TE111 = TE111 + "/shutdown"
SALTAR_LENTAS = os.environ.get("NETLAB_SALTAR_LENTAS") == "1"


class ConServidor(unittest.TestCase):
    """Un servidor por clase. ENTORNO se suma al entorno del proceso."""

    ENTORNO = {}
    servidor = None

    @classmethod
    def setUpClass(cls):
        cls.servidor = Servidor(**cls.ENTORNO).arrancar()

    @classmethod
    def tearDownClass(cls):
        if cls.servidor is not None:
            cls.servidor.detener()

    def pedir(self, metodo, ruta, cuerpo=None, cabeceras=None, **kw):
        return self.servidor.pedir(metodo, ruta, cuerpo, cabeceras, **kw)

    def interfaz(self, equipo, ruta=TE111):
        r = self.pedir("GET", ruta, cabeceras={"X-Lab-Team": equipo})
        self.assertEqual(r.status, 200, r)
        return r.json


# ===========================================================================
# B1. XSS almacenado: el esc del HTML servido
# ===========================================================================
class B1EscDeLaInterfaz(ConServidor):

    @staticmethod
    def definicion_de_esc(html):
        m = re.search(r"(?:const|let|var)\s+esc\s*=|function\s+esc\s*\(", html)
        if not m:
            return None
        lineas = html[m.start():].splitlines()
        tomadas = [lineas[0]]
        for linea in lineas[1:30]:
            if re.match(r"\s*(?:const|let|var|function|async\s+function|/\*|//)\b", linea):
                break
            tomadas.append(linea)
        return "\n".join(tomadas)

    def test_b1_esc_escapa_las_cinco_entidades(self):
        """B1: el esc del HTML servido escapa & < > " y la comilla simple."""
        html = self.pedir("GET", "/").texto
        esc = self.definicion_de_esc(html)
        self.assertIsNotNone(esc, "no se encontro la funcion esc en la interfaz servida")
        entidades = {
            "&": ("&amp;", "&#38;", "&#x26;"),
            "<": ("&lt;", "&#60;", "&#x3c;"),
            ">": ("&gt;", "&#62;", "&#x3e;"),
            '"': ("&quot;", "&#34;", "&#x22;"),
            "'": ("&#39;", "&#039;", "&#x27;", "&apos;"),
        }
        faltan = [c for c, formas in entidades.items()
                  if not any(f in esc.lower() for f in formas)]
        self.assertEqual(faltan, [], f"esc no escapa {faltan}. Definicion servida:\n{esc}")


# ===========================================================================
# B2. Clave de administrador en la interfaz
# ===========================================================================
class B2ClaveDeFabrica(ConServidor):

    def test_b2_regresion_con_clave_de_fabrica_la_interfaz_la_trae(self):
        """B2 (Regresion): con la clave de fabrica la interfaz funciona como hoy y la trae."""
        for ruta in ("/", "/ui", "/index.html"):
            with self.subTest(ruta=ruta):
                r = self.pedir("GET", ruta)
                self.assertEqual(r.status, 200)
                self.assertIn(CLAVE_FABRICA, r.texto)


class B2ClavePropia(ConServidor):
    CLAVE = "clave-propia-" + secrets.token_hex(8)
    ENTORNO = {"NETLAB_ADMIN_KEY": CLAVE}

    def test_b2_ninguna_clave_de_administrador_en_la_interfaz(self):
        """B2: con NETLAB_ADMIN_KEY propia, GET / no contiene la clave propia ni la de fabrica (pone null)."""
        for ruta in ("/", "/ui", "/ui/", "/index.html", "/?team=x"):
            with self.subTest(ruta=ruta):
                r = self.pedir("GET", ruta)
                self.assertEqual(r.status, 200)
                self.assertNotIn(self.CLAVE, r.texto, "la interfaz sirve la clave propia")
                self.assertNotIn(CLAVE_FABRICA, r.texto,
                                 "la interfaz trae la clave de fabrica aunque el servidor tiene otra: "
                                 "deberia recibir null y pedirla")

    def test_b2_regresion_la_clave_propia_no_sale_por_la_api(self):
        """B2 (Regresion): la clave propia no aparece en ninguna respuesta de lectura ni de error."""
        pruebas = [("GET", "/api/v1", None, {}), ("GET", "/api/v1/health", None, {}),
                   ("GET", "/api/v1/topology", None, {}), ("GET", "/api/v1/stats", None, {}),
                   ("GET", "/api/v1/events", None, {}), ("POST", SHUTDOWN_TE111, None, {}),
                   ("POST", SHUTDOWN_TE111, None, {"X-API-Key": "mala"})]
        for metodo, ruta, cuerpo, cab in pruebas:
            with self.subTest(metodo=metodo, ruta=ruta, cab=cab):
                r = self.pedir(metodo, ruta, cuerpo, dict(cab, **{"X-Lab-Team": "b2fuga"}))
                self.assertNotIn(self.CLAVE.encode(), r.cuerpo)
                self.assertNotIn(self.CLAVE, json.dumps(r.cabeceras))

    def test_b2_la_clave_de_fabrica_ya_no_vale_y_da_401(self):
        """B2 + I3: con clave propia, la de fabrica es invalida: 401 con WWW-Authenticate y sin efecto."""
        r = self.pedir("POST", SHUTDOWN_TE111, cabeceras={"X-Lab-Team": "b2", **ADMIN})
        self.assertEqual(r.status, 401, r)
        self.assertIsNotNone(r.cab("WWW-Authenticate"))
        self.assertEqual(self.interfaz("b2")["admin_status"], "up", "la clave de fabrica opero")

    def test_b2_regresion_la_clave_propia_opera(self):
        """B2 (Regresion): la clave propia si modifica la red."""
        r = self.pedir("POST", SHUTDOWN_TE111,
                       cabeceras={"X-Lab-Team": "b2ok", "X-API-Key": self.CLAVE})
        self.assertEqual(r.status, 200, r)
        self.assertEqual(self.interfaz("b2ok")["admin_status"], "administratively down")


class B2AvisoAlArrancar(unittest.TestCase):
    """B2: el banner advierte si alguna clave es la de fabrica y dice como cambiarla."""

    @staticmethod
    def salida(**entorno):
        s = Servidor(**entorno).arrancar()
        try:
            time.sleep(0.3)
            return s.salida()
        finally:
            s.detener()

    @staticmethod
    def lineas_que_nombran_variables(salida):
        return [l for l in salida.splitlines()
                if "NETLAB_ADMIN_KEY" in l or "NETLAB_READONLY_KEY" in l]

    def test_b2_aviso_con_las_dos_claves_de_fabrica(self):
        """B2: con las dos claves de fabrica, el banner lo advierte y nombra las dos variables."""
        salida = self.salida()
        self.assertIn("NETLAB_ADMIN_KEY", salida, salida)
        self.assertIn("NETLAB_READONLY_KEY", salida, salida)

    def test_b2_aviso_si_solo_la_de_lectura_es_de_fabrica(self):
        """B2: basta con que una sea de fabrica (aqui la de lectura) para advertirlo."""
        salida = self.salida(NETLAB_ADMIN_KEY="propia-" + secrets.token_hex(6))
        self.assertIn("NETLAB_READONLY_KEY", salida, salida)

    def test_b2_aviso_si_solo_la_de_administrador_es_de_fabrica(self):
        """B2: basta con que una sea de fabrica (aqui la de administrador) para advertirlo."""
        salida = self.salida(NETLAB_READONLY_KEY="propia-" + secrets.token_hex(6))
        self.assertIn("NETLAB_ADMIN_KEY", salida, salida)

    def test_b2_sin_aviso_si_ninguna_es_de_fabrica(self):
        """B2: con las dos claves propias, el aviso desaparece (menos lineas que nombran las variables)."""
        con_aviso = self.lineas_que_nombran_variables(self.salida())
        sin_aviso = self.lineas_que_nombran_variables(self.salida(
            NETLAB_ADMIN_KEY="propia-" + secrets.token_hex(6),
            NETLAB_READONLY_KEY="propia-" + secrets.token_hex(6)))
        self.assertLess(len(sin_aviso), len(con_aviso),
                        f"con claves de fabrica: {con_aviso}; con claves propias: {sin_aviso}")


# ===========================================================================
# B3. Tope de equipos
# ===========================================================================
class B3TopeDeEquipos(unittest.TestCase):
    """B3: MAX_EQUIPOS = 50, shared cuenta. Un servidor nuevo por prueba."""

    def setUp(self):
        self.servidor = Servidor().arrancar()
        self.addCleanup(self.servidor.detener)

    def pedir(self, metodo, ruta, cuerpo=None, cabeceras=None, **kw):
        return self.servidor.pedir(metodo, ruta, cuerpo, cabeceras, **kw)

    def llenar(self, nombres):
        """Una lectura valida por equipo. Devuelve {equipo: status}."""
        c = http.client.HTTPConnection("127.0.0.1", self.servidor.puerto, timeout=10)
        estados = {}
        try:
            for n in nombres:
                c.request("GET", "/api/v1/devices", headers={"X-Lab-Team": n})
                r = c.getresponse()
                r.read()
                estados[n] = r.status
        finally:
            c.close()
        return estados

    def llenar_hasta_el_tope(self, prefijo="eq"):
        nombres = [f"{prefijo}{i:02d}" for i in range(TOPE_EQUIPOS - 1)]
        self.llenar(nombres)
        return nombres

    def assertTopeVigente(self):
        r = self.pedir("GET", "/api/v1/devices", cabeceras={"X-Lab-Team": "recien-llegado"})
        self.assertEqual(r.status, 403, f"con {TOPE_EQUIPOS} equipos, uno nuevo deberia dar 403: {r}")

    def test_b3_300_rutas_inexistentes_no_crean_mundos(self):
        """B3 (criterio): 300 peticiones a rutas inexistentes con 300 equipos dejan equipos_activos en 1."""
        c = http.client.HTTPConnection("127.0.0.1", self.servidor.puerto, timeout=10)
        try:
            for i in range(300):
                ruta = "/api/v1/no-existe" if i % 2 else "/no-existe"
                c.request("GET", ruta, headers={"X-Lab-Team": f"fantasma{i}"})
                r = c.getresponse()
                r.read()
                self.assertEqual(r.status, 404)
        finally:
            c.close()
        activos = self.servidor.equipos_activos()
        self.assertEqual(activos, ["shared"], f"{len(activos)} equipos activos")

    def test_b3_fallos_de_ruta_cuerpo_y_clave_no_crean_mundos(self):
        """B3: 404, 405, 401, 403, 415, 400, 413, OPTIONS, HEAD y la interfaz no crean mundos."""
        vlans = "/api/v1/devices/csr-core-01/vlans"
        # (descripcion, estados aceptables, metodo, ruta, cuerpo, cabeceras)
        casos = [
            ("404 fuera de /api", {404}, "GET", "/no-existe", None, {}),
            ("404 en /api", {404}, "GET", "/api/v1/no-existe", None, {}),
            ("405", {405}, "DELETE", "/api/v1/devices", None, {}),
            ("405 HEAD en accion", {405}, "HEAD", "/api/v1/devices/csr-core-01/actions/save", None, {}),
            ("OPTIONS", {204}, "OPTIONS", "/api/v1/devices", None, {}),
            ("OPTIONS 404", {404}, "OPTIONS", "/api/v1/no-existe", None, {}),
            ("401 sin clave", {401}, "POST", vlans, {"id": 3001}, {}),
            ("clave invalida", {401, 403}, "POST", vlans, {"id": 3002}, {"X-API-Key": "mala"}),
            ("403 lectura", {403}, "POST", vlans, {"id": 3003}, {"X-API-Key": CLAVE_LECTURA_FABRICA}),
            ("415", {415}, "POST", vlans, '{"id": 3004}', dict(ADMIN, **{"Content-Type": "text/plain"})),
            ("400 JSON roto", {400}, "POST", vlans, '{"id": ', dict(ADMIN, **JSON)),
            ("interfaz", {200}, "GET", "/", None, {}),
            ("favicon", {204}, "GET", "/favicon.ico", None, {}),
        ]
        equipo_de = {}
        for n, (desc, esperados, metodo, ruta, cuerpo, cab) in enumerate(casos):
            equipo = f"fallo{n:02d}"
            equipo_de[equipo] = desc
            r = self.pedir(metodo, ruta, cuerpo, dict(cab, **{"X-Lab-Team": equipo}))
            self.assertIn(r.status, esperados, f"{desc}: {r}")
        # Cuerpos que la API rechaza sin leerlos: van por socket, sin mandar el cuerpo.
        for n, (desc, esperado, cl) in enumerate((("413", 413, "2000000"),
                                                  ("400 Content-Length invalido", 400, "abc"))):
            equipo = f"crudo{n}"
            equipo_de[equipo] = desc
            s = conectar(self.servidor.puerto)
            try:
                s.sendall(peticion_cruda("POST", vlans, dict(ADMIN, **JSON, **{
                    "X-Lab-Team": equipo, "Content-Length": cl})))
                r, _ = leer_una_respuesta(s, "POST")
            finally:
                s.close()
            self.assertIsNotNone(r, desc)
            self.assertEqual(r.status, esperado, desc)
        creados = [f"{e} ({equipo_de[e]})" for e in self.servidor.equipos_activos() if e != "shared"]
        self.assertEqual(creados, [], "peticiones fallidas crearon mundos")

    def test_b3_fallos_dentro_del_manejador_no_crean_mundos(self):
        """B3 (lectura estricta de 'cualquier ruta que falle (404...)'): 404 de dispositivo, 400, 409 y 422 no crean mundos."""
        vlans = "/api/v1/devices/csr-core-01/vlans"
        casos = [
            ("404 dispositivo", "GET", "/api/v1/devices/no-existe", None, {}),
            ("404 interfaz", "GET", "/api/v1/devices/csr-core-01/interfaces/Xx9", None, {}),
            ("400 parametro", "GET", "/api/v1/devices?limit=abc", None, {}),
            ("422 validacion", "POST", vlans, {"id": "diez"}, ADMIN),
            ("409 duplicada", "POST", vlans, {"id": 10}, ADMIN),
        ]
        equipo_de = {}
        for n, (desc, metodo, ruta, cuerpo, cab) in enumerate(casos):
            equipo = f"manejador{n}"
            equipo_de[equipo] = desc
            r = self.pedir(metodo, ruta, cuerpo, dict(cab, **{"X-Lab-Team": equipo}))
            self.assertEqual(r.status, int(desc[:3]), f"{desc}: {r}")
        creados = [f"{e} ({equipo_de[e]})" for e in self.servidor.equipos_activos() if e != "shared"]
        self.assertEqual(creados, [], "peticiones fallidas dentro del manejador crearon mundos")

    def test_b3_tope_de_50_contando_shared(self):
        """B3 (criterio): 60 lecturas validas con equipos distintos: el total queda en 50 (shared cuenta) y el resto da 403 limite_de_equipos."""
        self.assertEqual(self.servidor.equipos_activos(), ["shared"])
        nombres = [f"valido{i:02d}" for i in range(60)]
        c = http.client.HTTPConnection("127.0.0.1", self.servidor.puerto, timeout=10)
        respuestas = {}
        try:
            for n in nombres:
                c.request("GET", "/api/v1/devices", headers={"X-Lab-Team": n})
                r = c.getresponse()
                respuestas[n] = (r.status, r.read())
        finally:
            c.close()
        aceptados = [n for n, (st, _) in respuestas.items() if st == 200]
        rechazados = [(n, st, cuerpo) for n, (st, cuerpo) in respuestas.items() if st != 200]
        activos = self.servidor.equipos_activos()
        self.assertEqual(len(activos), TOPE_EQUIPOS, f"equipos activos: {len(activos)}")
        self.assertEqual(len(aceptados), TOPE_EQUIPOS - 1,
                         "shared cuenta dentro del tope: se aceptan 49 equipos nuevos")
        self.assertEqual(set(activos), set(aceptados) | {"shared"})
        for n, st, cuerpo in rechazados:
            with self.subTest(equipo=n):
                self.assertEqual(st, 403)
                error = json.loads(cuerpo)["error"]
                self.assertEqual(error["codigo"], "limite_de_equipos")
                self.assertIn(str(TOPE_EQUIPOS), error["mensaje"], "el mensaje debe decir el tope")
                self.assertTrue(es_ascii(cuerpo), f"mensaje con caracteres no ASCII: {cuerpo!r}")

    def test_b3_tope_con_peticiones_simultaneas(self):
        """B3: 100 equipos nuevos a la vez no pasan el tope (comprobar y crear bajo el mismo LOCK)."""
        n = 100
        barrera = threading.Barrier(n)
        estados = [None] * n

        def cliente(i):
            c = http.client.HTTPConnection("127.0.0.1", self.servidor.puerto, timeout=20)
            try:
                c.connect()
                barrera.wait(10)
                c.request("GET", "/api/v1/devices", headers={"X-Lab-Team": f"carrera{i:03d}"})
                r = c.getresponse()
                r.read()
                estados[i] = r.status
            except Exception as e:  # noqa: BLE001
                estados[i] = repr(e)
            finally:
                c.close()

        hilos = [threading.Thread(target=cliente, args=(i,)) for i in range(n)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(30)
        activos = self.servidor.equipos_activos()
        self.assertEqual(len(activos), TOPE_EQUIPOS, f"{len(activos)} equipos con {n} simultaneos")
        self.assertEqual(estados.count(200), TOPE_EQUIPOS - 1, estados)
        self.assertEqual(estados.count(403), n - (TOPE_EQUIPOS - 1), estados)

    def test_b3_alias_saneados_no_son_equipos_nuevos(self):
        """B3: con el tope lleno, un X-Lab-Team que el saneamiento convierte en un equipo existente (o en shared) no da 403."""
        largo = "L" * 24
        nombres = [largo] + [f"alias-{i:02d}" for i in range(TOPE_EQUIPOS - 2)]
        self.llenar(nombres)
        self.assertTopeVigente()
        alias = {
            "alias-01!": "alias-01", "al.ias-02": "alias-02", "ali as-03": "alias-03",
            "alias-04/../": "alias-04", largo + "XYZ": largo,
            "!!!": "shared", "": "shared", "ñá": "shared",
        }
        for crudo, esperado in alias.items():
            with self.subTest(x_lab_team=crudo):
                c = http.client.HTTPConnection("127.0.0.1", self.servidor.puerto, timeout=10)
                try:
                    c.putrequest("GET", "/api/v1/devices")
                    c.putheader("X-Lab-Team", crudo.encode("utf-8"))
                    c.endheaders()
                    r = c.getresponse()
                    r.read()
                finally:
                    c.close()
                self.assertEqual(r.status, 200, f"{crudo!r} es {esperado!r}, que ya existe")
                self.assertEqual(r.getheader("X-NetLab-Team"), esperado)
        self.assertEqual(len(self.servidor.equipos_activos()), TOPE_EQUIPOS)

    def test_b3_flujo_sse_no_esquiva_el_tope(self):
        """B3: con el tope lleno, abrir /api/v1/stream con un equipo nuevo no crea su mundo."""
        self.llenar_hasta_el_tope()
        self.assertTopeVigente()
        s = conectar(self.servidor.puerto)
        try:
            s.sendall(peticion_cruda("GET", "/api/v1/stream", {"X-Lab-Team": "sse-intruso"}))
            leer_hasta_cierre(s, silencio=1.5, maximo=2.0)
        finally:
            s.close()
        activos = self.servidor.equipos_activos()
        self.assertNotIn("sse-intruso", activos)
        self.assertLessEqual(len(activos), TOPE_EQUIPOS)

    def test_b3_equipos_existentes_siguen_operando_con_el_tope(self):
        """B3: con el tope lleno, los equipos que ya existen (y shared) leen y escriben normalmente."""
        nombres = self.llenar_hasta_el_tope()
        self.assertTopeVigente()
        r = self.pedir("POST", SHUTDOWN_TE111, cabeceras=dict(ADMIN, **{"X-Lab-Team": nombres[0]}))
        self.assertEqual(r.status, 200, r)
        self.assertEqual(self.pedir("GET", "/api/v1/devices").status, 200)
        r = self.pedir("POST", SHUTDOWN_TE111, cabeceras=ADMIN)
        self.assertEqual(r.status, 200, r)


class B3RestaurarDesdeSQLite(unittest.TestCase):

    def test_b3_restaurar_carga_como_maximo_50_y_deja_shared(self):
        """B3: al restaurar 80 equipos desde SQLite se cargan como maximo 50, y shared sigue funcionando."""
        tmp = tempfile.mkdtemp(prefix="netlab-db-")
        self.addCleanup(shutil.rmtree, tmp, True)
        db = os.path.join(tmp, "lab.db")
        s = Servidor(NETLAB_DB=db).arrancar()
        try:
            self.assertEqual(s.pedir("GET", "/api/v1/devices", cabeceras={"X-Lab-Team": "semilla"}).status, 200)
            r = s.pedir("POST", "/api/v1/reset", cabeceras=dict(ADMIN, **{"X-Lab-Team": "semilla"}))
            self.assertEqual(r.status, 200, r)
        finally:
            s.detener()
        con = sqlite3.connect(db)
        try:
            columnas = [c[1] for c in con.execute("PRAGMA table_info(worlds)")]
            fila = con.execute("SELECT * FROM worlds WHERE team = ?", ("semilla",)).fetchone()
            self.assertIsNotNone(fila, "el reset no guardo el equipo; no se puede preparar la base")
            con.execute("DELETE FROM worlds")
            i_team = columnas.index("team")
            marcas = ",".join("?" * len(columnas))
            for n in range(80):
                nueva = list(fila)
                nueva[i_team] = f"restaurado{n:02d}"
                con.execute(f"INSERT INTO worlds ({','.join(columnas)}) VALUES ({marcas})", nueva)
            con.commit()
        finally:
            con.close()
        s = Servidor(NETLAB_DB=db).arrancar()
        try:
            activos = s.equipos_activos()
            shared = s.pedir("GET", "/api/v1/devices")
        finally:
            s.detener()
        self.assertLessEqual(len(activos), TOPE_EQUIPOS, f"se restauraron {len(activos)} equipos")
        self.assertIn("shared", activos)
        self.assertEqual(shared.status, 200, f"shared quedo fuera tras restaurar: {shared}")


# ===========================================================================
# B4. Cuerpo sin leer en una conexion keep-alive
# ===========================================================================
class B4CuerpoSinLeer(ConServidor):

    def escondida(self, equipo):
        """Peticion completa que se esconde en el cuerpo: apaga Te1/1/1 del equipo."""
        return peticion_cruda("POST", SHUTDOWN_TE111,
                              dict(ADMIN, **{"X-Lab-Team": equipo, "Content-Length": "0"}))

    def enviar_y_leer(self, datos, metodos):
        s = conectar(self.servidor.puerto)
        try:
            s.sendall(datos)
            crudo, cerrado = leer_hasta_cierre(s, silencio=1.5)
        finally:
            s.close()
        return separar_respuestas(crudo, metodos), cerrado

    @unittest.skipIf(shutil.which("curl") is None, "curl no esta instalado")
    def test_b4_curl_del_bloque_10_con_next(self):
        """B4 (criterio): el curl del bloque 10 seguido de --next con un GET da 405 y despues 200, no 501."""
        base = self.servidor.base + "/api/v1"
        cmd = ["curl", "-s", "-o", os.devnull, "-w", "%{http_code}\n", "-X", "POST",
               "-H", f"X-API-Key: {CLAVE_FABRICA}", "-H", "Content-Type: application/json",
               "-d", "{}", f"{base}/devices/csr-core-01/interfaces/Gi1/0/10",
               "--next", "-s", "-o", os.devnull, "-w", "%{http_code}\n", f"{base}/devices"]
        salida = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.split()
        self.assertEqual(salida, ["405", "200"])

    def test_b4_bloque_10_por_socket_como_lo_hace_curl(self):
        """B4 (criterio): POST con cuerpo a un recurso que no lo admite y luego GET; si el servidor no cierra, el GET va por la misma conexion y debe dar 200."""
        s = conectar(self.servidor.puerto)
        try:
            s.sendall(peticion_cruda("POST", "/api/v1/devices/csr-core-01/interfaces/Gi1/0/10",
                                     dict(ADMIN, **JSON), b"{}"))
            r1, cerrado = leer_una_respuesta(s, "POST")
            self.assertIsNotNone(r1)
            self.assertEqual(r1.status, 405)
            if cerrado or (r1.cab("connection") or "").lower() == "close":
                s.close()
                s = conectar(self.servidor.puerto)
            s.sendall(peticion_cruda("GET", "/api/v1/devices"))
            r2, _ = leer_una_respuesta(s, "GET")
        finally:
            s.close()
        self.assertIsNotNone(r2)
        self.assertEqual(r2.status, 200, r2)

    def test_b4_405_con_cuerpo_sin_leer_envia_connection_close_y_cierra(self):
        """B4: la respuesta con el cuerpo sin leer lleva Connection: close y el servidor cierra."""
        resps, cerrado = self.enviar_y_leer(
            peticion_cruda("POST", "/api/v1/devices", JSON, b'{"x": 1}'), ("POST",))
        self.assertEqual([r.status for r in resps], [405])
        self.assertEqual((resps[0].cab("connection") or "").lower(), "close")
        self.assertTrue(cerrado, "el servidor dejo la conexion abierta con el cuerpo sin leer")

    def test_b4_peticion_escondida_en_el_cuerpo_produce_una_sola_respuesta(self):
        """B4 (criterio): un POST cuyo cuerpo es una peticion HTTP completa produce una sola respuesta y la escondida no se ejecuta."""
        equipo = "victima"
        resps, _ = self.enviar_y_leer(
            peticion_cruda("POST", "/api/v1/devices", JSON, self.escondida(equipo)), ("POST", "POST"))
        self.assertEqual([r.status for r in resps], [405])
        self.assertEqual(self.interfaz(equipo)["admin_status"], "up",
                         "la peticion escondida en el cuerpo apago la interfaz de otro equipo")

    def test_b4_toda_respuesta_sin_leer_el_cuerpo_cierra(self):
        """B4: interfaz (GET y HEAD), favicon, OPTIONS, 404, 405 y 413 con cuerpo: una respuesta, Connection: close, cierre y sin efecto."""
        variantes = [
            ("interfaz GET /", "GET", "/", {}),
            ("interfaz HEAD /", "HEAD", "/", {}),
            ("favicon", "GET", "/favicon.ico", {}),
            ("OPTIONS", "OPTIONS", "/api/v1/devices", {}),
            ("404 en /api", "POST", "/api/v1/no-existe", JSON),
            ("404 fuera de /api", "POST", "/nada", JSON),
            ("405", "DELETE", "/api/v1/devices", JSON),
            ("413", "POST", "/api/v1/devices/csr-core-01/vlans",
             dict(ADMIN, **JSON, **{"Content-Length": "2000000"})),
        ]
        for n, (desc, metodo, ruta, cab) in enumerate(variantes):
            with self.subTest(desc):
                equipo = f"victima{n}"
                resps, cerrado = self.enviar_y_leer(
                    peticion_cruda(metodo, ruta, dict(cab, **{"X-Lab-Team": "atacante"}),
                                   self.escondida(equipo)),
                    (metodo, "POST"))
                self.assertEqual(len(resps), 1, f"respuestas: {[r.status for r in resps]}")
                self.assertEqual((resps[0].cab("connection") or "").lower(), "close")
                self.assertTrue(cerrado, "el servidor no cerro la conexion")
                self.assertEqual(self.interfaz(equipo)["admin_status"], "up",
                                 "la peticion escondida se ejecuto")

    def test_b4_regresion_cuerpo_leido_antes_del_error(self):
        """B4 (Regresion): 401, clave invalida, 403, 415 y 400 leen el cuerpo antes de responder; la peticion escondida no se ejecuta."""
        vlans = "/api/v1/devices/csr-core-01/vlans"
        variantes = [
            ("401 sin clave", JSON), ("clave invalida", dict(JSON, **{"X-API-Key": "mala"})),
            ("403 lectura", dict(JSON, **{"X-API-Key": CLAVE_LECTURA_FABRICA})),
            ("415", dict(ADMIN, **{"Content-Type": "text/plain"})), ("400 JSON", dict(ADMIN, **JSON)),
        ]
        for n, (desc, cab) in enumerate(variantes):
            with self.subTest(desc):
                equipo = f"leido{n}"
                resps, _ = self.enviar_y_leer(
                    peticion_cruda("POST", vlans, dict(cab, **{"X-Lab-Team": "atacante"}),
                                   self.escondida(equipo)), ("POST", "POST"))
                self.assertEqual(len(resps), 1, f"respuestas: {[r.status for r in resps]}")
                self.assertEqual(self.interfaz(equipo)["admin_status"], "up")

    def test_b4_regresion_keep_alive_cuando_el_cuerpo_se_leyo(self):
        """B4 (Regresion): con el cuerpo leido (201) la conexion sigue viva y atiende la siguiente peticion."""
        datos = (peticion_cruda("POST", "/api/v1/devices/csr-core-01/vlans",
                                dict(ADMIN, **JSON, **{"X-Lab-Team": "keepalive"}), b'{"id": 3901}')
                 + peticion_cruda("GET", "/api/v1/devices", {"X-Lab-Team": "keepalive"}))
        resps, cerrado = self.enviar_y_leer(datos, ("POST", "GET"))
        self.assertEqual([r.status for r in resps], [201, 200])
        self.assertNotEqual((resps[0].cab("connection") or "").lower(), "close")
        self.assertFalse(cerrado, "el servidor cerro una conexion sana")


# ===========================================================================
# I1. Conexiones sin tope ni tiempo limite
# ===========================================================================
def esperar_cierre(sock, maximo):
    """Segundos hasta que el servidor cierra la conexion, o None si no la cerro."""
    t0 = time.time()
    try:
        while True:
            restante = maximo - (time.time() - t0)
            if restante <= 0:
                return None
            sock.settimeout(restante)
            if not sock.recv(4096):
                return time.time() - t0
    except socket.timeout:
        return None
    except OSError:
        return time.time() - t0


@unittest.skipIf(SALTAR_LENTAS, "pruebas lentas desactivadas con NETLAB_SALTAR_LENTAS=1")
class I1ConexionesLentas(unittest.TestCase):
    """LENTA: unos 33 s con la correccion, 40 s como maximo.

    Abre a la vez una conexion con media cabecera, una muda, una keep-alive
    inactiva, un flujo SSE y un /demo/slow de 20 s, espera una sola vez y cada
    prueba revisa su resultado."""

    LIMITE = 35.0
    MAXIMO = 40.0
    resultados = {}

    @classmethod
    def setUpClass(cls):
        servidor = Servidor().arrancar()
        puerto = servidor.puerto
        r = cls.resultados = {}

        def media_cabecera():
            s = conectar(puerto)
            s.sendall(b"GET /api/v1/devices HTTP/1.1\r\nHost: x\r\n")
            r["media_cabecera"] = esperar_cierre(s, cls.MAXIMO)
            s.close()

        def muda():
            s = conectar(puerto)
            r["muda"] = esperar_cierre(s, cls.MAXIMO)
            s.close()

        def inactiva():
            s = conectar(puerto)
            s.sendall(peticion_cruda("GET", "/api/v1/health"))
            resp, _ = leer_una_respuesta(s)
            r["inactiva_status"] = resp.status if resp else None
            r["inactiva"] = esperar_cierre(s, cls.MAXIMO - 1)
            s.close()

        def sse():
            s = conectar(puerto)
            s.sendall(peticion_cruda("GET", "/api/v1/stream"))
            t0 = time.time()
            ultimo, cerrado = None, None
            s.settimeout(1.0)
            while time.time() - t0 < 33:
                try:
                    d = s.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    cerrado = time.time() - t0
                    break
                if not d:
                    cerrado = time.time() - t0
                    break
                ultimo = time.time() - t0
            r["sse_ultimo"], r["sse_cerrado"] = ultimo, cerrado
            s.close()

        def lenta():
            c = http.client.HTTPConnection("127.0.0.1", puerto, timeout=cls.MAXIMO)
            try:
                c.request("GET", "/api/v1/demo/slow?seconds=20")
                resp = c.getresponse()
                resp.read()
                r["lenta"] = resp.status
            except OSError as e:
                r["lenta"] = repr(e)
            finally:
                c.close()

        hilos = [threading.Thread(target=f, daemon=True) for f in (media_cabecera, muda, inactiva, sse, lenta)]
        try:
            for h in hilos:
                h.start()
            for h in hilos:
                h.join(cls.MAXIMO + 2)
        finally:
            servidor.detener()

    def assertCerradaAntesDelLimite(self, clave):
        t = self.resultados.get(clave)
        self.assertIsNotNone(t, f"la conexion '{clave}' seguia abierta a los {self.MAXIMO:.0f} s")
        self.assertLess(t, self.LIMITE, f"la conexion '{clave}' se cerro a los {t:.1f} s")

    def test_i1_media_cabecera_se_cierra_antes_de_35_s(self):
        """I1 (criterio): una conexion que manda media cabecera y se calla queda cerrada antes de 35 s."""
        self.assertCerradaAntesDelLimite("media_cabecera")

    def test_i1_conexion_muda_se_cierra_antes_de_35_s(self):
        """I1: una conexion que no manda nada queda cerrada antes de 35 s."""
        self.assertCerradaAntesDelLimite("muda")

    def test_i1_keep_alive_inactiva_se_cierra_antes_de_35_s(self):
        """I1: una conexion keep-alive que ya recibio su respuesta y se calla queda cerrada antes de 35 s."""
        self.assertEqual(self.resultados.get("inactiva_status"), 200)
        self.assertCerradaAntesDelLimite("inactiva")

    def test_i1_regresion_sse_sigue_vivo_pasados_30_s(self):
        """I1 (Regresion): el flujo SSE sigue recibiendo datos pasados los 30 s del tiempo limite."""
        self.assertIsNone(self.resultados.get("sse_cerrado"), "el servidor corto el flujo SSE")
        self.assertGreater(self.resultados.get("sse_ultimo") or 0, 31)

    def test_i1_regresion_demo_slow_de_20_s_responde(self):
        """I1 (Regresion): /demo/slow con 20 s sigue respondiendo 200."""
        self.assertEqual(self.resultados.get("lenta"), 200)


class I1TopeDeConexiones(unittest.TestCase):

    def test_i1_por_encima_de_200_conexiones_se_cierran(self):
        """I1: con 200 conexiones ocupadas, las siguientes se cierran de inmediato; al liberarlas el servidor se recupera."""
        try:
            import resource
            blando, duro = resource.getrlimit(resource.RLIMIT_NOFILE)
            deseado = 4096 if duro == resource.RLIM_INFINITY else min(duro, 4096)
            if blando < deseado:
                resource.setrlimit(resource.RLIMIT_NOFILE, (deseado, duro))
        except (ImportError, ValueError, OSError):
            pass
        servidor = Servidor().arrancar()
        self.addCleanup(servidor.detener)
        media = b"GET /api/v1/devices HTTP/1.1\r\nHost: x\r\n"
        abiertas = []
        self.addCleanup(lambda: [s.close() for s in abiertas])
        for _ in range(200):
            s = conectar(servidor.puerto)
            s.sendall(media)
            abiertas.append(s)
        time.sleep(1.0)
        extra = []
        for _ in range(10):
            s = conectar(servidor.puerto)
            try:
                s.sendall(media)
            except OSError:
                pass
            extra.append(s)
            abiertas.append(s)
        limite = time.time() + 5.0
        cerradas = sum(1 for s in extra
                       if esperar_cierre(s, max(0.05, limite - time.time())) is not None)
        for s in abiertas:
            s.close()
        abiertas.clear()
        recuperado = False
        fin = time.time() + 10
        while time.time() < fin and not recuperado:
            try:
                recuperado = servidor.pedir("GET", "/api/v1/health", timeout=2).status == 200
            except OSError:
                time.sleep(0.2)
        self.assertEqual(cerradas, 10, f"de 10 conexiones por encima del tope se cerraron {cerradas}")
        self.assertTrue(recuperado, "el servidor no volvio a atender tras liberar las conexiones")


# ===========================================================================
# I2. X-Forwarded-For y memoria del limite de peticiones
# ===========================================================================
class I2ForwardedFor(ConServidor):

    def test_i2_la_ip_de_x_forwarded_for_se_recorta_a_45(self):
        """I2: una IP de 60 KB en X-Forwarded-For queda en 45 caracteres (eco y registro)."""
        larga = "1" * 60_000
        r = self.pedir("GET", "/api/v1/demo/echo",
                       cabeceras={"X-Forwarded-For": larga, "X-Lab-Team": "xff"})
        self.assertEqual(r.status, 200)
        self.assertLessEqual(len(r.json["su_ip"]), 45)
        log = self.pedir("GET", "/api/v1/events", cabeceras={"X-Lab-Team": "xff"}).json["http_log"]
        largos = [len(x["ip"]) for x in log if len(x["ip"]) > 45]
        self.assertEqual(largos, [], "el registro guarda la IP sin recortar")

    def test_i2_regresion_ips_validas_se_conservan(self):
        """I2 (Regresion): una IPv6 de 45 caracteres y una IPv4 llegan intactas."""
        for ip in ("ffff:ffff:ffff:ffff:ffff:ffff:255.255.255.255", "203.0.113.9"):
            with self.subTest(ip=ip):
                r = self.pedir("GET", "/api/v1/demo/echo", cabeceras={"X-Forwarded-For": f"{ip}, 10.0.0.1"})
                self.assertEqual(r.json["su_ip"], ip)


class I2MemoriaDelLimiteDePeticiones(unittest.TestCase):

    def test_i2_3000_xff_distintos_de_60_kb_no_hacen_crecer_la_memoria(self):
        """I2 (criterio): 3.000 peticiones con XFF distintos de 60 KB no hacen crecer la memoria (ni RATE_STATE ni el registro)."""
        servidor = Servidor().arrancar()
        self.addCleanup(servidor.detener)
        antes = servidor.memoria_mb()
        relleno = "a" * 60_000
        c = http.client.HTTPConnection("127.0.0.1", servidor.puerto, timeout=30)
        try:
            for i in range(3000):
                c.request("GET", "/api/v1/demo/rate-limit", headers={"X-Forwarded-For": f"{i}-{relleno}"})
                c.getresponse().read()
        finally:
            c.close()
        crecimiento = servidor.memoria_mb() - antes
        self.assertLess(crecimiento, 50, f"la memoria crecio {crecimiento:.0f} MB")


class I2PurgaDeRateState(unittest.TestCase):

    def test_i2_rate_state_se_purga_fuera_de_la_ventana(self):
        """I2: RATE_STATE se purga; pasada la ventana solo quedan las entradas de la ultima ventana (en proceso, con reloj falso)."""
        spec = importlib.util.spec_from_file_location("netlab_bajo_prueba", ruta_app())
        mod = importlib.util.module_from_spec(spec)
        antes, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # no deja __pycache__ en app/
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.dont_write_bytecode = antes

        class Reloj:
            def __init__(self, t):
                self.t = t

            def time(self):
                return self.t

            monotonic = perf_counter = time

            def sleep(self, s):
                self.t += s

            def __getattr__(self, nombre):
                return getattr(time, nombre)

        reloj = Reloj(time.time())
        with mock.patch.object(mod, "time", reloj):
            mod.RATE_STATE.clear()
            for i in range(2000):
                mod.check_rate_limit(f"10.{i // 65536}.{(i // 256) % 256}.{i % 256}")
            reloj.t += mod.RATE_WINDOW * 3 + 1
            for _ in range(20):
                try:
                    mod.check_rate_limit("192.0.2.1")
                except mod.ApiError:
                    pass
        self.assertLessEqual(len(mod.RATE_STATE), 1,
                             f"RATE_STATE conserva {len(mod.RATE_STATE)} entradas fuera de la ventana")


# ===========================================================================
# I3. Clave invalida = 401
# ===========================================================================
class I3ClaveInvalida(ConServidor):

    def test_i3_clave_mala_en_un_post_da_401_con_www_authenticate(self):
        """I3 (criterio): -H "X-API-Key: mala" en un POST da 401 con WWW-Authenticate y no opera."""
        r = self.pedir("POST", SHUTDOWN_TE111, cabeceras={"X-API-Key": "mala", "X-Lab-Team": "i3"})
        self.assertEqual(r.status, 401, r)
        self.assertIsNotNone(r.cab("WWW-Authenticate"))
        self.assertEqual(self.interfaz("i3")["admin_status"], "up")

    def test_i3_clave_mala_en_cada_metodo_de_escritura(self):
        """I3: PUT, PATCH, DELETE y POST con clave invalida dan 401 con WWW-Authenticate."""
        gi = "/api/v1/devices/csr-core-01/interfaces/Gi1%2F0%2F10"
        casos = [("PUT", gi, {"enabled": False, "mode": "access", "vlan": 30}),
                 ("PATCH", gi, {"description": "intruso"}),
                 ("DELETE", "/api/v1/devices/csr-core-01/vlans/50", None),
                 ("POST", "/api/v1/devices/csr-core-01/vlans", {"id": 3333})]
        for metodo, ruta, cuerpo in casos:
            with self.subTest(metodo=metodo):
                r = self.pedir(metodo, ruta, cuerpo, {"X-API-Key": "mala", "X-Lab-Team": "i3m"})
                self.assertEqual(r.status, 401, r)
                self.assertIsNotNone(r.cab("WWW-Authenticate"))
        vlans = self.pedir("GET", "/api/v1/devices/csr-core-01/vlans", cabeceras={"X-Lab-Team": "i3m"}).json
        ids = {v["id"] for v in vlans["vlans"]}
        self.assertIn(50, ids)
        self.assertNotIn(3333, ids)

    def test_i3_claves_parecidas_son_invalidas(self):
        """I3: variantes de las claves reales (mayusculas, prefijo, sufijo, esquema) son invalidas: 401."""
        for clave in (CLAVE_FABRICA.upper(), CLAVE_FABRICA[:-1], CLAVE_FABRICA + "x",
                      "Bearer " + CLAVE_FABRICA, CLAVE_LECTURA_FABRICA.upper()):
            with self.subTest(clave=clave):
                r = self.pedir("POST", SHUTDOWN_TE111, cabeceras={"X-API-Key": clave, "X-Lab-Team": "i3p"})
                self.assertEqual(r.status, 401, r)
        self.assertEqual(self.interfaz("i3p")["admin_status"], "up")

    def test_i3_regresion_sin_clave_o_vacia_da_401(self):
        """I3 (Regresion): sin clave o con la cabecera vacia, 401 con WWW-Authenticate."""
        for cab in ({}, {"X-API-Key": ""}):
            with self.subTest(cab=cab):
                r = self.pedir("POST", SHUTDOWN_TE111, cabeceras=dict(cab, **{"X-Lab-Team": "i3v"}))
                self.assertEqual(r.status, 401, r)
                self.assertIsNotNone(r.cab("WWW-Authenticate"))

    def test_i3_regresion_clave_de_lectura_en_escritura_da_403(self):
        """I3 (Regresion): clave valida de solo lectura en un metodo de escritura da 403."""
        r = self.pedir("POST", SHUTDOWN_TE111,
                       cabeceras={"X-API-Key": CLAVE_LECTURA_FABRICA, "X-Lab-Team": "i3l"})
        self.assertEqual(r.status, 403, r)
        self.assertEqual(self.interfaz("i3l")["admin_status"], "up")


# ===========================================================================
# I4. 204 y 304 sin Content-Length
# ===========================================================================
class I4SinContentLength(ConServidor):
    DEV = "/api/v1/devices/csr-core-01"

    def test_i4_delete_de_vlan_da_204_sin_content_length(self):
        """I4 (criterio): DELETE de una VLAN da 204 sin Content-Length."""
        cab = dict(ADMIN, **{"X-Lab-Team": "i4"})
        self.assertEqual(self.pedir("POST", self.DEV + "/vlans", {"id": 3500}, cab).status, 201)
        r = self.pedir("DELETE", self.DEV + "/vlans/3500", cabeceras=cab)
        self.assertEqual(r.status, 204)
        self.assertIsNone(r.cab("Content-Length"), r.cabeceras)
        self.assertEqual(r.cuerpo, b"")

    def test_i4_options_da_204_sin_content_length(self):
        """I4 (criterio): OPTIONS da 204 sin Content-Length."""
        r = self.pedir("OPTIONS", self.DEV + "/vlans")
        self.assertEqual(r.status, 204)
        self.assertIsNone(r.cab("Content-Length"), r.cabeceras)

    def test_i4_304_sin_content_length_con_etag_y_cache_control(self):
        """I4 (criterio): GET con If-None-Match da 304 sin Content-Length, con el ETag y el Cache-Control del 200 (no-cache)."""
        r200 = self.pedir("GET", self.DEV)
        etag = r200.cab("ETag")
        self.assertIsNotNone(etag)
        r = self.pedir("GET", self.DEV, cabeceras={"If-None-Match": etag})
        self.assertEqual(r.status, 304)
        self.assertIsNone(r.cab("Content-Length"), r.cabeceras)
        self.assertEqual(r.cab("ETag"), etag)
        self.assertEqual(r.cab("Cache-Control"), r200.cab("Cache-Control"))
        self.assertEqual(r.cab("Cache-Control"), "no-cache")

    def test_i4_head_condicional_da_304_sin_content_length(self):
        """I4: HEAD con If-None-Match da 304 sin Content-Length y con Cache-Control."""
        etag = self.pedir("GET", self.DEV).cab("ETag")
        r = self.pedir("HEAD", self.DEV, cabeceras={"If-None-Match": etag})
        self.assertEqual(r.status, 304)
        self.assertIsNone(r.cab("Content-Length"), r.cabeceras)
        self.assertEqual(r.cab("Cache-Control"), "no-cache")

    def test_i4_los_demas_204_y_304_tampoco_llevan_content_length(self):
        """I4: ningun 204 ni 304 lleva Content-Length (favicon, /demo/status/204 y /demo/status/304)."""
        for ruta, esperado in (("/favicon.ico", 204), ("/api/v1/demo/status/204", 204),
                               ("/api/v1/demo/status/304", 304)):
            with self.subTest(ruta=ruta):
                r = self.pedir("GET", ruta)
                self.assertEqual(r.status, esperado)
                self.assertIsNone(r.cab("Content-Length"), r.cabeceras)

    def test_i4_regresion_keep_alive_tras_204_y_304(self):
        """I4 (Regresion): tras un 204 y un 304 la misma conexion sigue entregando respuestas bien delimitadas."""
        etag = self.pedir("GET", self.DEV).cab("ETag")
        cab = dict(ADMIN, **{"X-Lab-Team": "i4k"})
        self.assertEqual(self.pedir("POST", self.DEV + "/vlans", {"id": 3501}, cab).status, 201)
        datos = (peticion_cruda("DELETE", self.DEV + "/vlans/3501", cab)
                 + peticion_cruda("GET", self.DEV, {"If-None-Match": etag})
                 + peticion_cruda("GET", "/api/v1/health"))
        s = conectar(self.servidor.puerto)
        try:
            s.sendall(datos)
            crudo, _ = leer_hasta_cierre(s, silencio=1.0)
        finally:
            s.close()
        resps = separar_respuestas(crudo, ("DELETE", "GET", "GET"))
        self.assertEqual([r.status for r in resps], [204, 304, 200])
        self.assertEqual(resps[2].json["status"], "ok")


# ===========================================================================
# I5. Allow coherente
# ===========================================================================
class I5Allow(ConServidor):
    DEV = "/api/v1/devices/csr-core-01"
    ACCIONES = [DEV + "/interfaces/Te1/1/1/shutdown", DEV + "/interfaces/Te1/1/1/no-shutdown",
                DEV + "/interfaces/Te1%2F1%2F1/shutdown", DEV + "/interfaces/Gi1/0/10/no-shutdown"]

    def test_i5_head_en_accion_da_405_sin_head_en_allow(self):
        """I5 (criterio): HEAD .../actions/save da 405 con un Allow sin HEAD."""
        r = self.pedir("HEAD", self.DEV + "/actions/save")
        self.assertEqual(r.status, 405)
        allow = metodos_de_allow(r.cab("Allow"))
        self.assertNotIn("HEAD", allow)
        self.assertIn("POST", allow)

    def test_i5_options_en_ruta_de_accion_solo_options_y_post(self):
        """I5 (criterio): OPTIONS .../Te1/1/1/shutdown da un Allow que solo incluye OPTIONS y POST."""
        for ruta in self.ACCIONES + [self.DEV + "/actions/save", self.DEV + "/actions/reload", "/api/v1/reset"]:
            with self.subTest(ruta=ruta):
                r = self.pedir("OPTIONS", ruta)
                self.assertEqual(r.status, 204)
                self.assertEqual(metodos_de_allow(r.cab("Allow")), {"OPTIONS", "POST"})

    def test_i5_get_en_ruta_de_accion_da_405(self):
        """I5: la ruta de accion ya no cae en el patron de interfaz: GET .../shutdown da 405 con Allow OPTIONS, POST."""
        for ruta in self.ACCIONES:
            with self.subTest(ruta=ruta):
                r = self.pedir("GET", ruta)
                self.assertEqual(r.status, 405, r)
                self.assertEqual(metodos_de_allow(r.cab("Allow")), {"OPTIONS", "POST"})

    def test_i5_patch_en_ruta_de_accion_da_405_y_no_toca_la_interfaz(self):
        """I5: PATCH .../Te1/1/1/shutdown con clave da 405 y no modifica la interfaz."""
        cab = dict(ADMIN, **{"X-Lab-Team": "i5"})
        r = self.pedir("PATCH", self.ACCIONES[0], {"description": "intruso"}, cab)
        self.assertEqual(r.status, 405, r)
        self.assertNotEqual(self.interfaz("i5")["description"], "intruso")

    def test_i5_allow_coherente_en_todas_las_rutas(self):
        """I5: en cada ruta, HEAD esta en Allow si y solo si GET lo esta, y el Allow del 405 es el mismo de OPTIONS."""
        rutas = ["/api/v1", "/api/v1/devices", self.DEV, self.DEV + "/config", self.DEV + "/interfaces",
                 self.DEV + "/interfaces/Gi1/0/10", self.DEV + "/interfaces/Gi1%2F0%2F10",
                 self.DEV + "/vlans", self.DEV + "/vlans/10", self.DEV + "/actions/reload",
                 self.DEV + "/actions/save", "/api/v1/events", "/api/v1/topology", "/api/v1/stats",
                 "/api/v1/health", "/api/v1/reset", "/api/v1/demo/echo", "/api/v1/demo/status/200",
                 "/api/v1/demo/slow", "/api/v1/demo/rate-limit", "/api/v1/demo/redirect"] + self.ACCIONES
        cab = dict(ADMIN, **{"X-Lab-Team": "i5p"})
        for ruta in rutas:
            with self.subTest(ruta=ruta):
                o = self.pedir("OPTIONS", ruta)
                self.assertEqual(o.status, 204)
                allow = metodos_de_allow(o.cab("Allow"))
                self.assertIn("OPTIONS", allow)
                self.assertEqual("HEAD" in allow, "GET" in allow, f"Allow: {sorted(allow)}")
                for metodo in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"):
                    if metodo in allow:
                        continue
                    r = self.pedir(metodo, ruta, cabeceras=cab)
                    self.assertEqual(r.status, 405, f"{metodo} {ruta}: {r.status}")
                    self.assertEqual(metodos_de_allow(r.cab("Allow")), allow,
                                     f"el Allow del 405 de {metodo} no coincide con el de OPTIONS")

    def test_i5_regresion_head_donde_hay_get(self):
        """I5 (Regresion): donde GET esta permitido, HEAD responde 200 y aparece en Allow."""
        self.assertEqual(self.pedir("HEAD", "/api/v1/devices").status, 200)
        allow = metodos_de_allow(self.pedir("OPTIONS", "/api/v1/devices").cab("Allow"))
        self.assertTrue({"GET", "HEAD", "OPTIONS"} <= allow, allow)


# ===========================================================================
# Restriccion: mensajes de la API en ASCII
# ===========================================================================
class MensajesEnASCII(ConServidor):

    def test_ascii_el_429_dice_supero_sin_tilde(self):
        """Restriccion ASCII: el significado del 429 en /demo/status dice 'supero', sin tilde."""
        r = self.pedir("GET", "/api/v1/demo/status/429")
        self.assertTrue(es_ascii(r.cuerpo), r.texto)
        self.assertIn("supero", r.json["significado"])

    def test_ascii_todas_las_respuestas_de_la_api(self):
        """Restriccion ASCII: respuestas normales y de error (incluidos los mensajes nuevos 401 y 413) sin tildes."""
        dev = "/api/v1/devices/csr-core-01"
        vlans = dev + "/vlans"
        t = {"X-Lab-Team": "ascii"}
        casos = [("GET", "/api/v1", None, {}), ("GET", "/api/v1/devices", None, {}),
                 ("GET", "/api/v1/no-existe", None, {}), ("GET", "/no-existe", None, {}),
                 ("DELETE", "/api/v1/devices", None, {}),
                 ("POST", vlans, {"id": 3600}, {}), ("POST", vlans, {"id": 3601}, {"X-API-Key": "mala"}),
                 ("POST", vlans, {"id": 3602}, {"X-API-Key": CLAVE_LECTURA_FABRICA}),
                 ("POST", vlans, '{"id": 1}', dict(ADMIN, **{"Content-Type": "text/plain"})),
                 ("POST", vlans, '{"id": ', dict(ADMIN, **JSON)),
                 ("POST", vlans, {"id": "diez"}, ADMIN), ("POST", vlans, {"id": 10}, ADMIN),
                 ("PATCH", dev + "/interfaces/Gi1%2F0%2F10", {"description": "x"},
                  dict(ADMIN, **{"If-Match": '"viejo"'})),
                 ("PATCH", dev + "/interfaces/Gi1%2F0%2F10", None, ADMIN),
                 ("POST", dev + "/actions/reload", {}, ADMIN),
                 ("GET", "/api/v1/devices?limit=abc", None, {}), ("GET", "/api/v1/devices?offset=-1", None, {}),
                 ("GET", "/api/v1/demo/redirect", None, {})]
        casos += [("GET", f"/api/v1/demo/status/{c}", None, {}) for c in range(200, 600)]
        no_ascii = []
        for metodo, ruta, cuerpo, cab in casos:
            r = self.pedir(metodo, ruta, cuerpo, dict(cab, **t))
            if not es_ascii(r.cuerpo):
                no_ascii.append(f"{metodo} {ruta} -> {r.status}: {r.texto[:160]}")
        for _ in range(6):
            r = self.pedir("GET", "/api/v1/demo/rate-limit", cabeceras={"X-Forwarded-For": "198.51.100.77"})
        self.assertEqual(r.status, 429)
        if not es_ascii(r.cuerpo):
            no_ascii.append(f"429 rate-limit: {r.texto[:160]}")
        s = conectar(self.servidor.puerto)
        try:
            s.sendall(peticion_cruda("POST", vlans, dict(ADMIN, **JSON, **{"Content-Length": "2000000"})))
            r, _ = leer_una_respuesta(s, "POST")
        finally:
            s.close()
        self.assertEqual(r.status, 413)
        if not es_ascii(r.cuerpo):
            no_ascii.append(f"413: {r.texto[:160]}")
        self.assertEqual(no_ascii, [], "\n".join(no_ascii))


if __name__ == "__main__":
    unittest.main()
