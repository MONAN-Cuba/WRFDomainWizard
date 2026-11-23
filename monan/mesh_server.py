#!/usr/bin/env python3
"""
mesh_server.py - servidor LOCAL de la interfaz interactiva de mallas MONAN (solo biblioteca estandar + numpy/scipy/matplotlib/netCDF4).

  conda activate mpasmesh
  python3 mesh_server.py --workdir ~/mallas --limited-area ~/MPAS-Limited-Area
  -> abrir http://127.0.0.1:8765/

Sirve la pagina (web/) y expone una API JSON:
  POST /api/preview   {config, grid_deg?}          ancho de celda, resumen (celdas, dx, dt), imagen para el mapa, avisos de gradiente
  POST /api/build     {config, stages, nproc, name} lanza make_mpas_mesh.py en segundo plano -> {job}
  GET  /api/job/<id>                                estado y cola del registro
  POST /api/job/<id>/cancel
  GET  /api/quality?name=<nombre>                   <name>.quality.json de la ultima construccion
  GET  /api/cells?name=<nombre>&max=40000           centros de celda de la malla construida (lat, lon, dx) para dibujarlos
  GET  /api/meshes                                  mallas existentes en --workdir

Seguridad: escucha solo en 127.0.0.1 (ejecuta procesos); los nombres se limitan a [A-Za-z0-9_.-] y todo se escribe dentro de --workdir.
"""
import os, re, sys, io, json, math, time, base64, argparse, threading, subprocess, uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import meshlib as ML

WEB = os.path.join(HERE, "web")
LIBS = os.path.join(WEB, "lib")                           # leaflet y leaflet-providers copiados del repo (la carpeta es autonoma)
JOBS = {}
OPT = {}
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,60}$")


# ----------------------------------------------------------------------------- preview
def merc_y(lat):
    return np.log(np.tan(np.pi / 4 + np.radians(np.clip(lat, -85.05, 85.05)) / 2))


def merc_inv(y):
    return np.degrees(2 * np.arctan(np.exp(y)) - np.pi / 2)


def preview(body):
    t0 = time.time()
    cfg = ML.normalize_config(body["config"])
    gd = float(body.get("grid_deg") or (0.25 if cfg["mode"] == "global" else 0.1))
    if gd < 0.02:
        raise ValueError("grid_deg minimo 0.02 para la vista previa")
    L = cfg["levels"]
    if cfg["mode"] == "regional":
        b = ML.level_bounds(L[0]); m = 2.0
        win = (max(-90, b[0] - m), min(90, b[1] + m), max(-180, b[2] - m), min(180, b[3] + m))
    else:
        win = None
    lon, lat, LON, LAT, w, d = ML.build_cellwidth(cfg, gd, win)
    sm = ML.summarize(cfg, w, d, LAT, gd)
    # gradiente |dw/ds| (km por km) sobre la rejilla: aviso si una rampa es demasiado corta
    dy = gd * ML.KM
    gy = np.abs(np.diff(w, axis=0)) / dy
    gx = np.abs(np.diff(w, axis=1)) / (dy * np.cos(np.radians(LAT[:, :-1])))
    inside = d[0] == 0
    gi = np.maximum(gy[:, :-1], gx[:-1, :])[inside[:-1, :-1]] if inside.sum() > 10 else np.array([0.0])
    sm["grad_max"] = float(gi.max()); sm["grad_p999"] = float(np.percentile(gi, 99.9))
    warn = []
    if sm["grad_p999"] > 0.35:
        warn.append("gradiente de espaciado alto (%.2f > 0.35): alguna rampa es muy corta; la malla puede salir de mala calidad" % sm["grad_p999"])
    if cfg["smooth_km"] > 0 and win:
        warn.append("con suavizado, la vista previa tiene efecto de borde en la ventana mostrada")
    sm["warnings"] = warn
    # imagen RGBA con remuestreo uniforme en y de Mercator (Leaflet usa Web Mercator)
    import matplotlib
    cmap = matplotlib.colormaps["viridis_r"]
    lat0, lat1 = float(lat[0]), float(lat[-1]); lon0, lon1 = float(lon[0]), float(lon[-1])
    ny = min(900, max(2, len(lat))); nx = len(lon)
    ys = np.linspace(merc_y(lat0), merc_y(lat1), ny)
    rows = np.clip(np.round((merc_inv(ys) - lat0) / gd).astype(int), 0, len(lat) - 1)
    W = w[rows]; IN = inside[rows]
    vmin, vmax = float(w.min()), float(w.max())
    norm = (W - vmin) / max(vmax - vmin, 1e-9)
    rgba = (cmap(norm) * 255).astype(np.uint8)
    rgba[..., 3] = np.where(IN, 190, 60)
    rgba = rgba[::-1]                                         # PNG: fila 0 = norte
    from matplotlib.image import imsave
    buf = io.BytesIO(); imsave(buf, rgba, format="png")
    stops = [[float(x), "#%02x%02x%02x" % tuple((np.array(cmap(x)[:3]) * 255).astype(int))] for x in np.linspace(0, 1, 6)]
    return {"summary": sm, "image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
            "bounds": [[lat0, lon0], [lat1, lon1]], "vmin": vmin, "vmax": vmax, "colors": stops, "grid_deg": gd,
            "seconds": round(time.time() - t0, 2), "config": cfg}


# ----------------------------------------------------------------------------- build
def mesh_dir(name):
    if not NAME_RE.match(name or ""):
        raise ValueError("nombre invalido (use letras, numeros, _ . -)")
    return os.path.join(OPT["workdir"], name)


def start_build(body):
    cfg = ML.normalize_config(body["config"])
    name = cfg["name"]; out = mesh_dir(name)
    os.makedirs(out, exist_ok=True)
    stages = body.get("stages") or "cellwidth,mesh,region,rescale,density,partition,check"
    if isinstance(stages, list): stages = ",".join(stages)
    if any(x not in "cellwidth,mesh,region,rescale,density,partition,check".split(",") for x in stages.split(",")):
        raise ValueError("etapas invalidas")
    cfgf = os.path.join(out, name + ".config_in.json")
    json.dump(body["config"], open(cfgf, "w"), indent=1)
    cmd = [sys.executable, "-u", os.path.join(HERE, "make_mpas_mesh.py"), "--config", cfgf, "--name", name, "--outdir", out,
           "--stages", stages, "--limited-area", OPT["limited_area"]]
    if body.get("nproc"):
        nproc = str(body["nproc"])
        if not re.match(r"^\d+(,\d+)*$", nproc): raise ValueError("nproc invalido")
        cmd += ["--nproc", nproc]
    jid = uuid.uuid4().hex[:8]
    job = {"id": jid, "name": name, "cmd": cmd, "log": [], "status": "running", "start": time.time(), "proc": None, "rc": None}
    JOBS[jid] = job

    def run():
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=out)
            job["proc"] = p
            for line in p.stdout:
                job["log"].append(line.rstrip("\n"))
                if len(job["log"]) > 5000: del job["log"][:1000]
            job["rc"] = p.wait()
            job["status"] = "cancelled" if job.get("cancel") else ("done" if job["rc"] == 0 else "error")
        except Exception as e:
            job["log"].append("ERROR lanzando el proceso: %s" % e); job["status"] = "error"
        job["end"] = time.time()
    threading.Thread(target=run, daemon=True).start()
    return {"job": jid, "command": " ".join(cmd)}


def job_info(jid, tail=400):
    j = JOBS.get(jid)
    if not j: raise KeyError("trabajo desconocido")
    return {"id": jid, "name": j["name"], "status": j["status"], "rc": j["rc"], "log": j["log"][-tail:],
            "seconds": round((j.get("end") or time.time()) - j["start"], 1)}


def mesh_file(name):
    d = mesh_dir(name)
    for f in (name + ".region.nc", name + "_global.nc"):
        if os.path.exists(os.path.join(d, f)): return os.path.join(d, f)
    raise FileNotFoundError("no hay malla construida para " + name)


def cells(name, mx):
    import netCDF4 as nc
    with nc.Dataset(mesh_file(name)) as d:
        la = np.degrees(d["latCell"][:]); lo = np.degrees(d["lonCell"][:]); lo = np.where(lo > 180, lo - 360, lo)
        sr = float(getattr(d, "sphere_radius", 6371229.0))
        A = np.asarray(d["areaCell"][:]) * (6371229.0 / sr) ** 2
    dx = np.sqrt(A / ML.HEX) / 1e3
    n = len(la); step = max(1, n // mx)
    sel = np.arange(0, n, step)
    return {"n": n, "shown": int(len(sel)), "points": [[round(float(la[i]), 3), round(float(lo[i]), 3), round(float(dx[i]), 2)] for i in sel]}


def list_meshes():
    out = []
    for n in sorted(os.listdir(OPT["workdir"])) if os.path.isdir(OPT["workdir"]) else []:
        d = os.path.join(OPT["workdir"], n)
        if os.path.isdir(d):
            out.append({"name": n, "region": os.path.exists(os.path.join(d, n + ".region.nc")), "global": os.path.exists(os.path.join(d, n + "_global.nc")),
                        "quality": os.path.exists(os.path.join(d, n + ".quality.json"))})
    return out


# ----------------------------------------------------------------------------- HTTP
MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript", ".css": "text/css", ".png": "image/png", ".json": "application/json"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def _static(self, base, rel):
        p = os.path.normpath(os.path.join(base, rel))
        if not p.startswith(os.path.normpath(base)) or not os.path.isfile(p):
            return self._json({"error": "no encontrado"}, 404)
        b = open(p, "rb").read()
        self.send_response(200); self.send_header("Content-Type", MIME.get(os.path.splitext(p)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(b))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)

    def _guard(self):
        host = (self.headers.get("Host") or "").split(":")[0]          # evita DNS rebinding
        if host not in ("127.0.0.1", "localhost"):
            self._json({"error": "host no permitido"}, 403); return False
        return True

    def do_GET(self):
        if not self._guard(): return
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"): return self._static(WEB, "index.html")
            if u.path.startswith("/lib/"): return self._static(LIBS, u.path[5:])
            if u.path.startswith("/web/"): return self._static(WEB, u.path[5:])
            if u.path == "/api/meshes": return self._json(list_meshes())
            if u.path == "/api/info": return self._json({"workdir": OPT["workdir"], "limited_area": OPT["limited_area"], "python": sys.executable})
            m = re.match(r"^/api/job/([0-9a-f]+)$", u.path)
            if m: return self._json(job_info(m.group(1)))
            if u.path == "/api/quality":
                return self._json(json.load(open(os.path.join(mesh_dir(q["name"]), q["name"] + ".quality.json"))))
            if u.path == "/api/cells": return self._json(cells(q["name"], int(q.get("max", 40000))))
            if os.path.isfile(os.path.join(WEB, u.path.lstrip("/"))): return self._static(WEB, u.path.lstrip("/"))   # rutas relativas de la pagina (app.js, lib/...)
            self._json({"error": "ruta desconocida"}, 404)
        except (KeyError, FileNotFoundError) as e:
            self._json({"error": str(e)}, 404)
        except Exception as e:
            self._json({"error": "%s: %s" % (type(e).__name__, e)}, 400)

    def do_POST(self):
        if not self._guard(): return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/preview": return self._json(preview(body))
            if self.path == "/api/build": return self._json(start_build(body))
            m = re.match(r"^/api/job/([0-9a-f]+)/cancel$", self.path)
            if m:
                j = JOBS[m.group(1)]; j["cancel"] = True
                if j.get("proc") and j["status"] == "running": j["proc"].terminate()
                return self._json({"ok": True})
            self._json({"error": "ruta desconocida"}, 404)
        except (ValueError, KeyError) as e:
            self._json({"error": "%s: %s" % (type(e).__name__, e)}, 400)
        except Exception as e:
            self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--workdir", default=os.path.expanduser("~/mallas_monan"), help="carpeta donde se construyen las mallas (una subcarpeta por nombre)")
    ap.add_argument("--limited-area", default=os.environ.get("MPAS_LIMITED_AREA", "MPAS-Limited-Area"))
    a = ap.parse_args()
    OPT["workdir"] = os.path.abspath(os.path.expanduser(a.workdir)); OPT["limited_area"] = a.limited_area
    os.makedirs(OPT["workdir"], exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print("Interfaz de mallas MONAN: http://127.0.0.1:%d/   (carpeta de trabajo: %s)   Ctrl+C para salir" % (a.port, OPT["workdir"]))
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\nadios")


if __name__ == "__main__":
    main()
