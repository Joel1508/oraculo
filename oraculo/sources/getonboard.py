"""Get on Board: API pública v0 (https://www.getonbrd.com/api/v0, documentación en api-doc.getonbrd.com).

Usa GET /search/jobs?query=...&expand[]=company. Se quedan las vacantes remotas con modalidad
freelance (id 3) o que mencionan contractor/freelance/LATAM. La empresa trae su web (dominio) y
su país: si la sede está fuera de LATAM, la vacante es evidencia de entidad extranjera
contratando talento LATAM (hecho 'entidad_extranjera' del criterio paises).
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from . import TIMEOUT, USER_AGENT, Signal, company_domain, strip_html, vacancy_hits

BASE = "https://www.getonbrd.com/api/v0/search/jobs"
FREELANCE = "3"
LATAM_ISO = {"AR", "BO", "BR", "CL", "CO", "CR", "CU", "DO", "EC", "SV", "GT", "HN", "MX",
             "NI", "PA", "PY", "PE", "PR", "UY", "VE"}


def parse(jobs: list[dict], icp: dict) -> list[Signal]:
    out = []
    for j in jobs:
        a = j.get("attributes") or {}
        if not a.get("remote"):
            continue
        comp = ((a.get("company") or {}).get("data") or {}).get("attributes") or {}
        if not comp.get("name"):
            continue
        modalidad = str(((a.get("modality") or {}).get("data") or {}).get("id") or "")
        texto = " ".join(strip_html(a.get(k)) for k in ("title", "description", "projects", "benefits", "functions"))
        texto += " " + strip_html(comp.get("projects")) + " " + " ".join(a.get("countries") or [])
        hits = vacancy_hits(texto, icp)
        if modalidad != FREELANCE and not (hits["latam"] or hits["contractor"]):
            continue
        fecha = (datetime.fromtimestamp(a["published_at"], timezone.utc).isoformat()
                 if a.get("published_at") else None)
        url = (j.get("links") or {}).get("public_url")
        dominio, resolver = company_domain(comp["name"], comp.get("web"))
        datos = {"coincidencias": hits, "modalidad": "freelance" if modalidad == FREELANCE else modalidad,
                 "pais_empresa": comp.get("country"), "remote_modality": a.get("remote_modality"),
                 "url_empresa": comp.get("web"), "resolver_manual": resolver}
        out.append(Signal(
            empresa=comp["name"].strip(), tipo="vacante",
            texto=f"Publicó la vacante remota «{a.get('title')}» en Get on Board"
                  + (" (freelance)" if modalidad == FREELANCE else ""),
            fuente="Get on Board", url=url, fecha=fecha, dominio=dominio,
            criterio="disparador", valor={"tipo": "vacante", "fecha": fecha}, datos=datos,
        ))
        pais = (comp.get("country") or "").upper()
        if pais and pais not in LATAM_ISO:
            out.append(Signal(
                empresa=comp["name"].strip(), tipo="hecho",
                texto=f"Empresa con sede en {pais} publica vacante remota para talento LATAM en Get on Board",
                fuente="Get on Board", url=url, fecha=fecha, dominio=dominio,
                criterio="entidad_extranjera", valor=True,
            ))
    return out


def fetch(cfg: dict, icp: dict) -> list[Signal]:
    vistos, jobs = set(), []
    for q in cfg.get("consultas") or ["contractor", "freelance", "latam"]:
        for page in range(1, int(cfg.get("max_paginas", 2)) + 1):
            r = requests.get(BASE, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT},
                             params={"query": q, "per_page": cfg.get("por_pagina", 50), "page": page,
                                     "expand[]": "company"})
            r.raise_for_status()
            d = r.json()
            for j in d.get("data", []):
                if j.get("id") not in vistos:
                    vistos.add(j.get("id"))
                    jobs.append(j)
            if page >= (d.get("meta") or {}).get("total_pages", 1):
                break
    return parse(jobs, icp)
