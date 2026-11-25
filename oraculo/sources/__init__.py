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

# ATS y portales de vacantes: nunca son el dominio de la empresa (dominio = null, resolver_manual).
ATS = {
    "workable.com", "lever.co", "greenhouse.io", "ashbyhq.com", "bamboohr.com", "breezy.hr",
    "recruitee.com", "smartrecruiters.com", "jobvite.com", "teamtailor.com", "personio.de",
    "personio.com", "icims.com", "myworkdayjobs.com", "jazzhr.com", "applytojob.com", "pinpointhq.com",
}
# Otros dominios que no identifican a la empresa (bolsas de empleo, correo gratuito, redes).
NO_EMPRESA = ATS | {
    "remoteok.com", "weworkremotely.com", "getonbrd.com", "getonboard.com", "linkedin.com",
    "gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "icloud.com", "live.com",
}
# Subdominios de vacantes: jobs.dropbox.com -> dropbox.com
PREFIJOS_VACANTES = ("jobs", "careers", "boards", "apply", "job", "career")
SUFIJOS_DOBLES = {"co.uk", "com.co", "com.mx", "com.br", "com.ar", "com.pe", "com.uy", "com.ec", "com.ve",
                  "com.au", "co.za", "org.co", "net.co", "com.gt", "com.py", "com.bo", "com.do"}
# Palabras que no sirven para comparar el nombre con el dominio
_GENERICAS = {"inc", "llc", "ltd", "sas", "corp", "group", "the", "technologies", "technology", "tech",
              "labs", "health", "global", "solutions", "company", "software", "digital", "studio", "agency"}


class Batch(list):
    """Lista de señales que además cuenta las vacantes descartadas por motivo."""

    def __init__(self, items=(), descartadas: dict[str, int] | None = None):
        super().__init__(items)
        self.descartadas: dict[str, int] = dict(descartadas or {})

    def descartar(self, motivo: str) -> None:
        self.descartadas[motivo] = self.descartadas.get(motivo, 0) + 1

    def extend_batch(self, other: "Batch") -> None:
        self.extend(other)
        for k, v in other.descartadas.items():
            self.descartadas[k] = self.descartadas.get(k, 0) + v


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


def _host(s: str | None) -> str:
    if not s:
        return ""
    s = s.strip().lower()
    if "@" in s and "/" not in s:
        s = s.split("@", 1)[1]
    if "://" not in s:
        s = "http://" + s
    try:
        return (urlparse(s).hostname or "").removeprefix("www.")
    except ValueError:
        return ""


def registrable(host: str) -> str:
    """'jobs.dropbox.com' -> 'dropbox.com', 'careers.acme.com.co' -> 'acme.com.co'."""
    parts = host.split(".")
    n = 3 if ".".join(parts[-2:]) in SUFIJOS_DOBLES else 2
    return ".".join(parts[-n:])


def normalize_domain(s: str | None) -> str | None:
    """'https://www.Acme.com/jobs' -> 'acme.com'; 'jobs.dropbox.com' -> 'dropbox.com'.
    None si no es un dominio de empresa (ATS, bolsa de empleo, correo gratuito)."""
    host = _host(s)
    if "." not in host:
        return None
    root = registrable(host)
    return None if root in NO_EMPRESA else root


def name_matches_domain(nombre: str | None, dominio: str) -> bool:
    """¿El nombre de la empresa corresponde al dominio? 'Clover Health'~cloverhealth.com, 'I2B'~i2btech.com."""
    etiqueta = slug(dominio.split(".")[0])
    s = slug(nombre)
    if not etiqueta or not s:
        return False
    palabras = [slug(w) for w in ascii_lower(nombre).split() if slug(w)]
    utiles = [w for w in palabras if len(w) >= 3 and w not in _GENERICAS]
    sigla = "".join(w[0] for w in palabras)
    return etiqueta in s or s in etiqueta or any(w in etiqueta for w in utiles) or (len(sigla) >= 2 and sigla == etiqueta)


def company_domain(nombre: str | None, raw: str | None) -> tuple[str | None, str | None]:
    """(dominio, motivo_resolver_manual). Si el dominio es de un ATS o no corresponde al nombre,
    devuelve (None, motivo) para que se resuelva a mano."""
    host = _host(raw)
    if "." not in host:
        return None, "sin dominio"
    root = registrable(host)
    if root in ATS:
        return None, f"dominio de ATS ({host})"
    if root in NO_EMPRESA:
        return None, f"dominio de bolsa o correo genérico ({host})"
    if not name_matches_domain(nombre, root):
        return None, f"el nombre «{nombre}» no coincide con {root}"
    return root, None


def global_filter(ubicacion: str | None, texto: str, icp: dict) -> str | None:
    """Filtro de Remote OK y WWR. Devuelve el motivo de descarte o None si la vacante se queda.
    Se queda si la ubicación o el texto mencionan LATAM/país LATAM/contractor; una ubicación
    global, de otra región o vacía se descarta aunque el texto mencione algo."""
    f = icp["filtro_global"]
    ubicacion = ubicacion or ""
    if matches(ubicacion, f["mencionar"]):
        return None
    if not ubicacion.strip() or matches(ubicacion, f["ubicaciones_descartar"]):
        return "ubicacion_global_o_vacia"
    if matches(texto, f["mencionar"]):
        return None
    return "sin_mencion_latam_contractor"


def company_key(dominio: str | None, empresa: str) -> str:
    return dominio or f"sin-dominio:{slug(empresa)}"


def group_by_domain(signals: list[Signal]) -> dict[str, list[Signal]]:
    """Agrupa por dominio. Una señal sin dominio se une al dominio que otra fuente dio
    para el mismo nombre; si nadie lo dio, queda en 'sin-dominio:<nombre>' para resolverla a mano."""
    nombre_a_dominio: dict[str, str] = {}  # dominios ya validados por cada fuente
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
