"""crm.csv: importa la semilla, recalcula puntajes y controla el avance por etapas.

crm.csv contiene datos personales (contactos, LinkedIn) y está en .gitignore. Nunca se publica.

Uso:
  python -m oraculo.crm importar data/seed/ontop_crm_v0.csv
  python -m oraculo.crm recalcular
  python -m oraculo.crm validar
  python -m oraculo.crm etapa <id> <Etapa>
  python -m oraculo.crm contacto <id> --nombre "..." --cargo "..." --email x@empresa.com   (fuente=manual)
  python -m oraculo.crm buscar-correo <id>      (Hunter Email Finder para el contacto manual)
  python -m oraculo.crm en-crm <id> libre|tomado|pendiente

Regla de contactos:
  - Solo vienen de Hunter (contacto_fuente=hunter, con su email_status) o de ingreso manual
    (contacto_fuente=manual, email_status=unknown).
  - Ningún LLM genera ni completa nombres, correos, cargos ni LinkedIn.
  - Sin email_status el estado es 'unknown' y el criterio acceso no suma puntos.
  - LinkedIn solo el que devuelve Hunter. Solo correos del dominio corporativo.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

import yaml

from . import score as sc
from .sources import Signal, company_key, normalize_domain, slug

SEED_COLUMNS = ["id", "empresa", "segmento", "senal_publica", "fuente", "por_que_encaja", "desconocido",
                "puntaje_publico", "tier", "etapa", "acepta_intro", "en_crm_ontop", "decisor_objetivo", "notas"]
EXTRA_COLUMNS = ["dominio", "contractors_n", "paises", "metodo_pago", "proveedor_actual",
                 "contacto_nombre", "contacto_cargo", "contacto_email", "email_status", "contacto_fuente",
                 "contacto_linkedin", "contacto_sabe_ontop", "fecha_conversacion",
                 "puntaje", "cobertura", "ultima_actualizacion"]
COLUMNS = SEED_COLUMNS + EXTRA_COLUMNS

ETAPAS = ["Identified", "Researched", "Contacted", "Engaged", "Qualified", "Introduced", "Activated"]
EN_CRM_ONTOP = ("pendiente", "libre", "tomado")
EMAIL_STATUS = ("verified", "accept_all", "unknown")
FUENTES_CONTACTO = ("hunter", "manual")
CORREO_PERSONAL = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "icloud.com", "live.com",
                   "hotmail.es", "yahoo.es", "outlook.es", "protonmail.com", "proton.me", "aol.com"}
CONTACT_FIELDS = ("contacto_nombre", "contacto_cargo", "contacto_email", "contacto_linkedin")

DEFAULT_CRM = Path("crm.csv")
SIGNALS_CACHE = Path("out/signals.json")


class GateError(ValueError):
    """La fila no cumple los requisitos para pasar a la etapa pedida."""


# ---------- E/S ----------

def read(path: Path = DEFAULT_CRM) -> list[dict]:
    if not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [normalize({**{c: "" for c in COLUMNS}, **r}) for r in csv.DictReader(f)]


def write(rows: list[dict], path: Path = DEFAULT_CRM) -> None:
    extra = [k for r in rows for k in r if k not in COLUMNS]
    cols = COLUMNS + list(dict.fromkeys(extra))
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    tmp.replace(path)


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def normalize(r: dict) -> dict:
    r = {k: _s(v) for k, v in r.items()}
    if not r.get("etapa"):
        r["etapa"] = "Identified"
    else:
        r["etapa"] = next((e for e in ETAPAS if e.lower() == r["etapa"].lower()), r["etapa"])
    r["en_crm_ontop"] = r.get("en_crm_ontop", "").lower() or "pendiente"
    r["email_status"] = r.get("email_status", "").lower() or "unknown"
    r["contacto_fuente"] = r.get("contacto_fuente", "").lower()
    for k in ("acepta_intro", "contacto_sabe_ontop"):
        v = sc.norm_key(r.get(k, ""))
        r[k] = {"si": "si", "yes": "si", "no": "no"}.get(v, r.get(k, ""))
    if r.get("dominio"):
        r["dominio"] = normalize_domain(r["dominio"]) or r["dominio"].lower()
    return r


def touch(r: dict, hoy: date | None = None) -> None:
    r["ultima_actualizacion"] = (hoy or date.today()).isoformat()


# ---------- importación ----------

def import_seed(seed_path: Path, rows: list[dict], hoy: date | None = None) -> tuple[list[dict], int, int]:
    """Fusiona la semilla en el CRM por id. Lo que ya está en el CRM prevalece; la semilla llena vacíos.
    puntaje_publico se copia a 'puntaje' solo si 'puntaje' está vacío (luego 'recalcular' lo reemplaza)."""
    with open(seed_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        faltan = [c for c in SEED_COLUMNS if c not in (reader.fieldnames or [])]
        if faltan:
            raise ValueError(f"La semilla no tiene las columnas: {', '.join(faltan)}")
        seed = list(reader)
    by_id = {r["id"]: r for r in rows}
    nuevos = actualizados = 0
    for s in seed:
        s = {k: _s(v) for k, v in s.items()}
        r = by_id.get(s["id"])
        if r is None:
            r = {c: "" for c in COLUMNS}
            rows.append(r)
            by_id[s["id"]] = r
            nuevos += 1
        cambio = False
        for k, v in s.items():
            if v and not r.get(k):
                r[k] = v
                cambio = True
        if not r.get("puntaje") and r.get("puntaje_publico"):
            r["puntaje"] = r["puntaje_publico"]
            cambio = True
        r.update(normalize(r))
        if cambio:
            actualizados += 1
            touch(r, hoy)
    return rows, nuevos, actualizados


# ---------- regla de contactos ----------

def validate_contact(r: dict) -> list[str]:
    if not any(r.get(k) for k in CONTACT_FIELDS):
        return []
    errs = []
    fuente = r.get("contacto_fuente", "")
    status = r.get("email_status") or "unknown"
    if fuente not in FUENTES_CONTACTO:
        errs.append("contacto_fuente debe ser 'hunter' o 'manual' (ningún otro origen, ni LLM)")
    if status not in EMAIL_STATUS:
        errs.append(f"email_status inválido: {status!r} (verified|accept_all|unknown)")
    if fuente == "manual" and status != "unknown":
        errs.append("un contacto manual no tiene verificación de Hunter: email_status debe ser 'unknown'")
    if r.get("contacto_linkedin") and fuente != "hunter":
        errs.append("contacto_linkedin solo se acepta si lo devolvió Hunter")
    email = r.get("contacto_email", "").lower()
    if email:
        dom = r.get("dominio", "").lower()
        edom = email.split("@", 1)[1] if "@" in email else ""
        if not edom:
            errs.append(f"correo inválido: {email}")
        elif edom in CORREO_PERSONAL:
            errs.append(f"correo personal ({edom}): solo correos corporativos (Ley 1581)")
        elif not dom:
            errs.append("falta 'dominio' para validar que el correo sea corporativo")
        elif not (edom == dom or edom.endswith("." + dom)):
            errs.append(f"el correo ({edom}) no es del dominio de la empresa ({dom})")
    return errs


def set_contact(r: dict, *, nombre: str, cargo: str = "", email: str = "", fuente: str,
                email_status: str = "unknown", linkedin: str = "", hoy: date | None = None) -> dict:
    nuevo = {**r, "contacto_nombre": _s(nombre), "contacto_cargo": _s(cargo), "contacto_email": _s(email).lower(),
             "contacto_fuente": _s(fuente).lower(), "email_status": _s(email_status).lower() or "unknown",
             "contacto_linkedin": _s(linkedin)}
    errs = validate_contact(nuevo)
    if errs:
        raise ValueError("Contacto rechazado: " + "; ".join(errs))
    r.update(nuevo)
    touch(r, hoy)
    return r


# ---------- etapas ----------

def _idx(etapa: str) -> int:
    if etapa not in ETAPAS:
        raise ValueError(f"Etapa desconocida: {etapa!r}. Válidas: {', '.join(ETAPAS)}")
    return ETAPAS.index(etapa)


def blockers(r: dict, etapa: str, hoy: date | None = None) -> list[str]:
    i, hoy = _idx(etapa), hoy or date.today()
    out = []
    if i >= _idx("Contacted"):
        if r.get("en_crm_ontop") == "tomado":
            out.append("en_crm_ontop = tomado: la empresa ya está en el CRM de Ontop")
        if not r.get("contacto_email"):
            out.append("falta contacto_email (corporativo)")
        out += validate_contact(r)
    if i >= _idx("Engaged"):
        f = sc._fecha(r.get("fecha_conversacion"))
        if not f:
            out.append("falta fecha_conversacion (AAAA-MM-DD)")
        elif f > hoy:
            out.append("fecha_conversacion está en el futuro")
    if i >= _idx("Introduced"):
        if r.get("acepta_intro") != "si":
            out.append('acepta_intro debe ser "si"')
        try:
            if int(r.get("contractors_n") or "x") < 0:
                raise ValueError
        except ValueError:
            out.append("falta contractors_n (número)")
        if not r.get("paises"):
            out.append("faltan paises")
        if sc.norm_key(r.get("metodo_pago")) in ("", "desconocido"):
            out.append("falta metodo_pago")
        if r.get("contacto_sabe_ontop") != "si":
            out.append('falta confirmar que el contacto sabe que Ontop lo contactará (contacto_sabe_ontop = "si")')
    return out


def set_stage(r: dict, etapa: str, hoy: date | None = None) -> dict:
    b = blockers(r, etapa, hoy)
    if b:
        raise GateError(f"{r.get('empresa')} no puede pasar a {etapa}:\n  - " + "\n  - ".join(b))
    r["etapa"] = etapa
    touch(r, hoy)
    return r


# ---------- puntaje ----------

def facts_from_row(r: dict) -> dict[str, dict]:
    H: dict[str, dict] = {}

    def put(clave, valor, col):
        if valor not in (None, "", []):
            H[clave] = {"valor": valor, "fuente": f"crm.csv ({col})", "url": None}

    put("contractors_activos", r.get("contractors_n"), "contractors_n")
    paises = [p.strip() for p in r.get("paises", "").replace(",", ";").split(";") if p.strip()]
    put("paises", paises, "paises")
    put("metodo_pago", r.get("metodo_pago"), "metodo_pago")
    put("vertical", r.get("segmento"), "segmento")
    put("en_crm_ontop", r.get("en_crm_ontop"), "en_crm_ontop")
    if sc._fecha(r.get("fecha_conversacion")):
        put("acceso", "contacto_conocido", "fecha_conversacion")
    elif r.get("contacto_email") and not validate_contact(r):
        # unknown -> null en el ICP: sin puntos de acceso
        H["acceso"] = {"valor": r.get("email_status") or "unknown",
                       "fuente": f"crm.csv (email_status, contacto {r.get('contacto_fuente')})", "url": None}
    elif r.get("dominio"):
        put("acceso", "solo_dominio", "dominio")
    return H


def row_key(r: dict, grupos: dict[str, list[Signal]] | None = None) -> str:
    """Clave del grupo de señales de la fila: su dominio; si no tiene, el grupo cuyo nombre coincide."""
    k = company_key(normalize_domain(r.get("dominio")), r.get("empresa", ""))
    if grupos and k not in grupos and not r.get("dominio"):
        n = slug(r.get("empresa"))
        k = next((g for g, ss in grupos.items() if any(slug(s.empresa) == n for s in ss)), k)
    return k


def recalc(rows: list[dict], icp: dict, grupos: dict[str, list[Signal]] | None = None,
           hoy: date | None = None) -> list[dict]:
    grupos = grupos or {}
    for r in rows:
        k = row_key(r, grupos)
        hechos = {**sc.facts_from_signals(grupos.get(k, [])), **facts_from_row(r)}
        res = sc.score(hechos, icp, hoy)
        res["excluido"] = sc.is_excluded(r.get("empresa"), r.get("dominio") or (None if k.startswith("sin-dominio:") else k), icp)
        if res["excluido"]:
            res.update(puntaje=0, tier=None)
        nuevo = {"puntaje": str(res["puntaje"]), "tier": res["tier"] or "", "cobertura": str(res["cobertura"])}
        if any(r.get(k) != v for k, v in nuevo.items()):
            r.update(nuevo)
            touch(r, hoy)
        r["_score"] = res
    return rows


def load_signal_groups(path: Path = SIGNALS_CACHE) -> dict[str, list[Signal]]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: [Signal(**s) for s in ss] for k, ss in raw.items()}


def clean(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


# ---------- CLI ----------

def _find(rows: list[dict], rid: str) -> dict:
    r = next((r for r in rows if r["id"] == rid), None)
    if r is None:
        raise SystemExit(f"No existe el id {rid!r} en crm.csv")
    return r


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CRM de Oráculo (crm.csv, ignorado por git)")
    ap.add_argument("--crm", default=str(DEFAULT_CRM))
    ap.add_argument("--icp", default="icp/ontop.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("importar"); p.add_argument("semilla")
    sub.add_parser("recalcular")
    sub.add_parser("validar")
    p = sub.add_parser("etapa"); p.add_argument("id"); p.add_argument("etapa")
    p = sub.add_parser("contacto"); p.add_argument("id")
    p.add_argument("--nombre", required=True); p.add_argument("--cargo", default=""); p.add_argument("--email", default="")
    p = sub.add_parser("buscar-correo"); p.add_argument("id")
    p = sub.add_parser("en-crm"); p.add_argument("id"); p.add_argument("estado", choices=EN_CRM_ONTOP)
    a = ap.parse_args(argv)

    crm_path = Path(a.crm)
    rows = read(crm_path)
    icp = yaml.safe_load(Path(a.icp).read_text(encoding="utf-8"))

    if a.cmd == "importar":
        rows, n, u = import_seed(Path(a.semilla), rows)
        recalc(rows, icp, load_signal_groups())
        print(f"{n} filas nuevas, {u} actualizadas -> {crm_path}")
    elif a.cmd == "recalcular":
        recalc(rows, icp, load_signal_groups())
        for r in sorted(rows, key=lambda r: -int(r["puntaje"] or 0)):
            s = r["_score"]
            print(f"{r['id']:>4} {r['empresa'][:28]:<28} {r['puntaje']:>3} {r['tier'] or '-':<3} "
                  f"cobertura {r['cobertura']:>3}%  desconocidos: {', '.join(d['criterio'] for d in s['desconocidos']) or '-'}"
                  + (f"  DESCALIFICADO: {'; '.join(m['motivo'] for m in s['motivos'])}" if s["descalificado"] else "")
                  + ("  EXCLUIDO (lista excluir de la ICP)" if s["excluido"] else ""))
    elif a.cmd == "validar":
        malos = 0
        for r in rows:
            errs = validate_contact(r) + ([f"etapa inválida {r['etapa']!r}"] if r["etapa"] not in ETAPAS else [])
            if r["etapa"] in ETAPAS:
                errs += blockers(r, r["etapa"])
            if errs:
                malos += 1
                print(f"{r['id']} {r['empresa']}: " + "; ".join(errs))
        print("OK" if not malos else f"{malos} filas con problemas")
        return 1 if malos else 0
    elif a.cmd == "etapa":
        try:
            set_stage(_find(rows, a.id), a.etapa)
        except GateError as e:
            print(e, file=sys.stderr)
            return 2
    elif a.cmd == "contacto":
        r = _find(rows, a.id)
        set_contact(r, nombre=a.nombre, cargo=a.cargo, email=a.email, fuente="manual")
    elif a.cmd == "buscar-correo":
        from .enrich import hunter_email_finder
        from .run import load_env
        load_env()
        r = _find(rows, a.id)
        if not (r.get("contacto_nombre") and r.get("dominio")):
            raise SystemExit("Se necesita contacto_nombre (manual) y dominio")
        res = hunter_email_finder(r["contacto_nombre"], r["dominio"], icp.get("descubrimiento"), r.get("contacto_cargo"))
        if not res.get("correo"):
            print("Hunter no devolvió correo: " + ("; ".join(res["errores"]) or "sin resultado"))
            return 1
        set_contact(r, nombre=r["contacto_nombre"], cargo=r.get("contacto_cargo", ""), email=res["correo"],
                    fuente="hunter", email_status=res["email_status"], linkedin=res.get("linkedin_url") or "")
        print(f"{res['correo']} ({res['email_status']}) " + " | ".join(res["revisar"]))
    elif a.cmd == "en-crm":
        r = _find(rows, a.id)
        r["en_crm_ontop"] = a.estado
        touch(r)
    if a.cmd != "validar":
        if a.cmd in ("etapa", "contacto", "buscar-correo", "en-crm"):
            recalc(rows, icp, load_signal_groups())
        write(clean(rows), crm_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
