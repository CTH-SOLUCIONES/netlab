"""B2 en el navegador: la interfaz con clave de administrador propia y con la de fabrica.

Con NETLAB_ADMIN_KEY propia la interfaz no trae la clave: la pide la primera vez
que hace falta, la guarda en sessionStorage, el curl de ejemplo muestra $K y el
boton Reiniciar dice en pantalla si el servidor lo rechazo. Con la clave de
fabrica todo funciona como hoy, sin pedir nada.
"""

import os
import re
import secrets
import sys
import unittest

try:
    from .comun import PAUSA_SONDEO, ConNavegador, necesita_playwright
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from comun import PAUSA_SONDEO, ConNavegador, necesita_playwright

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from netlab_servidor import CLAVE_FABRICA  # noqa: E402

TE111 = "/api/v1/devices/csr-core-01/interfaces/Te1%2F1%2F1"
TE112 = "/api/v1/devices/csr-core-01/interfaces/Te1%2F1%2F2"

# Texto visible fuera de los registros y de la topologia, que cambian solos.
TEXTO_FIJO_JS = """() => {
  const copia = document.body.cloneNode(true);
  copia.querySelectorAll('#logHttp, #logEv, #devs, #topo, #tt, script, style').forEach(e => e.remove());
  return copia.innerText;
}"""
AVISO = re.compile(r"401|clave|no se pudo|error|rechaz|autoriz|fall", re.I)


def boton(pagina, texto):
    return pagina.locator("#rapidos button", has_text=texto)


@necesita_playwright
class B2InterfazConClavePropia(ConNavegador):
    CLAVE = "clave-propia-" + secrets.token_hex(8)
    ENTORNO = {"NETLAB_ADMIN_KEY": CLAVE}

    def estado(self, ruta):
        return self.servidor.pedir("GET", ruta).json["admin_status"]

    def test_b2_pide_la_clave_una_vez_la_guarda_y_no_la_muestra(self):
        """B2: con clave propia, enviar con clave la pide una vez, la guarda en sessionStorage, la usa, y el curl muestra $K."""
        pagina = self.abrir("/", respuestas_prompt=[self.CLAVE])
        enviadas = []
        pagina.on("request", lambda r: enviadas.append(r.headers.get("x-api-key"))
                  if r.method == "POST" and "/shutdown" in r.url else None)
        boton(pagina, "Apagar Te1/1/1").click()
        curl = pagina.locator("#curl").inner_text()
        self.assertIn("$K", curl, f"el curl de ejemplo no usa $K:\n{curl}")
        self.assertNotIn(self.CLAVE, curl)
        pagina.locator("#enviar").click()
        self.assertTrue(self.esperar(pagina, "document.querySelector('#chip').textContent.startsWith('200')"),
                        f"la peticion no dio 200: {pagina.locator('#chip').text_content()!r}; "
                        f"dialogos: {pagina.dialogos}")
        self.assertEqual([t for t, _ in pagina.dialogos], ["prompt"], pagina.dialogos)
        self.assertEqual(enviadas, [self.CLAVE])
        self.assertEqual(self.estado(TE111), "administratively down")
        guardadas = pagina.evaluate("Object.values(sessionStorage)")
        self.assertIn(self.CLAVE, guardadas, "la clave no quedo en sessionStorage")
        # Segunda escritura: no vuelve a pedirla.
        boton(pagina, "Encender Te1/1/1").click()
        pagina.locator("#enviar").click()
        self.assertTrue(self.esperar(pagina, "document.querySelector('#resp').textContent.includes('no shutdown')"))
        self.assertEqual([t for t, _ in pagina.dialogos], ["prompt"], "volvio a pedir la clave")
        self.assertEqual(self.estado(TE111), "up")
        pagina.wait_for_timeout(int(PAUSA_SONDEO * 1000))
        self.assertNotIn(self.CLAVE, pagina.content(), "la clave quedo escrita en la pagina")
        self.assertEqual(pagina.errores, [])

    def test_b2_reiniciar_con_clave_mala_lo_dice_en_pantalla(self):
        """B2: el boton Reiniciar revisa la respuesta; con una clave invalida lo dice en pantalla y el laboratorio no se reinicia."""
        r = self.servidor.pedir("POST", TE112 + "/shutdown", cabeceras={"X-API-Key": self.CLAVE})
        self.assertEqual(r.status, 200)
        pagina = self.abrir("/", respuestas_prompt=["clave-que-no-es"])
        pagina.wait_for_timeout(int(PAUSA_SONDEO * 1000))
        antes = pagina.evaluate(TEXTO_FIJO_JS)
        pagina.locator("#btnReset").click()
        pagina.wait_for_timeout(int(PAUSA_SONDEO * 1.5 * 1000))
        despues = pagina.evaluate(TEXTO_FIJO_JS)
        nuevo = "\n".join(l for l in despues.splitlines() if l not in antes.splitlines())
        alertas = [m for t, m in pagina.dialogos if t == "alert"]
        self.assertIn("prompt", [t for t, _ in pagina.dialogos], f"no pidio la clave: {pagina.dialogos}")
        self.assertTrue(AVISO.search(nuevo) or any(AVISO.search(m) for m in alertas),
                        f"el rechazo no se ve en pantalla. Texto nuevo: {nuevo!r}; alertas: {alertas}")
        self.assertEqual(self.estado(TE112), "administratively down", "el laboratorio se reinicio")
        self.assertEqual(pagina.errores, [])


@necesita_playwright
class B2InterfazConClaveDeFabrica(ConNavegador):

    def estado(self, ruta):
        return self.servidor.pedir("GET", ruta).json["admin_status"]

    def test_b2_regresion_con_la_de_fabrica_funciona_como_hoy(self):
        """B2 (Regresion): con la clave de fabrica no se pide nada, el curl la muestra, enviar y reiniciar funcionan."""
        pagina = self.abrir("/")
        boton(pagina, "Apagar Te1/1/1").click()
        self.assertIn(CLAVE_FABRICA, pagina.locator("#curl").inner_text())
        pagina.locator("#enviar").click()
        self.assertTrue(self.esperar(pagina, "document.querySelector('#chip').textContent.startsWith('200')"))
        self.assertEqual(self.estado(TE111), "administratively down")
        pagina.locator("#btnReset").click()
        limite = 20
        while limite and self.estado(TE111) != "up":
            pagina.wait_for_timeout(250)
            limite -= 1
        self.assertEqual(self.estado(TE111), "up", "Reiniciar no reinicio el laboratorio")
        self.assertEqual([t for t, _ in pagina.dialogos], ["confirm"], pagina.dialogos)
        self.assertEqual(pagina.errores, [])


if __name__ == "__main__":
    unittest.main()
