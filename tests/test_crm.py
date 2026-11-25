import csv

import pytest

from oraculo import crm

from conftest import HOY

SEED_HEADER = ("id,empresa,segmento,senal_publica,fuente,por_que_encaja,desconocido,puntaje_publico,tier,"
               "etapa,acepta_intro,en_crm_ontop,decisor_objetivo,notas")


def fila(**kw):
    r = {c: "" for c in crm.COLUMNS}
    r.update({"id": "1", "empresa": "Acme", "dominio": "acme.com"})
    r.update(kw)
    return crm.normalize(r)


def lista_para_intro(**kw):
    base = dict(contacto_nombre="Ana Pérez", contacto_cargo="COO", contacto_email="ana@acme.com",
                contacto_fuente="hunter", email_status="verified", fecha_conversacion="2026-11-10",
                acepta_intro="si", contractors_n="25", paises="CO;MX", metodo_pago="wise",
                contacto_sabe_ontop="si", en_crm_ontop="libre")
    base.update(kw)
    return fila(**base)


def test_columnas():
    assert crm.COLUMNS[: len(crm.SEED_COLUMNS)] == SEED_HEADER.split(",")
    for c in ("dominio", "contractors_n", "paises", "metodo_pago", "proveedor_actual", "contacto_nombre",
              "contacto_cargo", "contacto_email", "email_status", "contacto_linkedin", "contacto_sabe_ontop",
              "fecha_conversacion", "puntaje", "cobertura", "ultima_actualizacion"):
        assert c in crm.COLUMNS


def test_importar_semilla(tmp_path, icp):
    seed = tmp_path / "seed.csv"
    seed.write_text(SEED_HEADER + "\n"
                    "7,Acme,fintech,Vacantes LATAM,Remote OK,paga en 3 países,metodo_pago,72,T2,,,,COO,\n",
                    encoding="utf-8")
    rows, nuevos, _ = crm.import_seed(seed, [], HOY)
    r = rows[0]
    assert nuevos == 1 and r["puntaje"] == "72" and r["puntaje_publico"] == "72"
    assert r["etapa"] == "Identified" and r["en_crm_ontop"] == "pendiente" and r["email_status"] == "unknown"
    crm.recalc(rows, icp, hoy=HOY)
    assert r["puntaje"] == "10" and r["puntaje_publico"] == "72"   # solo se conoce vertical=fintech
    out = tmp_path / "crm.csv"
    crm.write(crm.clean(rows), out)
    with open(out, encoding="utf-8") as f:
        assert next(csv.reader(f)) == crm.COLUMNS


def test_reimportar_no_pisa_el_crm(tmp_path):
    seed = tmp_path / "seed.csv"
    seed.write_text(SEED_HEADER + "\n7,Acme,fintech,,,,,72,,,,,,nota semilla\n", encoding="utf-8")
    rows, _, _ = crm.import_seed(seed, [], HOY)
    rows[0]["notas"] = "editado a mano"
    rows, nuevos, _ = crm.import_seed(seed, rows, HOY)
    assert nuevos == 0 and rows[0]["notas"] == "editado a mano"


# ---------- compuerta ----------

def test_intro_completa_pasa():
    r = lista_para_intro()
    crm.set_stage(r, "Introduced", HOY)
    assert r["etapa"] == "Introduced"


@pytest.mark.parametrize("campo,valor,texto", [
    ("acepta_intro", "no", "acepta_intro"),
    ("acepta_intro", "", "acepta_intro"),
    ("contractors_n", "", "contractors_n"),
    ("paises", "", "paises"),
    ("metodo_pago", "", "metodo_pago"),
    ("metodo_pago", "desconocido", "metodo_pago"),
    ("contacto_sabe_ontop", "no", "Ontop lo contactará"),
    ("contacto_sabe_ontop", "", "Ontop lo contactará"),
])
def test_intro_bloqueada(campo, valor, texto):
    r = lista_para_intro(**{campo: valor})
    with pytest.raises(crm.GateError, match=texto):
        crm.set_stage(r, "Introduced", HOY)
    assert r["etapa"] == "Identified"


@pytest.mark.parametrize("etapa", ["Engaged", "Qualified", "Introduced", "Activated"])
def test_fecha_conversacion_obligatoria_desde_engaged(etapa):
    r = lista_para_intro(fecha_conversacion="")
    assert any("fecha_conversacion" in b for b in crm.blockers(r, etapa, HOY))


def test_contacted_no_exige_fecha():
    assert crm.blockers(lista_para_intro(fecha_conversacion=""), "Contacted", HOY) == []


def test_fecha_futura_bloquea():
    assert any("futuro" in b for b in crm.blockers(lista_para_intro(fecha_conversacion="2027-01-01"), "Engaged", HOY))


def test_tomado_bloquea_contacto():
    assert any("tomado" in b for b in crm.blockers(lista_para_intro(en_crm_ontop="tomado"), "Contacted", HOY))


def test_etapa_invalida():
    with pytest.raises(ValueError):
        crm.blockers(fila(), "Closed", HOY)


def test_en_crm_ontop_valores():
    assert fila()["en_crm_ontop"] == "pendiente"
    assert crm.EN_CRM_ONTOP == ("pendiente", "libre", "tomado")


def test_fila_sin_dominio_se_cruza_por_nombre(icp):
    from oraculo.sources import Signal, group_by_domain
    g = group_by_domain([Signal("Acme", "vacante", "x", "Get on Board", "https://g", "2026-11-20",
                                dominio="acme.com", criterio="disparador",
                                valor={"tipo": "vacante", "fecha": "2026-11-20"})])
    r = fila(dominio="")
    crm.recalc([r], icp, g, HOY)
    assert r["_score"]["puntaje"] == 15


def test_excluido_en_crm_queda_en_cero(icp):
    r = fila(empresa="Torre", dominio="torre.co", segmento="hr-tech")
    crm.recalc([r], icp, hoy=HOY)
    assert r["puntaje"] == "0" and r["tier"] == "" and r["_score"]["excluido"]
