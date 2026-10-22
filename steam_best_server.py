"""
Steam Best Server
Encuentra el servidor de descarga de Steam con MAYOR VELOCIDAD REAL para tu conexion.

Como funciona:
  1. Pide a la API publica de Steam la lista de servidores de contenido.
  2. Mide la latencia a todos (rapido) y se queda con los mejores candidatos de cada region.
  3. Descarga datos reales (chunks de juegos) de cada candidato y mide MB/s.
  4. Vuelve a probar a los 3 mas rapidos para confirmar y muestra UN solo ganador.

Solo usa la libreria estandar de Python (sin dependencias).
"""

import ctypes
import http.client
import json
import queue
import re
import socket
import statistics
import threading
import time
import tkinter as tk
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from tkinter import font as tkfont

# --------------------------------------------------------------------------- #
#  Red
# --------------------------------------------------------------------------- #

API = (
    "https://api.steampowered.com/IContentServerDirectoryService/"
    "GetServersForSteamPipe/v1/?cell_id={cell}&max_servers={n}"
)

# Chunks reales de depots publicos de Steam, tomados de logs publicos de caches.
# Se piden por HTTP sin login; el programa prueba varios por si alguno ya no existe.
CHUNKS = [
    (389731, "e36798661d164d1164813d7c33aae505dfba5c57"),
    (389731, "385f6befc76919c5900c1765c37d3423d9da8b1e"),
    (389731, "bf33ca4d3791edf308ae50f7f15c11a932f3cc83"),
    (389731, "24f724f0e1c69f87f7210050d9c14b428a059c7e"),
    (107410, "254acf75ecc8ca9ec0163a23156747738cab2c7a"),
    (107410, "7741d309db44ecde753203270eeb18ed26a584ee"),
    (107410, "a321cf98af5bb190d285aea0339ef6fa41df14a2"),
    (107410, "c95b4de26d80339d69f4a8ecdc3c8614cc780783"),
]

REGIONES = {
    "eze": "Argentina - Buenos Aires",
    "scl": "Chile - Santiago",
    "gru": "Brasil - Sao Paulo",
    "lim": "Peru - Lima",
    "bog": "Colombia - Bogota",
    "mia": "EE.UU. - Miami",
    "dfw": "EE.UU. - Dallas",
    "iad": "EE.UU. - Washington",
    "lax": "EE.UU. - Los Angeles",
    "ord": "EE.UU. - Chicago",
    "atl": "EE.UU. - Atlanta",
    "sea": "EE.UU. - Seattle",
    "jfk": "EE.UU. - Nueva York",
    "mad": "Espana - Madrid",
    "fra": "Alemania - Frankfurt",
    "ams": "Paises Bajos - Amsterdam",
    "lhr": "Reino Unido - Londres",
    "par": "Francia - Paris",
    "sto": "Suecia - Estocolmo",
}


def region_codigo(host):
    m = re.search(r"-([a-z]{3})\d*\.", host)
    return m.group(1) if m else None


def region_nombre(host):
    c = region_codigo(host)
    if not c:
        return ""
    return REGIONES.get(c, c.upper())


def consultar_api(cell, n):
    try:
        req = urllib.request.Request(
            API.format(cell=cell, n=n), headers={"User-Agent": "SteamBestServer/2.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
        return data.get("response", {}).get("servers", [])
    except Exception:
        return []


def obtener_servidores(amplia, prog):
    encontrados = {}

    def agregar(servers):
        for s in servers:
            if s.get("type") not in ("SteamCache", "CDN"):
                continue
            if s.get("allowed_app_ids"):
                continue
            host = s.get("host") or s.get("vhost")
            if not host or host in encontrados:
                continue
            encontrados[host] = {
                "host": host,
                "load": s.get("load", 0) or 0,
                "https": s.get("https_support") == "mandatory",
            }

    prog(0.02, "Consultando servidores de Steam")
    agregar(consultar_api(0, 100))
    if amplia:
        celdas = list(range(1, 251))
        hechos = 0
        with ThreadPoolExecutor(max_workers=16) as ex:
            for servers in ex.map(lambda c: consultar_api(c, 20), celdas):
                hechos += 1
                agregar(servers)
                prog(0.02 + 0.18 * hechos / len(celdas), "Consultando servidores de Steam")
    return encontrados


def medir_latencia(info, muestras=5):
    puertos = (443,) if info["https"] else (80, 443)
    tiempos = []
    for _ in range(muestras):
        for puerto in puertos:
            try:
                t0 = time.perf_counter()
                with socket.create_connection((info["host"], puerto), timeout=2):
                    pass
                tiempos.append((time.perf_counter() - t0) * 1000)
                break
            except OSError:
                continue
    if len(tiempos) < 2:
        return None
    return statistics.median(tiempos)


def puntaje(c):
    return c["ping"] * (1 + c["load"] / 100)


def seleccionar_candidatos(medidos, grupos=5, por_grupo=2):
    """Los mejores servidores de las mejores regiones (maximo grupos*por_grupo)."""
    por_region = {}
    for m in medidos:
        clave = region_codigo(m["host"]) or m["host"]
        por_region.setdefault(clave, []).append(m)
    ordenados = sorted(por_region.values(), key=lambda g: min(x["ping"] for x in g))
    elegidos = []
    for g in ordenados[:grupos]:
        elegidos += sorted(g, key=puntaje)[:por_grupo]
    return elegidos


def _descargar(host, https, puerto, fin, contador, lock, idx):
    conn = None
    i = idx
    errores = 0
    while time.perf_counter() < fin and errores < 3:
        depot, chunk = CHUNKS[i % len(CHUNKS)]
        i += 1
        try:
            if conn is None:
                if https:
                    conn = http.client.HTTPSConnection(host, puerto or 443, timeout=6)
                else:
                    conn = http.client.HTTPConnection(host, puerto or 80, timeout=6)
            conn.request(
                "GET",
                f"/depot/{depot}/chunk/{chunk}",
                headers={"User-Agent": "Valve/Steam HTTP Client 1.0"},
            )
            r = conn.getresponse()
            if r.status != 200:
                r.read()
                errores += 1
                continue
            while time.perf_counter() < fin:
                b = r.read(65536)
                if not b:
                    break
                with lock:
                    contador[0] += len(b)
            else:
                conn.close()
                conn = None
                break
        except Exception:
            errores += 1
            try:
                conn.close()
            except Exception:
                pass
            conn = None
    try:
        if conn is not None:
            conn.close()
    except Exception:
        pass


def medir_velocidad(host, https, segundos, hilos=4, puerto=None):
    """Descarga datos reales durante 'segundos' y devuelve MB/s (o None si no pudo)."""
    contador = [0]
    lock = threading.Lock()
    inicio = time.perf_counter()
    fin = inicio + segundos
    ts = [
        threading.Thread(
            target=_descargar, args=(host, https, puerto, fin, contador, lock, i), daemon=True
        )
        for i in range(hilos)
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join(segundos + 8)
    dur = max(time.perf_counter() - inicio, 0.001)
    if contador[0] < 200_000:
        return None
    return contador[0] / dur / 1e6


def buscar(amplia, prog):
    servers = obtener_servidores(amplia, prog)
    if not servers:
        raise RuntimeError("No pude obtener la lista de servidores de Steam. Revisa tu conexion a internet.")

    lista = list(servers.values())
    medidos = []
    hechos = 0
    with ThreadPoolExecutor(max_workers=32) as ex:
        for info, ping in zip(lista, ex.map(medir_latencia, lista)):
            hechos += 1
            prog(0.20 + 0.15 * hechos / len(lista), "Midiendo latencia")
            if ping is not None:
                info["ping"] = ping
                medidos.append(info)
    if not medidos:
        raise RuntimeError("Ningun servidor respondio. Puede que algo este bloqueando la conexion.")

    cands = seleccionar_candidatos(medidos)
    n = len(cands)
    probados = []
    for i, c in enumerate(cands):
        prog(0.35 + 0.45 * i / n, f"Midiendo velocidad real  ({i + 1} de {n})")
        v = medir_velocidad(c["host"], c["https"], 3.0)
        if v:
            c["mbs"] = [v]
            probados.append(c)

    if not probados:
        mejor = min(medidos, key=puntaje)
        return {
            "host": mejor["host"],
            "region": region_nombre(mejor["host"]),
            "ping": mejor["ping"],
            "mbs": None,
        }

    probados.sort(key=lambda c: c["mbs"][0], reverse=True)
    top = probados[:3]
    for j, c in enumerate(top):
        prog(0.80 + 0.19 * j / len(top), "Confirmando los mas rapidos")
        v = medir_velocidad(c["host"], c["https"], 5.0)
        if v:
            c["mbs"].append(v)
    mejor = max(top, key=lambda c: statistics.mean(c["mbs"]))
    return {
        "host": mejor["host"],
        "region": region_nombre(mejor["host"]),
        "ping": mejor["ping"],
        "mbs": statistics.mean(mejor["mbs"]),
    }


# --------------------------------------------------------------------------- #
#  Interfaz estilo Explorador de archivos de Windows 11
# --------------------------------------------------------------------------- #

LIGHT = dict(
    bg="#F3F3F3", card="#FFFFFF", addr="#FFFFFF", text="#1B1B1B", sub="#616161",
    dis="#A8A8A8", line="#E3E3E3", hover="#E8E8E8", accent="#005FB8", ok="#0F7B0F",
    err="#C42B1C", bar="#0F7B0F",
)
DARK = dict(
    bg="#202020", card="#272727", addr="#2D2D2D", text="#FFFFFF", sub="#A0A0A0",
    dis="#5E5E5E", line="#383838", hover="#333333", accent="#60CDFF", ok="#6CCB5F",
    err="#FF99A4", bar="#6CCB5F",
)

# nombre -> (glifo Segoe Fluent Icons / MDL2, alternativa de texto)
GLIFOS = {
    "play": ("\uE768", "\u25B6"),
    "copy": ("\uE8C8", "\u29C9"),
    "refresh": ("\uE72C", "\u21BB"),
    "check": ("\uE73E", "\u2713"),
    "warn": ("\uE7BA", "!"),
    "globe": ("\uE774", "\u25CE"),
    "on": ("\uE73A", "\u2611"),
    "off": ("\uE739", "\u2610"),
    "folder": ("\uE8B7", "\u25A4"),
}


def tema_oscuro():
    try:
        import winreg

        k = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def estilo_titulo(root, t, dark):
    """Barra de titulo del mismo color que la ventana (como Windows 11)."""
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        dwm = ctypes.windll.dwmapi

        def attr(a, v):
            val = ctypes.c_int(v)
            dwm.DwmSetWindowAttribute(hwnd, a, ctypes.byref(val), 4)

        def cref(h):
            return int(h[1:3], 16) | (int(h[3:5], 16) << 8) | (int(h[5:7], 16) << 16)

        attr(20, 1 if dark else 0)
        attr(35, cref(t["bg"]))
        attr(36, cref(t["text"]))
    except Exception:
        pass


def rrect(c, x1, y1, x2, y2, r, **kw):
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return c.create_polygon(pts, smooth=True, **kw)


def num(v, dec=1):
    return f"{v:.{dec}f}".replace(".", ",")


class App:
    def __init__(self, root):
        self.root = root
        self.k = root.winfo_fpixels("1i") / 96.0
        self.dark = tema_oscuro()
        self.t = DARK if self.dark else LIGHT

        root.title("Steam Best Server")
        root.geometry(f"{self.s(780)}x{self.s(470)}")
        root.minsize(self.s(560), self.s(400))
        root.configure(bg=self.t["bg"])

        fams = set(tkfont.families())
        base = next((f for f in ("Segoe UI Variable Text", "Segoe UI") if f in fams), None)
        if base is None:
            base = tkfont.nametofont("TkDefaultFont").actual("family")
        disp = next((f for f in ("Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI") if f in fams), base)
        self.icon_fam = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in fams), None)

        self.f_text = tkfont.Font(family=base, size=10)
        self.f_small = tkfont.Font(family=base, size=9)
        self.f_mid = tkfont.Font(family=disp, size=13)
        self.f_big = tkfont.Font(family=disp, size=24, weight="bold")
        self.f_big2 = tkfont.Font(family=disp, size=19, weight="bold")
        self.f_big3 = tkfont.Font(family=disp, size=15, weight="bold")

        self.c = tk.Canvas(root, bg=self.t["bg"], highlightthickness=0, bd=0)
        self.c.pack(fill="both", expand=True)

        self.q = queue.Queue()
        self.estado = "idle"       # idle | run | ok | err
        self.status = "Listo"
        self.detalle = ""
        self.prog = 0.0
        self.prog_shown = 0.0
        self.resultado = None
        self.amplia = True
        self.hover = None
        self.hits = []

        self.c.bind("<Configure>", lambda e: self.draw())
        self.c.bind("<Motion>", self.on_motion)
        self.c.bind("<Leave>", lambda e: self.set_hover(None))
        self.c.bind("<Button-1>", self.on_click)

        root.after(60, lambda: estilo_titulo(root, self.t, self.dark))
        root.after(60, self.poll)

    # -- utilidades de dibujo ------------------------------------------------
    def s(self, v):
        return int(v * self.k)

    def glifo(self, x, y, nombre, color, size=12):
        fluent, alt = GLIFOS[nombre]
        if self.icon_fam:
            self.c.create_text(x, y, text=fluent, fill=color, font=(self.icon_fam, size))
        else:
            self.c.create_text(x, y, text=alt, fill=color, font=(self.f_text.actual("family"), size))

    def item(self, x, y, label, icono, name, cb, enabled=True, color=None):
        c, t = self.c, self.t
        iw = self.s(22)
        w = self.s(12) + iw + self.s(6) + self.f_text.measure(label) + self.s(14)
        h = self.s(34)
        if enabled and self.hover == name:
            rrect(c, x, y, x + w, y + h, self.s(5), fill=t["hover"], outline=t["hover"])
        col = t["text"] if enabled else t["dis"]
        self.glifo(x + self.s(12) + iw / 2, y + h / 2, icono, color if (enabled and color) else col)
        c.create_text(x + self.s(12) + iw + self.s(6), y + h / 2, text=label, anchor="w", font=self.f_text, fill=col)
        if enabled:
            self.hits.append((x, y, x + w, y + h, cb, name))
        return x + w

    def separador(self, x, y):
        self.c.create_line(x + self.s(4), y + self.s(7), x + self.s(4), y + self.s(27), fill=self.t["line"])
        return x + self.s(9)

    # -- dibujo --------------------------------------------------------------
    def draw(self):
        c, t, s = self.c, self.t, self.s
        c.delete("all")
        self.hits = []
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or h < 50:
            return
        corriendo = self.estado == "run"

        # Barra de direcciones
        ax1, ay1, ax2, ay2 = s(16), s(12), w - s(16), s(52)
        rrect(c, ax1, ay1, ax2, ay2, s(8), fill=t["addr"], outline=t["line"])
        self.glifo(ax1 + s(22), (ay1 + ay2) / 2, "folder", t["sub"], 12)
        c.create_text(
            ax1 + s(44), (ay1 + ay2) / 2, anchor="w", font=self.f_text, fill=t["text"],
            text="Steam   \u203A   Descargas   \u203A   Mejor servidor",
        )
        # refrescar
        rx = ax2 - s(26)
        if self.hover == "refresh" and not corriendo:
            rrect(c, rx - s(16), ay1 + s(6), rx + s(16), ay2 - s(6), s(5), fill=t["hover"], outline=t["hover"])
        self.glifo(rx, (ay1 + ay2) / 2, "refresh", t["dis"] if corriendo else t["text"], 11)
        if not corriendo:
            self.hits.append((rx - s(16), ay1, rx + s(16), ay2, self.iniciar, "refresh"))
        # progreso verde (como el Explorador)
        if corriendo:
            xa, xb = ax1 + s(10), ax2 - s(10)
            c.create_line(xa, ay2 - 1, xb, ay2 - 1, fill=t["line"], width=s(3))
            c.create_line(xa, ay2 - 1, xa + (xb - xa) * self.prog_shown, ay2 - 1, fill=t["bar"], width=s(3), capstyle="round")

        # Barra de comandos
        cy0 = s(60)
        x = s(14)
        x = self.item(x, cy0, "Buscar mejor servidor", "play", "buscar", self.iniciar, not corriendo, t["accent"])
        x = self.separador(x, cy0)
        x = self.item(x, cy0, "Copiar servidor", "copy", "copiar", self.copiar, self.estado == "ok")
        x = self.separador(x, cy0)
        self.item(
            x, cy0, "Probar mas regiones", "on" if self.amplia else "off", "amplia",
            self.alternar_amplia, not corriendo, t["accent"] if self.amplia else None,
        )

        # Panel de contenido
        px1, py1, px2, py2 = s(12), s(104), w - s(12), h - s(40)
        rrect(c, px1, py1, px2, py2, s(8), fill=t["card"], outline=t["line"])
        cx, cy = (px1 + px2) / 2, (py1 + py2) / 2
        ancho = (px2 - px1) - s(60)

        if self.estado == "idle":
            self.glifo(cx, cy - s(46), "globe", t["dis"], 34)
            c.create_text(cx, cy + s(6), text="Busca el servidor de descarga mas rapido", font=self.f_mid, fill=t["text"], width=ancho, justify="center")
            c.create_text(cx, cy + s(38), text="Mide la velocidad real de descarga, no solo el ping.", font=self.f_text, fill=t["sub"], width=ancho, justify="center")
        elif self.estado == "run":
            c.create_text(cx, cy - s(10), text=self.status, font=self.f_mid, fill=t["text"], width=ancho, justify="center")
            c.create_text(cx, cy + s(22), text=f"{int(self.prog_shown * 100)} %", font=self.f_text, fill=t["sub"])
        elif self.estado == "err":
            self.glifo(cx, cy - s(40), "warn", t["err"], 30)
            c.create_text(cx, cy + s(8), text=self.detalle, font=self.f_text, fill=t["text"], width=ancho, justify="center")
        elif self.estado == "ok":
            r = self.resultado
            self.glifo(cx, cy - s(92), "check", t["ok"], 26)
            c.create_text(cx, cy - s(56), text="Mejor servidor para descargar", font=self.f_text, fill=t["sub"])
            fuente = self.f_big
            for f in (self.f_big, self.f_big2, self.f_big3):
                fuente = f
                if f.measure(r["host"]) <= ancho:
                    break
            c.create_text(cx, cy - s(14), text=r["host"], font=fuente, fill=t["text"])
            partes = []
            if r["region"]:
                partes.append(r["region"])
            if r["mbs"] is not None:
                partes.append(f"{num(r['mbs'])} MB/s  ({num(r['mbs'] * 8, 0)} Mbps)")
            partes.append(f"{num(r['ping'], 0)} ms")
            c.create_text(cx, cy + s(26), text="   \u00B7   ".join(partes), font=self.f_text, fill=t["sub"], width=ancho, justify="center")
            if r["mbs"] is None:
                aviso = "No pude medir la velocidad real; lo elegi por ping y carga."
                c.create_text(cx, cy + s(56), text=aviso, font=self.f_small, fill=t["err"], width=ancho, justify="center")
            if r["region"]:
                hint = f"En Steam: Configuracion \u203A Descargas \u203A Region de descarga \u203A {r['region']} (o la mas parecida)"
                c.create_text(cx, cy + s(86), text=hint, font=self.f_small, fill=t["sub"], width=ancho, justify="center")

        # Barra de estado
        sy = h - s(20)
        c.create_text(s(18), sy, anchor="w", text=self.status if self.estado != "run" else "Trabajando...", font=self.f_small, fill=t["sub"])
        c.create_text(w - s(18), sy, anchor="e", text="Steam Best Server", font=self.f_small, fill=t["dis"])

    # -- eventos ---------------------------------------------------------------
    def set_hover(self, name):
        if name != self.hover:
            self.hover = name
            self.c.configure(cursor="hand2" if name else "")
            self.draw()

    def on_motion(self, e):
        for x1, y1, x2, y2, cb, name in self.hits:
            if x1 <= e.x <= x2 and y1 <= e.y <= y2:
                self.set_hover(name)
                return
        self.set_hover(None)

    def on_click(self, e):
        for x1, y1, x2, y2, cb, name in self.hits:
            if x1 <= e.x <= x2 and y1 <= e.y <= y2:
                cb()
                return

    def alternar_amplia(self):
        self.amplia = not self.amplia
        self.draw()

    def copiar(self):
        if self.resultado:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.resultado["host"])
            self.status = "Servidor copiado al portapapeles"
            self.draw()

    def iniciar(self):
        if self.estado == "run":
            return
        self.estado = "run"
        self.status = "Consultando servidores de Steam"
        self.prog = 0.0
        self.prog_shown = 0.0
        self.resultado = None
        self.draw()
        amplia = self.amplia

        def trabajo():
            try:
                res = buscar(amplia, lambda f, m: self.q.put(("p", f, m)))
                self.q.put(("ok", res, ""))
            except Exception as ex:
                self.q.put(("err", str(ex), ""))

        threading.Thread(target=trabajo, daemon=True).start()

    def poll(self):
        cambio = False
        try:
            while True:
                tipo, a, b = self.q.get_nowait()
                cambio = True
                if tipo == "p":
                    self.prog, self.status = a, b
                elif tipo == "ok":
                    self.resultado = a
                    self.estado = "ok"
                    self.status = "Listo"
                    self.prog_shown = 1.0
                elif tipo == "err":
                    self.estado = "err"
                    self.detalle = a
                    self.status = "Error"
        except queue.Empty:
            pass
        if self.estado == "run":
            self.prog_shown += (self.prog - self.prog_shown) * 0.25
        if cambio or self.estado == "run":
            self.draw()
        self.root.after(80 if self.estado == "run" else 250, self.poll)


if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()
