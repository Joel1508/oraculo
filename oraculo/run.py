"""CLI: python -m oraculo.run --icp icp/ontop.yaml [--sync-crm] [--discover]

Pasos: fuentes -> señales -> agrupar por dominio -> excluir -> hechos (señales + crm.csv) -> puntaje
-> (opcional) Hunter Domain Search -> mensaje de diagnóstico.

Escribe:
  out/signals.json          señales agrupadas por dominio (git lo ignora; lo usa `crm recalcular`)
  out/prospects_full.json   todo, con contactos (git lo ignora)
  public/prospects.json     versión pública: sin nombres ni LinkedIn, correos enmascarados
  crm.csv                   si existe o con --sync-crm (git lo ignora)
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

from . import crm, discover, enrich, outreach, score
from .sources import Signal, group_by_domain, slug


def load_env(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_icp(path: str) -> dict:
    icp = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    errs = score.validate_icp(icp)
    if errs:
        raise SystemExit("ICP inválido: " + "; ".join(errs))
    return icp


def collect(icp: dict, only: list[str] | None) -> tuple[list[Signal], list[dict]]:
    signals, log = [], []
    for name, cfg in (icp.get("fuentes") or {}).items():
        if only and name not in only:
            continue
        try:
            got = importlib.import_module(f"oraculo.sources.{name}").fetch(cfg or {}, icp)
            signals += got
            log.append({"fuente": name, "ok": True, "senales": len(got)})
        except Exception as e:  # una fuente caída no tumba el pipeline
            log.append({"fuente": name, "ok": False, "error": f"{type(e).__name__}: {e}"})
            print(f"[aviso] fuente {name} falló: {e}", file=sys.stderr)
    return signals, log


def _best_signal(ss: list[Signal]) -> Signal | None:
    cand = [s for s in ss if s.tipo in ("vacante", "ronda", "prensa")]
    return sorted(cand, key=lambda s: (s.manual, s.fecha or ""), reverse=True)[0] if cand else None


def build(icp: dict, grupos: dict[str, list[Signal]], rows: list[dict]) -> tuple[list[dict], list[str]]:
    by_key = {crm.row_key(r, grupos): r for r in rows}
    keys = list(dict.fromkeys(list(grupos) + list(by_key)))
    prospects, excluded = [], []
    for k in keys:
        ss, row = grupos.get(k, []), by_key.get(k)
        nombre = row["empresa"] if row else Counter(s.empresa for s in ss).most_common(1)[0][0]
        dominio = None if k.startswith("sin-dominio:") else k
        if score.is_excluded(nombre, dominio, icp):
            excluded.append(nombre)
            continue
        hechos = {**score.facts_from_signals(ss), **(crm.facts_from_row(row) if row else {})}
        res = score.score(hechos, icp)
        best = _best_signal(ss)
        prospects.append({
            "id": row["id"] if row else None, "clave": k, "nombre": nombre, "dominio": dominio,
            "en_crm": bool(row), "etapa": row["etapa"] if row else None, **res,
            "senal_principal": best.to_dict() if best else (
                {"texto": row["senal_publica"], "fuente": row.get("fuente"), "url": None} if row and row.get("senal_publica") else None),
            "senales": [s.to_dict() for s in ss],
        })
    prospects.sort(key=lambda p: (-p["puntaje"], -p["cobertura"], p["nombre"]))
    for i, p in enumerate(prospects, 1):
        p["rank"] = i
    return prospects, excluded


def sync_rows(prospects: list[dict], rows: list[dict], min_puntaje: int) -> int:
    ids = {r["id"] for r in rows}
    n = 0
    for p in prospects:
        if p["en_crm"] or p["puntaje"] < min_puntaje or p["descalificado"]:
            continue
        rid = base = f"auto-{slug(p['dominio'] or p['nombre'])[:32]}"
        i = 2
        while rid in ids:
            rid, i = f"{base}-{i}", i + 1
        s = p["senal_principal"] or {}
        r = {c: "" for c in crm.COLUMNS}
        r.update({"id": rid, "empresa": p["nombre"], "dominio": p["dominio"] or "",
                  "senal_publica": s.get("texto", ""), "fuente": f"{s.get('fuente', '')} {s.get('url') or ''}".strip(),
                  "desconocido": ", ".join(d["criterio"] for d in p["desconocidos"])})
        r = crm.normalize(r)
        crm.touch(r)
        rows.append(r)
        ids.add(rid)
        p.update({"id": rid, "en_crm": True, "etapa": r["etapa"]})
        n += 1
    return n


def discover_contacts(prospects: list[dict], rows: list[dict], icp: dict) -> list[str]:
    dcfg = icp.get("descubrimiento") or {}
    budget = discover.Budget(int(dcfg.get("max_llamadas", 3)))
    by_id = {r["id"]: r for r in rows}
    log = []
    for p in prospects:
        r = by_id.get(p["id"])
        if not r or not p["dominio"] or p["descalificado"] or r.get("contacto_email") or r.get("contacto_nombre"):
            continue  # nunca se pisa un contacto existente (manual o de Hunter)
        d = discover.discover_company(p["dominio"], dcfg, budget)
        if not d["personas"]:
            log.append(f"{p['nombre']}: sin decisores ({d['origen']})")
            continue
        x = d["personas"][0]
        try:
            crm.set_contact(r, nombre=x["nombre"], cargo=x["cargo"], email=x["correo"], fuente="hunter",
                            email_status=x["email_status"], linkedin=x.get("linkedin_url") or "")
            log.append(f"{p['nombre']}: {x['cargo']} ({x['email_status']}, {d['origen']})")
        except ValueError as e:
            log.append(f"{p['nombre']}: {e}")
        if budget.left <= 0:
            break
    return log


def attach_messages(prospects: list[dict], rows: list[dict], icp: dict) -> None:
    by_id = {r["id"]: r for r in rows}
    for p in prospects:
        r = by_id.get(p["id"])
        p["contacto"] = None
        p["mensaje"] = None
        if not r or not r.get("contacto_email"):
            continue
        p["contacto"] = {"nombre": r["contacto_nombre"], "cargo": r["contacto_cargo"], "correo": r["contacto_email"],
                         "email_status": r["email_status"], "fuente": r["contacto_fuente"],
                         "linkedin_url": r.get("contacto_linkedin") or None}
        if p["senal_principal"] and not p["descalificado"] and r.get("en_crm_ontop") != "tomado":
            paises = [x.strip() for x in r.get("paises", "").replace(",", ";").split(";") if x.strip()]
            p["mensaje"] = outreach.compose(p["nombre"], p["senal_principal"],
                                            {**p["contacto"], "dominio": p["dominio"]}, icp, paises or None)


def public_copy(doc: dict) -> dict:
    pub = json.loads(json.dumps(doc))
    for p in pub["prospectos"]:
        c = p.get("contacto")
        if c:
            p["contacto"] = {"cargo": c.get("cargo"), "correo": enrich.mask(c.get("correo")),
                             "email_status": c.get("email_status")}
        if p.get("mensaje"):
            p["mensaje"] = {"cumple": p["mensaje"]["cumple"], "enviar_desde": p["mensaje"]["enviar_desde"]}
    return pub


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Oráculo de prospección B2B")
    ap.add_argument("--icp", default="icp/ontop.yaml")
    ap.add_argument("--crm", default=str(crm.DEFAULT_CRM))
    ap.add_argument("--out", default="out")
    ap.add_argument("--public", default="public")
    ap.add_argument("--sources", help="lista separada por comas; por defecto todas las de la ICP")
    ap.add_argument("--sync-crm", action="store_true", help="agrega a crm.csv los prospectos nuevos")
    ap.add_argument("--min-puntaje", type=int, default=15, help="puntaje mínimo para --sync-crm")
    ap.add_argument("--discover", action="store_true", help="Hunter Domain Search para filas sin contacto")
    ap.add_argument("--solo-cache", action="store_true", help="Hunter solo desde caché (no gasta créditos)")
    a = ap.parse_args(argv)

    load_env()
    icp = load_icp(a.icp)
    if a.solo_cache:
        icp.setdefault("descubrimiento", {})["solo_cache"] = True
    signals, log = collect(icp, a.sources.split(",") if a.sources else None)
    grupos = group_by_domain(signals)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "signals.json").write_text(json.dumps({k: [s.to_dict() for s in ss] for k, ss in grupos.items()},
                                                 ensure_ascii=False, indent=1), encoding="utf-8")

    crm_path = Path(a.crm)
    rows = crm.read(crm_path)
    prospects, excluded = build(icp, grupos, rows)
    nuevos = sync_rows(prospects, rows, a.min_puntaje) if a.sync_crm else 0
    hunter_log = discover_contacts(prospects, rows, icp) if a.discover else []
    if a.discover:  # el acceso cambia con el contacto nuevo
        prospects, excluded = build(icp, grupos, rows)
    attach_messages(prospects, rows, icp)
    if rows and (crm_path.exists() or a.sync_crm):
        crm.write(crm.clean(crm.recalc(rows, icp, grupos)), crm_path)

    doc = {"generado": datetime.now(outreach.TZ).isoformat(timespec="seconds"),
           "vendedor": icp["vendedor"]["empresa"], "criterios": icp["criterios"],
           "excluidos": icp.get("excluir", []), "excluidos_encontrados": sorted(set(excluded)),
           "fuentes": log, "atribucion": "Vacantes de Remote OK (https://remoteok.com), Get on Board y We Work Remotely; cada señal enlaza a su fuente.",
           "prospectos": prospects}
    (out / "prospects_full.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    if a.public:
        Path(a.public).mkdir(parents=True, exist_ok=True)
        (Path(a.public) / "prospects.json").write_text(json.dumps(public_copy(doc), ensure_ascii=False, indent=2),
                                                       encoding="utf-8")

    for x in log:
        print(f"fuente {x['fuente']}: " + (f"{x['senales']} señales" if x["ok"] else f"ERROR {x['error']}"))
    for line in hunter_log:
        print(f"hunter {line}")
    for p in prospects[:25]:
        flag = " DESCALIFICADO" if p["descalificado"] else ""
        print(f"{p['rank']:>3}. {p['nombre'][:30]:<30} {p['puntaje']:>3}/100 {p['tier'] or '-':<3} "
              f"cobertura {p['cobertura']:>3}%{flag}")
    print(f"{len(prospects)} prospectos, {len(set(excluded))} excluidos, {nuevos} filas nuevas en CRM "
          f"-> {out}/prospects_full.json, {a.public}/prospects.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
