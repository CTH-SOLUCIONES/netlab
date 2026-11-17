"""Base comun de los guiones de Playwright: servidor propio y Chromium sin cabeza."""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from netlab_servidor import Servidor  # noqa: E402

try:
    from playwright.sync_api import Error as ErrorPlaywright
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
    ErrorPlaywright = Exception

necesita_playwright = unittest.skipIf(
    sync_playwright is None, "playwright no esta instalado: las pruebas de la interfaz se omiten")

PAUSA_SONDEO = 1.5  # la interfaz consulta /api/v1/topology cada 1,5 s


class ConNavegador(unittest.TestCase):
    ENTORNO = {}

    @classmethod
    def setUpClass(cls):
        cls.servidor = Servidor(**cls.ENTORNO).arrancar()
        try:
            cls._pw = sync_playwright().start()
            try:
                cls.navegador = cls._pw.chromium.launch()
            except ErrorPlaywright as e:
                cls._pw.stop()
                if "Executable doesn't exist" in str(e):
                    raise unittest.SkipTest("falta el Chromium de playwright (python3 -m playwright install chromium)")
                raise
        except BaseException:
            cls.servidor.detener()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.navegador.close()
            cls._pw.stop()
        finally:
            cls.servidor.detener()

    def abrir(self, ruta="/", respuestas_prompt=None):
        """Abre la interfaz en un contexto nuevo (sessionStorage vacio).

        Registra errores de JavaScript y dialogos. ``respuestas_prompt`` es la
        lista de textos con que se contestan, en orden, los prompt()."""
        contexto = self.navegador.new_context(viewport={"width": 1440, "height": 900})
        self.addCleanup(contexto.close)
        pagina = contexto.new_page()
        pagina.errores = []
        pagina.dialogos = []
        respuestas = list(respuestas_prompt or [])

        def dialogo(d):
            pagina.dialogos.append((d.type, d.message))
            if d.type == "prompt":
                if respuestas:
                    d.accept(respuestas.pop(0))
                else:
                    d.dismiss()
            elif d.type == "confirm":
                d.accept()
            else:
                d.dismiss()

        pagina.on("pageerror", lambda e: pagina.errores.append(str(e)))
        pagina.on("dialog", dialogo)
        pagina.goto(self.servidor.base + ruta, wait_until="networkidle")
        return pagina

    @staticmethod
    def esperar(pagina, condicion_js, maximo=5.0):
        """Espera a que la expresion JS sea verdadera; devuelve si lo fue."""
        limite = time.time() + maximo
        while time.time() < limite:
            if pagina.evaluate(condicion_js):
                return True
            pagina.wait_for_timeout(150)
        return bool(pagina.evaluate(condicion_js))
