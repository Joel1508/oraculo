"""Fuentes de señales. Cada módulo expone fetch(cfg: dict, icp: dict) -> list[Signal]."""
from __future__ import annotations

import html
import re
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

USER_AGENT = "oraculo/0.1 (prospeccion B2B con datos publicos)"
TIMEOUT = 20

# Dominios que nunca identifican a la empresa (bolsas de empleo, correo gratuito, redes).
NO_EMPRESA = {
    "remoteok.com", "weworkremotely.com", "getonbrd.com", "getonboard.com", "linkedin.com",
    "gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "icloud.com", "live.com",
    "lever.co", "greenhouse.io", "boards.greenhouse.io", "workable.com", "ashbyhq.com",
}


@dataclass
class Signal:
    empresa: str                 # nombre visible de la empresa
    tipo: str                    # vacante, ronda, prensa, hecho, perfil
    texto: str
    fuente: str                  # Get on Board, Remote OK, We Work Remotely, Crunchbase...
    url: str | None = None
    fecha: str | None = None     # ISO 8601
    dominio: str | None = None
    criterio: str | None = None  # hecho del ICP al que aporta (contractors_activos, disparador...)
    valor: Any = None
    manual: bool = False         # curada a mano: prevalece sobre las automáticas
    datos: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def ascii_lower(s: str | None) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def slug(s: str | None) -> str:
    return "".join(c for c in ascii_lower(s) if c.isalnum())


def strip_html(s: str | None) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def normalize_domain(s: str | None) -> str | None:
    """'https://www.Acme.com/jobs' -> 'acme.com'. None si no parece un dominio de empresa."""
    if not s:
        return None
    s = s.strip().lower()
    if "@" in s and "/" not in s:
        s = s.split("@", 1)[1]
    if "://" not in s:
        s = "http://" + s
    host = (urlparse(s).hostname or "").removeprefix("www.")
    if "." not in host or host in NO_EMPRESA:
        return None
    return host


def company_key(dominio: str | None, empresa: str) -> str:
    return dominio or f"sin-dominio:{slug(empresa)}"


def group_by_domain(signals: list[Signal]) -> dict[str, list[Signal]]:
    """Agrupa por dominio. Una señal sin dominio se une al dominio que otra fuente dio
    para el mismo nombre; si nadie lo dio, queda en 'sin-dominio:<nombre>' para resolverla a mano."""
    nombre_a_dominio: dict[str, str] = {}
    for s in signals:
        d = normalize_domain(s.dominio)
        if d:
            nombre_a_dominio.setdefault(slug(s.empresa), d)
    grupos: dict[str, list[Signal]] = defaultdict(list)
    for s in signals:
        d = normalize_domain(s.dominio) or nombre_a_dominio.get(slug(s.empresa))
        grupos[company_key(d, s.empresa)].append(s)
    return dict(grupos)


def matches(texto: str, palabras: list[str]) -> list[str]:
    """Palabras (o frases) de la lista que aparecen completas en el texto, sin tildes ni mayúsculas."""
    t = " " + " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_lower(texto)).split()) + " "
    out = []
    for p in palabras:
        q = " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_lower(p)).split())
        if q and f" {q} " in t:
            out.append(p)
    return out


def vacancy_hits(texto: str, icp: dict) -> dict[str, list[str]]:
    f = icp.get("filtros_vacantes") or {}
    return {"latam": matches(texto, f.get("latam", [])), "contractor": matches(texto, f.get("contractor", []))}
