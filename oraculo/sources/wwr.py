"""We Work Remotely: RSS público (https://weworkremotely.com/remote-jobs.rss y feeds por categoría).

El título trae "Empresa: Cargo"; la descripción trae la sede y la URL de la empresa, de donde
sale el dominio. Se conserva el enlace a la vacante en WWR como fuente.
"""
from __future__ import annotations

import re
from email.utils import parsedate_to_datetime

import requests
from defusedxml import ElementTree as ET

from . import TIMEOUT, USER_AGENT, Batch, Signal, company_domain, global_filter, strip_html, vacancy_hits

FEED = "https://weworkremotely.com/remote-jobs.rss"


def _iso(rfc822: str | None) -> str | None:
    try:
        return parsedate_to_datetime(rfc822).isoformat() if rfc822 else None
    except (TypeError, ValueError):
        return None


def parse(xml_text: str, icp: dict) -> Batch:
    out = Batch()
    root = ET.fromstring(xml_text)
    for item in root.iter("item"):
        g = lambda tag: (item.findtext(tag) or "").strip()
        titulo = g("title")
        if ":" not in titulo:
            continue
        empresa, cargo = (x.strip() for x in titulo.split(":", 1))
        desc_html = g("description")
        desc = strip_html(desc_html)
        ubicacion = " ".join(x for x in (g("region"), g("country")) if x)
        texto = " ".join([titulo, g("type"), desc[:5000]])
        motivo = global_filter(ubicacion, texto, icp)
        if motivo:
            out.descartar(motivo)
            continue
        hits = vacancy_hits(texto + " " + ubicacion, icp)
        m = re.search(r"URL:\s*</strong>\s*<a href=\"([^\"]+)\"", desc_html) or re.search(r"URL:\s*(https?://\S+)", desc)
        dominio, resolver = company_domain(empresa, m.group(1) if m else None)
        hq = re.search(r"Headquarters:\s*(.+?)\s+URL:", desc)
        fecha = _iso(g("pubDate"))
        out.append(Signal(
            empresa=empresa, tipo="vacante",
            texto=f"Publicó la vacante remota «{cargo}» ({g('region') or 'sin región'}, {g('type') or 's/d'})",
            fuente="We Work Remotely", url=g("link") or g("guid"), fecha=fecha,
            dominio=dominio, criterio="disparador", valor={"tipo": "vacante", "fecha": fecha},
            datos={"coincidencias": hits, "region": g("region"), "modalidad": g("type"),
                   "sede": hq.group(1) if hq else None, "url_empresa": m.group(1) if m else None,
                   "resolver_manual": resolver},
        ))
    return out


def fetch(cfg: dict, icp: dict) -> Batch:
    out = Batch()
    for feed in cfg.get("feeds") or [FEED]:
        r = requests.get(feed, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        r.raise_for_status()
        out.extend_batch(parse(r.text, icp))
    return out
