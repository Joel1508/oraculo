"""Decisores por dominio con Hunter Domain Search.

dominio -> Hunter (con caché y tope de llamadas) -> filtro por cargo (icp: descubrimiento.palabras_clave)
-> rol (decisor / aprobador / influenciador). Solo correos del dominio de la empresa.
El linkedin_url que devuelve Hunter se guarda solo en crm.csv (ignorado por git).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from .enrich import _ascii, _level, email_status

CACHE = Path("out/.discover_cache.json")   # respuestas completas; git lo ignora
TIMEOUT = 20


def _norm(s: str | None) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in _ascii(s or "").lower()).split())


def _has(text: str, words: list[str]) -> bool:
    t = f" {_norm(text)} "
    return any(f" {_norm(w)} " in t for w in words)


class Budget:
    """Tope de llamadas reales a Hunter por corrida; lo que sale de caché no cuenta."""

    def __init__(self, max_calls: int):
        self.left = max_calls
        self.used = 0


def _cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def domain_search(domain: str, cfg: dict, budget: Budget) -> tuple[list[dict], str]:
    """(correos de Hunter, origen): origen es 'cache', 'hunter' o el motivo de omisión."""
    c = _cache()
    if domain in c:
        return c[domain], "cache"
    if cfg.get("solo_cache"):
        return [], "omitido (solo_cache)"
    key = os.getenv("HUNTER_API_KEY")
    if not key:
        return [], "omitido (sin HUNTER_API_KEY)"
    if budget.left <= 0:
        return [], "omitido (tope de llamadas)"
    budget.left -= 1
    budget.used += 1
    r = requests.get("https://api.hunter.io/v2/domain-search", timeout=TIMEOUT,
                     params={"domain": domain, "api_key": key, "limit": cfg.get("limite", 10), "type": "personal"})
    if r.status_code != 200:
        raise RuntimeError(f"hunter domain-search {domain}: {r.status_code} {r.text[:200]}")
    emails = (r.json().get("data") or {}).get("emails") or []
    c[domain] = emails
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    return emails, "hunter"


def classify(cargo: str, roles: dict) -> str | None:
    for rol, regla in roles.items():
        if _has(cargo, regla.get("alguna", [])):
            return rol
    return None


def _dedupe(emails: list[dict]) -> list[dict]:
    best: dict[str, dict] = {}
    for e in emails:
        k = _norm(f"{e.get('first_name') or ''} {e.get('last_name') or ''}")
        if k and (k not in best or (e.get("confidence") or 0) > (best[k].get("confidence") or 0)):
            best[k] = e
    return list(best.values())


def discover_company(domain: str, cfg: dict, budget: Budget) -> dict:
    emails, origen = domain_search(domain, cfg, budget)
    personas = []
    for e in _dedupe(emails):
        cargo = e.get("position") or ""
        nombre = " ".join(x for x in (e.get("first_name"), e.get("last_name")) if x)
        correo = e.get("value") or ""
        if not nombre or not _has(cargo, cfg["palabras_clave"]) or not correo.lower().endswith("@" + domain):
            continue
        personas.append({
            "nombre": nombre, "cargo": cargo, "rol": classify(cargo, cfg["roles"]) or "influenciador",
            "correo": correo, "email_status": email_status((e.get("verification") or {}).get("status")),
            "puntaje_confianza": e.get("confidence"),
            "confianza": _level(e.get("confidence"), cfg.get("confianza_minima", 70)),
            "linkedin_url": e.get("linkedin"), "fuente": "hunter",
        })
    orden = {r: i for i, r in enumerate(cfg["roles"])}
    estado = {"verified": 0, "accept_all": 1, "unknown": 2}
    personas.sort(key=lambda p: (orden.get(p["rol"], 99), estado[p["email_status"]], -(p["puntaje_confianza"] or 0)))
    return {"dominio": domain, "origen": origen, "encontrados_hunter": len(emails), "personas": personas}
