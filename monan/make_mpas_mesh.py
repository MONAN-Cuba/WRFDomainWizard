#!/usr/bin/env python3
"""
make_mpas_mesh.py - malla MPAS regional de resolucion variable que imita dominios anidados del WRF/SisPI.

Etapas (--stages, por defecto todas):
  cellwidth  calcula el ancho de celda (km) sobre una rejilla lat/lon, estima el numero de celdas y dibuja una vista previa (segundos)
  mesh       malla global con JIGSAW (mpas_tools.build_spherical_mesh)        ~10 min
  region     recorta el dominio regional (MPAS-Limited-Area/create_region)    minutos
  rescale    pasa region.nc de metros (sphere_radius=6371229) a esfera unitaria (sphere_radius=1), como exige init_atmosphere;
             comprueba la geometria antes y despues de escribir (idempotente, con copia .bak_rescale)
  density    escribe meshDensity = (dx_ref/dx_local)^p en {name}.region.nc (idempotente, con copia .bak_density)
             MPAS escala la difusion con meshDensity (meshScalingDel2 = rho^-1/4, meshScalingDel4 = rho^-3/4).
             Sin este campo (rho = 1 en todas las celdas) la difusion queda calibrada para la celda mas fina en
             todo el dominio. Despues de cambiarlo hay que regenerar static.nc e init.nc.
  partition  gpmetis para cada numero de procesos pedido (por defecto = CPUs disponibles)
  check      metricas de calidad (JSON + pantalla) y verificaciones: sphere_radius = 1, numero de celdas frente a la
             estimacion, bdyMaskCell, meshDensity y particiones. Si algo esta mal, el script termina con codigo 1.
Orden por defecto: cellwidth, mesh, region, rescale, density, partition, check. Las etapas se ejecutan SIEMPRE en ese orden,
sea cual sea el orden en --stages.

Uso tipico:
  python make_mpas_mesh.py --config panama_wrf.json --preview                      # solo vista previa y estimacion
  python make_mpas_mesh.py --config panama_wrf.json --name panama_wrf              # todo
  python make_mpas_mesh.py --config panama_wrf.json --name panama_wrf --stages rescale,density,partition,check   # arreglar una region.nc ya existente
  python make_mpas_mesh.py --rescale panama_wrf.region.nc                          # solo reescalar un archivo (sin --config)
  python make_mpas_mesh.py --config panama_wrf.json --name panama_oval --corner-frac 0.6 --ramps 150,60 --smooth-km 20

Formas, malla global, resolucion constante y zonas hermanas: ver meshlib.py (nivel con "shape": rect, ellipse, circle,
stadium, polygon; "mode": "global"; "parent"). El JSON antiguo (solo cajas lat/lon) sigue funcionando igual.
En modo global no hay etapa region: el archivo final es {name}_global.nc y graph.info se genera desde sus vecinas.

Estructura de la configuracion (JSON): "levels" va del dominio mas grueso (region) al mas fino; el primero define el
recorte regional. "ramp_km[k-1]" es la longitud de la rampa que pasa de dx_k a dx_(k-1) hacia FUERA de la caja k.
"""
import argparse, json, os, sys, time, subprocess, math
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meshlib as ML
from meshlib import KM, HEX, normalize_config, build_cellwidth, estimate_cells


def preview(cfg, lon, lat, LON, LAT, w, d, out):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    L = cfg["levels"]
    bb = ML.level_bounds(L[0])
    if bb is None:                                           # global: ver todo el mundo si no hay zonas; si no, las zonas con margen
        zb = [ML.level_bounds(l) for l in L[1:]]
        bb = (-90, 90, -180, 180) if not zb else (min(z[0] for z in zb) - 20, max(z[1] for z in zb) + 20, min(z[2] for z in zb) - 30, max(z[3] for z in zb) + 30)
        m = 0.0
    else:
        m = 3.0
    b = {"lat": bb[:2], "lon": bb[2:]}
    sel_lat = (lat > b["lat"][0] - m) & (lat < b["lat"][1] + m); sel_lon = (lon > b["lon"][0] - m) & (lon < b["lon"][1] + m)
    W = w[np.ix_(sel_lat, sel_lon)]
    fig, ax = plt.subplots(1, 2, figsize=(14.5, 5.6), gridspec_kw={"width_ratios": [1.2, 1]})
    im = ax[0].imshow(W, origin="lower", cmap="viridis_r", vmin=L[-1]["dx"], vmax=L[0]["dx"],
                      extent=[lon[sel_lon][0], lon[sel_lon][-1], lat[sel_lat][0], lat[sel_lat][-1]], aspect="auto")
    for k, l in enumerate(L):
        if l["shape"] == "global": continue
        ax[0].contour(LON[np.ix_(sel_lat, sel_lon)], LAT[np.ix_(sel_lat, sel_lon)], d[k][np.ix_(sel_lat, sel_lon)],
                      levels=[0.5], colors=["k", "orange", "r", "m"][k % 4], linewidths=1.3)
    fig.colorbar(im, ax=ax[0], label="ancho de celda (km)")
    ax[0].set_title(f"{cfg['name']}: ancho de celda (contornos = formas de los dominios)")
    fb = ML.level_bounds(L[-1]) or (0, 0, 0, 0)
    clat = (fb[0] + fb[1]) / 2
    j = np.argmin(abs(lat - clat)); ax[1].plot(lon[sel_lon], w[j, sel_lon], "k")
    ax[1].set_xlabel(f"longitud a {lat[j]:.2f}°N"); ax[1].set_ylabel("km"); ax[1].grid(alpha=.3)
    ax[1].set_title("Perfil W-E por el centro de la zona mas fina")
    fig.savefig(out, dpi=120, bbox_inches="tight"); plt.close(fig)


# ----------------------------------------------------------------------------- meshDensity
def density_from_area(areaCell, power=4.0, ref_percentile=1.0):
    """meshDensity (adimensional, <=1) a partir del tamano local de celda.
    dx_local = sqrt(areaCell); dx_ref = percentil bajo (robusto frente a celdas pentagonales sueltas);
    rho = min(1, (dx_ref/dx_local)^power). Con power = 4, meshScalingDel4 = rho^-3/4 = (dx/dx_ref)^3, es decir
    la hiperviscosidad nu4 = c*len_disp^3 crece como dx^3; y meshScalingDel2 = rho^-1/4 = dx/dx_ref.
    Solo depende de cocientes de areas, asi que vale con la esfera unitaria o con el radio real."""
    dx = np.sqrt(np.asarray(areaCell, dtype=float))
    ref = np.percentile(dx, ref_percentile)
    return np.minimum(1.0, (ref / dx) ** power), ref


def write_density(path, power=4.0, ref_percentile=1.0, force=False):
    """Escribe meshDensity en una region.nc existente. Idempotente: si ya coincide, no toca el archivo.
    Hace una unica copia de seguridad path + '.bak_density' antes de la primera modificacion."""
    import netCDF4 as nc, shutil
    with nc.Dataset(path) as d:
        area = np.asarray(d["areaCell"][:])
        old = np.asarray(d["meshDensity"][:]) if "meshDensity" in d.variables else None
        has = "meshDensity" in d.variables
    rho, ref = density_from_area(area, power, ref_percentile)
    info = {"ref_dx_rel": float(ref), "power": power, "rho_min": float(rho.min()), "rho_max": float(rho.max()),
            "scaling_del2_max": float(rho.min() ** -0.25), "scaling_del4_max": float(rho.min() ** -0.75),
            "meshDensity_before_min": None if old is None else float(old.min()),
            "meshDensity_before_max": None if old is None else float(old.max())}
    if has and old is not None and np.allclose(old, rho, rtol=1e-9, atol=0) and not force:
        info["action"] = "sin cambios (ya correcto)"
        return info
    bak = path + ".bak_density"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
    with nc.Dataset(path, "r+") as d:
        if not has:
            d.createVariable("meshDensity", "f8", ("nCells",))
        d["meshDensity"][:] = rho
    info["action"] = "escrito (copia en " + os.path.basename(bak) + ")"
    return info


# ----------------------------------------------------------------------------- esfera unitaria
RESC_LEN = ["xCell", "yCell", "zCell", "xEdge", "yEdge", "zEdge", "xVertex", "yVertex", "zVertex", "dcEdge", "dvEdge"]
RESC_AREA = ["areaCell", "areaTriangle", "kiteAreasOnVertex"]


def sphere_checks(d, R):
    """Geometria coherente con el radio R: (ok, mensajes). 1) los centros de celda estan sobre la esfera de radio R;
    2) dcEdge coincide con el arco de circulo maximo entre los centros de las dos celdas de la arista."""
    x, y, z = (np.asarray(d[v][:], dtype=float) for v in ("xCell", "yCell", "zCell"))
    nrm = np.sqrt(x * x + y * y + z * z) / R
    dev = float(np.abs(nrm - 1).max())
    ce = np.asarray(d["cellsOnEdge"][:]).astype(int)
    m = (ce[:, 0] > 0) & (ce[:, 1] > 0)
    a, b = ce[m, 0] - 1, ce[m, 1] - 1
    arc = np.arccos(np.clip((x[a] * x[b] + y[a] * y[b] + z[a] * z[b]) / (nrm[a] * nrm[b]) / R ** 2, -1, 1))
    rel = np.abs(np.asarray(d["dcEdge"][:], dtype=float)[m] / R - arc) / np.maximum(arc, 1e-12)
    msgs = ["|xyz|/R en celdas: desviacion max %.2e (limite 1e-6)" % dev,
            "dcEdge/R frente al arco entre centros: error relativo max %.2e (limite 1e-3)" % rel.max()]
    return dev <= 1e-6 and rel.max() <= 1e-3, msgs


def rescale_unit_sphere(path, check_only=False):
    """region.nc en metros -> esfera unitaria. Longitudes /R, areas /R^2, sphere_radius = 1. No toca meshDensity,
    latCell/lonCell, indices ni gridSpacing. Si las comprobaciones fallan, NO modifica el archivo."""
    import netCDF4 as nc, shutil
    with nc.Dataset(path) as d:
        R = float(getattr(d, "sphere_radius", 0) or 0)
        if R == 0:
            raise RuntimeError(path + ": sin atributo sphere_radius; no se que escala tiene")
        if abs(R - 1.0) < 1e-12:
            return "ya estaba en esfera unitaria (sin cambios)"
        ok, msgs = sphere_checks(d, R)
    for m_ in msgs: print("  [antes]", m_)
    if not ok:
        raise RuntimeError("las comprobaciones geometricas fallaron; " + path + " NO se ha modificado")
    if check_only:
        return "comprobaciones correctas (check-only, no se escribio)"
    bak = path + ".bak_rescale"
    if not os.path.exists(bak): shutil.copy2(path, bak)
    with nc.Dataset(path, "r+") as d:
        for v in RESC_LEN:
            if v in d.variables: d[v][:] = np.asarray(d[v][:], dtype=float) / R
        for v in RESC_AREA:
            if v in d.variables: d[v][:] = np.asarray(d[v][:], dtype=float) / R ** 2
        d.sphere_radius = 1.0
    with nc.Dataset(path) as d:
        ok2, msgs2 = sphere_checks(d, 1.0)
    for m_ in msgs2: print("  [despues]", m_)
    if not ok2:
        raise RuntimeError("las comprobaciones posteriores fallaron; restaura la copia " + bak)
    return "reescalado a esfera unitaria (copia en " + os.path.basename(bak) + ")"


# ----------------------------------------------------------------------------- calidad
def check_mesh(path, box_fine=None, earth_radius=6371229.0, expected_cells=None, regional=True):
    import netCDF4 as nc
    d = nc.Dataset(path); n = len(d.dimensions["nCells"])
    lat = np.degrees(d["latCell"][:]); lon = np.degrees(d["lonCell"][:]); lon = np.where(lon > 180, lon - 360, lon)
    sr = float(getattr(d, "sphere_radius", earth_radius))      # malla reescalada a esfera unitaria -> volver a km reales
    dc = np.asarray(d["dcEdge"][:]) * (earth_radius / sr) / 1e3; ce = np.asarray(d["cellsOnEdge"][:])
    ok = (ce[:, 0] > 0) & (ce[:, 1] > 0); a, b = ce[ok, 0] - 1, ce[ok, 1] - 1; dci = dc[ok]
    w = np.zeros(n); c = np.zeros(n)
    for k in (0, 1):
        m = ce[:, k] > 0; np.add.at(w, ce[m, k] - 1, dc[m]); np.add.at(c, ce[m, k] - 1, 1)
    w /= np.maximum(c, 1)
    grad = np.abs(w[a] - w[b]) / dci; ratio = np.maximum(w[a], w[b]) / np.minimum(w[a], w[b])
    ne = np.asarray(d["nEdgesOnCell"][:]).astype(int)
    q = {"cells": n, "edges": len(dc), "bdyMaskCell_max": int(np.asarray(d["bdyMaskCell"][:]).max()) if "bdyMaskCell" in d.variables else 7,
         "dx_min_km": float(w.min()), "dx_median_km": float(np.median(w)), "dx_max_km": float(w.max()),
         "grad_max": float(grad.max()), "grad_p999": float(np.percentile(grad, 99.9)), "grad_p99": float(np.percentile(grad, 99)),
         "ratio_max": float(ratio.max()), "ratio_p999": float(np.percentile(ratio, 99.9)),
         "frac_non_hex": float((ne != 6).mean()),
         "n_dx_lt4": int((w < 4).sum()), "n_dx_4_12": int(((w >= 4) & (w < 12)).sum()), "n_dx_ge12": int((w >= 12).sum()),
         "dt_hint_s": float(6 * w.min())}
    if "meshDensity" in d.variables:
        rho = np.asarray(d["meshDensity"][:])
        q["meshDensity_min"] = float(rho.min()); q["meshDensity_max"] = float(rho.max())
        q["scaling_del4_max"] = float(rho.min() ** -0.75)
    for v in ("cellQuality", "triangleAngleQuality"):
        if v in d.variables:
            x = np.asarray(d[v][:]); q[v + "_min"] = float(x.min()); q[v + "_p1"] = float(np.percentile(x, 1))
    if box_fine:
        la0, la1, lo0, lo1 = box_fine; m = (lat > la0) & (lat < la1) & (lon > lo0) & (lon < lo1)
        q["fine_box_cells"] = int(m.sum()); q["fine_box_dx_mean"] = float(w[m].mean()); q["fine_box_dx_max"] = float(w[m].max())
    warn = []
    if q["ratio_max"] > 1.6: warn.append("razon de espaciado entre vecinas > 1.6 (rampa muy corta; heuristico)")
    if q["grad_p999"] > 0.35: warn.append("gradiente de espaciado p99.9 > 0.35 (rampa muy corta; heuristico)")
    if q.get("cellQuality_min", 1) < 0.2: warn.append("cellQuality minima < 0.2 (heuristico)")
    if regional and q["bdyMaskCell_max"] < 7: warn.append("bdyMaskCell max < 7: revisar el recorte regional")
    if "meshDensity_min" in q and q["meshDensity_max"] - q["meshDensity_min"] < 1e-9 and q["dx_max_km"] > 1.5 * q["dx_min_km"]:
        warn.append("meshDensity constante con malla variable: la difusion no se escala con dx (usar --stages density)")
    q["warnings"] = warn
    q["sphere_radius"] = sr
    errs = []
    if abs(sr - 1.0) > 1e-12: errs.append("sphere_radius = %g: la malla NO esta en esfera unitaria (init_atmosphere abortara); ejecuta la etapa rescale" % sr)
    if "meshDensity" not in d.variables: errs.append("falta meshDensity (ejecuta la etapa density)")
    if regional and q["bdyMaskCell_max"] < 7: errs.append("bdyMaskCell max < 7: el recorte regional no tiene las 7 capas de frontera")
    if q["cells"] < 1000: errs.append("menos de 1000 celdas: recorte vacio o erroneo")
    if expected_cells:
        q["cells_expected"] = float(expected_cells); r = q["cells"] / expected_cells
        if not 0.8 <= r <= 1.25: errs.append("celdas %d frente a %.0f estimadas (razon %.2f, fuera de 0.8-1.25): revisa la caja del recorte y las rampas" % (q["cells"], expected_cells, r))
    if "fine_box_cells" in q and q["fine_box_cells"] == 0: errs.append("no hay celdas en la caja mas fina")
    if q["frac_non_hex"] > 0.10: errs.append("mas del 10 % de celdas no hexagonales")
    q["errors"] = errs
    d.close()
    return q


def print_quality(q):
    print(f"  celdas {q['cells']}  aristas {q['edges']}  bdyMaskCell max {q['bdyMaskCell_max']}")
    print(f"  dx local (km): min {q['dx_min_km']:.2f}  mediana {q['dx_median_km']:.2f}  max {q['dx_max_km']:.2f}")
    print(f"  gradiente |ddx/dx|: max {q['grad_max']:.3f}  p99.9 {q['grad_p999']:.3f}  p99 {q['grad_p99']:.3f}")
    print(f"  razon dx vecinas: max {q['ratio_max']:.2f}  p99.9 {q['ratio_p999']:.2f}   celdas no hexagonales: {100*q['frac_non_hex']:.1f} %")
    for v in ("cellQuality", "triangleAngleQuality"):
        if v + "_min" in q: print(f"  {v}: min {q[v+'_min']:.3f}  p1 {q[v+'_p1']:.3f}")
    if "fine_box_cells" in q:
        print(f"  caja mas fina: {q['fine_box_cells']} celdas, dx medio {q['fine_box_dx_mean']:.2f} km (max {q['fine_box_dx_max']:.2f})")
    if "meshDensity_min" in q:
        print(f"  meshDensity: min {q['meshDensity_min']:.3g}  max {q['meshDensity_max']:.3g}   meshScalingDel4 max {q['scaling_del4_max']:.3g}")
    print(f"  celdas con dx <4 km: {q['n_dx_lt4']} | 4-12: {q['n_dx_4_12']} | >=12: {q['n_dx_ge12']}")
    print(f"  dt orientativo (6 s/km x dx_min): {q['dt_hint_s']:.0f} s   [orientativo, hay que probarlo]")
    for w_ in q["warnings"]: print("  AVISO:", w_)
    for e_ in q.get("errors", []): print("  ERROR:", e_)


# ----------------------------------------------------------------------------- principal
STAGE_ORDER = ["cellwidth", "mesh", "region", "rescale", "density", "partition", "check"]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config")
    p.add_argument("--rescale", metavar="REGION.NC", help="solo reescala ese archivo a esfera unitaria y sale (no necesita --config)")
    p.add_argument("--name"); p.add_argument("--outdir")
    p.add_argument("--ramps", help="p.ej. 150,50  (sobrescribe ramp_km)")
    p.add_argument("--smooth-km", type=float); p.add_argument("--smooth-keep", choices=["none", "finest", "all"])
    p.add_argument("--corner-frac", type=float, help="0 = rectangulo ... 1 = estadio (esquinas redondeadas)")
    p.add_argument("--grid-deg", type=float)
    p.add_argument("--nproc", help="procesos MPI para gpmetis; lista separada por comas; por defecto, CPUs disponibles")
    p.add_argument("--limited-area", default=os.environ.get("MPAS_LIMITED_AREA", "MPAS-Limited-Area"),
                   help="carpeta del repositorio MPAS-Limited-Area (donde esta create_region)")
    p.add_argument("--density-power", type=float, default=4.0, help="exponente p en meshDensity = (dx_ref/dx)^p (4 = convencion MPAS)")
    p.add_argument("--density-ref-pct", type=float, default=1.0, help="percentil de dx usado como dx_ref (celda fina de referencia)")
    p.add_argument("--density-force", action="store_true", help="reescribe meshDensity aunque ya coincida")
    p.add_argument("--stages", default=",".join(STAGE_ORDER))
    p.add_argument("--preview", action="store_true", help="solo cellwidth + figura + estimacion de celdas")
    a = p.parse_args()
    if a.rescale:
        if not os.path.exists(a.rescale): sys.exit("ERROR: no existe " + a.rescale)
        try: print("[rescale]", rescale_unit_sphere(a.rescale))
        except RuntimeError as e: sys.exit("ERROR: %s" % e)
        return
    if not a.config: p.error("falta --config")
    bad = [x for x in a.stages.split(",") if x not in STAGE_ORDER]
    if bad: p.error("etapas desconocidas: %s (validas: %s)" % (",".join(bad), ",".join(STAGE_ORDER)))

    cfg = json.load(open(a.config))
    if a.name: cfg["name"] = a.name
    if a.ramps:
        cfg["ramp_km"] = [float(x) for x in a.ramps.split(",")]
        for l in cfg["levels"]: l.pop("ramp_km", None)               # --ramps manda sobre las rampas por nivel
    if a.smooth_km is not None: cfg["smooth_km"] = a.smooth_km
    if a.smooth_keep: cfg["smooth_keep"] = a.smooth_keep
    if a.corner_frac is not None: cfg["corner_radius_frac"] = a.corner_frac
    if a.grid_deg: cfg["grid_deg"] = a.grid_deg
    try: cfg = normalize_config(cfg)
    except ValueError as e: sys.exit("ERROR en la configuracion: %s" % e)
    GLOBAL = cfg["mode"] == "global"
    out = a.outdir or cfg["outdir"] or cfg["name"]
    os.makedirs(out, exist_ok=True); out = os.path.abspath(out); cfgfile = os.path.abspath(a.config)
    stages = ["cellwidth"] if a.preview else [x for x in STAGE_ORDER if x in a.stages.split(",")]   # siempre en el orden correcto
    name = cfg["name"]
    json.dump(cfg, open(os.path.join(out, name + ".config_used.json"), "w"), indent=1)
    t0 = time.time(); L = cfg["levels"]
    print(f"== {name}  modo {cfg['mode']}  niveles {[ (l['name'], l['dx'], l['shape']) for l in L]}  rampas {cfg['ramp_km']} km  suavizado {cfg['smooth_km']} km "
          f"({cfg['smooth_keep']})  esquinas {cfg['corner_radius_frac']}")

    if "cellwidth" in stages:
        lon, lat, LON, LAT, w, d = build_cellwidth(cfg)
        n = estimate_cells(LAT, w, d[0], cfg["grid_deg"])
        print(f"[cellwidth] celdas estimadas en la region: {n:,.0f}   (dx min {w.min():.2f}, max dentro de region {w[d[0]==0].max():.2f} km)")
        np.savez_compressed(os.path.join(out, name + ".cellwidth.npz"), lon=lon, lat=lat, w=w)
        json.dump({"cells_estimated": n}, open(os.path.join(out, name + ".estimate.json"), "w"))
        preview(cfg, lon, lat, LON, LAT, w, d, os.path.join(out, name + ".preview.png"))
        print(f"[cellwidth] figura: {os.path.join(out, name + '.preview.png')}")
    os.chdir(out)

    if "mesh" in stages:
        from mpas_tools.mesh.creation.build_mesh import build_spherical_mesh
        if "cellwidth" not in stages:
            z = np.load(name + ".cellwidth.npz"); lon, lat, w = z["lon"], z["lat"], z["w"]
        print("[mesh] JIGSAW (varios minutos) ..."); t = time.time()
        build_spherical_mesh(w, lon, lat, cfg["earth_radius"], out_filename=f"{name}_global.nc", plot_cellWidth=False)
        print(f"[mesh] listo en {time.time()-t:.0f} s -> {name}_global.nc")

    if "region" in stages and GLOBAL:
        print("[region] modo global: no se recorta; se usa", f"{name}_global.nc")
        n_, e_ = ML.write_graph_info(f"{name}_global.nc", f"{name}.graph.info")
        print(f"[region] -> {name}.graph.info ({n_} celdas, {e_} aristas)")
    elif "region" in stages:
        b = L[0]
        pts = ML.polygon_points(b, cfg["corner_radius_frac"])
        cla, clo = ML.region_center(b, cfg["corner_radius_frac"])
        with open(f"{name}.txt", "w") as f:
            f.write(f"Name: {name}\nType: custom\nPoint: {cla:.3f}, {clo:.3f}\n")
            for la, lo in pts: f.write(f"{la:.4f}, {lo:.4f}\n")
        cr = os.path.join(os.path.expanduser(a.limited_area), "create_region")
        if not os.path.exists(cr): sys.exit(f"No encuentro {cr}: clona MPAS-Limited-Area o usa --limited-area")
        print("[region] create_region ...")
        subprocess.run([sys.executable, cr, f"{name}.txt", f"{name}_global.nc"], check=True)
        for f_ in (f"{name}.region.nc", f"{name}.graph.info"):
            if not os.path.exists(f_): sys.exit(f"ERROR: create_region no produjo {f_}")
        print(f"[region] -> {name}.region.nc, {name}.graph.info")

    MESHF = f"{name}_global.nc" if GLOBAL else f"{name}.region.nc"      # archivo final que usa MONAN

    if "rescale" in stages:
        rf = MESHF
        if not os.path.exists(rf): sys.exit(f"No encuentro {rf}: ejecuta antes la etapa region")
        try: print("[rescale]", rescale_unit_sphere(rf))
        except RuntimeError as e: sys.exit("ERROR [rescale]: %s" % e)

    if "density" in stages:
        rf = MESHF
        if not os.path.exists(rf): sys.exit(f"No encuentro {rf}: ejecuta antes la etapa " + ("mesh" if GLOBAL else "region"))
        i = write_density(rf, a.density_power, a.density_ref_pct, a.density_force)
        print(f"[density] {i['action']}: meshDensity antes [{i['meshDensity_before_min']}, {i['meshDensity_before_max']}]"
              f" -> [{i['rho_min']:.3g}, {i['rho_max']:.3g}]  meshScalingDel2 max {i['scaling_del2_max']:.3g}  Del4 max {i['scaling_del4_max']:.3g}")
        print("[density] AVISO: regenera static.nc e init.nc (init_atmosphere) con esta malla antes de correr MONAN")

    if "partition" in stages:
        ns = [int(x) for x in a.nproc.split(",")] if a.nproc else [len(os.sched_getaffinity(0))]
        for n_ in ns:
            subprocess.run(["gpmetis", f"{name}.graph.info", str(n_)], check=True, stdout=subprocess.DEVNULL)
            pf = f"{name}.graph.info.part.{n_}"
            if not os.path.exists(pf): sys.exit(f"ERROR: gpmetis no produjo {pf}")
            nlines = sum(1 for _ in open(pf))
            import netCDF4 as _nc
            with _nc.Dataset(MESHF) as _d: ncell = len(_d.dimensions["nCells"])
            if nlines != ncell: sys.exit(f"ERROR: {pf} tiene {nlines} lineas y la malla {ncell} celdas (graph.info no corresponde a region.nc)")
            print(f"[partition] {pf} ({nlines} celdas)")

    if "check" in stages:
        fine = L[-1]; fb = ML.level_bounds(fine)
        est = None
        if os.path.exists(f"{name}.estimate.json"): est = json.load(open(f"{name}.estimate.json")).get("cells_estimated")
        q = check_mesh(MESHF, fb, cfg["earth_radius"], est, regional=not GLOBAL)
        if a.nproc:                                                          # cada particion pedida debe existir
            for n_ in [int(x) for x in a.nproc.split(",")]:
                if not os.path.exists(f"{name}.graph.info.part.{n_}"): q["errors"].append(f"falta {name}.graph.info.part.{n_}")
        print("[check] calidad:"); print_quality(q)
        json.dump(q, open(f"{name}.quality.json", "w"), indent=1)
        if q["errors"]:
            print(f"== TERMINADO CON {len(q['errors'])} ERROR(ES); NO uses esta malla hasta corregirlos")
            sys.exit(1)
        print("[check] TODO CORRECTO: sphere_radius = 1, celdas y particiones coherentes")
    print(f"== terminado en {time.time()-t0:.0f} s; resultados en {out}")


if __name__ == "__main__":
    main()
