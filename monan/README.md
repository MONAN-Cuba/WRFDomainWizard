# Constructor interactivo de mallas MONAN

Herramienta para definir y construir mallas MPAS para MONAN: resolución constante o variable, regionales o globales, con zonas rectangulares, elípticas, circulares o poligonales; vista previa, estimación de celdas, métricas de calidad y construcción desde la propia interfaz. Documentación completa: [docs/GUIA.md](docs/GUIA.md). Ejemplos: [examples/](examples/).

Carpeta `monan/` del fork WRFDomainWizard (rama `monan-mesh`). Reutiliza Leaflet y los proveedores de mapas del repo; la lógica de WRF (proyecciones, namelist.wps) no se usa.

## Arranque (en tu ordenador)

    conda activate mpasmesh
    cd monan
    python3 mesh_server.py --workdir ~/mallas --limited-area ~/MPAS-Limited-Area
    # abrir http://127.0.0.1:8765/

El servidor escucha solo en 127.0.0.1. Necesita numpy, scipy, matplotlib y netCDF4; para construir, además `mpas_tools` (JIGSAW), MPAS-Limited-Area y `gpmetis` en el PATH (lo que ya usas con make_mpas_mesh.py). La vista previa no necesita nada más y los mapas base necesitan internet.

## Modo solo diseño (GitHub Pages)

La misma página funciona sin servidor: si no encuentra `mesh_server.py`, calcula la vista previa en el navegador (campo de dx, celdas estimadas, gradiente), permite dibujar, importar y exportar el JSON, y desactiva Construir. Con Pages activo en `main`, está en `https://monan-cuba.github.io/WRFDomainWizard/monan/`. La estimación local coincide con la del servidor dentro de ~1 % en los ejemplos; no aplica el suavizado. La construcción y la calidad real siguen necesitando el servidor local.

## Uso

1. Elegir una plantilla, importar un JSON (p. ej. `examples/cuba_sispi.json`) o dibujar con las herramientas.
2. Modo regional: la primera zona es el recorte; las siguientes refinan. Modo global: el nivel "base" fija el dx de fondo.
3. Arrastrar los tiradores en el mapa o escribir los números; el campo de dx, las celdas estimadas y los avisos se actualizan solos.
4. Exportar JSON (el mismo formato lo lee `make_mpas_mesh.py`) o pulsar Construir (lanza las etapas marcadas; el registro y la calidad salen en el panel). "Mostrar celdas reales" dibuja los centros de la malla construida.

## Línea de comandos (sin interfaz)

    python3 make_mpas_mesh.py --config examples/cuba_sispi.json --preview
    python3 make_mpas_mesh.py --config mi_malla.json --name mi_malla
    python3 tests/test_meshlib.py

Formato del JSON, formas y modo global: ver la cabecera de `meshlib.py`.

## Ejemplos (`examples/`)

| Archivo | Qué es |
| --- | --- |
| `cuba_sispi.json` | Tres cajas anidadas 27/9/3 km imitando d01/d02/d03 de SisPI |
| `regional_constante_15km.json` | Dominio regional con resolución constante |
| `elipses_anidadas.json` | Elipses y un círculo anidados |
| `global_60km_zona_15km.json` | Malla global de 60 km con una zona circular de 15 km |
| `dos_zonas_hermanas.json` | Dos zonas finas independientes (elipse y polígono) dentro de un mismo dominio |

## Origen y licencia

Se apoya en las librerías de mapa de [WRFDomainWizard](https://github.com/jiririchter/WRFDomainWizard) (Leaflet y leaflet-providers, copiadas en `web/lib/`). El proyecto original es GPL-3.0; este fork mantiene esa licencia.
