"""I6 en el navegador: el registro HTTP de la clase.

- Una peticion que no cambia la configuracion (un GET) aparece en el registro.
  El ETag de /topology excluye el registro, asi que un sondeo que revalida con
  If-None-Match recibe 304 y el registro se congela: el sondeo va en no-store.
- Sin peticiones nuevas, las filas no se vuelven a animar (sin parpadeo).
- Tras un reinicio hecho desde fuera de la pagina (curl), un 401 posterior se ve.
"""

import json
import os
import sys
import unittest
import urllib.error
import urllib.request
import uuid

try:
    from .comun import PAUSA_SONDEO, ConNavegador, necesita_playwright
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from comun import PAUSA_SONDEO, ConNavegador, necesita_playwright

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from netlab_servidor import CLAVE_FABRICA  # noqa: E402

FILAS_JS = "() => [...document.querySelectorAll('#logHttp > div')].map(d => d.innerText.replace(/\\s+/g, ' ').trim())"
CONTAR_ANIMACIONES_JS = """() => {
  window.__animaciones = [];
  for (const tipo of ['animationstart', 'transitionstart']) {
    document.addEventListener(tipo, e => {
      const log = e.target.closest && e.target.closest('#logHttp, #logEv');
      if (log) window.__animaciones.push(log.id + ' ' + tipo + ' ' + (e.animationName || e.propertyName));
    }, true);
  }
}"""


def curl(base, metodo, ruta, clave=None):
    """Peticion desde fuera de la pagina, como un curl de la clase. Devuelve el codigo."""
    peticion = urllib.request.Request(base + ruta, method=metodo,
                                      data=b"" if metodo == "POST" else None)
    if clave:
        peticion.add_header("X-API-Key", clave)
    try:
        with urllib.request.urlopen(peticion, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        e.close()
        return e.code


@necesita_playwright
class I6RegistroDeLaClase(ConNavegador):

    def test_i6_un_get_sin_cambios_aparece_en_el_registro(self):
        """I6: un GET hecho con curl, que no cambia la configuracion, aparece en el registro en dos sondeos."""
        base = self.servidor.base
        curl(base, "GET", "/api/v1/devices")
        pagina = self.abrir("/")
        pagina.wait_for_timeout(int(PAUSA_SONDEO * 2 * 1000))
        marca = uuid.uuid4().hex[:10]
        self.assertEqual(curl(base, "GET", f"/api/v1/devices?marca={marca}"), 200)
        visto = self.esperar(pagina, f"document.querySelector('#logHttp').textContent.includes('{marca}')",
                             PAUSA_SONDEO * 2.5)
        self.assertTrue(visto, f"el GET no aparecio en el registro: {pagina.evaluate(FILAS_JS)[:5]}")
        self.assertEqual(pagina.errores, [])

    def test_i6_sin_peticiones_nuevas_las_filas_no_parpadean(self):
        """I6: con el registro quieto durante dos sondeos, ninguna fila vuelve a animarse."""
        base = self.servidor.base
        for _ in range(3):
            curl(base, "GET", "/api/v1/devices")
        pagina = self.abrir("/")
        pagina.wait_for_timeout(int(PAUSA_SONDEO * 2 * 1000))
        self.assertTrue(pagina.evaluate(FILAS_JS), "el registro esta vacio; no hay nada que pueda parpadear")
        pagina.evaluate(CONTAR_ANIMACIONES_JS)
        pagina.wait_for_timeout(int(PAUSA_SONDEO * 2.5 * 1000))
        animaciones = pagina.evaluate("window.__animaciones")
        self.assertEqual(animaciones, [], f"{len(animaciones)} animaciones en filas que no cambiaron")
        self.assertEqual(pagina.errores, [])

    def test_i6_el_401_se_ve_tras_un_reinicio_hecho_con_curl(self):
        """I6: con dos peticiones ya mostradas, un reinicio por curl y luego un 401: el 401 aparece en el registro."""
        base = self.servidor.base
        curl(base, "GET", "/api/v1/devices")
        curl(base, "GET", "/api/v1/devices")
        pagina = self.abrir("/")
        pagina.wait_for_timeout(int(PAUSA_SONDEO * 1.5 * 1000))
        self.assertEqual(curl(base, "POST", "/api/v1/reset", CLAVE_FABRICA), 200)
        pagina.wait_for_timeout(int(PAUSA_SONDEO * 1.2 * 1000))
        self.assertEqual(curl(base, "POST", "/api/v1/devices/csr-core-01/interfaces/Te1/1/1/shutdown"), 401)
        visto = self.esperar(
            pagina, "[...document.querySelectorAll('#logHttp > div')].some(d => /\\b401\\b/.test(d.innerText))",
            PAUSA_SONDEO * 2.5)
        self.assertTrue(visto, f"el 401 no aparecio: {json.dumps(pagina.evaluate(FILAS_JS)[:5])}")
        self.assertEqual(pagina.errores, [])


if __name__ == "__main__":
    unittest.main()
