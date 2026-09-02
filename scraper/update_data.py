#!/usr/bin/env python3
"""
Actualiza data/bookings.json consultando:
  - El sitio de track & trace de cada naviera (ETA vigente)
  - VesselFinder (posición aproximada del buque, cuando hay IMO cargado)

Diseño deliberadamente conservador:
  - Si algo falla (timeout, cambio de layout, bloqueo anti-bot), el script
    NO borra el dato anterior: lo deja como estaba y agrega una nota de error.
  - Nunca inventa una ruta futura completa por su cuenta. Solo actualiza:
      * la ETA informada por la naviera
      * la fecha/posición del paso "actual" en la ruta
      * un changelog en observacion cuando la ETA cambió respecto a la corrida anterior
  - La estructura fina de la ruta (escalas futuras, buques de cada tramo)
    se revisa a mano cuando cambia algo grande (ver README).

Requiere: playwright (con navegadores instalados vía `playwright install chromium`)
"""
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

DATA_PATH = Path(__file__).parent.parent / "data" / "bookings.json"

MESES = {
    "jan": "ene", "feb": "feb", "mar": "mar", "apr": "abr", "may": "may", "jun": "jun",
    "jul": "jul", "aug": "ago", "sep": "sep", "oct": "oct", "nov": "nov", "dec": "dic",
}


def normalizar_fecha_en_a_es(texto):
    """'08-Sep-2026' -> '08-sep-2026'. Deja pasar lo que no matchea."""
    m = re.match(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", texto.strip())
    if not m:
        return texto.strip()
    dd, mmm, yyyy = m.groups()
    mes_es = MESES.get(mmm.lower(), mmm.lower())
    return f"{dd}-{mes_es}-{yyyy}"


async def obtener_texto_pagina(page, url, espera_selector=None, timeout_ms=25000):
    await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
    if espera_selector:
        try:
            await page.wait_for_selector(espera_selector, timeout=timeout_ms)
        except Exception:
            pass
    await page.wait_for_timeout(2500)  # las SPA de Maersk/ZIM tardan en hidratar
    return await page.inner_text("body")


async def scrapear_maersk_eta(page, booking_no):
    """Devuelve la ETA (string 'DD-mmm-YYYY HH:MM') del primer contenedor del booking, o None."""
    url = f"https://www.maersk.com/tracking/{booking_no}"
    texto = await obtener_texto_pagina(page, url, espera_selector="text=Fecha estimada de llegada")
    m = re.search(r"Fecha estimada de llegada\s*\n?\s*(\d{1,2}\s+\w{3}\s+\d{4}\s+\d{2}:\d{2})", texto)
    if not m:
        return None, texto
    fecha_raw = m.group(1)  # "20 Sep 2026 09:00"
    m2 = re.match(r"(\d{1,2})\s+(\w{3})\s+(\d{4})\s+(\d{2}:\d{2})", fecha_raw)
    if not m2:
        return None, texto
    dd, mmm, yyyy, hhmm = m2.groups()
    mes_es = MESES.get(mmm.lower(), mmm.lower())
    return f"{dd}-{mes_es}-{yyyy} {hhmm}", texto


async def scrapear_zim_eta(page, booking_no):
    url = f"https://www.zim.com/tools/track-a-shipment?consnumber={booking_no}"
    texto = await obtener_texto_pagina(page, url, espera_selector="text=Current ETA")
    m = re.search(r"Current ETA\s*\n?\s*(?:Berth\s*\n?)?\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", texto)
    if not m:
        return None, texto
    return normalizar_fecha_en_a_es(m.group(1)), texto


async def scrapear_vesselfinder(page, imo):
    """Devuelve (descripcion_corta, eta_puerto_texto) o (None, None) si no encuentra el patrón."""
    url = f"https://www.vesselfinder.com/?imo={imo}"
    texto = await obtener_texto_pagina(page, url, espera_selector="text=Destination")
    m = re.search(
        r"current position of .*? is at (.*?) reported (.*?) by AIS\. "
        r"The vessel is en route to the port of (.*?), sailing at a speed of ([\d.]+) knots "
        r"and expected to arrive there on (.*?)\.",
        texto, re.S,
    )
    if not m:
        return None
    region, hace, puerto_destino, velocidad, eta = m.groups()
    return {
        "region": region.strip(),
        "reportado_hace": hace.strip(),
        "proximo_puerto": puerto_destino.strip(),
        "velocidad_nudos": velocidad.strip(),
        "eta_texto_original": eta.strip(),
    }


async def procesar_booking(page, buque, booking, hoy_iso, cambios):
    eta_anterior = booking.get("etaInformada", "")
    eta_nueva = None
    error = None

    try:
        if booking["naviera"] == "Maersk":
            eta_nueva, _ = await scrapear_maersk_eta(page, booking["bookingNo"])
        elif booking["naviera"] == "ZIM":
            eta_nueva, _ = await scrapear_zim_eta(page, booking["bookingNo"])
    except Exception as e:
        error = str(e)[:200]

    if error:
        cambios.append(f"[{booking['bookingNo']}] ERROR al consultar naviera: {error}. Se mantiene el dato anterior.")
        return

    if not eta_nueva:
        cambios.append(f"[{booking['bookingNo']}] No se pudo extraer la ETA (posible cambio de diseño en la web de la naviera). Se mantiene el dato anterior.")
        return

    eta_anterior_fecha = eta_anterior.split(" (")[0].strip()
    if eta_anterior_fecha and eta_anterior_fecha != eta_nueva:
        nota = f"[Auto {hoy_iso[:10]}] ETA cambió de {eta_anterior_fecha} a {eta_nueva}."
        booking["observacion"] = (booking.get("observacion", "") + " " + nota).strip()
        booking["riesgo"] = True
        cambios.append(f"[{booking['bookingNo']}] ETA actualizada: {eta_anterior_fecha} → {eta_nueva}")

    # Reemplaza solo la fecha, preserva el sufijo "(Puerto, Terminal)" si existía
    sufijo = ""
    if " (" in eta_anterior:
        sufijo = " (" + eta_anterior.split(" (", 1)[1]
    booking["etaInformada"] = eta_nueva + sufijo

    # Actualiza el paso "destino final" de la ruta del buque, si la fecha cambió
    for paso in buque.get("ruta", []):
        if paso.get("estado") == "futuro" and "Destino final" in paso.get("tipo", ""):
            paso["fecha"] = eta_nueva


async def procesar_vesselfinder(buque, hoy_iso, cambios):
    imo = buque.get("vesselfinder_imo")
    if not imo:
        return
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        try:
            info = await scrapear_vesselfinder(page, imo)
        except Exception as e:
            cambios.append(f"[{buque['nombre']}] ERROR consultando VesselFinder: {str(e)[:200]}")
            info = None
        await browser.close()

    if not info:
        cambios.append(f"[{buque['nombre']}] No se pudo extraer posición de VesselFinder (revisar manualmente).")
        return

    for paso in buque.get("ruta", []):
        if paso.get("estado") == "actual":
            paso["tipo"] = info["region"]

    buque["rumbo"] = f"{info.get('velocidad_nudos','?')} nudos — próx. escala {info['proximo_puerto']} (ETA {info['eta_texto_original']})"
    buque["aisHora"] = f"{info['reportado_hace']} (consulta automática {hoy_iso[:10]})"
    cambios.append(f"[{buque['nombre']}] Posición AIS actualizada: {info['region']}")


def fecha_es_hoy():
    meses = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]
    ahora = datetime.now()
    return f"{ahora.day:02d}-{meses[ahora.month-1]}-{ahora.year}"


async def main():
    if not DATA_PATH.exists():
        print(f"No existe {DATA_PATH}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    hoy_iso = datetime.now(timezone.utc).isoformat()
    hoy_es = fecha_es_hoy()
    cambios = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        for buque in data["buques"]:
            for booking in buque["bookings"]:
                await procesar_booking(page, buque, booking, hoy_iso, cambios)
                # Corrige el paso "actual" de la ruta a la fecha de hoy (formato es)
                for paso in buque.get("ruta", []):
                    if paso.get("estado") == "actual":
                        paso["fecha"] = hoy_es
        await browser.close()

    for buque in data["buques"]:
        await procesar_vesselfinder(buque, hoy_iso, cambios)

    data["ultimaActualizacion"] = hoy_iso

    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== Resumen de esta corrida ===")
    if cambios:
        for c in cambios:
            print("-", c)
    else:
        print("Sin cambios detectados.")

    # Log a archivo para que quede como artifact del workflow
    log_path = Path(__file__).parent.parent / "data" / "ultimo_log.txt"
    log_path.write_text(
        f"Corrida: {hoy_iso}\n" + "\n".join(f"- {c}" for c in cambios) if cambios else f"Corrida: {hoy_iso}\nSin cambios detectados.",
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(main())
