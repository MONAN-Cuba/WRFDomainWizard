# Constructor interactivo de mallas MONAN

Herramienta para definir y construir mallas MPAS para MONAN: resolución constante o variable, regionales o globales, con zonas rectangulares, elípticas, circulares o poligonales; vista previa, estimación de celdas, métricas de calidad y construcción desde la propia interfaz. Documentación completa: [docs/GUIA.md](docs/GUIA.md). Ejemplos: [examples/](examples/).

Carpeta `monan/` del fork WRFDomainWizard (rama `monan-mesh`). Reutiliza Leaflet y los proveedores de mapas del repo; la lógica de WRF (proyecciones, namelist.wps) no se usa.

## Cómo se reparte el trabajo

| Qué quieres hacer | Dónde | Qué necesitas |
| --- | --- | --- |
| Diseñar la malla (zonas, resoluciones), ver la estimación de celdas y **exportar el JSON** | Navegador, en la página de GitHub Pages (sin instalar nada) | Internet para los mapas base |
| Lo mismo, y además **construir** desde la interfaz | Tu ordenador, con `mesh_server.py` | El entorno de la sección siguiente |
| **Construir mallas grandes** (millones de celdas) | Un nodo con mucha RAM o el cluster, por línea de comandos | El mismo entorno; el JSON viene de la página |

JIGSAW (etapa `mesh`) mallea la **esfera completa**, así que necesita bastante más memoria que la propia región; con mallas de más de un millón de celdas en la región, un portátil se queda sin RAM. Para esos casos el flujo recomendado es diseñar en la página, exportar el JSON y correr `make_mpas_mesh.py` donde haya memoria (ver "Mallas grandes").

## Instalación del entorno (una vez)

`make_mpas_mesh.py` y la construcción desde la interfaz necesitan, además de Python: JIGSAW (lo trae `mpas_tools`), `gpmetis` y el repositorio MPAS-Limited-Area. Con conda/mamba:

    mamba create -n mpasmesh -c conda-forge python=3.11 numpy scipy matplotlib netcdf4 mpas_tools jigsaw jigsawpy metis
    conda activate mpasmesh
    git clone https://github.com/MPAS-Dev/MPAS-Limited-Area.git ~/MPAS-Limited-Area

Comprobar que todo responde:

    python -c "import mpas_tools, jigsawpy, netCDF4, scipy; print('python ok')"
    which jigsaw gpmetis
    python ~/MPAS-Limited-Area/create_region --help

- **JIGSAW**: `mpas_tools` lo usa a través de `jigsawpy` y del ejecutable `jigsaw`; ambos vienen de conda-forge. Si `import jigsawpy` falla o falta `jigsaw` en el PATH, la etapa `mesh` no funcionará.
- **gpmetis** (partición para MPI): en conda-forge lo da el paquete `metis`; si en tu sistema no aparece, `sudo apt install metis` (Debian/Ubuntu) o `module load metis` en un cluster. Sin él, la etapa `partition` falla; el resto no.
- **MPAS-Limited-Area**: no es un paquete, es un repositorio con el script `create_region`. Pásalo con `--limited-area RUTA` (o la variable `MPAS_LIMITED_AREA`; en `mesh_server.py`, con `--limited-area`).
- **init_atmosphere_model** (de MONAN) no hace falta para construir la malla; se usa después, para `static.nc`, `init.nc` y las condiciones de frontera.
- En un cluster sin acceso a internet desde los nodos de cómputo, crea el entorno en el nodo de login y llámalo desde el script de envío.

*Estado*: estas órdenes están escritas a partir de lo que usa el código (`make_mpas_mesh.py`, `mesh_server.py`) y de la documentación de los paquetes; **no las he ejecutado en un entorno limpio**. Si un nombre de paquete cambia en conda-forge, el mensaje de error de `mamba` lo dirá.

## Arranque de la interfaz en tu ordenador

    conda activate mpasmesh
    cd monan
    python3 mesh_server.py --workdir ~/mallas --limited-area ~/MPAS-Limited-Area
    # abrir http://127.0.0.1:8765/

El servidor escucha solo en 127.0.0.1. La vista previa no necesita más que numpy, scipy, matplotlib y netCDF4; Construir necesita lo de la sección anterior. Si el servidor está en otra máquina (por ejemplo, un nodo del cluster), abre un túnel desde tu ordenador y usa el navegador local: `ssh -L 8765:127.0.0.1:8765 usuario@servidor` (no probado).

## Modo solo diseño (GitHub Pages) y exportar el JSON

La misma página funciona sin servidor: si no encuentra `mesh_server.py`, calcula la vista previa en el navegador (campo de dx, celdas estimadas, gradiente), permite dibujar e importar, y desactiva Construir (aparece el aviso "solo diseño"). Con Pages activo en `main`, está en `https://monan-cuba.github.io/WRFDomainWizard/monan/`. La estimación local coincide con la del servidor dentro de ~1 % en los ejemplos; no aplica el suavizado, así que para la cifra definitiva usa `--preview` (abajo).

Para llevarte el diseño: pulsa **Exportar JSON**; el navegador descarga `<nombre>.json` (el campo "nombre" de la página es el nombre del archivo). Ese mismo archivo lo lee `make_mpas_mesh.py --config`, y se puede volver a cargar con **Importar JSON** para seguir editándolo.

## Mallas grandes: construir fuera del navegador

1. Diseña en la página y exporta el JSON; cópialo al servidor o cluster (`scp malla.json usuario@host:~/mallas/`).
2. Con el entorno activo, mira antes el tamaño (segundos, sin JIGSAW): `python3 make_mpas_mesh.py --config malla.json --preview`. Da las celdas estimadas en la región y una figura.
3. Lanza la construcción detrás de `nohup` (o en un trabajo del gestor de colas) y guarda el registro:

       nohup python3 make_mpas_mesh.py --config malla.json --name mi_malla --nproc 64,128 \
             --limited-area ~/MPAS-Limited-Area > mi_malla.log 2>&1 &

   Las etapas se pueden repartir: `--stages cellwidth,mesh` (la que más memoria pide), luego `--stages region,rescale,density,partition,check`. Cada etapa deja sus archivos, así que se puede reanudar sin repetir JIGSAW.
4. Si la etapa `mesh` muere sin mensaje ("Killed") o con `MemoryError`, es falta de RAM: usa un nodo con más memoria o reduce el tamaño (por ejemplo, aumenta el `dx` más fino o recorta la región). Una prueba barata es repetir la misma configuración con todos los `dx` multiplicados por 3 y cronometrar con `time`; da una idea del coste sin esperar la malla completa.
5. Termina con `check`, que acaba con código 1 si algo está mal. Después, `init_atmosphere` (casos 7 y 9) y la corrida de MONAN. Los archivos que se llevan son `<nombre>.region.nc` y `<nombre>.graph.info.part.<N>`.

*Estado*: el flujo local con la interfaz y el modo solo diseño están probados (ver `docs/GUIA.md`); las etapas `mesh`, `region` y `partition` y el flujo en cluster **no están probados desde esta herramienta**, y los tiempos y memoria de JIGSAW para mallas de ~1 M de celdas no están medidos.

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
