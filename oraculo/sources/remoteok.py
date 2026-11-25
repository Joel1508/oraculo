"""Remote OK: API JSON pública (https://remoteok.com/api).

Términos de la API: enlazar a la URL de la vacante en Remote OK (enlace con follow) y
mencionar "Remote OK" como fuente. No usar su logo. Por eso cada señal guarda la URL original
de Remote OK y fuente="Remote OK", y la demo/README los muestran.

Filtro: vacantes que mencionan LATAM/Colombia/otro país LATAM o modalidad contractor/freelance.
"""
from __future__ import annotations

import requests

from . import TIMEOUT, USER_AGENT, Signal, strip_html, vacancy_hits

URL = "https://remoteok.com/api"
ATRIBUCION = "Vacante publicada en Remote OK (https://remoteok.com)"


def parse(items: list[dict], icp: dict) -> list[Signal]:
    out = []
    for it in items:
        if "legal" in it or not it.get("company") or not it.get("position"):
            continue  # el primer elemento es el aviso legal
        texto = " ".join([it["position"], it.get("location") or "", " ".join(it.get("tags") or []),
                          strip_html(it.get("description"))[:5000]])
        hits = vacancy_hits(texto, icp)
        if not (hits["latam"] or hits["contractor"]):
            continue
        fecha = it.get("date")
        out.append(Signal(
            empresa=it["company"].strip(), tipo="vacante",
            texto=f"Publicó la vacante remota «{it['position']}» ({it.get('location') or 'sin ubicación'})",
            fuente="Remote OK", url=it.get("url"), fecha=fecha,
            criterio="disparador", valor={"tipo": "vacante", "fecha": fecha},
            datos={"coincidencias": hits, "ubicacion": it.get("location"), "atribucion": ATRIBUCION},
        ))
    return out


def fetch(cfg: dict, icp: dict) -> list[Signal]:
    r = requests.get(cfg.get("url", URL), headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    r.raise_for_status()
    return parse(r.json(), icp)
