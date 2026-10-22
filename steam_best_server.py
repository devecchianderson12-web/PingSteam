"""
Steam Best Server - encuentra el mejor servidor de descarga de Steam para tu conexion.

Solo usa la libreria estandar de Python (sin dependencias).
1. Pide a la API publica de Steam la lista de servidores de contenido.
2. Mide la latencia real (conexion TCP) a cada uno, varias veces.
3. Combina latencia y carga del servidor en un puntaje y te dice el mejor.
"""

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
from tkinter import ttk

API = (
    "https://api.steampowered.com/IContentServerDirectoryService/"
    "GetServersForSteamPipe/v1/?cell_id={cell}&max_servers={n}"
)

# Codigos de aeropuerto que aparecen en los nombres de host de Steam
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


def region_de(host: str) -> str:
    m = re.search(r"-([a-z]{3})\d*\.", host)
    if m:
        return REGIONES.get(m.group(1), m.group(1).upper())
    return "-"


def consultar_api(cell: int, n: int):
    try:
        req = urllib.request.Request(
            API.format(cell=cell, n=n),
            headers={"User-Agent": "SteamBestServer/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
        return data.get("response", {}).get("servers", [])
    except Exception:
        return []


def obtener_servidores(amplia: bool, progreso):
    """Devuelve dict host -> info. Con 'amplia' recorre muchas celdas (regiones)."""
    celdas = [0]
    if amplia:
        celdas += list(range(1, 251))
    encontrados = {}
    hechos = 0
    with ThreadPoolExecutor(max_workers=16) as ex:
        for servers in ex.map(lambda c: consultar_api(c, 100 if c == 0 else 20), celdas):
            hechos += 1
            progreso(f"Consultando lista de servidores de Steam... {hechos}/{len(celdas)}")
            for s in servers:
                host = s.get("host") or s.get("vhost")
                if not host:
                    continue
                if host not in encontrados:
                    encontrados[host] = {
                        "host": host,
                        "load": s.get("load", 0) or 0,
                        "tipo": s.get("type", ""),
                    }
    return encontrados


def medir_latencia(host: str, muestras: int = 5):
    tiempos = []
    for _ in range(muestras):
        for puerto in (80, 443):
            try:
                t0 = time.perf_counter()
                with socket.create_connection((host, puerto), timeout=2):
                    pass
                tiempos.append((time.perf_counter() - t0) * 1000)
                break
            except OSError:
                continue
    if len(tiempos) < 2:
        return None
    return statistics.median(tiempos)


def buscar(amplia: bool, progreso):
    servers = obtener_servidores(amplia, progreso)
    if not servers:
        raise RuntimeError("No pude obtener la lista de servidores. Revisa tu conexion a internet.")

    lista = list(servers.values())
    total = len(lista)
    resultados = []
    hechos = 0
    with ThreadPoolExecutor(max_workers=32) as ex:
        for info, ping in zip(lista, ex.map(lambda i: medir_latencia(i["host"]), lista)):
            hechos += 1
            progreso(f"Midiendo latencia... {hechos}/{total}")
            if ping is None:
                continue
            carga = info["load"]
            # Puntaje: menor es mejor. La carga alta penaliza.
            puntaje = ping * (1 + carga / 100)
            resultados.append(
                {
                    "host": info["host"],
                    "region": region_de(info["host"]),
                    "ping": ping,
                    "carga": carga,
                    "puntaje": puntaje,
                }
            )
    if not resultados:
        raise RuntimeError("Ningun servidor respondio. Puede que algo este bloqueando la conexion.")
    resultados.sort(key=lambda r: r["puntaje"])
    return resultados


class App:
    def __init__(self, root):
        self.root = root
        self.cola = queue.Queue()
        root.title("Steam Best Server")
        root.geometry("720x470")
        root.minsize(620, 420)

        frm = ttk.Frame(root, padding=14)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Mejor servidor de descarga de Steam", font=("Segoe UI", 14, "bold")).pack(anchor="w")

        fila = ttk.Frame(frm)
        fila.pack(fill="x", pady=(10, 6))
        self.btn = ttk.Button(fila, text="Buscar mejor servidor", command=self.iniciar)
        self.btn.pack(side="left")
        self.amplia = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            fila, text="Busqueda amplia (mas lenta, prueba muchas regiones)", variable=self.amplia
        ).pack(side="left", padx=12)

        self.estado = ttk.Label(frm, text="Presiona el boton para empezar.")
        self.estado.pack(anchor="w")

        self.mejor = ttk.Label(frm, text="", font=("Segoe UI", 12, "bold"), foreground="#1a7f37")
        self.mejor.pack(anchor="w", pady=(8, 0))
        self.consejo = ttk.Label(frm, text="", wraplength=680, justify="left")
        self.consejo.pack(anchor="w", pady=(0, 8))

        cols = ("pos", "host", "region", "ping", "carga")
        self.tabla = ttk.Treeview(frm, columns=cols, show="headings", height=10)
        for c, t, w in (
            ("pos", "#", 35),
            ("host", "Servidor", 300),
            ("region", "Region", 190),
            ("ping", "Ping (ms)", 80),
            ("carga", "Carga %", 70),
        ):
            self.tabla.heading(c, text=t)
            self.tabla.column(c, width=w, anchor="w" if c in ("host", "region") else "center")
        self.tabla.pack(fill="both", expand=True)

        self.copiar = ttk.Button(frm, text="Copiar nombre del mejor servidor", command=self.copiar_mejor, state="disabled")
        self.copiar.pack(anchor="e", pady=(8, 0))
        self.mejor_host = ""

        self.root.after(100, self.revisar_cola)

    def iniciar(self):
        self.btn.config(state="disabled")
        self.copiar.config(state="disabled")
        self.mejor.config(text="")
        self.consejo.config(text="")
        self.tabla.delete(*self.tabla.get_children())
        amplia = self.amplia.get()

        def trabajo():
            try:
                res = buscar(amplia, lambda m: self.cola.put(("estado", m)))
                self.cola.put(("ok", res))
            except Exception as e:
                self.cola.put(("error", str(e)))

        threading.Thread(target=trabajo, daemon=True).start()

    def revisar_cola(self):
        try:
            while True:
                tipo, dato = self.cola.get_nowait()
                if tipo == "estado":
                    self.estado.config(text=dato)
                elif tipo == "error":
                    self.estado.config(text=dato)
                    self.btn.config(state="normal")
                elif tipo == "ok":
                    self.mostrar(dato)
        except queue.Empty:
            pass
        self.root.after(100, self.revisar_cola)

    def mostrar(self, res):
        self.btn.config(state="normal")
        self.estado.config(text=f"Listo. {len(res)} servidores respondieron.")
        for i, r in enumerate(res[:15], 1):
            self.tabla.insert("", "end", values=(i, r["host"], r["region"], f"{r['ping']:.0f}", r["carga"]))
        b = res[0]
        self.mejor_host = b["host"]
        self.mejor.config(text=f"Mejor servidor: {b['host']}  ({b['ping']:.0f} ms, carga {b['carga']}%)")
        if b["region"] != "-":
            self.consejo.config(
                text=f"En Steam: Configuracion > Descargas > Region de descarga, elegi \"{b['region']}\" "
                f"(o la mas parecida que aparezca en la lista)."
            )
        self.copiar.config(state="normal")

    def copiar_mejor(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.mejor_host)
        self.estado.config(text="Copiado al portapapeles.")


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
