"""Correo corporativo de un decisor con Hunter Email Finder (dominio validado con MX).

Regla de contactos: los contactos solo vienen de Hunter (con su email_status) o de ingreso
manual (contacto_fuente=manual). Nunca se inventan ni se completan correos: si Hunter no
responde, el correo queda vacío. No se buscan teléfonos ni correos personales.
"""
from __future__ import annotations

import json
import os
import unicodedata
from functools import lru_cache
from pathlib import Path

import requests

TIMEOUT = 20
CACHE = Path("out/.enrich_cache.json")   # respuestas completas de Hunter; git lo ignora

# Hunter verification.status -> email_status del CRM
EMAIL_STATUS = {"valid": "verified", "accept_all": "accept_all"}

_SYN = {
    "operaciones": "operations", "operacion": "operations", "ops": "operations",
    "finanzas": "finance", "financiero": "finance", "talento": "people", "personas": "people",
    "humano": "people", "hr": "people", "rrhh": "people", "cofundador": "founder", "cofounder": "founder",
    "fundador": "founder", "head": "lead", "lider": "lead", "director": "director", "directora": "director",
    "gerente": "manager", "vicepresidente": "vp", "chief": "chief",
}
_STOP = {"de", "del", "y", "la", "el", "of", "and", "the", "en", "a"}


def email_status(hunter_status: str | None) -> str:
    return EMAIL_STATUS.get(hunter_status or "", "unknown")


@lru_cache(maxsize=None)
def mx_valid(domain: str) -> list[str]:
    try:
        import dns.resolver
        ans = dns.resolver.resolve(domain, "MX", lifetime=8)
        return sorted(str(r.exchange).rstrip(".") for r in ans)
    except Exception:
        return []


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def _level(score: int | None, minimo: int) -> str:
    if score is None:
        return "desconocida"
    return "alta" if score >= 90 else "media" if score >= minimo else "baja"


def _tokens(cargo: str) -> set[str]:
    words = "".join(c if c.isalnum() else " " for c in _ascii(cargo).lower()).split()
    return {_SYN.get(w, w) for w in words if w not in _STOP and len(w) > 1}


def cargo_coincide(nuestro: str | None, proveedor: str | None) -> bool | None:
    """None si no hay con qué comparar; False si no comparten palabra clave (posible homónimo)."""
    if not nuestro or not proveedor:
        return None
    return bool(_tokens(nuestro) & _tokens(proveedor))


def _cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _cache_save(key: str, val: dict) -> None:
    c = _cache()
    c[key] = val
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")


def hunter_email_finder(nombre: str, domain: str, cfg: dict | None = None, cargo: str | None = None) -> dict:
    """Devuelve {correo, email_status, linkedin_url, cargo_proveedor, confianza, revisar, errores}."""
    cfg = cfg or {}
    key = f"{_ascii(nombre).lower()}@{domain}"
    res = _cache().get(key)
    if res is None:
        res = {"correo": None, "email_status": "unknown", "linkedin_url": None, "cargo_proveedor": None,
               "puntaje_confianza": None, "fuente": None, "errores": []}
        if not mx_valid(domain):
            res["errores"].append(f"{domain}: sin registros MX")
        elif cfg.get("solo_cache"):
            res["errores"].append("solo_cache: no se consultó Hunter")
        elif not os.getenv("HUNTER_API_KEY"):
            res["errores"].append("Sin HUNTER_API_KEY en el entorno")
        else:
            first, *rest = nombre.split()
            r = requests.get("https://api.hunter.io/v2/email-finder", timeout=TIMEOUT, params={
                "domain": domain, "first_name": _ascii(first), "last_name": _ascii(" ".join(rest)),
                "api_key": os.environ["HUNTER_API_KEY"]})
            if r.status_code != 200:
                res["errores"].append(f"hunter {r.status_code}: {r.text[:160]}")
            else:
                d = r.json().get("data") or {}
                correo = d.get("email")
                if correo and not correo.lower().endswith("@" + domain.lower()):
                    correo = None  # solo correos del dominio corporativo
                res.update({"correo": correo, "puntaje_confianza": d.get("score"), "fuente": "hunter",
                            "email_status": email_status((d.get("verification") or {}).get("status")) if correo else "unknown",
                            "linkedin_url": d.get("linkedin_url"), "cargo_proveedor": d.get("position")})
                _cache_save(key, res)
    res = dict(res)
    res["confianza"] = _level(res.get("puntaje_confianza"), cfg.get("confianza_minima", 70))
    res["cargo_coincide"] = cargo_coincide(cargo, res.get("cargo_proveedor"))
    res["revisar"] = []
    if res["cargo_coincide"] is False:
        res["revisar"].append(f"Hunter reporta otro cargo ({res.get('cargo_proveedor')}): posible homónimo")
    if res.get("email_status") == "accept_all":
        res["revisar"].append("El servidor acepta cualquier dirección: correo no verificable")
    return res


def mask(email: str | None) -> str | None:
    if not email or "@" not in email:
        return email
    user, dom = email.split("@", 1)
    return f"{user[0]}***@{dom}"
