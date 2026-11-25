"""Regla de contactos:
- Solo vienen de (a) Hunter con su email_status o (b) ingreso manual marcado fuente=manual.
- Ningún LLM genera ni completa nombres, correos, cargos ni LinkedIn.
- Sin email_status = unknown y sin puntos de acceso.
"""
import pytest
from pydantic import ValidationError

from oraculo import crm
from oraculo import score as sc
from oraculo.enrich_company import EmpresaExtraida

from conftest import HOY


def fila(**kw):
    r = {c: "" for c in crm.COLUMNS}
    r.update({"id": "1", "empresa": "Acme", "dominio": "acme.com"})
    r.update(kw)
    return crm.normalize(r)


def acceso(r, icp):
    res = sc.score(crm.facts_from_row(r), icp, HOY)
    return next((c["puntos"] for c in res["conocidos"] if c["criterio"] == "acceso"), None)


@pytest.mark.parametrize("fuente", ["", "llm", "claude", "apollo", "inferido"])
def test_solo_hunter_o_manual(fuente):
    r = fila(contacto_nombre="Ana Pérez", contacto_email="ana@acme.com", contacto_fuente=fuente)
    assert any("hunter" in e and "manual" in e for e in crm.validate_contact(r))
    with pytest.raises(ValueError):
        crm.set_contact(fila(), nombre="Ana Pérez", email="ana@acme.com", fuente=fuente)


def test_hunter_con_status_ok():
    r = crm.set_contact(fila(), nombre="Ana Pérez", cargo="COO", email="Ana@Acme.com", fuente="hunter",
                        email_status="verified", linkedin="https://linkedin.com/in/ana", hoy=HOY)
    assert r["contacto_email"] == "ana@acme.com" and r["email_status"] == "verified"


def test_manual_ok_y_queda_unknown():
    r = crm.set_contact(fila(), nombre="Ana Pérez", cargo="COO", email="ana@acme.com", fuente="manual", hoy=HOY)
    assert r["email_status"] == "unknown" and r["contacto_fuente"] == "manual"


def test_manual_no_puede_decirse_verificado():
    with pytest.raises(ValueError, match="unknown"):
        crm.set_contact(fila(), nombre="Ana", email="ana@acme.com", fuente="manual", email_status="verified")


def test_linkedin_solo_de_hunter():
    with pytest.raises(ValueError, match="LinkedIn|linkedin"):
        crm.set_contact(fila(), nombre="Ana", email="ana@acme.com", fuente="manual",
                        linkedin="https://linkedin.com/in/ana")


@pytest.mark.parametrize("email", ["ana@gmail.com", "ana@otraempresa.com", "ana@hotmail.com"])
def test_solo_correo_corporativo(email):
    with pytest.raises(ValueError):
        crm.set_contact(fila(), nombre="Ana", email=email, fuente="hunter", email_status="verified")


def test_subdominio_corporativo_ok():
    crm.set_contact(fila(), nombre="Ana", email="ana@latam.acme.com", fuente="hunter", email_status="verified")


def test_sin_email_status_es_unknown_y_sin_puntos(icp):
    r = fila(contacto_nombre="Ana", contacto_email="ana@acme.com", contacto_fuente="hunter", email_status="")
    assert r["email_status"] == "unknown"
    assert acceso(r, icp) is None   # null: suma 0 y queda en desconocidos
    res = sc.score(crm.facts_from_row(r), icp, HOY)
    assert "acceso" in [d["criterio"] for d in res["desconocidos"]]


def test_status_da_puntos_de_acceso(icp):
    assert acceso(fila(contacto_nombre="Ana", contacto_email="ana@acme.com", contacto_fuente="hunter",
                       email_status="verified"), icp) == 10
    assert acceso(fila(contacto_nombre="Ana", contacto_email="ana@acme.com", contacto_fuente="hunter",
                       email_status="accept_all"), icp) == 4
    assert acceso(fila(), icp) == 0   # solo dominio


def test_contacto_invalido_no_da_acceso(icp):
    r = fila(contacto_nombre="Ana", contacto_email="ana@gmail.com", contacto_fuente="hunter", email_status="verified")
    assert acceso(r, icp) == 0   # se ignora el contacto: queda como solo dominio


@pytest.mark.parametrize("campo", ["contacto_nombre", "contacto_email", "contacto_cargo", "contacto_linkedin",
                                   "nombre", "email", "telefono"])
def test_extraccion_llm_no_acepta_datos_de_personas(campo):
    with pytest.raises(ValidationError):
        EmpresaExtraida.model_validate({campo: "x"})


def test_extraccion_sin_cita_es_null():
    r = EmpresaExtraida.model_validate({
        "contrata_contractors": {"valor": True},
        "vertical": {"valor": "fintech", "cita": "somos una fintech", "url": "https://acme.com/about"},
        "paises_operacion": [{"valor": "CO", "cita": "", "url": "https://acme.com"}],
    })
    assert r.contrata_contractors.valor is None
    assert r.vertical.valor == "fintech"
    assert r.paises_operacion[0].valor is None
