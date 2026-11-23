#!/usr/bin/env python3
"""
meshlib.py - geometria y ancho de celda para mallas MPAS (parte pura, sin JIGSAW ni E/S pesada).

Lo usan make_mpas_mesh.py (linea de comandos) y mesh_server.py (servicio de la interfaz web).

Configuracion (JSON), compatible con la anterior (cuba_sispi.json, panama_wrf.json):
  {
    "name": "cuba_sispi",
    "mode": "regional",            # "regional" (recorte con level 0) o "global" (malla completa; level 0 solo da dx base)
    "levels": [ nivel0, nivel1, ... ],
    "ramp_km": [150, 50],          # opcional; rampa de cada nivel k>=1 hacia FUERA de su forma (o "ramp_km" dentro del nivel)
    "smooth_km": 0, "smooth_keep": "finest"|"all"|"none", "corner_radius_frac": 0.0, "grid_deg": 0.05
  }
Cada nivel:
  "name", "dx" (km), "shape" ("rect" por defecto, "ellipse", "circle", "stadium", "polygon")
  rect/stadium : "lat": [la0, la1], "lon": [lo0, lo1]   ("corner_radius_frac" 0 = rectangulo, 1 = estadio)
  ellipse      : "center": [lat, lon], "semi_axes_km": [a_este, b_norte], "rotation_deg": giro antihorario
  circle       : "center": [lat, lon], "radius_km"
  polygon      : "points": [[lat, lon], ...]
  "ramp_km"    : rampa hacia fuera de esta zona (por defecto cfg["ramp_km"][k-1])
  "parent"     : indice (o nombre) del nivel dentro del cual se refina; por defecto el nivel anterior (k-1).
                 Sirve para varias zonas finas hermanas: "parent": 0.
Resolucion constante: un solo nivel (regional: caja o forma; global: solo "dx").
"""
import math
import numpy as np

KM = 111.195          # km por grado de latitud
HEX = 0.866           # area de celda ~ HEX * dx^2
EARTH_KM = 6371.229
BIG = 1e9


# ----------------------------------------------------------------------------- normalizacion
def normalize_config(cfg):
    """Devuelve una copia con valores por defecto y comprueba coherencia. Lanza ValueError si algo esta mal."""
    c = {"name": "mesh", "mode": "regional", "grid_deg": 0.05, "earth_radius": 6371229.0, "smooth_km": 0.0,
         "smooth_keep": "finest", "corner_radius_frac": 0.0, "outdir": None}
    c.update(cfg)
    if c["mode"] == "global" and "grid_deg" not in cfg:
        c["grid_deg"] = 0.25                  # la rejilla global de 0.05 grados pesa >1 GB; 0.25 basta para mallas globales
    L = [dict(l) for l in c["levels"]]
    if not L:
        raise ValueError("levels vacio")
    c["levels"] = L
    if c["mode"] not in ("regional", "global"):
        raise ValueError("mode debe ser 'regional' o 'global'")
    ramps = list(c.get("ramp_km", []))
    for k, l in enumerate(L):
        l.setdefault("name", "d%02d" % (k + 1))
        if float(l["dx"]) <= 0:
            raise ValueError("nivel %s: dx debe ser > 0" % l["name"])
        shape = l.get("shape", "rect")
        if k == 0 and c["mode"] == "global":
            l["shape"] = "global"; continue
        l["shape"] = shape
        need = {"rect": ("lat", "lon"), "stadium": ("lat", "lon"), "ellipse": ("center", "semi_axes_km"),
                "circle": ("center", "radius_km"), "polygon": ("points",)}
        if shape not in need:
            raise ValueError("nivel %s: forma desconocida %r" % (l["name"], shape))
        for key in need[shape]:
            if key not in l:
                raise ValueError("nivel %s (%s): falta '%s'" % (l["name"], shape, key))
        if k >= 1:
            if "ramp_km" not in l:
                if k - 1 >= len(ramps):
                    raise ValueError("nivel %s: falta ramp_km (ni en el nivel ni en ramp_km[%d])" % (l["name"], k - 1))
                l["ramp_km"] = float(ramps[k - 1])
            if l["ramp_km"] <= 0:
                raise ValueError("nivel %s: ramp_km debe ser > 0" % l["name"])
            par = l.get("parent", k - 1)
            if isinstance(par, str):
                names = [x.get("name") for x in L]
                if par not in names:
                    raise ValueError("nivel %s: parent %r no existe" % (l["name"], par))
                par = names.index(par)
            if not (0 <= int(par) < k):
                raise ValueError("nivel %s: parent debe ser un nivel anterior" % l["name"])
            l["parent"] = int(par)
            if float(l["dx"]) > float(L[l["parent"]]["dx"]):
                raise ValueError("nivel %s: dx (%g) mayor que el de su padre (%g)" % (l["name"], l["dx"], L[l["parent"]]["dx"]))
    c["ramp_km"] = [l.get("ramp_km") for l in L[1:]]
    if c["mode"] == "regional" and L[0]["shape"] == "global":
        raise ValueError("modo regional: el nivel 0 necesita forma")
    return c


# ----------------------------------------------------------------------------- distancias a las formas
def _local_xy(LAT, LON, clat, clon):
    c = math.cos(math.radians(clat))
    return ((LON - clon + 180) % 360 - 180) * KM * c, (LAT - clat) * KM


def dist_rect(LAT, LON, box, frac):
    """Distancia (km) al borde de una caja lat/lon de esquinas redondeadas; 0 dentro.
    frac=0 -> rectangulo; frac=1 -> esquinas con radio = semilado menor ('estadio')."""
    la0, la1, lo0, lo1 = box
    clat, clon = (la0 + la1) / 2, (lo0 + lo1) / 2
    c = math.cos(math.radians(clat))
    hy, hx = (la1 - la0) / 2 * KM, (lo1 - lo0) / 2 * KM * c
    y = np.abs(LAT - clat) * KM
    x = np.abs((LON - clon + 180) % 360 - 180) * KM * c
    r = frac * min(hx, hy)
    ex, ey = np.maximum(x - (hx - r), 0), np.maximum(y - (hy - r), 0)
    return np.maximum(np.hypot(ex, ey) - r, 0)


def dist_ellipse(LAT, LON, center, a, b, rot_deg=0.0):
    """Distancia (km) aproximada al borde de una elipse (exacta para circulo, error pequeno cerca del borde)."""
    clat, clon = center
    x, y = _local_xy(LAT, LON, clat, clon)
    t = math.radians(rot_deg)
    xr = x * math.cos(t) + y * math.sin(t)
    yr = -x * math.sin(t) + y * math.cos(t)
    f = np.sqrt((xr / a) ** 2 + (yr / b) ** 2)
    g = np.sqrt((xr / a ** 2) ** 2 + (yr / b ** 2) ** 2)
    with np.errstate(divide="ignore", invalid="ignore"):
        d = np.where(f > 1, (f - 1) * f / np.maximum(g, 1e-300), 0.0)
    return np.where(f <= 1, 0.0, d)


def dist_polygon(LAT, LON, pts, margin_km):
    """Distancia (km) al poligono lat/lon; 0 dentro. Solo se calcula en una ventana alrededor (margin_km);
    mas lejos devuelve BIG (basta: mas alla de la rampa el ancho ya esta saturado)."""
    from matplotlib.path import Path
    from scipy.ndimage import distance_transform_edt
    P = np.array(pts, float)
    clat, clon = P[:, 0].mean(), P[:, 1].mean()
    c = math.cos(math.radians(clat))
    dlat = margin_km / KM; dlon = margin_km / (KM * c)
    rows = (LAT[:, 0] >= P[:, 0].min() - dlat) & (LAT[:, 0] <= P[:, 0].max() + dlat)
    lon1 = LON[0]
    rel = (lon1 - clon + 180) % 360 - 180
    relp = (P[:, 1] - clon + 180) % 360 - 180
    cols = (rel >= relp.min() - dlon) & (rel <= relp.max() + dlon)
    d = np.full(LAT.shape, BIG)
    if not rows.any() or not cols.any():
        return d
    ri, ci = np.where(rows)[0], np.where(cols)[0]
    la = LAT[np.ix_(ri, ci)]; lo = rel[ci][None, :] + 0 * la
    path = Path(np.column_stack([relp, P[:, 0]]))
    inside = path.contains_points(np.column_stack([lo.ravel(), la.ravel()])).reshape(la.shape)
    dl_lat = abs(LAT[1, 0] - LAT[0, 0]) if LAT.shape[0] > 1 else 0.05
    dl_lon = abs(LON[0, 1] - LON[0, 0]) if LON.shape[1] > 1 else 0.05
    out = distance_transform_edt(~inside, sampling=(dl_lat * KM, dl_lon * KM * c))
    out = np.maximum(out - 0.5 * min(dl_lat * KM, dl_lon * KM * c), 0.0)     # el borde esta a medio pixel del centro de celda interior mas cercano
    out[inside] = 0.0
    out[out > margin_km] = BIG
    d[np.ix_(ri, ci)] = out
    return d


def level_distance(LAT, LON, lv, default_frac=0.0):
    sh = lv["shape"]
    if sh == "global":
        return np.zeros(LAT.shape)
    if sh in ("rect", "stadium"):
        frac = lv.get("corner_radius_frac", 1.0 if sh == "stadium" else default_frac)
        return dist_rect(LAT, LON, (lv["lat"][0], lv["lat"][1], lv["lon"][0], lv["lon"][1]), frac)
    if sh == "ellipse":
        a, b = lv["semi_axes_km"]
        return dist_ellipse(LAT, LON, lv["center"], float(a), float(b), lv.get("rotation_deg", 0.0))
    if sh == "circle":
        r = float(lv["radius_km"])
        return dist_ellipse(LAT, LON, lv["center"], r, r, 0.0)
    if sh == "polygon":
        return dist_polygon(LAT, LON, lv["points"], max(float(lv.get("ramp_km", 0)) * 1.5, 50.0))
    raise ValueError(sh)


def level_bounds(lv):
    """(lat0, lat1, lon0, lon1) aproximado de la forma; None para 'global'."""
    sh = lv["shape"]
    if sh == "global":
        return None
    if sh in ("rect", "stadium"):
        return (lv["lat"][0], lv["lat"][1], lv["lon"][0], lv["lon"][1])
    if sh in ("ellipse", "circle"):
        clat, clon = lv["center"]
        a, b = (lv["semi_axes_km"] if sh == "ellipse" else (lv["radius_km"],) * 2)
        t = math.radians(lv.get("rotation_deg", 0.0)) if sh == "ellipse" else 0.0
        hx = math.hypot(a * math.cos(t), b * math.sin(t)); hy = math.hypot(a * math.sin(t), b * math.cos(t))
        c = math.cos(math.radians(clat))
        return (clat - hy / KM, clat + hy / KM, clon - hx / (KM * c), clon + hx / (KM * c))
    P = np.array(lv["points"], float)
    return (P[:, 0].min(), P[:, 0].max(), P[:, 1].min(), P[:, 1].max())


def polygon_points(lv, default_frac=0.0, narc=12, nell=96):
    """Vertices (lat, lon), sentido horario, de la forma del nivel 0, para create_region."""
    sh = lv["shape"]
    if sh in ("rect", "stadium"):
        box = (lv["lat"][0], lv["lat"][1], lv["lon"][0], lv["lon"][1])
        frac = lv.get("corner_radius_frac", 1.0 if sh == "stadium" else default_frac)
        la0, la1, lo0, lo1 = box
        if frac <= 0:
            return [(la1, lo0), (la1, lo1), (la0, lo1), (la0, lo0)]
        clat, clon = (la0 + la1) / 2, (lo0 + lo1) / 2
        c = math.cos(math.radians(clat))
        hy, hx = (la1 - la0) / 2, (lo1 - lo0) / 2
        r_km = frac * min(hy * KM, hx * KM * c)
        ry, rx = r_km / KM, r_km / (KM * c)
        pts = []
        for (sy, sx, a0) in ((1, 1, 90), (-1, 1, 0), (-1, -1, -90), (1, -1, -180)):
            cy, cx = clat + sy * (hy - ry), clon + sx * (hx - rx)
            for t in np.linspace(a0, a0 - 90, narc + 1):
                pts.append((cy + ry * math.sin(math.radians(t)), cx + rx * math.cos(math.radians(t))))
        return pts
    if sh in ("ellipse", "circle"):
        clat, clon = lv["center"]
        a, b = (lv["semi_axes_km"] if sh == "ellipse" else (lv["radius_km"],) * 2)
        rot = math.radians(lv.get("rotation_deg", 0.0)) if sh == "ellipse" else 0.0
        c = math.cos(math.radians(clat))
        pts = []
        for t in np.linspace(0, 2 * math.pi, nell, endpoint=False):       # t desde el norte, en sentido horario
            xr, yr = a * math.sin(t), b * math.cos(t)
            x = xr * math.cos(rot) - yr * math.sin(rot); y = xr * math.sin(rot) + yr * math.cos(rot)
            pts.append((clat + y / KM, clon + x / (KM * c)))
        return pts
    P = [tuple(p) for p in lv["points"]]
    A = sum(P[i][1] * P[(i + 1) % len(P)][0] - P[(i + 1) % len(P)][1] * P[i][0] for i in range(len(P)))
    return P[::-1] if A > 0 else P              # area con signo en (lon, lat): >0 = antihorario -> invertir a horario


def region_center(lv, default_frac=0.0):
    pts = np.array(polygon_points(lv, default_frac))
    b = level_bounds(lv)
    cen = ((b[0] + b[1]) / 2, (b[2] + b[3]) / 2)
    if lv["shape"] == "polygon":
        cen = (float(pts[:, 0].mean()), float(pts[:, 1].mean()))
    return cen


# ----------------------------------------------------------------------------- ancho de celda
def build_cellwidth(cfg, grid_deg=None, window=None):
    """Campo de ancho de celda (km) sobre una rejilla lat/lon. Con la configuracion de siempre (niveles anidados,
    parent por defecto k-1) da exactamente el mismo resultado que la version anterior."""
    cfg = normalize_config(cfg)
    dl = grid_deg or cfg["grid_deg"]
    lon = np.arange(-180, 180 + dl / 2, dl)
    lat = np.arange(-90, 90 + dl / 2, dl)
    if window:                                   # (lat0, lat1, lon0, lon1): solo esa ventana de la misma rejilla (mas rapido; el resultado es puntual)
        lat = lat[(lat >= window[0]) & (lat <= window[1])]; lon = lon[(lon >= window[2]) & (lon <= window[3])]
    LON, LAT = np.meshgrid(lon, lat)
    L = cfg["levels"]
    d = [level_distance(LAT, LON, l, cfg["corner_radius_frac"]) for l in L]
    w = np.full(LAT.shape, float(L[0]["dx"]))
    for k in range(1, len(L)):
        l = L[k]; p = l["parent"]
        g = np.clip(l["dx"] + (L[p]["dx"] - l["dx"]) / l["ramp_km"] * d[k], l["dx"], L[p]["dx"])
        w = np.minimum(w, g) if p == 0 else np.where(d[p] == 0, np.minimum(w, g), w)
    raw = w.copy()
    s = cfg["smooth_km"]
    if s > 0:
        from scipy.ndimage import gaussian_filter
        w = gaussian_filter(w, sigma=s / (KM * dl), mode="nearest")
        keep = cfg["smooth_keep"]
        if keep in ("finest", "all"):
            fine = range(1, len(L)) if keep == "all" else [k for k in range(1, len(L)) if not any(L[j].get("parent") == k for j in range(k + 1, len(L)))]
            for k in fine:
                m = (d[k] == 0) & (raw <= L[k]["dx"] + 1e-6)
                w[m] = L[k]["dx"]
        w = np.clip(w, min(l["dx"] for l in L), L[0]["dx"])
    return lon, lat, LON, LAT, w, d


def estimate_cells(LAT, w, d0, dl):
    A = (dl * math.radians(1) * EARTH_KM) ** 2 * np.cos(np.radians(LAT))
    return float((A / (HEX * w ** 2))[d0 == 0].sum())


def summarize(cfg, w, d, LAT, dl):
    """Resumen rapido para la interfaz: celdas estimadas, dx min/max en la region y celdas por zona."""
    L = cfg["levels"]
    n = estimate_cells(LAT, w, d[0], dl)
    inside = d[0] == 0
    out = {"cells_estimated": n, "dx_min": float(w.min()), "dx_max_region": float(w[inside].max()),
           "dt_hint_s": float(6 * w.min()), "zones": []}
    A = (dl * math.radians(1) * EARTH_KM) ** 2 * np.cos(np.radians(LAT))
    cells = A / (HEX * w ** 2)
    for k, l in enumerate(L):
        m = (d[k] == 0) & inside
        out["zones"].append({"name": l["name"], "dx": l["dx"], "cells_inside": float(cells[m].sum())})
    return out


# ----------------------------------------------------------------------------- grafo para gpmetis
def write_graph_info(region_nc, out_path):
    """Escribe graph.info (formato METIS: 'nCells nEdges' y, por celda, sus vecinas 1-based) desde cellsOnCell."""
    import netCDF4 as nc
    with nc.Dataset(region_nc) as d:
        coc = np.asarray(d["cellsOnCell"][:]).astype(int); ne = np.asarray(d["nEdgesOnCell"][:]).astype(int)
    n = coc.shape[0]
    nb = [[int(v) for v in coc[i, :ne[i]] if 0 < v <= n] for i in range(n)]
    edges = sum(len(x) for x in nb) // 2
    with open(out_path, "w") as f:
        f.write("%d %d\n" % (n, edges))
        for x in nb:
            f.write(" ".join(str(v) for v in x) + "\n")
    return n, edges
