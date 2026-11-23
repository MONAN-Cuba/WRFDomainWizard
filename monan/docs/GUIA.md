# Guía: constructor de mallas MONAN

## 1. Formato de la configuración (JSON)

```json
{
 "name": "mi_malla",
 "mode": "regional",
 "smooth_km": 0, "smooth_keep": "finest", "grid_deg": 0.05,
 "levels": [ { nivel 0 }, { nivel 1 }, ... ]
}
```

| Campo | Significado |
| --- | --- |
| `name` | Nombre de la malla y de los archivos (letras, números, `_`, `.`, `-`) |
| `mode` | `regional`: el nivel 0 define el recorte. `global`: malla completa; el nivel 0 solo da el `dx` de fondo |
| `smooth_km`, `smooth_keep` | Suavizado gaussiano del campo de `dx` (0 = ninguno); `finest`/`all`/`none` conservan el `dx` exacto de las zonas finas |
| `grid_deg` | Rejilla de cálculo del ancho de celda (0.05 regional, 0.25 global por defecto) |

Campos de cada nivel:

| Campo | Significado |
| --- | --- |
| `name`, `dx` | Nombre y ancho de celda (km) dentro de la zona |
| `shape` | `rect` (por defecto), `stadium`, `ellipse`, `circle`, `polygon`; en modo global el nivel 0 no lleva forma |
| `rect`/`stadium` | `lat: [sur, norte]`, `lon: [oeste, este]`, `corner_radius_frac` (0 recto, 1 estadio) |
| `ellipse` | `center: [lat, lon]`, `semi_axes_km: [a, b]`, `rotation_deg` (antihorario desde el este) |
| `circle` | `center: [lat, lon]`, `radius_km` |
| `polygon` | `points: [[lat, lon], ...]` |
| `ramp_km` | Longitud de la rampa de transición hacia fuera de la zona (obligatorio desde el nivel 1; también vale la lista `ramp_km` global del formato antiguo) |
| `parent` | Índice o nombre del nivel dentro del cual se refina; por defecto el anterior. Usa `"parent": 0` para varias zonas hermanas |

Reglas que valida el programa: un nivel hijo no puede ser más grueso que su padre; `ramp_km` > 0; faltan parámetros de la forma → error claro.

Resolución constante: un solo nivel (regional: caja o forma; global: solo `dx`).

## 2. Línea de comandos (`make_mpas_mesh.py`)

```bash
python3 make_mpas_mesh.py --config examples/cuba_sispi.json --preview            # vista previa y estimación de celdas
python3 make_mpas_mesh.py --config examples/cuba_sispi.json --name cuba_sispi    # todas las etapas
python3 make_mpas_mesh.py --config mi.json --stages rescale,density,partition,check
python3 make_mpas_mesh.py --rescale mi.region.nc
```

Etapas, siempre en este orden: `cellwidth`, `mesh` (JIGSAW), `region` (create_region), `rescale` (esfera unitaria), `density` (meshDensity), `partition` (gpmetis), `check` (calidad; termina con código 1 si hay errores). En modo global no hay `region`: el archivo final es `<name>_global.nc` y `graph.info` se genera desde sus vecinas.
Opciones útiles: `--nproc 8,16`, `--ramps 150,50`, `--smooth-km`, `--corner-frac`, `--grid-deg`, `--limited-area RUTA`, `--density-power`, `--density-ref-pct`, `--density-force`.

Después de `density` hay que regenerar `static.nc` e `init.nc` con `init_atmosphere` antes de correr MONAN.

## 3. Interfaz web (`mesh_server.py`)

Ver `README.md`. Resumen de la API local (solo 127.0.0.1): `POST /api/preview`, `POST /api/build`, `GET /api/job/<id>`, `POST /api/job/<id>/cancel`, `GET /api/quality?name=`, `GET /api/cells?name=&max=`, `GET /api/meshes`, `GET /api/info`.

Dibujo: rectángulo, elipse y círculo se arrastran en el mapa; el polígono se hace con clics y doble clic. Tiradores: rectángulo (4 esquinas + centro), círculo (centro + radio), elipse (centro, semieje `a` que también gira, semieje `b`), polígono (vértices; clic derecho borra). El primer nivel en modo regional es el recorte; los siguientes se asignan como hijos del nivel más fino que contenga su centro.

## 3b. Modo solo diseño

Sin `mesh_server.py` (p. ej. en GitHub Pages) la página calcula la vista previa en JavaScript: mismas formas, rampas y padres; mallado de 0.1° regional o 0.5° global; sin suavizado. Diferencia con el servidor ≤ 1 % en las celdas estimadas de los ejemplos. Construir, calidad y centros de celda reales requieren el servidor.

## 4. Pruebas

```bash
python3 tests/test_meshlib.py      # equivalencia con la versión anterior, formas, constante, global, hermanas, polígono, errores
```

## 5. Estado de verificación

| Pieza | Estado |
| --- | --- |
| `meshlib.py` (cálculo de ancho de celda y formas) | Verificado: 6 pruebas; coincide con `make_mpas_mesh.orig.py` en Cuba (121 894 celdas) |
| Interfaz web (dibujo, tiradores, plantillas, vista previa) | Verificado con un navegador automatizado sin errores de JavaScript; mapas base no vistos |
| Servidor: preview, build (lanzamiento/registro/error), calidad, celdas | Verificado; `rescale`, `density` y `check` probados sobre una malla regional real |
| Etapas `mesh` (JIGSAW), `region` (create_region), `partition` (gpmetis); ruta global completa | **No probadas** (requieren `mpas_tools`) |
| `polygon` | Parcial: distancia por rejilla con error de hasta una celda de la rejilla de cálculo |

## 6. Problemas frecuentes

| Síntoma | Causa | Solución |
| --- | --- | --- |
| `ModuleNotFoundError: mpas_tools` en el panel de construcción | El servidor no corre en el entorno con JIGSAW | `conda activate mpasmesh` antes de `mesh_server.py` |
| `No encuentro .../create_region` | Falta MPAS-Limited-Area | `--limited-area RUTA` (o variable `MPAS_LIMITED_AREA`) |
| La vista previa no aparece | Configuración inválida (mensaje rojo) | Revisar `dx` del hijo, `ramp_km`, parámetros de la forma |
| `HDF5-DIAG ... unable to lock file, errno = 11` al abrir un `.nc` | Otro proceso tiene el archivo abierto en escritura, o el sistema de archivos (disco externo NTFS/exFAT, red) no admite bloqueos | Cerrar el otro proceso (`lsof archivo.nc`) o `export HDF5_USE_FILE_LOCKING=FALSE` antes de arrancar el servidor |
| La etapa `mesh` termina con "Killed" o `MemoryError` | Falta de RAM: JIGSAW mallea toda la esfera | Usar un nodo con más memoria o reducir el tamaño de la malla; ver "Mallas grandes" en `README.md` |
| `gpmetis: command not found` | Falta METIS | `mamba install -c conda-forge metis`, `apt install metis` o `module load` |
| Aviso "gradiente de espaciado alto" | Rampa demasiado corta | Alargar `ramp_km` (regla práctica: ~5× el `dx` del padre) |
