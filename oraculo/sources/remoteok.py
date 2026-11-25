"""Remote OK: API JSON pública (https://remoteok.com/api).

Términos de la API: enlazar a la URL de la vacante en Remote OK (enlace con follow) y
mencionar "Remote OK" como fuente. No usar su logo. Por eso cada señal guarda la URL original
de Remote OK y fuente="Remote OK", y la demo/README los muestran.

Filtro (icp: filtro_global): se queda si la ubicación o el texto mencionan LATAM / Latin America /
South America / Colombia / México / Argentina / Chile / Perú / contractor. Ubicación global
(Worldwide, USA only...) o vacía: se descarta. Remote OK no trae el dominio de la empresa:
esas empresas quedan con resolver_manual.
"""
from __future__ import annotations

import requests

from . import TIMEOUT, USER_AGENT, Batch, Signal, global_filter, strip_html, vacancy_hits

URL = "https://remoteok.com/api"
ATRIBUCION = "Vacante publicada en Remote OK (https://remoteok.com)"


def parse(items: list[dict], icp: dict) -> Batch:
    out = Batch()
    for it in items:
        if "legal" in it or not it.get("company") or not it.get("position"):
            continue  # el primer elemento es el aviso legal
        texto = " ".join([it["position"], " ".join(it.get("tags") or []), strip_html(it.get("description"))[:5000]])
        motivo = global_filter(it.get("location"), texto, icp)
        if motivo:
            out.descartar(motivo)
            continue
        hits = vacancy_hits(texto + " " + (it.get("location") or ""), icp)
        fecha = it.get("date")
        out.append(Signal(
            empresa=it["company"].strip(), tipo="vacante",
            texto=f"Publicó la vacante remota «{it['position']}» ({it.get('location') or 'sin ubicación'})",
            fuente="Remote OK", url=it.get("url"), fecha=fecha,
            criterio="disparador", valor={"tipo": "vacante", "fecha": fecha},
            datos={"coincidencias": hits, "ubicacion": it.get("location"), "atribucion": ATRIBUCION,
                   "resolver_manual": "Remote OK no publica el dominio de la empresa"},
        ))
    return out


def fetch(cfg: dict, icp: dict) -> Batch:
    r = requests.get(cfg.get("url", URL), headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    r.raise_for_status()
    return parse(r.json(), icp)
