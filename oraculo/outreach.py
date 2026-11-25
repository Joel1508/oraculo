"""Primer mensaje de diagnóstico: parte de la señal concreta, no ataca al proveedor actual.

Chequeos de la casa (Ley 2300 de 2023 y Ley 1581 de 2012, aplicados también en B2B):
canal corporativo, remitente identificado, forma clara de darse de baja, motivo del contacto,
sin presión, máximo una vez por semana por canal, lunes a viernes 7:00-19:00 y sábados
8:00-15:00 (Bogotá), nunca domingos ni festivos.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .sources import matches

TZ = ZoneInfo("America/Bogota")
PRESION = ("urgente", "ultimo aviso", "último aviso", "solo hoy", "embargo", "reportado")
NEGATIVO = ("malo", "mala", "caro", "costoso", "peor", "problemas con", "deficiente", "lento")


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def next_send_slot(cfg: dict, now: datetime | None = None) -> datetime:
    """Primer momento permitido a partir de ahora (hora de Bogotá)."""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    v = cfg["ventana"]
    festivos = {d if isinstance(d, date) else date.fromisoformat(str(d)) for d in cfg.get("festivos", [])}
    d = now
    for _ in range(14):
        wd = d.weekday()
        win = v["lunes_viernes"] if wd < 5 else v["sabado"] if wd == 5 else None
        if win and d.date() not in festivos:
            start = datetime.combine(d.date(), _t(win[0]), TZ)
            end = datetime.combine(d.date(), _t(win[1]), TZ)
            if d < end:
                return max(d, start).replace(second=0, microsecond=0)
        d = datetime.combine(d.date() + timedelta(days=1), time(0, 0), TZ)
    raise RuntimeError("No hay ventana de envío en 14 días")


def _lista(paises: list[str]) -> str:
    return paises[0] if len(paises) == 1 else ", ".join(paises[:-1]) + " y " + paises[-1]


def compose(empresa: str, senal: dict, contacto: dict, icp: dict, paises: list[str] | None = None,
            now: datetime | None = None) -> dict:
    """senal = {"texto": ..., "url": ...}; contacto = {"nombre", "cargo", "correo", "dominio"}."""
    v, m = icp["vendedor"], icp["mensaje"]
    texto_senal = senal["texto"][0].lower() + senal["texto"][1:]
    plantilla = m["plantilla_con_paises"] if paises else m["plantilla_sin_paises"]
    texto = plantilla.format(
        nombre=contacto["nombre"].split()[0], empresa=empresa, senal=texto_senal,
        paises=_lista(paises) if paises else "", descripcion_vendedor=v["descripcion"],
        remitente=v["remitente"], cargo=v["cargo"], empresa_remitente=v["empresa_remitente"],
        url_baja=v["url_baja"],
    ).strip()
    checks = check(texto, texto_senal, contacto, icp)
    return {
        "para": contacto["nombre"], "cargo": contacto.get("cargo"), "canal": "correo corporativo",
        "asunto": f"Pregunta sobre los contractors de {empresa}",
        "texto": texto, "senal_url": senal.get("url"),
        "enviar_desde": next_send_slot(m, now).isoformat(),
        "max_contactos_por_semana": m.get("max_contactos_por_semana", 1),
        "cumplimiento": checks, "cumple": all(c["ok"] for c in checks),
    }


def check(texto: str, texto_senal: str, contacto: dict, icp: dict) -> list[dict]:
    v, m = icp["vendedor"], icp["mensaje"]
    correo = (contacto.get("correo") or "").lower()
    dominio = (contacto.get("dominio") or "").lower()
    low = texto.lower()
    edom = correo.split("@", 1)[1] if "@" in correo else ""
    corporativo = bool(edom and dominio and (edom == dominio or edom.endswith("." + dominio)))
    placeholders = [x for x in (v["remitente"], v["cargo"], v["empresa_remitente"], v["url_baja"]) if x.startswith("<")]
    return [
        {"regla": "Identifica a quien escribe y a su empresa (sin marcadores pendientes)",
         "ok": not placeholders and v["remitente"] in texto and v["empresa_remitente"] in texto},
        {"regla": "Ofrece una forma clara de no recibir más mensajes", "ok": "baja" in low},
        {"regla": "Solo correo corporativo del dominio de la empresa",
         "ok": corporativo},
        {"regla": "Explica por qué se le contacta (cargo) y cita la señal concreta",
         "ok": "cargo" in low and texto_senal in texto},
        {"regla": "No menciona ni ataca al proveedor actual",
         "ok": not matches(texto, m.get("proveedores", [])) and not matches(texto, list(NEGATIVO))},
        {"regla": "Sin lenguaje de presión", "ok": not matches(texto, list(PRESION))},
        {"regla": "Breve (menos de 130 palabras)", "ok": len(texto.split()) < 130},
    ]
