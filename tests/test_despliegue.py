"""Pruebas del despliegue: secreto de Kubernetes (B2) y CI (I7).

Leen los archivos del repositorio al que pertenece el netlab.py bajo prueba
(NETLAB_APP), asi corren igual contra main. desplegar.sh se ejecuta de verdad
con un kubectl falso que guarda el estado del cluster en un directorio: se
comprueba el efecto (el secreto que queda creado), no el texto del script.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from netlab_servidor import raiz_de_la_app  # noqa: E402

RAIZ = raiz_de_la_app()
YAML = os.path.join(RAIZ, "k8s", "netlab.yaml")
SCRIPT = os.path.join(RAIZ, "k8s", "desplegar.sh")
WORKFLOW = os.path.join(RAIZ, ".github", "workflows", "publicar-imagen.yml")
VARIABLES = ("NETLAB_ADMIN_KEY", "NETLAB_READONLY_KEY")


def sin_comentarios(ruta):
    with open(ruta, encoding="utf-8") as f:
        return "\n".join(l for l in f.read().splitlines() if not l.lstrip().startswith("#"))


def referencias_a_secretos(texto):
    """{variable: (secreto, llave)} para las variables que leen de un secretKeyRef."""
    refs = {}
    for var in VARIABLES:
        m = re.search(r"name:\s*[\"']?" + var + r"[\"']?", texto)
        if not m:
            continue
        tramo = re.split(r"\n\s*-\s*name:", texto[m.end():m.end() + 300])[0]
        s = re.search(r"secretKeyRef:(.*)", tramo, re.S)
        if not s:
            continue
        cuerpo = s.group(1)
        nombre = re.search(r"name:\s*[\"']?([\w.-]+)", cuerpo)
        llave = re.search(r"key:\s*[\"']?([\w.-]+)", cuerpo)
        if nombre and llave:
            refs[var] = (nombre.group(1), llave.group(1))
    return refs


class B2SecretoEnElManifiesto(unittest.TestCase):

    def test_b2_netlab_yaml_lee_las_claves_del_secreto(self):
        """B2 (k8s): netlab.yaml referencia netlab-claves con secretKeyRef, descomentado, para las dos claves."""
        refs = referencias_a_secretos(sin_comentarios(YAML))
        self.assertEqual(set(refs), set(VARIABLES), f"referencias activas: {refs}")
        for var, (secreto, _) in refs.items():
            self.assertEqual(secreto, "netlab-claves", var)
        self.assertEqual(len({llave for _, llave in refs.values()}), 2, f"las dos usan la misma llave: {refs}")


FALSO_KUBECTL = textwrap.dedent(r'''
    import base64, json, os, re, sys
    estado = os.environ["KUBECTL_FALSO_ESTADO"]
    args = sys.argv[1:]
    lee_stdin = any(a in ("-", "--filename=-", "-f-") for a in args)
    entrada = sys.stdin.read() if lee_stdin else ""
    with open(os.path.join(estado, "llamadas.log"), "a") as f:
        f.write(json.dumps({"args": args, "stdin": entrada}) + "\n")

    def ruta(n): return os.path.join(estado, n)
    def existe(n): return os.path.exists(ruta(n))
    def guardar(n, datos):
        with open(ruta(n), "w") as f: json.dump(datos, f)

    def secreto_del_manifiesto(texto):
        for doc in re.split(r"\n---", texto):
            if not re.search(r"kind:\s*Secret\b", doc) or "netlab-claves" not in doc:
                continue
            datos, modo, sangria = {}, None, None
            for linea in doc.splitlines():
                m = re.match(r"^(\s*)(data|stringData):\s*$", linea)
                if m:
                    modo, sangria = m.group(2), len(m.group(1))
                    continue
                if modo:
                    m = re.match(r"^(\s+)([\w.-]+):\s*[\"']?([^\"'\s]*)[\"']?\s*$", linea)
                    if m and len(m.group(1)) > sangria:
                        v = m.group(3)
                        datos[m.group(2)] = base64.b64decode(v).decode() if modo == "data" else v
                    else:
                        modo = None
            return datos
        return None

    def literales():
        datos = {}
        for i, a in enumerate(args):
            valor = None
            if a.startswith("--from-literal="):
                valor = a.split("=", 1)[1]
            elif a == "--from-literal" and i + 1 < len(args):
                valor = args[i + 1]
            if valor:
                k, _, v = valor.partition("=")
                datos[k] = v
        return datos

    def falla(msg):
        sys.stderr.write(msg + "\n")
        sys.exit(1)

    verbo = next((a for a in args if a in ("apply", "create", "get", "patch", "rollout", "delete", "describe", "wait", "label", "annotate")), "")
    if verbo == "apply":
        if re.search(r"kind:\s*Namespace\b", entrada):
            guardar("ns", True)
        datos = secreto_del_manifiesto(entrada)
        if datos is not None:
            if not existe("ns"):
                falla('Error from server (NotFound): namespaces "netlab" not found')
            guardar("secreto", datos)
        sys.exit(0)
    if verbo == "create":
        seco = any(a.startswith("--dry-run") for a in args)
        if "namespace" in args or "ns" in args:
            if not seco:
                guardar("ns", True)
            sys.exit(0)
        if "secret" in args:
            datos = literales()
            if seco:
                print("apiVersion: v1\nkind: Secret\nmetadata:\n  name: netlab-claves\n  namespace: netlab\ndata:")
                for k, v in datos.items():
                    print(f"  {k}: {base64.b64encode(v.encode()).decode()}")
                sys.exit(0)
            if not existe("ns"):
                falla('Error from server (NotFound): namespaces "netlab" not found')
            if existe("secreto"):
                falla('Error from server (AlreadyExists): secrets "netlab-claves" already exists')
            guardar("secreto", datos)
            sys.exit(0)
        if "configmap" in args and seco:
            print("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: netlab-codigo\n  namespace: netlab")
        sys.exit(0)
    if verbo == "get":
        texto = " ".join(args)
        if re.search(r"\bsecrets?\b|secrets?/", texto):
            if existe("secreto"):
                print("netlab-claves")
                sys.exit(0)
            falla('Error from server (NotFound): secrets "netlab-claves" not found')
        if re.search(r"\b(ns|namespaces?)\b|namespaces?/", texto):
            if existe("ns"):
                print("netlab")
                sys.exit(0)
            falla('Error from server (NotFound): namespaces "netlab" not found')
    sys.exit(0)
''')


@unittest.skipIf(any(shutil.which(h) is None for h in ("bash", "openssl", "sed")),
                 "faltan bash, openssl o sed para correr desplegar.sh")
class B2SecretoDesplegar(unittest.TestCase):
    """B2 (k8s): desplegar.sh crea netlab-claves con openssl rand -hex 16 si no existe e imprime las claves una sola vez."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="netlab-k8s-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.estado = os.path.join(self.tmp, "cluster")
        binarios = os.path.join(self.tmp, "bin")
        os.makedirs(self.estado)
        os.makedirs(binarios)
        kubectl = os.path.join(binarios, "kubectl")
        with open(kubectl, "w") as f:
            f.write(f"#!{sys.executable}\n" + FALSO_KUBECTL)
        os.chmod(kubectl, 0o755)
        if shutil.which("shasum") is None and shutil.which("sha256sum"):
            envoltura = os.path.join(binarios, "shasum")
            with open(envoltura, "w") as f:
                f.write('#!/usr/bin/env bash\nexec sha256sum\n')
            os.chmod(envoltura, 0o755)
        self.entorno = dict(os.environ, PATH=binarios + os.pathsep + os.environ.get("PATH", ""),
                            KUBECTL_FALSO_ESTADO=self.estado, NETLAB_DOMINIO="netlab.prueba.test")

    def correr(self):
        return subprocess.run(["bash", SCRIPT], env=self.entorno, capture_output=True, text=True,
                              timeout=60, cwd=self.tmp)

    def secreto(self):
        ruta = os.path.join(self.estado, "secreto")
        if not os.path.exists(ruta):
            return None
        with open(ruta) as f:
            return json.load(f)

    def test_b2_desplegar_sh_es_sintacticamente_valido(self):
        """B2 (k8s): desplegar.sh pasa bash -n."""
        r = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_b2_primera_vez_crea_el_secreto_con_las_llaves_del_manifiesto(self):
        """B2 (k8s): sin secreto, desplegar.sh lo crea con las llaves que lee netlab.yaml, valores de openssl rand -hex 16, y los imprime."""
        r = self.correr()
        self.assertEqual(r.returncode, 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}")
        secreto = self.secreto()
        self.assertIsNotNone(secreto, f"no se creo netlab-claves.\nstdout:\n{r.stdout}")
        llaves = {llave for _, llave in referencias_a_secretos(sin_comentarios(YAML)).values()}
        self.assertTrue(llaves, "netlab.yaml no referencia llaves del secreto")
        self.assertTrue(llaves <= set(secreto), f"el manifiesto lee {llaves}, el secreto trae {set(secreto)}")
        for llave in llaves:
            with self.subTest(llave=llave):
                self.assertRegex(secreto[llave], r"^[0-9a-f]{32}$")
                self.assertIn(secreto[llave], r.stdout, "la clave generada no se imprimio")
        self.assertNotEqual(*[secreto[l] for l in sorted(llaves)][:2])

    def test_b2_segunda_vez_no_toca_el_secreto_ni_imprime_claves(self):
        """B2 (k8s): con el secreto ya creado, desplegar.sh no lo cambia ni vuelve a imprimir las claves."""
        primera = self.correr()
        self.assertEqual(primera.returncode, 0, primera.stderr)
        antes = self.secreto()
        self.assertIsNotNone(antes, "la primera corrida no creo el secreto")
        segunda = self.correr()
        self.assertEqual(segunda.returncode, 0, f"stdout:\n{segunda.stdout}\nstderr:\n{segunda.stderr}")
        self.assertEqual(self.secreto(), antes, "la segunda corrida cambio las claves")
        for valor in antes.values():
            self.assertNotIn(valor, segunda.stdout, "la segunda corrida volvio a imprimir una clave")


class I7Workflow(unittest.TestCase):

    def setUp(self):
        self.texto = sin_comentarios(WORKFLOW)

    def trabajos(self):
        bloque = self.texto.split("\njobs:", 1)
        self.assertEqual(len(bloque), 2, "el workflow no tiene jobs")
        trabajos, actual = {}, None
        for linea in bloque[1].splitlines():
            m = re.match(r"^  ([\w-]+):\s*$", linea)
            if m:
                actual = m.group(1)
                trabajos[actual] = ""
            elif actual and (linea.startswith("   ") or not linea.strip()):
                trabajos[actual] += linea + "\n"
            elif linea.strip() and not linea.startswith(" "):
                break
        return trabajos

    def test_i7_la_publicacion_depende_del_job_de_pruebas(self):
        """I7: un job corre las pruebas con unittest y el de publicacion depende de el con needs."""
        trabajos = self.trabajos()
        pruebas = [n for n, t in trabajos.items() if re.search(r"python3?\s+-m\s+unittest", t)]
        publica = [n for n, t in trabajos.items() if "build-push-action" in t]
        self.assertTrue(pruebas, f"ningun job corre unittest: {list(trabajos)}")
        self.assertTrue(publica, "ningun job publica la imagen")
        for n in publica:
            self.assertNotIn(n, pruebas, "las pruebas y la publicacion van en el mismo job")
            needs = re.search(r"^\s{4}needs:\s*(.+(?:\n\s{6,}-.*)*)", trabajos[n], re.M)
            self.assertIsNotNone(needs, f"el job {n} no tiene needs")
            self.assertTrue(any(p in needs.group(1) for p in pruebas),
                            f"needs de {n} = {needs.group(1)!r}; no incluye {pruebas}")

    def test_i7_dispara_con_etiquetas_v_y_publica_semver(self):
        """I7: el workflow se dispara con etiquetas v* y publica las etiquetas {{version}} y {{major}}.{{minor}}."""
        self.assertRegex(self.texto, r"tags:\s*(?:\[\s*[\"']?v\*[\"']?|\n\s*-\s*[\"']?v\*[\"']?)",
                         "falta el disparador por etiquetas v*")
        self.assertIn("type=semver,pattern={{version}}", self.texto)
        self.assertIn("type=semver,pattern={{major}}.{{minor}}", self.texto)


if __name__ == "__main__":
    unittest.main()
