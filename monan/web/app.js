/* Constructor interactivo de mallas MONAN. Mismo formato JSON que make_mpas_mesh.py / meshlib.py. */
(function () {
  'use strict';
  const KM = 111.195, COLORS = ['#111111', '#ff9800', '#e53935', '#8e24aa', '#1e88e5', '#43a047', '#00897b', '#6d4c41'];
  const STAGES = ['cellwidth', 'mesh', 'region', 'rescale', 'density', 'partition', 'check'];
  const $ = (id) => document.getElementById(id);
  const rd = (x, n) => Math.round(x * Math.pow(10, n || 3)) / Math.pow(10, n || 3);

  const TEMPLATES = {
    cuba: { name: 'cuba_sispi', mode: 'regional', smooth_km: 0, levels: [
      { name: 'd01', dx: 27, shape: 'rect', lat: [13.52, 32.16], lon: [-99.41, -62.70] },
      { name: 'd02', dx: 9, shape: 'rect', lat: [17.59, 26.64], lon: [-88.32, -70.88], ramp_km: 150, parent: 0 },
      { name: 'd03', dx: 3, shape: 'rect', lat: [19.26, 24.25], lon: [-85.76, -73.70], ramp_km: 50, parent: 1 }] },
    const: { name: 'regional_15km', mode: 'regional', smooth_km: 0, levels: [
      { name: 'dominio', dx: 15, shape: 'rect', lat: [10, 30], lon: [-100, -60] }] },
    glob: { name: 'global_60_15', mode: 'global', smooth_km: 0, levels: [
      { name: 'base', dx: 60, shape: 'global' },
      { name: 'zona', dx: 15, shape: 'circle', center: [22, -80], radius_km: 500, ramp_km: 300, parent: 0 }] }
  };

  const S = { static: false, cfg: JSON.parse(JSON.stringify(TEMPLATES.cuba)), sel: 0, tool: '', seq: 0, timer: null, job: null, preview: null };

  /* ------------------------------------------------------------------ geometria (espejo de meshlib.py) */
  const cosd = (lat) => Math.cos(lat * Math.PI / 180);
  const ptKm = (c, x, y) => [c[0] + y / KM, c[1] + x / (KM * cosd(c[0]))];
  const toKm = (c, ll) => [(ll[1] - c[1]) * KM * cosd(c[0]), (ll[0] - c[0]) * KM];

  function outline(lv) {
    const sh = lv.shape;
    if (sh === 'rect' || sh === 'stadium') {
      const [la0, la1] = lv.lat, [lo0, lo1] = lv.lon;
      const frac = lv.corner_radius_frac != null ? lv.corner_radius_frac : (sh === 'stadium' ? 1 : 0);
      if (frac <= 0) return [[la1, lo0], [la1, lo1], [la0, lo1], [la0, lo0]];
      const clat = (la0 + la1) / 2, clon = (lo0 + lo1) / 2, hy = (la1 - la0) / 2, hx = (lo1 - lo0) / 2, c = cosd(clat);
      const r = frac * Math.min(hy * KM, hx * KM * c), ry = r / KM, rx = r / (KM * c), pts = [];
      [[1, 1, 90], [-1, 1, 0], [-1, -1, -90], [1, -1, -180]].forEach(([sy, sx, a0]) => {
        const cy = clat + sy * (hy - ry), cx = clon + sx * (hx - rx);
        for (let i = 0; i <= 12; i++) { const t = (a0 - 90 * i / 12) * Math.PI / 180; pts.push([cy + ry * Math.sin(t), cx + rx * Math.cos(t)]); }
      });
      return pts;
    }
    if (sh === 'ellipse' || sh === 'circle') {
      const c = lv.center, a = sh === 'circle' ? lv.radius_km : lv.semi_axes_km[0], b = sh === 'circle' ? lv.radius_km : lv.semi_axes_km[1];
      const rot = sh === 'ellipse' ? (lv.rotation_deg || 0) * Math.PI / 180 : 0, pts = [];
      for (let i = 0; i < 96; i++) {
        const t = 2 * Math.PI * i / 96, xr = a * Math.sin(t), yr = b * Math.cos(t);
        pts.push(ptKm(c, xr * Math.cos(rot) - yr * Math.sin(rot), xr * Math.sin(rot) + yr * Math.cos(rot)));
      }
      return pts;
    }
    if (sh === 'polygon') return lv.points;
    return [];
  }

  function bounds(lv) {
    const o = outline(lv);
    if (!o.length) return null;
    const la = o.map((p) => p[0]), lo = o.map((p) => p[1]);
    return [Math.min(...la), Math.max(...la), Math.min(...lo), Math.max(...lo)];
  }

  function inside(pt, poly) {
    let c = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [yi, xi] = poly[i], [yj, xj] = poly[j];
      if ((yi > pt[0]) !== (yj > pt[0]) && pt[1] < (xj - xi) * (pt[0] - yi) / (yj - yi) + xi) c = !c;
    }
    return c;
  }

  function center(lv) {
    if (lv.center) return lv.center;
    const b = bounds(lv); return b ? [(b[0] + b[1]) / 2, (b[2] + b[3]) / 2] : [0, 0];
  }

  function convert(lv, shape) {
    if (lv.shape === shape) return;
    const o = outline(lv), b = bounds(lv), c = center(lv), keep = { name: lv.name, dx: lv.dx };
    if (lv.ramp_km != null) keep.ramp_km = lv.ramp_km; if (lv.parent != null) keep.parent = lv.parent;
    let w = 100, h = 100;
    if (b) { w = (b[3] - b[2]) * KM * cosd(c[0]) / 2; h = (b[1] - b[0]) * KM / 2; }
    Object.keys(lv).forEach((k) => delete lv[k]); Object.assign(lv, keep, { shape });
    if (shape === 'rect' || shape === 'stadium') { lv.lat = [rd(b[0]), rd(b[1])]; lv.lon = [rd(b[2]), rd(b[3])]; }
    else if (shape === 'ellipse') { lv.center = [rd(c[0]), rd(c[1])]; lv.semi_axes_km = [rd(w, 1), rd(h, 1)]; lv.rotation_deg = 0; }
    else if (shape === 'circle') { lv.center = [rd(c[0]), rd(c[1])]; lv.radius_km = rd(Math.min(w, h), 1); }
    else if (shape === 'polygon') { const step = Math.max(1, Math.floor(o.length / 24)); lv.points = o.filter((_, i) => i % step === 0).map((p) => [rd(p[0]), rd(p[1])]); }
  }

  /* ------------------------------------------------------------------ mapa */
  const map = L.map('map', { center: [21, -80], zoom: 5, worldCopyJump: false });
  const bases = { 'Esri imagen': L.tileLayer.provider('Esri.WorldImagery'), 'Esri topográfico': L.tileLayer.provider('Esri.WorldTopoMap'), 'OpenStreetMap': L.tileLayer.provider('OpenStreetMap') };
  bases['Esri topográfico'].addTo(map);
  L.control.layers(bases, null, { position: 'topright' }).addTo(map);
  L.control.scale({ metric: true, imperial: false }).addTo(map);
  map.on('mousemove', (e) => { $('coords').textContent = e.latlng.lat.toFixed(3) + '°, ' + e.latlng.lng.toFixed(3) + '°'; });
  const shapesG = L.layerGroup().addTo(map), handlesG = L.layerGroup().addTo(map), drawG = L.layerGroup().addTo(map), cellsG = L.layerGroup().addTo(map);
  let overlay = null;

  function redrawShapes() {
    shapesG.clearLayers();
    S.cfg.levels.forEach((lv, k) => {
      const o = outline(lv); if (!o.length) return;
      L.polygon(o, { color: COLORS[k % COLORS.length], weight: k === S.sel ? 3 : 1.5, fill: true, fillOpacity: 0.02, dashArray: k === S.sel ? null : '5 4' })
        .on('click', (e) => { if (!S.tool) { L.DomEvent.stopPropagation(e); select(k); } }).addTo(shapesG);
    });
    redrawHandles();
  }

  function hIcon(c) { return L.divIcon({ className: '', html: '<div class="handle' + (c ? ' c' : '') + '"></div>', iconSize: [12, 12], iconAnchor: [6, 6] }); }
  function hMarker(ll, onDrag, c, onDel) {
    const m = L.marker(ll, { icon: hIcon(c), draggable: true, zIndexOffset: 1000 }).addTo(handlesG);
    m.on('drag', (e) => onDrag(e.target.getLatLng()));
    m.on('dragend', () => { syncFields(); changed(); });
    if (onDel) m.on('contextmenu', () => { onDel(); redrawShapes(); fillEditor(); changed(); });
    return m;
  }

  function redrawHandles() {
    handlesG.clearLayers();
    const lv = S.cfg.levels[S.sel]; if (!lv || lv.shape === 'global') return;
    const upd = () => { redrawOutlineOnly(); };
    if (lv.shape === 'rect' || lv.shape === 'stadium') {
      const mk = (laI, loI) => hMarker([lv.lat[laI], lv.lon[loI]], (ll) => { lv.lat[laI] = rd(ll.lat); lv.lon[loI] = rd(ll.lng); lv.lat.sort((a, b) => a - b); lv.lon.sort((a, b) => a - b); upd(); });
      const ms = [mk(1, 0), mk(1, 1), mk(0, 1), mk(0, 0)];
      const cm = hMarker(center(lv), (ll) => {
        const c0 = [(lv.lat[0] + lv.lat[1]) / 2, (lv.lon[0] + lv.lon[1]) / 2], dy = ll.lat - c0[0], dx = ll.lng - c0[1];
        lv.lat = [rd(lv.lat[0] + dy), rd(lv.lat[1] + dy)]; lv.lon = [rd(lv.lon[0] + dx), rd(lv.lon[1] + dx)]; upd();
      }, true);
      cm.on('dragend', redrawHandles);
      ms.forEach((m) => m.on('dragend', redrawHandles));
    } else if (lv.shape === 'circle') {
      const c = lv.center;
      hMarker(c, (ll) => { lv.center = [rd(ll.lat), rd(ll.lng)]; upd(); }, true).on('dragend', redrawHandles);
      hMarker(ptKm(c, lv.radius_km, 0), (ll) => { const v = toKm(c, [ll.lat, ll.lng]); lv.radius_km = rd(Math.max(1, Math.hypot(v[0], v[1])), 1); upd(); }).on('dragend', redrawHandles);
    } else if (lv.shape === 'ellipse') {
      const c = lv.center, t = (lv.rotation_deg || 0) * Math.PI / 180;
      hMarker(c, (ll) => { lv.center = [rd(ll.lat), rd(ll.lng)]; upd(); }, true).on('dragend', redrawHandles);
      hMarker(ptKm(c, lv.semi_axes_km[0] * Math.cos(t), lv.semi_axes_km[0] * Math.sin(t)), (ll) => {
        const v = toKm(c, [ll.lat, ll.lng]); lv.semi_axes_km[0] = rd(Math.max(1, Math.hypot(v[0], v[1])), 1); lv.rotation_deg = rd(Math.atan2(v[1], v[0]) * 180 / Math.PI, 1); upd();
      }).on('dragend', redrawHandles);
      hMarker(ptKm(c, -lv.semi_axes_km[1] * Math.sin(t), lv.semi_axes_km[1] * Math.cos(t)), (ll) => {
        const v = toKm(c, [ll.lat, ll.lng]); lv.semi_axes_km[1] = rd(Math.max(1, Math.abs(-v[0] * Math.sin(t) + v[1] * Math.cos(t))), 1); upd();
      }).on('dragend', redrawHandles);
    } else if (lv.shape === 'polygon') {
      lv.points.forEach((p, i) => hMarker(p, (ll) => { lv.points[i] = [rd(ll.lat), rd(ll.lng)]; upd(); }, false,
        () => { if (lv.points.length > 3) lv.points.splice(i, 1); }));
    }
  }

  function redrawOutlineOnly() {            // mientras se arrastra: solo la forma, sin recrear los tiradores
    shapesG.clearLayers();
    S.cfg.levels.forEach((lv, k) => { const o = outline(lv); if (o.length) L.polygon(o, { color: COLORS[k % COLORS.length], weight: k === S.sel ? 3 : 1.5, fillOpacity: 0.02, dashArray: k === S.sel ? null : '5 4', interactive: false }).addTo(shapesG); });
  }

  /* ------------------------------------------------------------------ herramientas de dibujo */
  function hint(t) { const h = $('hint'); h.style.display = t ? 'block' : 'none'; h.textContent = t || ''; }

  function setTool(t) {
    S.tool = t; drawG.clearLayers(); poly = [];
    document.querySelectorAll('#tools button[data-tool]').forEach((b) => b.classList.toggle('on', b.dataset.tool === t && t !== ''));
    map.dragging[t ? 'disable' : 'enable'](); map.doubleClickZoom[t ? 'disable' : 'enable']();
    hint(t === 'polygon' ? 'Polígono: haz clic para cada vértice; doble clic para terminar' : (t ? 'Arrastra en el mapa para dibujar (Esc cancela)' : ''));
  }

  let start = null, poly = [];
  map.on('mousedown', (e) => { if (S.tool && S.tool !== 'polygon') start = e.latlng; });
  map.on('mousemove', (e) => {
    if (!S.tool) return;
    drawG.clearLayers();
    if (S.tool === 'polygon' && poly.length) L.polyline(poly.concat([[e.latlng.lat, e.latlng.lng]]), { color: '#1f6feb', dashArray: '4 3' }).addTo(drawG);
    else if (start) { const lv = fromDrag(start, e.latlng); if (lv) L.polygon(outline(lv), { color: '#1f6feb', dashArray: '4 3', interactive: false }).addTo(drawG); }
  });
  map.on('mouseup', (e) => {
    if (!S.tool || S.tool === 'polygon' || !start) return;
    const lv = fromDrag(start, e.latlng); start = null;
    if (lv) addLevel(lv);
    setTool('');
  });
  map.on('click', (e) => { if (S.tool === 'polygon') { poly.push([rd(e.latlng.lat), rd(e.latlng.lng)]); } });
  map.on('dblclick', () => {
    if (S.tool !== 'polygon') return;
    if (poly.length > 3) poly.pop();           // el doble clic añadió el último vértice dos veces
    if (poly.length >= 3) addLevel({ shape: 'polygon', points: poly.slice() });
    setTool('');
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setTool(''); });

  function fromDrag(a, b) {
    const c = [(a.lat + b.lat) / 2, (a.lng + b.lng) / 2];
    if (S.tool === 'rect') { if (Math.abs(a.lat - b.lat) < 0.01 || Math.abs(a.lng - b.lng) < 0.01) return null; return { shape: 'rect', lat: [rd(Math.min(a.lat, b.lat)), rd(Math.max(a.lat, b.lat))], lon: [rd(Math.min(a.lng, b.lng)), rd(Math.max(a.lng, b.lng))] }; }
    if (S.tool === 'ellipse') { const w = Math.abs(a.lng - b.lng) * KM * cosd(c[0]) / 2, h = Math.abs(a.lat - b.lat) * KM / 2; if (w < 1 || h < 1) return null; return { shape: 'ellipse', center: [rd(c[0]), rd(c[1])], semi_axes_km: [rd(w, 1), rd(h, 1)], rotation_deg: 0 }; }
    if (S.tool === 'circle') { const v = toKm([a.lat, a.lng], [b.lat, b.lng]), r = Math.hypot(v[0], v[1]); if (r < 1) return null; return { shape: 'circle', center: [rd(a.lat), rd(a.lng)], radius_km: rd(r, 1) }; }
    return null;
  }

  function addLevel(lv) {
    const L_ = S.cfg.levels;
    if (S.cfg.mode === 'regional' && L_.length === 0) {
      Object.assign(lv, { name: 'd01', dx: 27 });
    } else {
      const c = center(lv); let par = 0;
      L_.forEach((p, i) => { if (p.shape === 'global' || inside(c, outline(p))) par = i; });
      const pdx = L_[par].dx;
      Object.assign(lv, { name: 'd' + String(L_.length + 1).padStart(2, '0'), dx: rd(pdx / 3, 2), ramp_km: Math.round(5 * pdx), parent: par });
    }
    L_.push(lv); S.sel = L_.length - 1;
    fillLevels(); fillEditor(); redrawShapes(); changed();
  }

  /* ------------------------------------------------------------------ panel de niveles */
  function select(k) { S.sel = k; fillLevels(); fillEditor(); redrawShapes(); }

  function fillLevels() {
    const box = $('levels'); box.innerHTML = '';
    S.cfg.levels.forEach((lv, k) => {
      const d = document.createElement('div');
      d.className = 'lv' + (k === S.sel ? ' sel' : ''); d.style.borderLeftColor = COLORS[k % COLORS.length];
      d.innerHTML = '<b>' + (lv.name || '') + '</b> &nbsp; ' + lv.dx + ' km <small>· ' + lv.shape + (k > 0 ? ' · rampa ' + lv.ramp_km + ' km · padre ' + (S.cfg.levels[lv.parent] || {}).name : '') + '</small>';
      d.onclick = () => select(k); box.appendChild(d);
    });
    if (!S.cfg.levels.length) box.innerHTML = '<div class="muted">Sin zonas. Dibuja la primera con las herramientas de arriba o elige una plantilla.</div>';
  }

  function numField(label, get, set, step) {
    const w = document.createElement('div'); const l = document.createElement('label'); l.textContent = label;
    const i = document.createElement('input'); i.type = 'number'; i.step = step || 'any'; i.value = get(); i.dataset.get = '1'; i._get = get;
    i.oninput = () => { const v = parseFloat(i.value); if (!isNaN(v)) { set(v); redrawShapes(); fillLevels(); changed(); } };
    w.appendChild(l); w.appendChild(i); return w;
  }

  function fillEditor() {
    const box = $('editor'); box.innerHTML = '';
    const lv = S.cfg.levels[S.sel]; if (!lv) return;
    const k = S.sel, ed = document.createElement('div'); ed.className = 'ed'; box.appendChild(ed);
    const r1 = document.createElement('div'); r1.className = 'row'; ed.appendChild(r1);
    const nm = document.createElement('div'); nm.innerHTML = '<label>Nombre</label><input>'; nm.lastChild.value = lv.name; nm.lastChild.oninput = (e) => { lv.name = e.target.value; fillLevels(); }; r1.appendChild(nm);
    r1.appendChild(numField('dx (km)', () => lv.dx, (v) => { lv.dx = v; }));
    if (!(k === 0 && S.cfg.mode === 'global')) {
      const sh = document.createElement('div'); sh.innerHTML = '<label>Forma</label><select><option>rect</option><option>stadium</option><option>ellipse</option><option>circle</option><option>polygon</option></select>';
      sh.lastChild.value = lv.shape; sh.lastChild.onchange = (e) => { convert(lv, e.target.value); fillLevels(); fillEditor(); redrawShapes(); changed(); }; ed.appendChild(sh);
    }
    if (k > 0) {
      const r2 = document.createElement('div'); r2.className = 'row'; ed.appendChild(r2);
      r2.appendChild(numField('Rampa hacia fuera (km)', () => lv.ramp_km, (v) => { lv.ramp_km = v; }));
      const pw = document.createElement('div'); pw.innerHTML = '<label>Dentro de (padre)</label><select></select>';
      S.cfg.levels.slice(0, k).forEach((p, i) => { const o = document.createElement('option'); o.value = i; o.textContent = p.name; pw.lastChild.appendChild(o); });
      pw.lastChild.value = lv.parent; pw.lastChild.onchange = (e) => { lv.parent = parseInt(e.target.value, 10); fillLevels(); changed(); }; r2.appendChild(pw);
    }
    const g = document.createElement('div'); g.className = 'row'; ed.appendChild(g);
    if (lv.shape === 'rect' || lv.shape === 'stadium') {
      [['lat sur', () => lv.lat[0], (v) => { lv.lat[0] = v; }], ['lat norte', () => lv.lat[1], (v) => { lv.lat[1] = v; }], ['lon oeste', () => lv.lon[0], (v) => { lv.lon[0] = v; }], ['lon este', () => lv.lon[1], (v) => { lv.lon[1] = v; }]]
        .forEach(([a, b, c]) => g.appendChild(numField(a, b, c, '0.01')));
      ed.appendChild(numField('Esquinas redondeadas (0 = recto, 1 = estadio)', () => lv.corner_radius_frac || 0, (v) => { lv.corner_radius_frac = v; }, '0.1'));
    } else if (lv.shape === 'circle') {
      g.appendChild(numField('centro lat', () => lv.center[0], (v) => { lv.center[0] = v; })); g.appendChild(numField('centro lon', () => lv.center[1], (v) => { lv.center[1] = v; }));
      ed.appendChild(numField('radio (km)', () => lv.radius_km, (v) => { lv.radius_km = v; }));
    } else if (lv.shape === 'ellipse') {
      g.appendChild(numField('centro lat', () => lv.center[0], (v) => { lv.center[0] = v; })); g.appendChild(numField('centro lon', () => lv.center[1], (v) => { lv.center[1] = v; }));
      const g2 = document.createElement('div'); g2.className = 'row'; ed.appendChild(g2);
      g2.appendChild(numField('semieje a (km)', () => lv.semi_axes_km[0], (v) => { lv.semi_axes_km[0] = v; })); g2.appendChild(numField('semieje b (km)', () => lv.semi_axes_km[1], (v) => { lv.semi_axes_km[1] = v; }));
      g2.appendChild(numField('giro (°)', () => lv.rotation_deg || 0, (v) => { lv.rotation_deg = v; }));
    } else if (lv.shape === 'polygon') {
      const m = document.createElement('div'); m.className = 'muted'; m.textContent = lv.points.length + ' vértices: arrástralos en el mapa; clic derecho sobre uno lo borra.'; ed.appendChild(m);
    }
    const del = document.createElement('button'); del.className = 'danger'; del.style.marginTop = '6px'; del.textContent = 'Borrar este nivel';
    del.onclick = () => deleteLevel(k); ed.appendChild(del);
  }

  function syncFields() { document.querySelectorAll('#editor input[data-get]').forEach((i) => { i.value = i._get(); }); fillLevels(); }

  function deleteLevel(k) {
    const L_ = S.cfg.levels, par = L_[k].parent;
    L_.splice(k, 1);
    L_.forEach((l, i) => { if (i >= k && l.parent != null) { if (l.parent === k) l.parent = par != null ? par : 0; else if (l.parent > k) l.parent -= 1; } });
    S.sel = Math.max(0, Math.min(S.sel, L_.length - 1));
    fillLevels(); fillEditor(); redrawShapes(); changed();
  }

  /* ------------------------------------------------------------------ configuracion <-> formulario */
  function cleanCfg() {
    const c = JSON.parse(JSON.stringify(S.cfg));
    c.name = $('f-name').value.trim(); c.mode = $('f-mode').value; c.smooth_km = parseFloat($('f-smooth').value) || 0;
    c.levels.forEach((l, k) => { if (k === 0) { delete l.ramp_km; delete l.parent; } });
    return c;
  }

  function loadCfg(c) {
    c = JSON.parse(JSON.stringify(c));
    const ramps = c.ramp_km || []; delete c.ramp_km;
    c.mode = c.mode || 'regional';
    (c.levels || []).forEach((l, k) => {
      l.name = l.name || 'd' + String(k + 1).padStart(2, '0'); l.shape = l.shape || (c.mode === 'global' && k === 0 ? 'global' : 'rect');
      if (k > 0) { if (l.ramp_km == null) l.ramp_km = ramps[k - 1] != null ? ramps[k - 1] : Math.round(5 * (c.levels[k - 1].dx)); l.parent = l.parent == null ? k - 1 : (typeof l.parent === 'string' ? c.levels.findIndex((x) => x.name === l.parent) : l.parent); }
    });
    S.cfg = c; S.sel = 0;
    $('f-name').value = c.name || 'mi_malla'; $('f-mode').value = c.mode; $('f-smooth').value = c.smooth_km || 0;
    fillLevels(); fillEditor(); redrawShapes(); fitToLevels(); changed();
  }

  function fitToLevels() {
    const bs = S.cfg.levels.map(bounds).filter(Boolean);
    if (bs.length) map.fitBounds([[Math.min(...bs.map((b) => b[0])), Math.min(...bs.map((b) => b[2]))], [Math.max(...bs.map((b) => b[1])), Math.max(...bs.map((b) => b[3]))]], { padding: [30, 30] });
    else map.setView([20, 0], 2);
  }

  function setMode(m) {
    const L_ = S.cfg.levels;
    if (m === 'global' && S.cfg.mode !== 'global') {
      const dxb = L_.length ? Math.max(...L_.map((l) => l.dx)) * 2 : 60;
      L_.forEach((l) => { if (l.parent != null) l.parent += 1; });
      if (L_.length) { L_[0].parent = 0; L_[0].ramp_km = Math.round(5 * dxb); }
      L_.unshift({ name: 'base', dx: dxb, shape: 'global' });
    } else if (m === 'regional' && S.cfg.mode === 'global') {
      L_.shift(); L_.forEach((l) => { if (l.parent != null) l.parent = Math.max(0, l.parent - 1); });
      if (L_.length) { delete L_[0].ramp_km; delete L_[0].parent; if (L_[0].shape === 'global') L_[0].shape = 'rect'; }
    }
    S.cfg.mode = m; S.sel = 0; fillLevels(); fillEditor(); redrawShapes(); changed();
  }


  /* ------------------------------------------------------------------ calculo local (modo solo diseño, espejo de meshlib.py) */
  const HEX = 0.866, EARTH_KM = 6371.229, VIRIDIS_R = ['#fde724', '#bdde26', '#79d151', '#44be70', '#22a784', '#208f8c', '#29788e', '#345e8d', '#404387', '#482374', '#440154'];
  const mod = (a, n) => ((a % n) + n) % n;
  const hex2rgb = (h) => [1, 3, 5].map((i) => parseInt(h.substr(i, 2), 16));

  function validate(cfg) {
    const L_ = cfg.levels; if (!L_.length) throw new Error('levels vacio');
    L_.forEach((l, k) => {
      if (!(l.dx > 0)) throw new Error('nivel ' + l.name + ': dx debe ser > 0');
      if (k === 0 && cfg.mode === 'global') return;
      if (l.shape === 'global') throw new Error('modo regional: el nivel 0 necesita forma');
      const need = { rect: ['lat', 'lon'], stadium: ['lat', 'lon'], ellipse: ['center', 'semi_axes_km'], circle: ['center', 'radius_km'], polygon: ['points'] }[l.shape];
      if (!need) throw new Error('nivel ' + l.name + ': forma desconocida ' + l.shape);
      need.forEach((key) => { if (l[key] == null) throw new Error('nivel ' + l.name + ' (' + l.shape + '): falta ' + key); });
      if (k >= 1) {
        if (!(l.ramp_km > 0)) throw new Error('nivel ' + l.name + ': ramp_km debe ser > 0');
        if (!(l.parent >= 0 && l.parent < k)) throw new Error('nivel ' + l.name + ': parent debe ser un nivel anterior');
        if (l.dx > L_[l.parent].dx) throw new Error('nivel ' + l.name + ': dx (' + l.dx + ') mayor que el de su padre (' + L_[l.parent].dx + ')');
      }
    });
  }

  function distFn(lv) {
    const sh = lv.shape;
    if (sh === 'global') return () => 0;
    if (sh === 'rect' || sh === 'stadium') {
      const [la0, la1] = lv.lat, [lo0, lo1] = lv.lon, frac = lv.corner_radius_frac != null ? lv.corner_radius_frac : (sh === 'stadium' ? 1 : 0);
      const clat = (la0 + la1) / 2, clon = (lo0 + lo1) / 2, c = cosd(clat), hy = (la1 - la0) / 2 * KM, hx = (lo1 - lo0) / 2 * KM * c, r = frac * Math.min(hx, hy);
      return (la, lo) => { const y = Math.abs(la - clat) * KM, x = Math.abs(mod(lo - clon + 180, 360) - 180) * KM * c; return Math.max(Math.hypot(Math.max(x - (hx - r), 0), Math.max(y - (hy - r), 0)) - r, 0); };
    }
    if (sh === 'ellipse' || sh === 'circle') {
      const c0 = lv.center, a = sh === 'circle' ? lv.radius_km : lv.semi_axes_km[0], b = sh === 'circle' ? lv.radius_km : lv.semi_axes_km[1], t = sh === 'ellipse' ? (lv.rotation_deg || 0) * Math.PI / 180 : 0;
      const ct = Math.cos(t), st = Math.sin(t), c = cosd(c0[0]);
      return (la, lo) => {
        const x = mod(lo - c0[1] + 180, 360) - 180, X = x * KM * c, Y = (la - c0[0]) * KM, xr = X * ct + Y * st, yr = -X * st + Y * ct;
        const f = Math.sqrt((xr / a) ** 2 + (yr / b) ** 2); if (f <= 1) return 0;
        const g = Math.sqrt((xr / (a * a)) ** 2 + (yr / (b * b)) ** 2); return (f - 1) * f / Math.max(g, 1e-300);
      };
    }
    // poligono: dentro = 0; fuera = distancia minima a las aristas (km, plano local)
    const P = lv.points, clat = P.reduce((s, p) => s + p[0], 0) / P.length, clon = P.reduce((s, p) => s + p[1], 0) / P.length, c = cosd(clat);
    const Q = P.map((p) => [(p[1] - clon) * KM * c, (p[0] - clat) * KM]);
    return (la, lo) => {
      if (inside([la, lo], P)) return 0;
      const x = (lo - clon) * KM * c, y = (la - clat) * KM; let best = Infinity;
      for (let i = 0; i < Q.length; i++) {
        const [x1, y1] = Q[i], [x2, y2] = Q[(i + 1) % Q.length], dx = x2 - x1, dy = y2 - y1, L2 = dx * dx + dy * dy;
        const t = L2 > 0 ? Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / L2)) : 0, d = Math.hypot(x - x1 - t * dx, y - y1 - t * dy); if (d < best) best = d;
      }
      return best;
    };
  }

  function previewLocal(cfg, gdIn) {
    const t0 = performance.now(); validate(cfg);
    const L_ = cfg.levels, glob = cfg.mode === 'global', gd = gdIn || (glob ? 0.5 : 0.1);
    let win = [-90, 90, -180, 180];
    if (!glob) { const b = bounds(L_[0]), m = 2; win = [Math.max(-90, b[0] - m), Math.min(90, b[1] + m), Math.max(-180, b[2] - m), Math.min(180, b[3] + m)]; }
    const i0 = Math.ceil((win[0] + 90) / gd - 1e-9), i1 = Math.floor((win[1] + 90) / gd + 1e-9), j0 = Math.ceil((win[2] + 180) / gd - 1e-9), j1 = Math.floor((win[3] + 180) / gd + 1e-9);
    const ny = i1 - i0 + 1, nx = j1 - j0 + 1; if (ny < 2 || nx < 2) throw new Error('ventana vacia');
    const lat = (i) => -90 + (i0 + i) * gd, lon = (j) => -180 + (j0 + j) * gd;
    const D = L_.map((l) => { const f = distFn(l), a = new Float32Array(ny * nx); for (let i = 0; i < ny; i++) for (let j = 0; j < nx; j++) a[i * nx + j] = f(lat(i), lon(j)); return a; });
    const w = new Float32Array(ny * nx).fill(L_[0].dx);
    for (let k = 1; k < L_.length; k++) {
      const l = L_[k], p = l.parent, dp = L_[p].dx, s = (dp - l.dx) / l.ramp_km;
      for (let n = 0; n < w.length; n++) { const g = Math.min(dp, Math.max(l.dx, l.dx + s * D[k][n])); if (p === 0 || D[p][n] === 0) w[n] = Math.min(w[n], g); }
    }
    const cellsOf = (i, n) => { const A = Math.pow(gd * Math.PI / 180 * EARTH_KM, 2) * cosd(lat(i)); return A / (HEX * w[n] * w[n]); };
    let total = 0, wmin = Infinity, wmaxIn = 0; const zones = L_.map((l) => ({ name: l.name, dx: l.dx, cells_inside: 0 }));
    for (let i = 0; i < ny; i++) for (let j = 0; j < nx; j++) {
      const n = i * nx + j; if (w[n] < wmin) wmin = w[n];
      if (D[0][n] !== 0) continue;
      const c = cellsOf(i, n); total += c; if (w[n] > wmaxIn) wmaxIn = w[n];
      L_.forEach((l, k) => { if (D[k][n] === 0) zones[k].cells_inside += c; });
    }
    const dy = gd * KM, grads = [];
    for (let i = 0; i < ny - 1; i++) for (let j = 0; j < nx - 1; j++) {
      const n = i * nx + j; if (D[0][n] !== 0) continue;
      grads.push(Math.max(Math.abs(w[n + nx] - w[n]) / dy, Math.abs(w[n + 1] - w[n]) / (dy * cosd(lat(i)))));
    }
    grads.sort((a, b) => a - b);
    const gp = grads.length ? grads[Math.min(grads.length - 1, Math.floor(0.999 * (grads.length - 1)))] : 0, warn = [];
    if (gp > 0.35) warn.push('gradiente de espaciado alto (' + gp.toFixed(2) + ' > 0.35): alguna rampa es muy corta; la malla puede salir de mala calidad');
    if (cfg.smooth_km > 0) warn.push('modo solo diseño: el suavizado no se aplica en esta vista previa');
    // imagen con remuestreo uniforme en y de Mercator
    let vmin = Infinity, vmax = -Infinity; for (let n = 0; n < w.length; n++) { if (w[n] < vmin) vmin = w[n]; if (w[n] > vmax) vmax = w[n]; }
    const my = (la) => Math.log(Math.tan(Math.PI / 4 + Math.max(-85.05, Math.min(85.05, la)) * Math.PI / 360)), minv = (y) => (2 * Math.atan(Math.exp(y)) - Math.PI / 2) * 180 / Math.PI;
    const lat0 = lat(0), lat1 = lat(ny - 1), H = Math.min(900, ny), cv = document.createElement('canvas'); cv.width = nx; cv.height = H;
    const ctx = cv.getContext('2d'), img = ctx.createImageData(nx, H), stops = VIRIDIS_R.map(hex2rgb);
    for (let r = 0; r < H; r++) {
      const y = my(lat1) - (my(lat1) - my(lat0)) * r / (H - 1), ii = Math.max(0, Math.min(ny - 1, Math.round((minv(y) - lat0) / gd)));
      for (let j = 0; j < nx; j++) {
        const n = ii * nx + j, f = (w[n] - vmin) / Math.max(vmax - vmin, 1e-9) * 10, a = Math.min(9, Math.floor(f)), u = f - a, o = (r * nx + j) * 4;
        for (let q = 0; q < 3; q++) img.data[o + q] = Math.round(stops[a][q] * (1 - u) + stops[a + 1][q] * u);
        img.data[o + 3] = D[0][n] === 0 ? 190 : 60;
      }
    }
    ctx.putImageData(img, 0, 0);
    return { summary: { cells_estimated: total, dx_min: wmin, dx_max_region: wmaxIn, dt_hint_s: 6 * wmin, grad_p999: gp, zones, warnings: warn },
      image: cv.toDataURL('image/png'), bounds: [[lat0, lon(0)], [lat1, lon(nx - 1)]], vmin, vmax, colors: VIRIDIS_R.map((c, i) => [i / 10, c]), grid_deg: gd, seconds: Math.round((performance.now() - t0) / 10) / 100 };
  }

  /* ------------------------------------------------------------------ vista previa */
  const api = (p, body) => fetch(p, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}).then((r) => r.json());

  function changed() { clearTimeout(S.timer); S.timer = setTimeout(runPreview, 250); }

  function fmt(n) { return Math.round(n).toLocaleString('es'); }

  function runPreview() {
    const seq = ++S.seq, cfg = cleanCfg();
    if (!cfg.levels.length) { $('sum').innerHTML = '<span class="muted">Sin zonas.</span>'; if (overlay) { map.removeLayer(overlay); overlay = null; } return; }
    const gd = parseFloat($('f-gd').value);
    $('sum').style.opacity = 0.5;
    const getPrev = S.static ? new Promise((res) => { try { res(previewLocal(cfg, isNaN(gd) ? undefined : gd)); } catch (e) { res({ error: String(e.message || e) }); } })
      : api('api/preview', { config: cfg, grid_deg: isNaN(gd) ? undefined : gd });
    getPrev.then((r) => {
      if (seq !== S.seq) return;
      $('sum').style.opacity = 1;
      if (r.error) { $('sum').innerHTML = '<div class="err">' + r.error + '</div>'; return; }
      S.preview = r;
      const s = r.summary, cap = parseFloat($('f-cap').value) || Infinity;
      let h = '<table><tr><td>Celdas estimadas</td><td><b>' + fmt(s.cells_estimated) + '</b></td></tr><tr><td>dx mínimo / máximo en la región</td><td>' + s.dx_min.toFixed(1) + ' / ' + s.dx_max_region.toFixed(1) + ' km</td></tr>' +
        '<tr><td>dt orientativo (6 s/km)</td><td>' + Math.round(s.dt_hint_s) + ' s</td></tr><tr><td>gradiente de espaciado p99.9</td><td>' + s.grad_p999.toFixed(2) + '</td></tr></table>';
      h += '<div class="muted" style="margin-top:4px">Celdas dentro de cada zona (incluye lo que hay en sus hijas):</div><table>' + s.zones.map((z) => '<tr><td>' + z.name + ' (' + z.dx + ' km)</td><td>' + fmt(z.cells_inside) + '</td></tr>').join('') + '</table>';
      if (s.cells_estimated > cap) h += '<div class="err">Supera el tope de ' + fmt(cap) + ' celdas.</div>';
      (s.warnings || []).forEach((w) => { h += '<div class="warn">' + w + '</div>'; });
      h += '<div class="muted">Rejilla de la vista previa ' + r.grid_deg + '° · ' + r.seconds + ' s</div>';
      $('sum').innerHTML = h;
      if (overlay) map.removeLayer(overlay);
      overlay = L.imageOverlay(r.image, r.bounds, { opacity: parseFloat($('f-op').value), interactive: false, zIndex: 300 }).addTo(map);
      $('legend').style.background = 'linear-gradient(to right,' + r.colors.map((c) => c[1]).join(',') + ')';
      $('lg0').textContent = r.vmax.toFixed(0); $('lg1').textContent = r.vmin.toFixed(0);
      $('legend').style.transform = 'scaleX(-1)';
    }).catch((e) => { $('sum').innerHTML = '<div class="err">Sin respuesta del servidor: ' + e + '</div>'; });
  }

  /* ------------------------------------------------------------------ construccion */
  function drawStages() {
    $('stages').innerHTML = STAGES.map((s) => '<label style="display:inline-block;margin-right:8px"><input type="checkbox" class="stg" value="' + s + '" checked> ' + s + '</label>').join('');
  }

  function build() {
    const stages = [...document.querySelectorAll('.stg:checked')].map((x) => x.value).join(',');
    if (!stages) return;
    const cfg = cleanCfg(); $('log').textContent = 'lanzando…'; $('qual').innerHTML = '';
    api('api/build', { config: cfg, stages, nproc: $('f-nproc').value.trim() || undefined }).then((r) => {
      if (r.error) { $('log').textContent = 'ERROR: ' + r.error; return; }
      S.job = r.job; S.jobName = cfg.name; poll();
    });
  }

  function poll() {
    if (!S.job) return;
    api('api/job/' + S.job).then((j) => {
      if (j.error) { $('log').textContent = j.error; return; }
      const lg = $('log'), atEnd = lg.scrollTop + lg.clientHeight >= lg.scrollHeight - 5;
      lg.textContent = '[' + j.status + ' · ' + j.seconds + ' s]\n' + j.log.join('\n'); if (atEnd) lg.scrollTop = lg.scrollHeight;
      if (j.status === 'running') setTimeout(poll, 1500);
      else { S.job = null; if (j.status === 'done') quality(); }
    });
  }

  function quality() {
    api('api/quality?name=' + encodeURIComponent(S.jobName)).then((q) => {
      if (q.error) return;
      const rows = [['celdas', q.cells], ['dx min / mediana / máx (km)', [q.dx_min_km, q.dx_median_km, q.dx_max_km].map((x) => x.toFixed(2)).join(' / ')], ['razón dx vecinas (máx / p99.9)', q.ratio_max.toFixed(2) + ' / ' + q.ratio_p999.toFixed(2)],
        ['celdas no hexagonales', (100 * q.frac_non_hex).toFixed(1) + ' %'], ['meshDensity mín', q.meshDensity_min != null ? q.meshDensity_min.toPrecision(3) : '—'], ['dt orientativo', Math.round(q.dt_hint_s) + ' s']];
      $('qual').innerHTML = '<table class="qt">' + rows.map((r) => '<tr><td>' + r[0] + '</td><td>' + r[1] + '</td></tr>').join('') + '</table>' +
        (q.errors && q.errors.length ? q.errors.map((e) => '<div class="err">' + e + '</div>').join('') : '<div class="ok">Comprobaciones correctas</div>') + (q.warnings || []).map((w) => '<div class="warn">' + w + '</div>').join('');
    });
  }

  function showCells() {
    const name = S.jobName || $('f-name').value.trim();
    api('api/cells?name=' + encodeURIComponent(name) + '&max=30000').then((r) => {
      if (r.error) { alert(r.error); return; }
      cellsG.clearLayers();
      const cv = L.canvas({ padding: 0.3 });
      r.points.forEach((p) => L.circleMarker([p[0], p[1]], { renderer: cv, radius: 1.6, weight: 0, fillOpacity: 0.8, color: '#000', fillColor: '#000' }).bindTooltip(p[2].toFixed(1) + ' km').addTo(cellsG));
      $('qual').insertAdjacentHTML('beforeend', '<div class="muted">Mostrando ' + r.shown + ' de ' + r.n + ' centros de celda</div>');
    });
  }

  /* ------------------------------------------------------------------ eventos */
  $('f-name').oninput = () => { S.jobName = null; };
  $('f-mode').onchange = (e) => setMode(e.target.value);
  $('f-smooth').oninput = changed; $('f-cap').oninput = changed; $('f-gd').oninput = changed;
  $('f-op').oninput = (e) => { if (overlay) overlay.setOpacity(parseFloat(e.target.value)); };
  $('f-tpl').onchange = (e) => { if (e.target.value) { loadCfg(TEMPLATES[e.target.value]); e.target.value = ''; } };
  document.querySelectorAll('#tools button[data-tool]').forEach((b) => { b.onclick = () => setTool(b.dataset.tool); });
  $('b-import').onclick = () => $('file').click();
  $('file').onchange = (e) => { const f = e.target.files[0]; if (!f) return; f.text().then((t) => { try { loadCfg(JSON.parse(t)); } catch (x) { alert('JSON inválido: ' + x); } }); e.target.value = ''; };
  $('b-export').onclick = () => {
    const c = cleanCfg(), a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(c, null, 1)], { type: 'application/json' })); a.download = (c.name || 'malla') + '.json'; a.click();
  };
  $('b-build').onclick = build;
  $('b-stop').onclick = () => { if (S.job) api('api/job/' + S.job + '/cancel', {}); };
  $('b-cells').onclick = showCells; $('b-hide').onclick = () => cellsG.clearLayers();
  function setStatic() {
    S.static = true; $('srvinfo').textContent = 'Sin servidor: modo solo diseño'; $('staticmsg').style.display = 'block';
    ['b-build', 'b-stop', 'b-cells', 'b-hide'].forEach((id) => { $(id).disabled = true; });
    $('log').textContent = 'La construcción necesita mesh_server.py en tu ordenador (ver README). Aquí puedes exportar el JSON.'; changed();
  }
  drawStages(); loadCfg(TEMPLATES.cuba);
  fetch('api/info').then((r) => r.json()).then((i) => { if (!i.workdir) throw new Error('sin servidor'); $('srvinfo').textContent = 'Carpeta de trabajo: ' + i.workdir; }).catch(setStatic);
  window.__S = S;                         // para depurar en la consola
})();
