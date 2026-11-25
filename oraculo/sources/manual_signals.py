"""Señales curadas a mano desde un JSON. Aquí entran YC, Crunchbase, Clutch, Wellfound,
Fedesoft y prensa: se leen en el navegador y se registran con su URL; nunca se hace scraping.

Formato (ver data/manual_signals.example.json):
{"empresas": [{"nombre": "...", "dominio": "acme.com",
               "senales": [{"tipo": "ronda", "texto": "...", "fuente": "Crunchbase",
                            "url": "https://...", "fecha": "2026-09-01",
                            "criterio": "disparador", "valor": {"tipo": "ronda", "fecha": "2026-09-01"}}]}]}

Cada señal exige 'url' (http/https). Sin URL, la carga falla e indica cuál señal la tiene vacía.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import Signal


def load(cfg: dict) -> dict:
    p = Path(cfg.get("archivo", "data/manual_signals.json"))
    if not p.exists():
        return {"empresas": []}
    return json.loads(p.read_text(encoding="utf-8"))


def parse(doc: dict) -> list[Signal]:
    out: list[Signal] = []
    errores = []
    for e in doc.get("empresas", []):
        for i, s in enumerate(e.get("senales", [])):
            url = (s.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                errores.append(f"{e.get('nombre')} señal #{i + 1} ({s.get('fuente')}): falta la URL de la fuente")
                continue
            if not s.get("fuente"):
                errores.append(f"{e.get('nombre')} señal #{i + 1}: falta el nombre de la fuente")
                continue
            out.append(Signal(
                empresa=e["nombre"], tipo=s.get("tipo", "hecho"), texto=s.get("texto", ""),
                fuente=s["fuente"], url=url, fecha=s.get("fecha"), dominio=e.get("dominio"),
                criterio=s.get("criterio"), valor=s.get("valor"), manual=True,
            ))
    if errores:
        raise ValueError("Señales manuales sin fuente verificable:\n  " + "\n  ".join(errores))
    return out


def fetch(cfg: dict, icp: dict) -> list[Signal]:
    return parse(load(cfg))
