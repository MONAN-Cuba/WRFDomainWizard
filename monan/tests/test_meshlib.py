"""Pruebas de meshlib: equivalencia con la version anterior de make_mpas_mesh.py y formas nuevas.
Ejecutar:  python3 -m pytest monan/tests -q   (o  python3 monan/tests/test_meshlib.py)"""
import os, sys, json, importlib.util, copy
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import meshlib as M

def _orig():
    sp = importlib.util.spec_from_file_location("orig", os.path.join(os.path.dirname(HERE), "make_mpas_mesh.orig.py"))
    m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m); return m

def _cfg(name="cuba_sispi.json", **kw):
    c = {"name": "mesh", "grid_deg": 0.05, "earth_radius": 6371229.0, "smooth_km": 0.0, "smooth_keep": "finest", "corner_radius_frac": 0.0}
    c.update(json.load(open(os.path.join(os.path.dirname(HERE), "examples", name)))); c.update(kw); return c

def test_equiv_cuba():
    O = _orig()
    for kw in ({}, {"smooth_km": 20, "smooth_keep": "finest"}, {"smooth_km": 20, "smooth_keep": "all", "corner_radius_frac": 0.6}, {"ramp_km": [300, 100]}):
        c = _cfg(grid_deg=0.1, **kw)
        w0 = O.build_cellwidth(copy.deepcopy(c))[4]
        lon, lat, LON, LAT, w1, d = M.build_cellwidth(copy.deepcopy(c))
        assert np.allclose(w0, w1, atol=1e-9), (kw, float(np.abs(w0 - w1).max()))
        n0 = O.estimate_cells(LAT, w0, O.build_cellwidth(copy.deepcopy(c))[5][0], 0.1)
        assert abs(n0 - M.estimate_cells(LAT, w1, d[0], 0.1)) < 1e-6 * n0

def test_circle_ellipse():
    LAT, LON = np.meshgrid(np.arange(10, 30.01, 0.05), np.arange(-90, -60.01, 0.05), indexing="ij")
    d = M.dist_ellipse(LAT, LON, (20, -75), 100, 100)
    # circulo: distancia = r_local - 100 km, medida en un punto al norte a 150 km del centro
    j = np.argmin(abs(LON[0] + 75)); i = np.argmin(abs(LAT[:, 0] - (20 + 150 / M.KM)))
    assert abs(d[i, j] - 50) < 1.0
    e = M.dist_ellipse(LAT, LON, (20, -75), 200, 50, 0)       # semieje este 200, norte 50
    i2 = np.argmin(abs(LAT[:, 0] - (20 + 100 / M.KM)))
    assert abs(e[i2, j] - 50) < 2.0                              # 100 km al norte, borde a 50 km
    r = M.dist_ellipse(LAT, LON, (20, -75), 200, 50, 90)     # girada 90 grados: el eje largo apunta al norte
    assert r[i2, j] == 0

def test_constant_and_global():
    c = M.normalize_config({"name": "k", "levels": [{"dx": 15, "lat": [15, 30], "lon": [-95, -65]}]})
    lon, lat, LON, LAT, w, d = M.build_cellwidth(c, 0.1)
    assert np.all(w == 15)
    n = M.estimate_cells(LAT, w, d[0], 0.1)
    area = 30 * M.KM * 15 * M.KM * np.cos(np.radians(22.5)) ; assert abs(n - area / (M.HEX * 15 ** 2)) / n < 0.05
    g = M.normalize_config({"name": "g", "mode": "global", "levels": [{"dx": 60}]})
    lon, lat, LON, LAT, w, d = M.build_cellwidth(g, 0.5)
    n = M.estimate_cells(LAT, w, d[0], 0.5); assert abs(n - 4 * np.pi * M.EARTH_KM ** 2 / (M.HEX * 60 ** 2)) / n < 0.01

def test_global_with_refinement_and_siblings():
    g = M.normalize_config({"name": "g", "mode": "global", "levels": [
        {"dx": 60}, {"name": "a", "dx": 15, "shape": "circle", "center": [22, -80], "radius_km": 300, "ramp_km": 300},
        {"name": "b", "dx": 15, "shape": "ellipse", "center": [10, 100], "semi_axes_km": [400, 200], "rotation_deg": 30, "ramp_km": 300, "parent": 0}]})
    lon, lat, LON, LAT, w, d = M.build_cellwidth(g, 0.25)
    assert w.min() == 15 and w.max() == 60
    ia = np.argmin(abs(lat - 22)); ja = np.argmin(abs(lon + 80)); assert w[ia, ja] == 15
    ib = np.argmin(abs(lat - 10)); jb = np.argmin(abs(lon - 100)); assert w[ib, jb] == 15
    assert w[np.argmin(abs(lat + 40)), np.argmin(abs(lon - 0))] == 60

def test_polygon_matches_rect():
    LAT, LON = np.meshgrid(np.arange(10, 30.01, 0.1), np.arange(-90, -60.01, 0.1), indexing="ij")
    pts = [(25, -80), (25, -70), (15, -70), (15, -80)]
    dp = M.dist_polygon(LAT, LON, pts, 400)
    dr = M.dist_rect(LAT, LON, (15, 25, -80, -70), 0)
    m = dr < 300
    assert np.abs(dp[m] - dr[m]).max() < 12     # distancia por rejilla vs analitica: error de discretizacion <= 1 celda (0.1 grado = 11 km)
    assert ((dp == 0) == (dr == 0))[m].mean() > 0.99

def test_errors():
    for bad in ({"levels": [{"dx": 9, "lat": [1, 2], "lon": [1, 2]}, {"dx": 27, "lat": [1, 2], "lon": [1, 2], "ramp_km": 10}]},   # hijo mas grueso
                {"levels": [{"dx": 27, "lat": [1, 2], "lon": [1, 2]}, {"dx": 9, "lat": [1, 2], "lon": [1, 2]}]},                     # sin rampa
                {"levels": [{"dx": 27, "shape": "circle", "center": [1, 2]}]}):
        try: M.normalize_config(bad)
        except ValueError: continue
        raise AssertionError(bad)

if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"): v(); print("ok", k)
