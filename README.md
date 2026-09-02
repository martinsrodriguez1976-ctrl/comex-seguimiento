# Seguimiento de Comercio Exterior — Refinería del Centro

Reporte HTML de seguimiento de bookings de comercio exterior (buque, ruta, ETA, riesgos),
con actualización automática diaria a las **10:15 (hora Argentina)** vía GitHub Actions.

## Ver el reporte

Una vez que actives GitHub Pages (ver más abajo), el reporte queda disponible en:

```
https://<tu-usuario>.github.io/<este-repo>/
```

También podés abrir `index.html` localmente, pero necesitás servirlo con un servidor
(no `file://` directo) porque carga los datos desde `data/bookings.json` con `fetch`:

```bash
python3 -m http.server 8000
# abrir http://localhost:8000
```

## Estructura del repo

```
index.html                    → el reporte (HTML/CSS/JS, sin datos hardcodeados)
data/bookings.json            → los datos: buques, rutas, bookings, ETAs
data/ultimo_log.txt           → log de la última corrida del scraper
scraper/update_data.py        → script que actualiza data/bookings.json
scraper/requirements.txt      → dependencias Python del scraper
.github/workflows/update.yml  → el cron que corre el scraper todos los días
```

## Qué actualiza el scraper automáticamente y qué no

**Sí, todos los días:**
- ETA vigente de cada booking (consultando la web de track & trace de Maersk / ZIM).
- Si la ETA cambió respecto al día anterior, agrega una nota con fecha en la
  observación del booking y lo marca en alerta.
- Posición aproximada del buque en VesselFinder — **solo para los buques que tienen
  un IMO cargado en `vesselfinder_imo`** dentro de `data/bookings.json`.

**No, requiere revisión manual:**
- Cambios grandes de ruta (ej. reasignación de buque, escalas nuevas o eliminadas,
  como pasó con el booking 273895250 cuando Maersk reemplazó el tramo Itapoa +
  Cap San Artemissio por Maersk Rubicon + Stephanie C). El script no reconstruye
  la ruta completa solo, porque eso implica interpretar el itinerario y no solo
  leer un dato.
- Cargar un booking nuevo: hay que agregar el objeto correspondiente a mano en
  `data/bookings.json` (ver la estructura de los que ya están cargados como
  ejemplo) y, si tiene IMO conocido, completar `vesselfinder_imo`.

## Por qué es frágil (léase antes de confiar 100% en la automatización)

Maersk, ZIM y VesselFinder son aplicaciones web (no ofrecen una API pública gratuita
para esto), así que el script literalmente simula un navegador y lee el texto de la
página. Si cualquiera de los tres sitios cambia el texto o la estructura de esa
pantalla, el scraper puede dejar de encontrar el dato. Cuando eso pasa:

- El script **no borra ni inventa** el dato anterior — lo deja como estaba.
- Deja constancia del error en `data/ultimo_log.txt` y como nota en el booking.
- Revisá el artifact "log-corrida" de la pestaña *Actions* de GitHub para ver el
  detalle de cada corrida.

## Setup necesario en GitHub (una sola vez)

1. **Permisos de Actions para escribir al repo**: `Settings → Actions → General →
   Workflow permissions` → elegir **"Read and write permissions"**. Sin esto, el
   workflow corre pero no puede commitear los cambios.
2. **GitHub Pages** (opcional, para ver el reporte online): `Settings → Pages` →
   Source: `Deploy from a branch` → Branch: `main` / `/(root)`.
3. El cron corre en UTC; ya está seteado a `15 13 * * *` (13:15 UTC = 10:15 ART).
   Argentina no tiene horario de verano, así que no hace falta tocar esto en el año.

## Correr el scraper a mano

Desde la pestaña **Actions** del repo → workflow "Actualizar seguimiento de comercio
exterior" → **Run workflow**. También podés correrlo local:

```bash
cd scraper
pip install -r requirements.txt
playwright install chromium
python update_data.py
```
