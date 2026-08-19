#!/usr/bin/env python3
"""
NetLab API - Laboratorio de automatizacion de redes.

Simula dispositivos Cisco (switches y routers) y los expone por una API REST
disenada para ensenar HTTP: todos los verbos, codigos de estado, cabeceras,
negociacion de contenido, cache con ETag y concurrencia optimista.

Sin dependencias externas: solo libreria estandar de Python 3.
"""

import json
import os
import re
import socket
import threading
import time
import copy
import random
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

PORT = int(os.environ.get("PORT", "8080"))
UI_FILE = os.environ.get("UI_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html"))
START_TIME = time.time()

# ---------------------------------------------------------------------------
# Autenticacion del laboratorio
# ---------------------------------------------------------------------------
# Dos claves con permisos distintos para poder demostrar la diferencia
# entre 401 (no te identificaste) y 403 (te identificaste pero no puedes).
# Si el laboratorio se expone a internet, cambie las claves por variables de
# entorno en lugar de usar las de los ejemplos de la guia.
ADMIN_KEY = os.environ.get("NETLAB_ADMIN_KEY", "netlab-admin-2026")
READONLY_KEY = os.environ.get("NETLAB_READONLY_KEY", "netlab-lectura-2026")
API_KEYS = {
    ADMIN_KEY: {"role": "admin", "owner": "instructor"},
    READONLY_KEY: {"role": "readonly", "owner": "estudiante"},
}
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


# ---------------------------------------------------------------------------
# Definicion del inventario simulado
# ---------------------------------------------------------------------------
def _iface(name, abbrev, description="", enabled=True, mode="access", vlan=1,
           ip_address=None, speed_mbps=1000, link_to=None, media="cobre"):
    return {
        "name": name,
        "abbrev": abbrev,
        "description": description,
        "enabled": enabled,
        "mode": mode,
        "vlan": vlan,
        "allowed_vlans": [] if mode != "trunk" else [1, 10, 20, 30],
        "ip_address": ip_address,
        "speed_mbps": speed_mbps,
        "duplex": "full",
        "mtu": 1500,
        "media": media,
        "mac_address": "",
        "link_to": link_to,
        "counters": {
            "rx_bytes": 0, "tx_bytes": 0,
            "rx_packets": 0, "tx_packets": 0,
            "input_errors": 0, "crc_errors": 0,
        },
        "utilization_pct": 0.0,
        "history": [0.0] * 30,
        "last_change": None,
        "revision": 1,
    }


def build_seed():
    """Construye el inventario inicial de dispositivos."""
    devices = {}

    # --- Router WAN -------------------------------------------------------
    rtr = {
        "id": "rtr-wan-01",
        "hostname": "RTR-WAN-01",
        "model": "Cisco ISR 4331",
        "os": "IOS-XE",
        "version": "17.06.05",
        "role": "router",
        "site": "Bogota-DC",
        "mgmt_ip": "10.10.10.254",
        "serial": "FDO2436A1BC",
        "status": "online",
        "config_saved": True,
        "uptime_seconds": 1_842_300,
        "interfaces": {},
        "vlans": {},
    }
    for i in [
        _iface("GigabitEthernet0/0/0", "Gi0/0/0", "UPLINK-ISP-PRINCIPAL", True,
               "routed", None, "190.85.44.2/30", 1000, None, "fibra"),
        _iface("GigabitEthernet0/0/1", "Gi0/0/1", "HACIA-CORE-01", True,
               "routed", None, "10.10.0.1/30", 1000,
               {"device": "csr-core-01", "interface": "GigabitEthernet1/0/1"}),
        _iface("GigabitEthernet0/0/2", "Gi0/0/2", "RESERVA-MPLS", False,
               "routed", None, None, 1000),
        _iface("Loopback0", "Lo0", "ROUTER-ID", True, "routed", None, "10.255.255.1/32", 0),
    ]:
        rtr["interfaces"][i["name"]] = i
    devices[rtr["id"]] = rtr

    # --- Switch core ------------------------------------------------------
    core = {
        "id": "csr-core-01",
        "hostname": "CSR-CORE-01",
        "model": "Cisco Catalyst 9300-48P",
        "os": "IOS-XE",
        "version": "17.09.04a",
        "role": "core",
        "site": "Bogota-DC",
        "mgmt_ip": "10.10.10.1",
        "serial": "FCW2245L0KP",
        "status": "online",
        "config_saved": True,
        "uptime_seconds": 3_120_450,
        "interfaces": {},
        "vlans": {},
    }
    core_ifaces = [
        _iface("GigabitEthernet1/0/1", "Gi1/0/1", "HACIA-RTR-WAN-01", True,
               "routed", None, "10.10.0.2/30", 1000,
               {"device": "rtr-wan-01", "interface": "GigabitEthernet0/0/1"}),
        _iface("TenGigabitEthernet1/1/1", "Te1/1/1", "TRUNK-ACCESO-01", True,
               "trunk", None, None, 10000,
               {"device": "sw-access-01", "interface": "GigabitEthernet0/1"}, "fibra"),
        _iface("TenGigabitEthernet1/1/2", "Te1/1/2", "TRUNK-ACCESO-02", True,
               "trunk", None, None, 10000,
               {"device": "sw-access-02", "interface": "GigabitEthernet0/1"}, "fibra"),
        _iface("GigabitEthernet1/0/10", "Gi1/0/10", "SERVIDOR-ERP", True, "access", 30),
        _iface("GigabitEthernet1/0/11", "Gi1/0/11", "SERVIDOR-BD", True, "access", 30),
        _iface("GigabitEthernet1/0/12", "Gi1/0/12", "LIBRE", False, "access", 1),
        _iface("Vlan1", "Vl1", "ADMINISTRACION", True, "routed", None, "10.10.10.1/24", 0, None, "virtual"),
    ]
    for i in core_ifaces:
        core["interfaces"][i["name"]] = i
    devices[core["id"]] = core

    # --- Switches de acceso ----------------------------------------------
    for idx, (dev_id, model, version, site) in enumerate([
        ("sw-access-01", "Cisco Catalyst 2960X-24TS", "15.2(7)E6", "Piso-3"),
        ("sw-access-02", "Cisco Catalyst 9200L-24P", "17.09.04a", "Piso-4"),
    ], start=1):
        sw = {
            "id": dev_id,
            "hostname": dev_id.upper(),
            "model": model,
            "os": "IOS" if "2960" in model else "IOS-XE",
            "version": version,
            "role": "access",
            "site": site,
            "mgmt_ip": f"10.10.10.{10 + idx}",
            "serial": f"FOC23{idx}5X0{idx}Z",
            "status": "online",
            "config_saved": True,
            "uptime_seconds": 986_400 + idx * 5000,
            "interfaces": {},
            "vlans": {},
        }
        uplink = _iface("GigabitEthernet0/1", "Gi0/1", "UPLINK-AL-CORE", True,
                        "trunk", None, None, 1000,
                        {"device": "csr-core-01",
                         "interface": f"TenGigabitEthernet1/1/{idx}"}, "fibra")
        sw["interfaces"][uplink["name"]] = uplink
        puestos = [
            ("PC-RECEPCION", 10), ("PC-CONTABILIDAD", 20), ("PC-GERENCIA", 20),
            ("IMPRESORA", 10), ("TELEFONO-IP", 40), ("AP-WIFI", 10),
            ("CAMARA-CCTV", 50), ("LIBRE", 1),
        ]
        for p, (desc, vlan) in enumerate(puestos, start=2):
            en = desc != "LIBRE"
            sw["interfaces"][f"GigabitEthernet0/{p}"] = _iface(
                f"GigabitEthernet0/{p}", f"Gi0/{p}", desc, en, "access", vlan, None, 1000)
        devices[dev_id] = sw

    # --- VLANs por dispositivo -------------------------------------------
    vlan_base = [
        (1, "default"), (10, "DATOS"), (20, "ADMINISTRATIVO"),
        (30, "SERVIDORES"), (40, "VOZ"), (50, "CCTV"),
    ]
    for dev in devices.values():
        if dev["role"] == "router":
            continue
        for vid, vname in vlan_base:
            dev["vlans"][str(vid)] = {
                "id": vid, "name": vname, "status": "active",
                "created_by": "seed", "revision": 1,
            }

    # MAC coherentes y estado inicial de contadores
    for dev in devices.values():
        for n, i in dev["interfaces"].items():
            h = hashlib.md5(f"{dev['id']}{n}".encode()).hexdigest()[:6]
            i["mac_address"] = ":".join(["00", "1a"] + [h[j:j + 2] for j in (0, 2, 4)])
            if i["enabled"]:
                i["counters"]["rx_bytes"] = random.randint(10**8, 10**10)
                i["counters"]["tx_bytes"] = random.randint(10**8, 10**10)
                i["counters"]["rx_packets"] = i["counters"]["rx_bytes"] // 800
                i["counters"]["tx_packets"] = i["counters"]["tx_bytes"] // 800
    return devices


# ---------------------------------------------------------------------------
# Estado global. Cada "equipo" (header X-Lab-Team) tiene su propio mundo,
# asi varios grupos pueden trabajar sin pisarse.
# ---------------------------------------------------------------------------
LOCK = threading.RLock()
WORLDS = {}
MAX_EVENTS = 300


def new_world():
    return {
        "devices": build_seed(),
        "events": [],
        "requests": [],
        "seq": 0,
        "req_seq": 0,
        "created_at": time.time(),
        "stats": {"total_requests": 0, "by_method": {}, "by_status": {}},
    }


def get_world(team):
    with LOCK:
        if team not in WORLDS:
            WORLDS[team] = new_world()
        return WORLDS[team]


def add_event(world, level, device, message):
    with LOCK:
        world["seq"] += 1
        world["events"].append({
            "seq": world["seq"],
            "ts": time.time(),
            "level": level,
            "device": device,
            "message": message,
        })
        if len(world["events"]) > MAX_EVENTS:
            del world["events"][:-MAX_EVENTS]


# ---------------------------------------------------------------------------
# Estado operativo derivado: una interfaz solo esta "up" si esta habilitada
# administrativamente, su dispositivo esta online y, cuando tiene un vecino,
# ese vecino tambien la tiene habilitada. Asi apagar un puerto en el core
# tumba el enlace del switch de acceso, que es lo que hace visible la clase.
# ---------------------------------------------------------------------------
def oper_status(world, dev, iface):
    if dev["status"] != "online":
        return "down"
    if not iface["enabled"]:
        return "down"
    peer = iface.get("link_to")
    if peer:
        pdev = world["devices"].get(peer["device"])
        if not pdev or pdev["status"] != "online":
            return "down"
        pif = pdev["interfaces"].get(peer["interface"])
        if not pif or not pif["enabled"]:
            return "down"
    return "up"


def iface_view(world, dev, iface):
    """Representacion publica de una interfaz."""
    oper = oper_status(world, dev, iface)
    out = {
        "name": iface["name"],
        "abbrev": iface["abbrev"],
        "description": iface["description"],
        "admin_status": "up" if iface["enabled"] else "administratively down",
        "oper_status": oper,
        "status_text": ("up/up" if oper == "up"
                        else ("down/down" if iface["enabled"] else "admin-down/down")),
        "mode": iface["mode"],
        "vlan": iface["vlan"],
        "allowed_vlans": iface["allowed_vlans"],
        "ip_address": iface["ip_address"],
        "speed_mbps": iface["speed_mbps"],
        "duplex": iface["duplex"],
        "mtu": iface["mtu"],
        "media": iface["media"],
        "mac_address": iface["mac_address"],
        "link_to": iface["link_to"],
        "counters": dict(iface["counters"]),
        "utilization_pct": round(iface["utilization_pct"], 2),
        "history": [round(h, 2) for h in iface["history"]],
        "last_change": iface["last_change"],
        "revision": iface["revision"],
    }
    return out


def device_view(world, dev, with_interfaces=True):
    ifaces = [iface_view(world, dev, i) for i in dev["interfaces"].values()]
    up = sum(1 for i in ifaces if i["oper_status"] == "up")
    out = {
        "id": dev["id"],
        "hostname": dev["hostname"],
        "model": dev["model"],
        "os": dev["os"],
        "version": dev["version"],
        "role": dev["role"],
        "site": dev["site"],
        "mgmt_ip": dev["mgmt_ip"],
        "serial": dev["serial"],
        "status": dev["status"],
        "config_saved": dev["config_saved"],
        "uptime_seconds": int(dev["uptime_seconds"]),
        "uptime_text": humanize_uptime(dev["uptime_seconds"]),
        "interface_count": len(ifaces),
        "interfaces_up": up,
        "interfaces_down": len(ifaces) - up,
        "vlan_count": len(dev["vlans"]),
        "_links": {
            "self": f"/api/v1/devices/{dev['id']}",
            "interfaces": f"/api/v1/devices/{dev['id']}/interfaces",
            "vlans": f"/api/v1/devices/{dev['id']}/vlans",
            "config": f"/api/v1/devices/{dev['id']}/config",
        },
    }
    if with_interfaces:
        out["interfaces"] = ifaces
        out["vlans"] = sorted(dev["vlans"].values(), key=lambda v: v["id"])
    return out


def humanize_uptime(seconds):
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    return f"{d} dias, {h} horas, {m} minutos"


# ---------------------------------------------------------------------------
# Simulador de trafico: hilo de fondo que mueve contadores para que la
# interfaz de usuario se vea viva y las graficas cambien.
# ---------------------------------------------------------------------------
def simulator():
    while True:
        time.sleep(1)
        now = time.time()
        with LOCK:
            for world in list(WORLDS.values()):
                for dev in world["devices"].values():
                    # Un dispositivo en reload vuelve solo despues de 9 segundos
                    if dev["status"] == "reloading":
                        if now >= dev.get("_reload_until", 0):
                            dev["status"] = "online"
                            dev["uptime_seconds"] = 30
                            add_event(world, "info", dev["id"],
                                      f"{dev['hostname']} termino de reiniciar, sistema operativo")
                        continue
                    dev["uptime_seconds"] += 1
                    for iface in dev["interfaces"].values():
                        if oper_status(world, dev, iface) != "up":
                            iface["utilization_pct"] = 0.0
                            iface["history"] = iface["history"][1:] + [0.0]
                            continue
                        # Perfil de trafico segun el rol del puerto
                        if iface["mode"] == "trunk":
                            base = random.uniform(18, 55)
                        elif iface["ip_address"]:
                            base = random.uniform(12, 40)
                        else:
                            base = random.uniform(2, 22)
                        prev = iface["utilization_pct"] or base
                        util = max(0.4, min(97.0, prev * 0.7 + base * 0.3
                                            + random.uniform(-4, 4)))
                        iface["utilization_pct"] = util
                        iface["history"] = iface["history"][1:] + [util]
                        speed = iface["speed_mbps"] or 1000
                        bytes_s = int(speed * 125_000 * util / 100)
                        iface["counters"]["rx_bytes"] += bytes_s
                        iface["counters"]["tx_bytes"] += int(bytes_s * random.uniform(0.6, 1.2))
                        iface["counters"]["rx_packets"] += max(1, bytes_s // 800)
                        iface["counters"]["tx_packets"] += max(1, bytes_s // 900)
                        if random.random() < 0.02:
                            iface["counters"]["input_errors"] += random.randint(1, 3)


# ---------------------------------------------------------------------------
# Errores de la API
# ---------------------------------------------------------------------------
class ApiError(Exception):
    def __init__(self, status, code, message, detail=None, headers=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail
        self.headers = headers or {}


# Campos que cambian solos (trafico, tiempo de actividad, registros). Si
# entraran en el calculo del ETag, la etiqueta cambiaria cada segundo y las
# demostraciones de 304 y de If-Match serian imposibles de reproducir. El ETag
# debe representar la configuracion del recurso, no sus estadisticas.
VOLATILES = {"counters", "history", "utilization_pct", "uptime_seconds",
             "uptime_text", "servidor_ts", "events", "http_log", "stats",
             "hora_servidor", "peticiones"}


def _estable(obj):
    if isinstance(obj, dict):
        return {k: _estable(v) for k, v in obj.items() if k not in VOLATILES}
    if isinstance(obj, list):
        return [_estable(v) for v in obj]
    return obj


def etag_for(obj):
    raw = json.dumps(_estable(obj), sort_keys=True, default=str).encode()
    return '"' + hashlib.sha1(raw).hexdigest()[:16] + '"'


# ---------------------------------------------------------------------------
# Validaciones
# ---------------------------------------------------------------------------
VALID_MODES = ("access", "trunk", "routed")
IP_CIDR = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})/(\d{1,2})$")


def validate_iface_payload(body, dev, full):
    """Valida el cuerpo de un PUT (full=True) o PATCH (full=False)."""
    errors = []
    allowed = {"description", "enabled", "mode", "vlan", "allowed_vlans",
               "ip_address", "speed_mbps", "duplex", "mtu"}
    desconocidos = set(body) - allowed
    if desconocidos:
        errors.append({"field": ", ".join(sorted(desconocidos)),
                       "issue": "campo no reconocido",
                       "allowed": sorted(allowed)})
    if full:
        for req in ("enabled", "mode"):
            if req not in body:
                errors.append({"field": req,
                               "issue": "PUT requiere la representacion completa; campo obligatorio ausente"})
    if "enabled" in body and not isinstance(body["enabled"], bool):
        errors.append({"field": "enabled", "issue": "debe ser booleano true o false"})
    if "mode" in body and body["mode"] not in VALID_MODES:
        errors.append({"field": "mode", "issue": f"debe ser uno de {list(VALID_MODES)}"})
    if "description" in body and not isinstance(body["description"], str):
        errors.append({"field": "description", "issue": "debe ser texto"})
    if "description" in body and len(str(body.get("description", ""))) > 200:
        errors.append({"field": "description", "issue": "maximo 200 caracteres"})
    if "vlan" in body and body["vlan"] is not None:
        v = body["vlan"]
        if not isinstance(v, int) or isinstance(v, bool):
            errors.append({"field": "vlan", "issue": "debe ser numero entero"})
        elif not (1 <= v <= 4094):
            errors.append({"field": "vlan", "issue": "fuera de rango, use 1 a 4094"})
        elif str(v) not in dev["vlans"]:
            errors.append({"field": "vlan",
                           "issue": f"la VLAN {v} no existe en {dev['hostname']}; creela primero con POST /vlans"})
    if "mtu" in body and body["mtu"] is not None:
        if not isinstance(body["mtu"], int) or not (68 <= body["mtu"] <= 9216):
            errors.append({"field": "mtu", "issue": "debe ser entero entre 68 y 9216"})
    if "speed_mbps" in body and body["speed_mbps"] is not None:
        if body["speed_mbps"] not in (0, 10, 100, 1000, 10000, 25000, 40000):
            errors.append({"field": "speed_mbps",
                           "issue": "valores validos: 10, 100, 1000, 10000, 25000, 40000"})
    if "duplex" in body and body["duplex"] not in (None, "full", "half", "auto"):
        errors.append({"field": "duplex", "issue": "debe ser full, half o auto"})
    if "ip_address" in body and body["ip_address"] is not None:
        m = IP_CIDR.match(str(body["ip_address"]))
        if not m:
            errors.append({"field": "ip_address",
                           "issue": "formato esperado A.B.C.D/prefijo, ejemplo 192.168.1.1/24"})
        else:
            octetos = [int(g) for g in m.groups()[:4]]
            prefijo = int(m.group(5))
            if any(o > 255 for o in octetos) or prefijo > 32:
                errors.append({"field": "ip_address", "issue": "octetos 0-255 y prefijo 0-32"})
    if "allowed_vlans" in body and body["allowed_vlans"] is not None:
        av = body["allowed_vlans"]
        if not isinstance(av, list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in av):
            errors.append({"field": "allowed_vlans", "issue": "debe ser una lista de enteros"})
    if errors:
        raise ApiError(422, "validacion_fallida",
                       "El cuerpo de la peticion no paso las validaciones",
                       {"errors": errors})


def validate_vlan_payload(body, dev, full, existing_id=None):
    errors = []
    allowed = {"id", "name", "status"}
    desconocidos = set(body) - allowed
    if desconocidos:
        errors.append({"field": ", ".join(sorted(desconocidos)),
                       "issue": "campo no reconocido", "allowed": sorted(allowed)})
    if existing_id is None:
        if "id" not in body:
            errors.append({"field": "id", "issue": "obligatorio al crear una VLAN"})
        elif not isinstance(body["id"], int) or isinstance(body["id"], bool):
            errors.append({"field": "id", "issue": "debe ser numero entero, no texto"})
        elif not (1 <= body["id"] <= 4094):
            errors.append({"field": "id", "issue": "fuera de rango, use 1 a 4094"})
        elif body["id"] in (1002, 1003, 1004, 1005):
            errors.append({"field": "id",
                           "issue": "rango reservado por Cisco para Token Ring y FDDI"})
    if full and "name" not in body:
        errors.append({"field": "name", "issue": "PUT requiere el objeto completo"})
    if "name" in body:
        name = body["name"]
        if not isinstance(name, str) or not name.strip():
            errors.append({"field": "name", "issue": "debe ser texto no vacio"})
        elif len(name) > 32:
            errors.append({"field": "name", "issue": "maximo 32 caracteres en IOS"})
        elif not re.match(r"^[A-Za-z0-9_-]+$", name):
            errors.append({"field": "name",
                           "issue": "solo letras, numeros, guion y guion bajo, sin espacios"})
    if "status" in body and body["status"] not in ("active", "suspended"):
        errors.append({"field": "status", "issue": "debe ser active o suspended"})
    if errors:
        raise ApiError(422, "validacion_fallida",
                       "El cuerpo de la peticion no paso las validaciones",
                       {"errors": errors})


# ---------------------------------------------------------------------------
# running-config en texto plano, para demostrar negociacion de contenido
# ---------------------------------------------------------------------------
def running_config(world, dev):
    L = ["!", f"! Configuracion generada por NetLab API",
         f"! Dispositivo: {dev['model']}  {dev['os']} {dev['version']}", "!",
         "version " + dev["version"].split("(")[0], "!",
         f"hostname {dev['hostname']}", "!"]
    if dev["vlans"]:
        for v in sorted(dev["vlans"].values(), key=lambda x: x["id"]):
            if v["id"] == 1:
                continue
            L += [f"vlan {v['id']}", f" name {v['name']}"]
            if v["status"] == "suspended":
                L.append(" state suspend")
            L.append("!")
    for i in dev["interfaces"].values():
        L.append(f"interface {i['name']}")
        if i["description"]:
            L.append(f" description {i['description']}")
        if i["mode"] == "access" and i["vlan"]:
            L += [" switchport mode access", f" switchport access vlan {i['vlan']}"]
        elif i["mode"] == "trunk":
            L.append(" switchport mode trunk")
            if i["allowed_vlans"]:
                L.append(" switchport trunk allowed vlan "
                         + ",".join(str(x) for x in sorted(i["allowed_vlans"])))
        if i["ip_address"]:
            ip, prefix = i["ip_address"].split("/")
            L.append(f" ip address {ip} {prefix_to_mask(int(prefix))}")
        elif i["mode"] == "routed":
            L.append(" no ip address")
        if i["mtu"] != 1500:
            L.append(f" mtu {i['mtu']}")
        if i["duplex"] != "auto":
            L.append(f" duplex {i['duplex']}")
        if not i["enabled"]:
            L.append(" shutdown")
        L.append("!")
    L += ["line con 0", " logging synchronous", "!", "end", ""]
    return "\n".join(L)


def prefix_to_mask(p):
    bits = (0xFFFFFFFF << (32 - p)) & 0xFFFFFFFF if p else 0
    return ".".join(str((bits >> s) & 0xFF) for s in (24, 16, 8, 0))


# ---------------------------------------------------------------------------
# Endpoints demostrativos (rate limit y retardo)
# ---------------------------------------------------------------------------
RATE_STATE = {}
RATE_LIMIT = 5
RATE_WINDOW = 10.0


def check_rate_limit(ip):
    now = time.time()
    with LOCK:
        hits = [t for t in RATE_STATE.get(ip, []) if now - t < RATE_WINDOW]
        if len(hits) >= RATE_LIMIT:
            retry = int(RATE_WINDOW - (now - hits[0])) + 1
            RATE_STATE[ip] = hits
            raise ApiError(429, "demasiadas_peticiones",
                           f"Limite de {RATE_LIMIT} peticiones cada {int(RATE_WINDOW)} segundos",
                           {"limite": RATE_LIMIT, "ventana_segundos": RATE_WINDOW},
                           {"Retry-After": str(retry),
                            "X-RateLimit-Limit": str(RATE_LIMIT),
                            "X-RateLimit-Remaining": "0"})
        hits.append(now)
        RATE_STATE[ip] = hits
        return len(hits)


# ---------------------------------------------------------------------------
# Enrutador HTTP
# ---------------------------------------------------------------------------
ROUTES = []


def route(pattern, methods):
    def deco(fn):
        ROUTES.append((re.compile("^" + pattern + "$"), tuple(methods), fn))
        return fn
    return deco


class Ctx:
    """Todo lo que un manejador necesita saber de la peticion."""

    def __init__(self, handler, method, path, query, body, world, team, role, ip):
        self.h = handler
        self.method = method
        self.path = path
        self.query = query
        self.body = body
        self.world = world
        self.team = team
        self.role = role
        self.ip = ip

    def q(self, name, default=None):
        v = self.query.get(name)
        return v[0] if v else default

    def qint(self, name, default=None):
        v = self.q(name)
        if v is None:
            return default
        try:
            return int(v)
        except ValueError:
            raise ApiError(400, "parametro_invalido",
                           f"El parametro de consulta '{name}' debe ser un numero entero",
                           {"recibido": v})

    def header(self, name, default=None):
        return self.h.headers.get(name, default)

    def require_body(self):
        if self.body is None:
            raise ApiError(400, "cuerpo_requerido",
                           f"{self.method} requiere un cuerpo JSON. Recuerde usar -d y "
                           "la cabecera Content-Type: application/json")
        if not isinstance(self.body, dict):
            raise ApiError(400, "cuerpo_invalido",
                           "El cuerpo debe ser un objeto JSON, no una lista ni un valor simple")
        return self.body


def find_device(ctx, dev_id):
    dev = ctx.world["devices"].get(dev_id)
    if not dev:
        raise ApiError(404, "dispositivo_no_encontrado",
                       f"No existe el dispositivo '{dev_id}'",
                       {"disponibles": sorted(ctx.world["devices"])})
    return dev


def find_iface(ctx, dev, name):
    """Acepta el nombre completo o la abreviatura, sin importar mayusculas."""
    if name in dev["interfaces"]:
        return dev["interfaces"][name]
    low = name.lower()
    for i in dev["interfaces"].values():
        if i["name"].lower() == low or i["abbrev"].lower() == low:
            return i
    raise ApiError(404, "interfaz_no_encontrada",
                   f"No existe la interfaz '{name}' en {dev['hostname']}",
                   {"disponibles": [i["name"] for i in dev["interfaces"].values()],
                    "nota": "puede usar el nombre completo o la abreviatura, ejemplo Gi1/0/1"})


def ensure_online(dev):
    if dev["status"] != "online":
        raise ApiError(409, "dispositivo_no_disponible",
                       f"{dev['hostname']} esta en estado '{dev['status']}' y no acepta cambios",
                       {"estado": dev["status"]})


def check_precondition(ctx, obj):
    """Concurrencia optimista: If-Match debe coincidir con el ETag actual."""
    im = ctx.header("If-Match")
    if not im:
        return
    actual = etag_for(obj)
    if im.strip() != "*" and actual not in [t.strip() for t in im.split(",")]:
        raise ApiError(412, "precondicion_fallida",
                       "El recurso cambio desde que usted lo leyo; vuelva a consultarlo",
                       {"etag_enviado": im, "etag_actual": actual})


# --------------------------- indice de la API ------------------------------
@route(r"/api/v1", ["GET"])
def r_index(ctx):
    return 200, {
        "servicio": "NetLab API",
        "version": "1.0",
        "descripcion": "Laboratorio de automatizacion de redes con dispositivos Cisco simulados",
        "equipo_actual": ctx.team,
        "autenticacion": {
            "cabecera": "X-API-Key",
            "requerida_en": list(WRITE_METHODS),
            "nota": "las lecturas GET no requieren clave",
        },
        "recursos": {
            "GET    /api/v1/devices": "lista de dispositivos",
            "GET    /api/v1/devices/{id}": "detalle de un dispositivo",
            "GET    /api/v1/devices/{id}/config": "running-config (text/plain o JSON)",
            "GET    /api/v1/devices/{id}/interfaces": "lista de interfaces",
            "GET    /api/v1/devices/{id}/interfaces/{if}": "detalle de una interfaz",
            "PUT    /api/v1/devices/{id}/interfaces/{if}": "reemplaza la configuracion completa",
            "PATCH  /api/v1/devices/{id}/interfaces/{if}": "modifica campos puntuales",
            "POST   /api/v1/devices/{id}/interfaces/{if}/shutdown": "apaga la interfaz",
            "POST   /api/v1/devices/{id}/interfaces/{if}/no-shutdown": "enciende la interfaz",
            "GET    /api/v1/devices/{id}/vlans": "lista de VLANs",
            "POST   /api/v1/devices/{id}/vlans": "crea una VLAN",
            "GET    /api/v1/devices/{id}/vlans/{vid}": "detalle de una VLAN",
            "PUT    /api/v1/devices/{id}/vlans/{vid}": "reemplaza una VLAN",
            "PATCH  /api/v1/devices/{id}/vlans/{vid}": "renombra o suspende una VLAN",
            "DELETE /api/v1/devices/{id}/vlans/{vid}": "elimina una VLAN",
            "POST   /api/v1/devices/{id}/actions/reload": "reinicia el dispositivo",
            "POST   /api/v1/devices/{id}/actions/save": "guarda la configuracion",
            "GET    /api/v1/events": "eventos del laboratorio",
            "GET    /api/v1/stream": "flujo de eventos en vivo (SSE)",
            "GET    /api/v1/stats": "estadisticas de uso de la API",
            "GET    /api/v1/health": "estado del servicio",
            "POST   /api/v1/reset": "reinicia el laboratorio al estado inicial",
        },
        "endpoints_didacticos": {
            "ANY  /api/v1/demo/echo": "devuelve su peticion tal como llego",
            "GET  /api/v1/demo/status/{codigo}": "responde con el codigo HTTP que usted pida",
            "GET  /api/v1/demo/slow?seconds=3": "responde despues de N segundos",
            "GET  /api/v1/demo/rate-limit": "devuelve 429 al superar 5 peticiones en 10 segundos",
            "GET  /api/v1/demo/redirect": "responde 302 hacia otro recurso",
        },
    }


# --------------------------- dispositivos ---------------------------------
@route(r"/api/v1/devices", ["GET"])
def r_devices(ctx):
    devs = [device_view(ctx.world, d, with_interfaces=False)
            for d in ctx.world["devices"].values()]
    rol = ctx.q("role")
    if rol:
        devs = [d for d in devs if d["role"] == rol]
    estado = ctx.q("status")
    if estado:
        devs = [d for d in devs if d["status"] == estado]
    q = ctx.q("q")
    if q:
        ql = q.lower()
        devs = [d for d in devs
                if ql in d["hostname"].lower() or ql in d["model"].lower()]
    devs.sort(key=lambda d: d["id"])
    total = len(devs)
    offset = ctx.qint("offset", 0) or 0
    limit = ctx.qint("limit", 50) or 50
    if offset < 0 or limit < 1:
        raise ApiError(400, "paginacion_invalida",
                       "offset debe ser mayor o igual a 0 y limit mayor o igual a 1")
    page = devs[offset:offset + limit]
    fields = ctx.q("fields")
    if fields:
        keep = {f.strip() for f in fields.split(",")}
        page = [{k: v for k, v in d.items() if k in keep} for d in page]
    return 200, {
        "total": total,
        "count": len(page),
        "offset": offset,
        "limit": limit,
        "devices": page,
    }


@route(r"/api/v1/devices/([^/]+)", ["GET"])
def r_device(ctx, dev_id):
    dev = find_device(ctx, dev_id)
    return 200, device_view(ctx.world, dev)


@route(r"/api/v1/devices/([^/]+)/config", ["GET"])
def r_config(ctx, dev_id):
    dev = find_device(ctx, dev_id)
    accept = (ctx.header("Accept") or "").lower()
    texto = running_config(ctx.world, dev)
    if "application/json" in accept:
        return 200, {"device": dev["id"], "hostname": dev["hostname"],
                     "lines": texto.split("\n"), "config": texto}
    return 200, ("text/plain; charset=utf-8", texto)


# --------------------------- interfaces -----------------------------------
@route(r"/api/v1/devices/([^/]+)/interfaces", ["GET"])
def r_ifaces(ctx, dev_id):
    dev = find_device(ctx, dev_id)
    ifaces = [iface_view(ctx.world, dev, i) for i in dev["interfaces"].values()]
    st = ctx.q("status")
    if st:
        if st not in ("up", "down"):
            raise ApiError(400, "parametro_invalido",
                           "status solo acepta 'up' o 'down'", {"recibido": st})
        ifaces = [i for i in ifaces if i["oper_status"] == st]
    modo = ctx.q("mode")
    if modo:
        ifaces = [i for i in ifaces if i["mode"] == modo]
    vlan = ctx.qint("vlan")
    if vlan is not None:
        ifaces = [i for i in ifaces if i["vlan"] == vlan]
    fields = ctx.q("fields")
    if fields:
        keep = {f.strip() for f in fields.split(",")}
        ifaces = [{k: v for k, v in i.items() if k in keep} for i in ifaces]
    return 200, {"device": dev["id"], "count": len(ifaces), "interfaces": ifaces}


# El nombre de la interfaz lleva barras (GigabitEthernet1/0/1). Se acepta tal
# cual o codificado como %2F, para que nadie tenga que escapar nada en curl.
@route(r"/api/v1/devices/([^/]+)/interfaces/(.+)", ["GET", "PUT", "PATCH"])
def r_iface(ctx, dev_id, if_name):
    dev = find_device(ctx, dev_id)
    iface = find_iface(ctx, dev, if_name)

    if ctx.method == "GET":
        return 200, iface_view(ctx.world, dev, iface)

    ensure_online(dev)
    check_precondition(ctx, iface_view(ctx.world, dev, iface))
    body = ctx.require_body()
    antes = iface_view(ctx.world, dev, iface)

    if ctx.method == "PUT":
        validate_iface_payload(body, dev, full=True)
        with LOCK:
            iface["description"] = body.get("description", "")
            iface["enabled"] = body["enabled"]
            iface["mode"] = body["mode"]
            iface["vlan"] = body.get("vlan") if body["mode"] == "access" else None
            iface["allowed_vlans"] = body.get("allowed_vlans", []) or []
            iface["ip_address"] = body.get("ip_address")
            iface["speed_mbps"] = body.get("speed_mbps", 1000)
            iface["duplex"] = body.get("duplex", "full")
            iface["mtu"] = body.get("mtu", 1500)
            iface["revision"] += 1
            iface["last_change"] = time.time()
            dev["config_saved"] = False
        add_event(ctx.world, "config", dev["id"],
                  f"PUT reemplazo la configuracion de {iface['abbrev']}")
    else:
        validate_iface_payload(body, dev, full=False)
        with LOCK:
            for k in ("description", "enabled", "mode", "vlan", "allowed_vlans",
                      "ip_address", "speed_mbps", "duplex", "mtu"):
                if k in body:
                    iface[k] = body[k]
            iface["revision"] += 1
            iface["last_change"] = time.time()
            dev["config_saved"] = False
        cambios = ", ".join(f"{k}={body[k]}" for k in body)
        add_event(ctx.world, "config", dev["id"],
                  f"PATCH sobre {iface['abbrev']}: {cambios}")

    despues = iface_view(ctx.world, dev, iface)
    if antes["oper_status"] != despues["oper_status"]:
        add_event(ctx.world, "alerta" if despues["oper_status"] == "down" else "info",
                  dev["id"],
                  f"Interfaz {iface['abbrev']} cambio de estado a {despues['oper_status'].upper()}")
        notify_peer(ctx.world, dev, iface)
    return 200, despues


def notify_peer(world, dev, iface):
    peer = iface.get("link_to")
    if not peer:
        return
    pdev = world["devices"].get(peer["device"])
    if not pdev:
        return
    pif = pdev["interfaces"].get(peer["interface"])
    if not pif:
        return
    estado = oper_status(world, pdev, pif)
    add_event(world, "alerta" if estado == "down" else "info", pdev["id"],
              f"El enlace vecino cambio: {pif['abbrev']} ahora esta {estado.upper()}")


@route(r"/api/v1/devices/([^/]+)/interfaces/(.+)/(shutdown|no-shutdown)", ["POST"])
def r_iface_action(ctx, dev_id, if_name, accion):
    dev = find_device(ctx, dev_id)
    iface = find_iface(ctx, dev, if_name)
    ensure_online(dev)
    # La operacion es idempotente: repetir shutdown sobre un puerto ya apagado
    # devuelve 200 igualmente, tal como lo haria el propio IOS.
    apagar = accion == "shutdown"
    with LOCK:
        iface["enabled"] = not apagar
        iface["revision"] += 1
        iface["last_change"] = time.time()
        dev["config_saved"] = False
    add_event(ctx.world, "alerta" if apagar else "info", dev["id"],
              f"{'shutdown' if apagar else 'no shutdown'} aplicado a {iface['abbrev']}")
    notify_peer(ctx.world, dev, iface)
    return 200, {
        "resultado": "aplicado",
        "comando_ios": [f"interface {iface['name']}",
                        "shutdown" if apagar else "no shutdown"],
        "interface": iface_view(ctx.world, dev, iface),
    }


# ------------------------------ VLANs -------------------------------------
@route(r"/api/v1/devices/([^/]+)/vlans", ["GET", "POST"])
def r_vlans(ctx, dev_id):
    dev = find_device(ctx, dev_id)
    if ctx.method == "GET":
        vlans = sorted(dev["vlans"].values(), key=lambda v: v["id"])
        salida = []
        for v in vlans:
            puertos = [i["abbrev"] for i in dev["interfaces"].values()
                       if i["mode"] == "access" and i["vlan"] == v["id"]]
            salida.append(dict(v, ports=puertos, port_count=len(puertos)))
        return 200, {"device": dev["id"], "count": len(salida), "vlans": salida}

    if dev["role"] == "router":
        raise ApiError(409, "operacion_no_soportada",
                       f"{dev['hostname']} es un router y no administra VLANs de capa 2",
                       {"sugerencia": "use un switch como csr-core-01 o sw-access-01"})
    ensure_online(dev)
    body = ctx.require_body()
    validate_vlan_payload(body, dev, full=False)
    vid = body["id"]
    if str(vid) in dev["vlans"]:
        raise ApiError(409, "vlan_duplicada",
                       f"La VLAN {vid} ya existe en {dev['hostname']}",
                       {"existente": dev["vlans"][str(vid)],
                        "sugerencia": f"use PATCH /api/v1/devices/{dev['id']}/vlans/{vid} para modificarla"})
    with LOCK:
        dev["vlans"][str(vid)] = {
            "id": vid,
            "name": body.get("name", f"VLAN{vid:04d}"),
            "status": body.get("status", "active"),
            "created_by": ctx.team,
            "revision": 1,
        }
        dev["config_saved"] = False
    add_event(ctx.world, "config", dev["id"],
              f"VLAN {vid} ({dev['vlans'][str(vid)]['name']}) creada")
    return 201, (dev["vlans"][str(vid)],
                 {"Location": f"/api/v1/devices/{dev['id']}/vlans/{vid}"})


@route(r"/api/v1/devices/([^/]+)/vlans/(\d+)", ["GET", "PUT", "PATCH", "DELETE"])
def r_vlan(ctx, dev_id, vid):
    dev = find_device(ctx, dev_id)
    vlan = dev["vlans"].get(str(int(vid)))
    if not vlan:
        raise ApiError(404, "vlan_no_encontrada",
                       f"La VLAN {vid} no existe en {dev['hostname']}",
                       {"existentes": sorted(int(k) for k in dev["vlans"])})
    puertos = [i["abbrev"] for i in dev["interfaces"].values()
               if i["mode"] == "access" and i["vlan"] == vlan["id"]]

    if ctx.method == "GET":
        return 200, dict(vlan, ports=puertos, port_count=len(puertos))

    ensure_online(dev)
    check_precondition(ctx, dict(vlan, ports=puertos, port_count=len(puertos)))

    if ctx.method == "DELETE":
        if vlan["id"] == 1:
            raise ApiError(409, "vlan_protegida",
                           "La VLAN 1 es la VLAN por defecto y no se puede eliminar")
        if puertos:
            raise ApiError(409, "vlan_en_uso",
                           f"La VLAN {vlan['id']} tiene {len(puertos)} puertos asignados",
                           {"puertos": puertos,
                            "sugerencia": "reasigne esos puertos a otra VLAN antes de eliminarla"})
        with LOCK:
            del dev["vlans"][str(vlan["id"])]
            dev["config_saved"] = False
        add_event(ctx.world, "config", dev["id"], f"VLAN {vlan['id']} eliminada")
        return 204, None

    body = ctx.require_body()
    validate_vlan_payload(body, dev, full=(ctx.method == "PUT"), existing_id=vlan["id"])
    if "id" in body and body["id"] != vlan["id"]:
        raise ApiError(409, "identificador_inmutable",
                       "El identificador de la VLAN no se puede cambiar",
                       {"en_la_url": vlan["id"], "en_el_cuerpo": body["id"]})
    with LOCK:
        if ctx.method == "PUT":
            vlan["name"] = body["name"]
            vlan["status"] = body.get("status", "active")
        else:
            if "name" in body:
                vlan["name"] = body["name"]
            if "status" in body:
                vlan["status"] = body["status"]
        vlan["revision"] += 1
        dev["config_saved"] = False
    add_event(ctx.world, "config", dev["id"],
              f"VLAN {vlan['id']} actualizada: nombre={vlan['name']}, estado={vlan['status']}")
    return 200, dict(vlan, ports=puertos, port_count=len(puertos))


# ----------------------------- acciones -----------------------------------
@route(r"/api/v1/devices/([^/]+)/actions/reload", ["POST"])
def r_reload(ctx, dev_id):
    dev = find_device(ctx, dev_id)
    if dev["status"] == "reloading":
        raise ApiError(409, "reinicio_en_curso",
                       f"{dev['hostname']} ya se esta reiniciando")
    body = ctx.body or {}
    if body.get("confirm") is not True:
        raise ApiError(422, "confirmacion_requerida",
                       "Esta operacion interrumpe el servicio; envie {\"confirm\": true}",
                       {"ejemplo": {"confirm": True}})
    with LOCK:
        dev["status"] = "reloading"
        dev["_reload_until"] = time.time() + 9
        dev["config_saved"] = True
    add_event(ctx.world, "alerta", dev["id"],
              f"{dev['hostname']} recibio la orden de reinicio; volvera en unos segundos")
    return 202, {"resultado": "reinicio_aceptado",
                 "comando_ios": ["reload"],
                 "device": dev["id"],
                 "estado": "reloading",
                 "estimado_segundos": 9,
                 "nota": "202 Accepted indica que la orden se acepto pero aun no termino"}


@route(r"/api/v1/devices/([^/]+)/actions/save", ["POST"])
def r_save(ctx, dev_id):
    dev = find_device(ctx, dev_id)
    ensure_online(dev)
    with LOCK:
        dev["config_saved"] = True
    add_event(ctx.world, "info", dev["id"], "Configuracion guardada en memoria NVRAM")
    return 200, {"resultado": "guardado",
                 "comando_ios": ["write memory"],
                 "device": dev["id"], "config_saved": True}


# ------------------------- eventos y utilidades ---------------------------
@route(r"/api/v1/events", ["GET"])
def r_events(ctx):
    since = ctx.qint("since", 0) or 0
    limit = ctx.qint("limit", 100) or 100
    with LOCK:
        evs = [e for e in ctx.world["events"] if e["seq"] > since][-limit:]
        reqs = ctx.world["requests"][-limit:]
    return 200, {"count": len(evs), "last_seq": ctx.world["seq"],
                 "events": evs, "http_log": reqs}


@route(r"/api/v1/topology", ["GET"])
def r_topology(ctx):
    """Fotografia completa del laboratorio en una sola peticion.

    La interfaz grafica consume este recurso cada segundo con ?silent=1 para
    no aparecer en el registro de peticiones que se proyecta en clase.
    """
    devs = [device_view(ctx.world, d) for d in
            sorted(ctx.world["devices"].values(), key=lambda x: x["id"])]
    enlaces = []
    vistos = set()
    for d in ctx.world["devices"].values():
        for i in d["interfaces"].values():
            peer = i.get("link_to")
            if not peer:
                continue
            clave = tuple(sorted([f"{d['id']}:{i['name']}",
                                  f"{peer['device']}:{peer['interface']}"]))
            if clave in vistos:
                continue
            vistos.add(clave)
            pdev = ctx.world["devices"].get(peer["device"])
            pif = pdev["interfaces"].get(peer["interface"]) if pdev else None
            estado = "up" if (oper_status(ctx.world, d, i) == "up" and pif
                              and oper_status(ctx.world, pdev, pif) == "up") else "down"
            enlaces.append({
                "a_device": d["id"], "a_interface": i["abbrev"],
                "b_device": peer["device"],
                "b_interface": pif["abbrev"] if pif else peer["interface"],
                "status": estado,
                "speed_mbps": i["speed_mbps"],
                "utilization_pct": round(i["utilization_pct"], 1),
            })
    with LOCK:
        eventos = list(ctx.world["events"][-60:])
        peticiones = list(ctx.world["requests"][-40:])
        stats = copy.deepcopy(ctx.world["stats"])
    return 200, {
        "equipo": ctx.team,
        "equipos_activos": sorted(WORLDS),
        "servidor_ts": time.time(),
        "devices": devs,
        "links": enlaces,
        "events": eventos,
        "http_log": peticiones,
        "stats": stats,
    }


@route(r"/api/v1/stats", ["GET"])
def r_stats(ctx):
    with LOCK:
        s = copy.deepcopy(ctx.world["stats"])
    ifaces = [(d, i) for d in ctx.world["devices"].values()
              for i in d["interfaces"].values()]
    up = sum(1 for d, i in ifaces if oper_status(ctx.world, d, i) == "up")
    return 200, {
        "equipo": ctx.team,
        "equipos_activos": sorted(WORLDS),
        "peticiones": s,
        "resumen_red": {
            "dispositivos": len(ctx.world["devices"]),
            "dispositivos_online": sum(1 for d in ctx.world["devices"].values()
                                       if d["status"] == "online"),
            "interfaces": len(ifaces),
            "interfaces_up": up,
            "interfaces_down": len(ifaces) - up,
            "vlans_totales": sum(len(d["vlans"]) for d in ctx.world["devices"].values()),
            "cambios_sin_guardar": [d["id"] for d in ctx.world["devices"].values()
                                    if not d["config_saved"]],
        },
    }


@route(r"/api/v1/health", ["GET"])
def r_health(ctx):
    return 200, {"status": "ok",
                 "uptime_seconds": int(time.time() - START_TIME),
                 "equipos": len(WORLDS),
                 "hora_servidor": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


@route(r"/api/v1/reset", ["POST"])
def r_reset(ctx):
    with LOCK:
        WORLDS[ctx.team] = new_world()
    add_event(WORLDS[ctx.team], "info", None,
              f"Laboratorio del equipo '{ctx.team}' reiniciado al estado inicial")
    return 200, {"resultado": "laboratorio_reiniciado", "equipo": ctx.team}


# ------------------------ endpoints didacticos ----------------------------
@route(r"/api/v1/demo/echo", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def r_echo(ctx):
    return 200, {
        "mensaje": "Esto es exactamente lo que recibio el servidor",
        "metodo": ctx.method,
        "ruta": ctx.path,
        "parametros_de_consulta": {k: v for k, v in ctx.query.items()},
        "cabeceras": {k: v for k, v in ctx.h.headers.items()},
        "cuerpo_interpretado": ctx.body,
        "su_ip": ctx.ip,
    }


@route(r"/api/v1/demo/status/(\d{3})", ["GET"])
def r_status(ctx, code):
    code = int(code)
    if not (100 <= code <= 599):
        raise ApiError(400, "codigo_invalido", "Use un codigo entre 100 y 599")
    significados = {
        200: "OK, la peticion tuvo exito",
        201: "Created, se creo un recurso nuevo",
        202: "Accepted, se acepto pero aun no termina",
        204: "No Content, exito sin cuerpo de respuesta",
        301: "Moved Permanently, el recurso cambio de direccion",
        304: "Not Modified, use su copia en cache",
        400: "Bad Request, la peticion esta mal formada",
        401: "Unauthorized, falta autenticacion",
        403: "Forbidden, esta autenticado pero no tiene permiso",
        404: "Not Found, el recurso no existe",
        405: "Method Not Allowed, ese verbo no aplica aqui",
        409: "Conflict, choca con el estado actual",
        412: "Precondition Failed, la condicion If-Match no se cumplio",
        415: "Unsupported Media Type, falta o sobra el Content-Type",
        422: "Unprocessable Content, sintaxis valida pero datos invalidos",
        429: "Too Many Requests, superó el limite de peticiones",
        500: "Internal Server Error, fallo del servidor",
        502: "Bad Gateway, un intermediario recibio una respuesta invalida",
        503: "Service Unavailable, el servicio no esta disponible",
    }
    if code == 204 or code == 304:
        return code, None
    return code, {"codigo_solicitado": code,
                  "significado": significados.get(code, "codigo sin descripcion en este lab"),
                  "familia": f"{code // 100}xx"}


@route(r"/api/v1/demo/slow", ["GET"])
def r_slow(ctx):
    seg = ctx.qint("seconds", 3) or 3
    seg = max(0, min(20, seg))
    time.sleep(seg)
    return 200, {"mensaje": f"Respondi despues de {seg} segundos",
                 "uso": "sirve para practicar timeouts con curl --max-time"}


@route(r"/api/v1/demo/rate-limit", ["GET"])
def r_rate(ctx):
    n = check_rate_limit(ctx.ip)
    return 200, {"mensaje": "peticion aceptada", "consumidas": n,
                 "limite": RATE_LIMIT, "ventana_segundos": RATE_WINDOW}, \
        {"X-RateLimit-Limit": str(RATE_LIMIT),
         "X-RateLimit-Remaining": str(max(0, RATE_LIMIT - n))}


@route(r"/api/v1/demo/redirect", ["GET"])
def r_redirect(ctx):
    raise ApiError(302, "redireccion",
                   "El recurso esta en otra ubicacion; siga la cabecera Location",
                   {"pista": "curl -L sigue las redirecciones automaticamente"},
                   {"Location": "/api/v1/devices"})


# ---------------------------------------------------------------------------
# Servidor HTTP
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "NetLabAPI/1.0"
    sys_version = ""

    # ---- utilidades de respuesta -----------------------------------------
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods",
                         "GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-API-Key, X-Lab-Team, If-Match, If-None-Match")
        self.send_header("Access-Control-Expose-Headers",
                         "ETag, Location, Retry-After, X-RateLimit-Limit, "
                         "X-RateLimit-Remaining, X-Request-Id")
        self.send_header("Access-Control-Max-Age", "600")

    def _send(self, status, payload=None, extra=None, ctype="application/json; charset=utf-8",
              head_only=False):
        body = b""
        if payload is not None:
            if isinstance(payload, (bytes, bytearray)):
                body = bytes(payload)
            elif isinstance(payload, str):
                body = payload.encode("utf-8")
            else:
                body = json.dumps(payload, ensure_ascii=False, indent=2,
                                  default=str).encode("utf-8")
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.send_header("X-Request-Id", self._req_id)
        self.send_header("X-NetLab-Team", self._team)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body and not head_only:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
        self._status = status

    # ---- registro de peticiones ------------------------------------------
    def log_message(self, fmt, *args):
        return  # silencio: llevamos nuestro propio registro

    def _record(self, status, started):
        # El refresco automatico de la interfaz grafica se marca con ?silent=1
        # para que el registro proyectado muestre solo lo que hace la clase.
        if "silent=1" in (urlparse(self.path).query or ""):
            return
        # Las sondas de Kubernetes consultan /health cada pocos segundos. Si se
        # registraran, ahogarian el registro que se proyecta a los estudiantes.
        if urlparse(self.path).path.rstrip("/") == "/api/v1/health":
            return
        world = get_world(self._team)
        dur = round((time.time() - started) * 1000, 1)
        with LOCK:
            world["req_seq"] += 1
            world["requests"].append({
                "seq": world["req_seq"], "ts": time.time(),
                "method": self.command, "path": self.path,
                "status": status, "ip": self._ip, "duration_ms": dur,
            })
            if len(world["requests"]) > MAX_EVENTS:
                del world["requests"][:-MAX_EVENTS]
            s = world["stats"]
            s["total_requests"] += 1
            s["by_method"][self.command] = s["by_method"].get(self.command, 0) + 1
            s["by_status"][str(status)] = s["by_status"].get(str(status), 0) + 1

    # ---- despacho --------------------------------------------------------
    def _dispatch(self, head_only=False):
        started = time.time()
        self._status = 500
        self._ip = (self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                    or self.client_address[0])
        # En pila dual, los clientes IPv4 llegan como ::ffff:A.B.C.D; se
        # normaliza para que el registro proyectado y el limite de peticiones
        # usen la direccion que la clase reconoce.
        if self._ip.startswith("::ffff:"):
            self._ip = self._ip[7:]
        self._team = re.sub(r"[^A-Za-z0-9_-]", "", self.headers.get("X-Lab-Team", "") or "")[:24] or "shared"
        self._req_id = hashlib.md5(f"{time.time()}{self.path}{self._ip}".encode()).hexdigest()[:12]
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        try:
            # 1) interfaz de usuario y archivos estaticos
            if path in ("/", "/index.html", "/ui"):
                return self._serve_ui(head_only)
            if path == "/favicon.ico":
                return self._send(204, None, head_only=head_only)

            # 2) preflight del navegador
            if self.command == "OPTIONS":
                permitidos = self._methods_for(path)
                if not permitidos:
                    raise ApiError(404, "ruta_no_encontrada",
                                   f"La ruta '{path}' no existe en esta API")
                allow = ", ".join(sorted(set(permitidos) | {"OPTIONS", "HEAD"}))
                return self._send(204, None, {"Allow": allow}, head_only=head_only)

            if not path.startswith("/api/"):
                raise ApiError(404, "ruta_no_encontrada",
                               f"La ruta '{path}' no existe",
                               {"sugerencia": "consulte GET /api/v1 para ver los recursos"})

            # 3) resolucion de la ruta
            match, fn, permitidos = None, None, []
            for rx, methods, handler in ROUTES:
                m = rx.match(path)
                if m:
                    permitidos.extend(methods)
                    if self.command in methods or (head_only and "GET" in methods):
                        match, fn = m, handler
                        break
            if fn is None:
                if permitidos:
                    allow = ", ".join(sorted(set(permitidos) | {"OPTIONS", "HEAD"}))
                    raise ApiError(405, "metodo_no_permitido",
                                   f"{self.command} no esta permitido en '{path}'",
                                   {"metodos_permitidos": sorted(set(permitidos))},
                                   {"Allow": allow})
                raise ApiError(404, "ruta_no_encontrada",
                               f"La ruta '{path}' no existe en esta API",
                               {"sugerencia": "consulte GET /api/v1 para ver los recursos"})

            # 4) cuerpo de la peticion
            body = self._read_body(path)

            # 5) autenticacion y autorizacion
            role = self._authorize(path)

            # 6) ejecucion
            world = get_world(self._team)
            ctx = Ctx(self, "GET" if head_only else self.command, path, query,
                      body, world, self._team, role, self._ip)
            # Los segmentos capturados pueden venir codificados (%2F) desde el
            # navegador; se normalizan antes de entregarlos al manejador.
            result = fn(ctx, *[unquote(g) for g in match.groups()])

            # 7) forma de la respuesta
            extra = {}
            if isinstance(result, tuple) and len(result) == 3:
                status, payload, extra = result
            else:
                status, payload = result
            ctype = "application/json; charset=utf-8"
            if isinstance(payload, tuple):
                if len(payload) == 2 and isinstance(payload[0], str) and "/" in payload[0]:
                    ctype, payload = payload
                else:
                    payload, extra2 = payload
                    extra = {**extra, **extra2}

            # 8) ETag y cache condicional en lecturas
            if self.command in ("GET", "HEAD") and payload is not None and status == 200:
                tag = etag_for(payload)
                extra.setdefault("ETag", tag)
                extra.setdefault("Cache-Control", "no-cache")
                inm = self.headers.get("If-None-Match")
                if inm and tag in [t.strip() for t in inm.split(",")]:
                    self._send(304, None, {"ETag": tag}, head_only=True)
                    return self._record(304, started)
            self._send(status, payload, extra, ctype, head_only=head_only)
            return self._record(status, started)

        except ApiError as e:
            payload = {"error": {"codigo": e.code, "mensaje": e.message,
                                 "estado_http": e.status}}
            if e.detail:
                payload["error"]["detalle"] = e.detail
            self._send(e.status, payload, e.headers, head_only=head_only)
            return self._record(e.status, started)
        except Exception as e:  # noqa: BLE001
            payload = {"error": {"codigo": "error_interno", "estado_http": 500,
                                 "mensaje": f"{type(e).__name__}: {e}"}}
            self._send(500, payload, head_only=head_only)
            return self._record(500, started)

    def _methods_for(self, path):
        out = []
        for rx, methods, _ in ROUTES:
            if rx.match(path):
                out.extend(methods)
        return out

    def _read_body(self, path=""):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "longitud_invalida", "Content-Length no es un numero")
        if length <= 0:
            return None
        if length > 1_000_000:
            raise ApiError(413, "cuerpo_muy_grande", "El cuerpo excede 1 MB")
        raw = self.rfile.read(length)
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        # El endpoint de eco acepta cualquier tipo: su proposito es mostrar
        # exactamente lo que llego, incluso si no es JSON.
        if path.startswith("/api/v1/demo/echo"):
            try:
                return json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {"_cuerpo_crudo": raw.decode("utf-8", "replace"),
                        "_nota": "el cuerpo no era JSON valido, se muestra como texto"}
        if self.command in WRITE_METHODS and ctype != "application/json":
            raise ApiError(415, "tipo_de_medio_no_soportado",
                           "Esta API solo acepta application/json",
                           {"content_type_recibido": ctype or "(ninguno)",
                            "solucion": 'agregue -H "Content-Type: application/json" a su comando curl'})
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ApiError(400, "json_malformado",
                           "El cuerpo enviado no es JSON valido",
                           {"detalle_del_parser": str(e),
                            "recibido": raw.decode("utf-8", "replace")[:200],
                            "pista": "en la terminal use comillas simples alrededor del JSON"})

    def _authorize(self, path):
        # Las lecturas son abiertas para que la clase pueda explorar sin friccion.
        if self.command in ("GET", "HEAD", "OPTIONS"):
            return "public"
        if path.startswith("/api/v1/demo/"):
            return "public"
        key = self.headers.get("X-API-Key")
        if not key:
            raise ApiError(401, "autenticacion_requerida",
                           "Esta operacion modifica la red y requiere la cabecera X-API-Key",
                           {"como": 'agregue -H "X-API-Key: <clave de escritura del laboratorio>"'},
                           {"WWW-Authenticate": 'ApiKey realm="netlab", header="X-API-Key"'})
        info = API_KEYS.get(key)
        if not info:
            raise ApiError(403, "credencial_invalida",
                           "La clave enviada no es valida en este laboratorio")
        if info["role"] == "readonly":
            raise ApiError(403, "permiso_insuficiente",
                           "Su clave es de solo lectura y no puede modificar la red",
                           {"rol": info["role"],
                            "diferencia": "401 significa que no se identifico; "
                                          "403 que se identifico pero no tiene permiso"})
        return info["role"]

    def _serve_ui(self, head_only=False):
        try:
            with open(UI_FILE, "rb") as f:
                html = f.read()
        except OSError:
            return self._send(500, {"error": {"codigo": "ui_no_disponible",
                                              "mensaje": "No se encontro ui.html"}})
        self._send(200, html, {"Cache-Control": "no-store"},
                   "text/html; charset=utf-8", head_only=head_only)

    # ---- flujo de eventos en vivo (SSE) ----------------------------------
    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()
        world = get_world(self._team)
        last = world["seq"]
        fin = time.time() + 300
        try:
            self.wfile.write(b": conectado al flujo de eventos de NetLab\n\n")
            self.wfile.flush()
            while time.time() < fin:
                with LOCK:
                    nuevos = [e for e in world["events"] if e["seq"] > last]
                    if nuevos:
                        last = nuevos[-1]["seq"]
                for e in nuevos:
                    self.wfile.write(f"event: {e['level']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n".encode())
                if not nuevos:
                    self.wfile.write(b": latido\n\n")
                self.wfile.flush()
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        self.close_connection = True

    # ---- verbos ----------------------------------------------------------
    def do_GET(self):
        if urlparse(self.path).path.rstrip("/") == "/api/v1/stream":
            self._ip = self.client_address[0]
            self._team = re.sub(r"[^A-Za-z0-9_-]", "",
                                self.headers.get("X-Lab-Team", "") or "")[:24] or "shared"
            return self._stream()
        self._dispatch()

    def do_HEAD(self):
        self._dispatch(head_only=True)

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = lambda self: self._dispatch()


# Servidor de pila dual: acepta IPv4 e IPv6 en el mismo puerto. Algunos
# entornos Docker con IPv6 habilitado (por ejemplo OrbStack) reenvian
# localhost hacia la direccion IPv6 del contenedor; si el proceso escuchara
# solo en 0.0.0.0 esas conexiones se rechazarian y el navegador mostraria
# "connection reset".
class DualStackHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        super().server_bind()


def main():
    get_world("shared")
    threading.Thread(target=simulator, daemon=True).start()
    try:
        srv = DualStackHTTPServer(("::", PORT), Handler)
        pila = "IPv4 e IPv6"
    except OSError:
        # Equipos sin IPv6: se escucha solo en IPv4, como antes.
        srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        pila = "IPv4"
    srv.daemon_threads = True
    print(f"NetLab API escuchando en el puerto {PORT} ({pila})", flush=True)
    print(f"Interfaz de usuario servida desde {UI_FILE}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
