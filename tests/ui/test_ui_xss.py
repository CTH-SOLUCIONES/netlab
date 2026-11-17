"""B1 en el navegador: XSS almacenado en la descripcion de una interfaz.

Se guardan descripciones hostiles por la API, se abre la interfaz sin tocar nada
y se comprueba que ninguna se ejecuta, que no aparecen elementos ni atributos
inyectados y que el texto se muestra tal cual en el tooltip.
"""

import os
import re
import sys
import unittest

try:
    from .comun import PAUSA_SONDEO, ConNavegador, necesita_playwright
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from comun import PAUSA_SONDEO, ConNavegador, necesita_playwright

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from netlab_servidor import CLAVE_FABRICA, conectar, leer_una_respuesta, peticion_cruda  # noqa: E402

EQUIPO = "xss"
EQUIPO_RUTA = "xss-ruta"  # sin descripciones hostiles: aisla la prueba del registro
CENTINELA = "(window.__xss=(window.__xss||[]).concat({n}))"

# interfaz de csr-core-01 -> (nombre del ataque, descripcion)
CARGAS = {
    "GigabitEthernet1/0/10": ("criterio del contrato",
                              "x' style='animation:latido 1s' onanimationstart='document.title=1//"),
    "GigabitEthernet1/0/11": ("comilla simple con centinela",
                              "x' style='animation:latido 1s' onanimationstart='" + CENTINELA.format(n=2) + "//"),
    "GigabitEthernet1/0/12": ("comilla doble",
                              'x" style="animation:latido 1s" onanimationstart="' + CENTINELA.format(n=3) + '//'),
    "GigabitEthernet1/0/1": ("cierre de div e img",
                             '</div><img src=x onerror="' + CENTINELA.format(n=4) + '">'),
    "TenGigabitEthernet1/1/1": ("cierre de script",
                                "</script><script>" + CENTINELA.format(n=5) + "</script>"),
    "TenGigabitEthernet1/1/2": ("entidad ya codificada",
                                "x&#39; style=&#39;animation:latido 1s&#39; onanimationstart=&#39;"
                                + CENTINELA.format(n=6) + "//"),
    "Vlan1": ("plantilla y comilla invertida", "`${" + CENTINELA.format(n=7) + "}` '\"<b>"),
}
RUTA_HOSTIL = "/api/v1/<img/src=x/onerror=" + CENTINELA.format(n=8).replace(" ", "") + ">"

SOSPECHOSOS_JS = """() => {
  const zonas = ['#devs', '#tt', '#logHttp', '#logEv', '#topo'];
  const hallados = [];
  for (const z of zonas) {
    const raiz = document.querySelector(z);
    if (!raiz) continue;
    for (const el of raiz.querySelectorAll('*')) {
      const tag = el.tagName.toLowerCase();
      if (['img', 'script', 'iframe', 'object', 'embed'].includes(tag)) hallados.push(z + ' <' + tag + '>');
      for (const a of el.attributes) if (a.name.startsWith('on')) hallados.push(z + ' ' + tag + '[' + a.name + ']');
    }
  }
  return hallados;
}"""


@necesita_playwright
class B1XssEnElNavegador(ConNavegador):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cab = {"X-API-Key": CLAVE_FABRICA, "X-Lab-Team": EQUIPO}
        for interfaz, (_, desc) in CARGAS.items():
            assert len(desc) <= 200, interfaz
            r = cls.servidor.pedir("PATCH", "/api/v1/devices/csr-core-01/interfaces/" + interfaz.replace("/", "%2F"),
                                   {"description": desc}, cab)
            assert r.status == 200, r
        # B3: un fallo de un equipo que aun no existe no se registra; primero se crea con una lectura.
        assert cls.servidor.pedir("GET", "/api/v1/devices", cabeceras={"X-Lab-Team": EQUIPO_RUTA}).status == 200
        s = conectar(cls.servidor.puerto)
        try:
            s.sendall(peticion_cruda("GET", RUTA_HOSTIL, {"X-Lab-Team": EQUIPO_RUTA}))
            leer_una_respuesta(s)
        finally:
            s.close()
        html = cls.servidor.pedir("GET", "/").texto
        cls.titulo = re.search(r"<title>(.*?)</title>", html, re.S).group(1)

    def test_b1_ninguna_descripcion_hostil_se_ejecuta(self):
        """B1 (criterio): con las descripciones hostiles guardadas, abrir la interfaz y esperar dos sondeos no ejecuta nada."""
        pagina = self.abrir(f"/?team={EQUIPO}")
        pagina.wait_for_timeout(int(PAUSA_SONDEO * 2.5 * 1000))
        self.assertEqual(pagina.title(), self.titulo, "una descripcion cambio document.title")
        self.assertIsNone(pagina.evaluate("window.__xss"), "se ejecuto un centinela")
        self.assertEqual(pagina.evaluate(SOSPECHOSOS_JS), [], "hay elementos o atributos inyectados")

    def test_b1_el_tooltip_muestra_el_texto_tal_cual(self):
        """B1: al pasar el raton por cada puerto, el tooltip muestra la descripcion como texto y no ejecuta nada."""
        pagina = self.abrir(f"/?team={EQUIPO}")
        pagina.wait_for_timeout(int(PAUSA_SONDEO * 1.5 * 1000))
        for interfaz, (ataque, desc) in CARGAS.items():
            with self.subTest(ataque):
                puerto = pagina.locator(f'#devs .p[data-dev="csr-core-01"][data-if="{interfaz}"]')
                self.assertEqual(puerto.count(), 1, f"no se encontro el puerto {interfaz}")
                puerto.hover()
                pagina.wait_for_timeout(150)
                texto = pagina.evaluate("document.querySelector('#tt').textContent")
                self.assertIn(desc, texto, "el tooltip no muestra la descripcion tal cual")
        self.assertEqual(pagina.errores, [], "errores de JavaScript al pasar el raton")
        self.assertIsNone(pagina.evaluate("window.__xss"))
        self.assertEqual(pagina.title(), self.titulo)
        self.assertEqual(pagina.evaluate(SOSPECHOSOS_JS), [])

    def test_b1_regresion_ruta_hostil_en_el_registro(self):
        """B1 (Regresion): una ruta con HTML en el registro de peticiones se muestra como texto."""
        pagina = self.abrir(f"/?team={EQUIPO_RUTA}")
        pagina.wait_for_timeout(int(PAUSA_SONDEO * 2 * 1000))
        texto = pagina.evaluate("document.querySelector('#logHttp').textContent")
        self.assertIn("<img/src=x/onerror=", texto)
        self.assertIsNone(pagina.evaluate("window.__xss"))
        self.assertEqual(pagina.evaluate(SOSPECHOSOS_JS), [])


if __name__ == "__main__":
    unittest.main()
