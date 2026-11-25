"""Puntaje 0-100 según icp/*.yaml. Explica cada punto.

Entrada: hechos = {clave: {"valor": ..., "fuente": "...", "url": "..."}}. Las claves son las de
los criterios (contractors_activos, metodo_pago, acceso, disparador, vertical) más los hechos
auxiliares: paises (lista), entidad_extranjera (bool), empleados (int), etapa (str),
plan_contratacion (bool) y los descalificadores (solo_eor, fuerza_laboral_domestica, en_crm_ontop...).

Reglas:
- Un criterio sin dato vale null: suma 0 y aparece en 'desconocidos'.
- Un descalificador deja el puntaje en 0, sin tier, con su motivo (el desglose se conserva).
- 'cobertura' = suma de pesos de los criterios conocidos (0-100).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from .sources import Signal, ascii_lower, slug


# ---------- utilidades ----------

def norm_key(v: Any) -> str:
    """'Serie A' -> 'serie_a', 'HR-tech' -> 'hr_tech', 'Deel insatisfecho' -> 'deel_insatisfecho'."""
    s = "_".join("".join(c if c.isalnum() else " " for c in ascii_lower(str(v))).split())
    return s.replace("series_", "serie_")


def _fecha(s: Any) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _known(h: dict | None) -> bool:
    return bool(h) and h.get("valor") is not None and h.get("valor") != "" and h.get("valor") != []


def _ev(clave: str, h: dict) -> dict:
    return {"dato": clave, "valor": h.get("valor"), "fuente": h.get("fuente"), "url": h.get("url")}


def _truthy(v: Any) -> bool:
    return v is True or norm_key(v) in ("si", "true", "1", "yes")


# ---------- evaluadores por tipo de criterio ----------
# Cada uno devuelve (puntos | None, valor mostrado, evidencias, nota)

def _rangos(c, H, hoy):
    h = H.get(c["clave"])
    if not _known(h):
        return None, None, [], None
    try:
        n = int(h["valor"])
    except (TypeError, ValueError):
        return None, h["valor"], [_ev(c["clave"], h)], f"valor no numérico: {h['valor']!r}"
    pts = next((r["puntos"] for r in c["rangos"] if n >= r["min"] and ("max" not in r or n <= r["max"])), 0)
    return pts, n, [_ev(c["clave"], h)], None


def _opciones(c, H, hoy):
    h = H.get(c["clave"])
    if not _known(h):
        return None, None, [], None
    v = norm_key(h["valor"])
    if v not in c["opciones"]:
        return None, v, [_ev(c["clave"], h)], f"valor no reconocido: {h['valor']!r}"
    pts = c["opciones"][v]
    nota = "seguimiento: usa un proveedor con el que está satisfecho" if v in c.get("seguimiento", []) else None
    return pts, v, [_ev(c["clave"], h)], nota


def _disparador(c, H, hoy):
    h = H.get(c["clave"])
    if not _known(h):
        return None, None, [], None
    v = h["valor"] if isinstance(h["valor"], dict) else {"tipo": h["valor"]}
    tipo, f = norm_key(v.get("tipo")), _fecha(v.get("fecha"))
    ventana = (c.get("ventana_dias") or {}).get(tipo)
    if ventana and f and 0 <= (hoy - f).days <= ventana:
        return c["puntos_reciente"], f"{tipo} {f.isoformat()}", [_ev(c["clave"], h)], f"{(hoy - f).days} días"
    nota = "sin fecha" if not f else f"{(hoy - f).days} días (fuera de ventana)" if ventana else "señal débil"
    return c["puntos_viejo_o_debil"], f"{tipo} {f.isoformat() if f else ''}".strip(), [_ev(c["clave"], h)], nota


def _paises(c, H, hoy):
    hp, he = H.get("paises"), H.get("entidad_extranjera")
    if not _known(hp) and not _known(he):
        return None, None, [], None
    pts, ev, partes = 0, [], []
    if _known(he):
        ev.append(_ev("entidad_extranjera", he))
        if _truthy(he["valor"]):
            pts += c["puntos_entidad_extranjera"]
            partes.append("entidad extranjera paga a talento LATAM")
    paises = []
    if _known(hp):
        paises = hp["valor"] if isinstance(hp["valor"], list) else [p for p in str(hp["valor"]).replace(",", ";").split(";")]
        paises = [p.strip() for p in paises if p.strip()]
        ev.append(_ev("paises", hp))
        if len(paises) >= c["min_paises"]:
            pts += c["puntos_multi_pais"]
            partes.append(f"{len(paises)} países")
    nota = None if _known(he) else "falta confirmar si es entidad extranjera"
    return pts, ", ".join(paises) or ("; ".join(partes) or None), ev, nota


def _categorias(c, H, hoy):
    h = H.get(c["clave"])
    if not _known(h):
        return None, None, [], None
    v = norm_key(h["valor"])
    for g in c["grupos"]:
        if v in {norm_key(x) for x in g["valores"]}:
            return g["puntos"], v, [_ev(c["clave"], h)], None
    return c.get("resto", 0), v, [_ev(c["clave"], h)], None


def _tamano_etapa(c, H, hoy):
    he, hs = H.get("empleados"), H.get("etapa")
    if not _known(he) and not _known(hs):
        return None, None, [], None
    ev = [_ev(k, h) for k, h in (("empleados", he), ("etapa", hs)) if _known(h)]
    etapa = norm_key(hs["valor"]) if _known(hs) else None
    try:
        emp = int(he["valor"]) if _known(he) else None
    except (TypeError, ValueError):
        emp = None
    valor = " / ".join(x for x in (f"{emp} empleados" if emp is not None else None, etapa) if x)
    if etapa in c["etapas_tardias"]:
        return c["puntos_otro"], valor, ev, "Serie D o posterior"
    if etapa in c["etapas_medias"] or (emp is not None and emp >= c["empleados_min"]):
        return c["puntos_medio"], valor, ev, None
    return c["puntos_otro"], valor, ev, None


EVALUADORES = {"rangos": _rangos, "opciones": _opciones, "disparador": _disparador,
               "paises": _paises, "categorias": _categorias, "tamano_etapa": _tamano_etapa}


def _max(c: dict) -> int:
    t = c["tipo"]
    if t == "rangos":
        return max(r["puntos"] for r in c["rangos"])
    if t == "opciones":
        return max(v for v in c["opciones"].values() if v is not None)
    if t == "disparador":
        return c["puntos_reciente"]
    if t == "paises":
        return c["puntos_entidad_extranjera"] + c["puntos_multi_pais"]
    if t == "categorias":
        return max(g["puntos"] for g in c["grupos"])
    if t == "tamano_etapa":
        return max(c["puntos_medio"], c["puntos_otro"])
    raise ValueError(f"tipo de criterio desconocido: {t}")


def validate_icp(icp: dict) -> list[str]:
    errs = []
    total = sum(c["peso"] for c in icp["criterios"])
    if total != 100:
        errs.append(f"los pesos suman {total}, no 100")
    for c in icp["criterios"]:
        if c["tipo"] not in EVALUADORES:
            errs.append(f"{c['clave']}: tipo {c['tipo']} no existe")
        elif _max(c) != c["peso"]:
            errs.append(f"{c['clave']}: máximo de puntos {_max(c)} != peso {c['peso']}")
    return errs


# ---------- hechos ----------

def facts_from_signals(signals: list[Signal]) -> dict[str, dict]:
    """Un hecho por criterio: primero lo curado a mano, después lo más reciente."""
    cands: dict[str, list[Signal]] = {}
    for s in signals:
        if s.criterio and s.valor is not None:
            cands.setdefault(s.criterio, []).append(s)
    out = {}
    for k, ss in cands.items():
        best = sorted(ss, key=lambda s: (s.manual, s.fecha or ""), reverse=True)[0]
        out[k] = {"valor": best.valor, "fuente": best.fuente, "url": best.url, "fecha": best.fecha}
    return out


def derive(H: dict[str, dict]) -> dict[str, dict]:
    H = dict(H)
    c, p = H.get("contractors_activos"), H.get("plan_contratacion")
    if "sin_contractors_ni_plan" not in H and _known(c) and _known(p):
        try:
            sin = int(c["valor"]) == 0 and not _truthy(p["valor"])
        except (TypeError, ValueError):
            sin = False
        if sin:
            H["sin_contractors_ni_plan"] = {"valor": True, "fuente": f"{c.get('fuente')} + {p.get('fuente')}",
                                            "url": c.get("url") or p.get("url")}
    return H


def disqualify(H: dict[str, dict], icp: dict) -> list[dict]:
    out = []
    for d in icp.get("descalificadores", []):
        h = H.get(d["clave"])
        if not _known(h):
            continue
        hit = _truthy(h["valor"]) if isinstance(d["si"], bool) else norm_key(h["valor"]) == norm_key(d["si"])
        if hit:
            out.append({"clave": d["clave"], "motivo": d["motivo"], "fuente": h.get("fuente"), "url": h.get("url")})
    return out


def is_excluded(nombre: str | None, dominio: str | None, icp: dict) -> str | None:
    """Motivo de exclusión, o None. 'excluir' compara nombre o dominio; 'excluir_grandes' solo nombre."""
    etiqueta = slug((dominio or "").split(".")[0]) if dominio else ""
    if slug(nombre) in {slug(x) for x in icp.get("excluir", [])} or etiqueta in {slug(x) for x in icp.get("excluir", [])}:
        return "En la lista 'excluir' de la ICP"
    grandes = icp.get("excluir_grandes") or {}
    if slug(nombre) in {slug(x) for x in grandes.get("empresas", [])}:
        return grandes.get("motivo", "En la lista 'excluir_grandes' de la ICP")
    return None


def tier(puntaje: int, icp: dict) -> str | None:
    for t in sorted(icp["tiers"], key=lambda t: -t["min"]):
        if puntaje >= t["min"]:
            return t["nombre"]
    return None


# ---------- puntaje ----------

def score(hechos: dict[str, dict], icp: dict, hoy: date | None = None) -> dict:
    hoy = hoy or date.today()
    H = derive(hechos)
    conocidos, desconocidos, total, cobertura, seguimiento = [], [], 0, 0, False
    for c in icp["criterios"]:
        pts, valor, ev, nota = EVALUADORES[c["tipo"]](c, H, hoy)
        if pts is None:
            desconocidos.append({"criterio": c["clave"], "nombre": c["nombre"], "max": c["peso"],
                                 "puntos": 0, "nota": nota or "sin dato"})
            continue
        total += pts
        cobertura += c["peso"]
        seguimiento |= bool(nota and nota.startswith("seguimiento"))
        conocidos.append({"criterio": c["clave"], "nombre": c["nombre"], "valor": valor, "puntos": pts,
                          "max": c["peso"], "fuentes": ev, "nota": nota})
    motivos = disqualify(H, icp)
    puntaje = 0 if motivos else total
    return {
        "puntaje": puntaje, "tier": None if motivos else tier(puntaje, icp),
        "descalificado": bool(motivos), "motivos": motivos,
        "puntaje_sin_descalificar": total, "cobertura": cobertura, "seguimiento": seguimiento,
        "conocidos": conocidos, "desconocidos": desconocidos,
    }
