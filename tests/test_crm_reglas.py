"""Reglas del CRM: descalificador, entidad extranjera, Contacted sin correo, acceso por rol, sin teléfonos."""
import pytest

from oraculo import crm
from oraculo import score as sc
from oraculo.sources import Signal

from conftest import HOY


def fila(**kw):
    r = {c: "" for c in crm.COLUMNS}
    r.update({"id": "1", "empresa": "Acme", "dominio": "acme.com", "segmento": "fintech"})
    r.update(kw)
    return crm.normalize(r)


def puntaje(r, icp):
    return sc.score(crm.facts_from_row(r), icp, HOY)


def puntos(res, criterio):
    return next((c["puntos"] for c in res["conocidos"] if c["criterio"] == criterio), None)


# ---------- 1. descalificador ----------

@pytest.mark.parametrize("valor,motivo", [("eor_only", "EOR"), ("domestico", "doméstica"),
                                          ("sin_contratacion", "contractors")])
@pytest.mark.parametrize("fuente,url", [("llamada", ""), ("correo", ""), ("web", "https://acme.com/blog")])
def test_descalificador_deja_cero(icp, valor, motivo, fuente, url):
    r = fila(descalificador=valor, descalificador_fuente=fuente, descalificador_fecha="2026-11-20",
             descalificador_url=url)
    assert crm.validate_disqualifier(r) == []
    res = puntaje(r, icp)
    assert res["puntaje"] == 0 and res["tier"] is None and res["descalificado"]
    assert motivo in res["motivos"][0]["motivo"] and fuente in res["motivos"][0]["fuente"]


def test_llamada_no_necesita_url_pero_si_fecha(icp):
    r = fila(descalificador="eor_only", descalificador_fuente="llamada")
    assert any("descalificador_fecha" in e for e in crm.validate_disqualifier(r))
    assert not puntaje(r, icp)["descalificado"]   # incompleto: no se aplica, queda como aviso
    r["descalificador_fecha"] = "2026-11-20"
    assert crm.validate_disqualifier(r) == [] and puntaje(r, icp)["descalificado"]


def test_web_necesita_url():
    r = fila(descalificador="domestico", descalificador_fuente="web", descalificador_fecha="2026-11-20")
    assert any("descalificador_url" in e for e in crm.validate_disqualifier(r))


@pytest.mark.parametrize("valor", ["", "ninguno"])
def test_vacio_o_ninguno_no_descalifica(icp, valor):
    r = fila(descalificador=valor)
    assert crm.validate_disqualifier(r) == [] and not puntaje(r, icp)["descalificado"]


def test_descalificador_invalido():
    assert crm.validate_disqualifier(fila(descalificador="quiza")) != []


def test_descalificador_bloquea_tier_en_recalc(icp):
    r = fila(descalificador="eor_only", descalificador_fuente="llamada", descalificador_fecha="2026-11-20",
             contractors_n="60", metodo_pago="wise", paises="CO;MX;AR")
    crm.recalc([r], icp, hoy=HOY)
    assert r["puntaje"] == "0" and r["tier"] == ""


# ---------- 2. entidad extranjera ----------

def test_entidad_extranjera_si_suma_5(icp):
    assert puntos(puntaje(fila(entidad_extranjera="si", pais_entidad="US"), icp), "paises") == 5
    assert puntos(puntaje(fila(entidad_extranjera="sí", paises="CO;MX;PE"), icp), "paises") == 10
    assert puntos(puntaje(fila(entidad_extranjera="no", paises="CO"), icp), "paises") == 0


def test_entidad_extranjera_vacia_es_desconocida(icp):
    res = puntaje(fila(), icp)
    assert "paises" in [d["criterio"] for d in res["desconocidos"]]


def _gob(pais="US"):
    return Signal("Acme", "hecho", "sede US", "Get on Board", "https://www.getonbrd.com/jobs/x", "2026-11-20",
                  dominio="acme.com", criterio="entidad_extranjera", valor=True, datos={"pais_empresa": pais})


def test_prellena_desde_get_on_board(icp):
    r = fila()
    crm.recalc([r], icp, {"acme.com": [_gob()]}, HOY)
    assert r["entidad_extranjera"] == "si" and r["pais_entidad"] == "US"
    assert r["entidad_extranjera_fuente"] == "Get on Board https://www.getonbrd.com/jobs/x"
    c = next(c for c in r["_score"]["conocidos"] if c["criterio"] == "paises")
    assert c["fuentes"][0]["url"] == "https://www.getonbrd.com/jobs/x"


def test_prellenado_no_pisa_valor_manual(icp):
    r = fila(entidad_extranjera="no", entidad_extranjera_fuente="llamada")
    crm.recalc([r], icp, {"acme.com": [_gob()]}, HOY)
    assert r["entidad_extranjera"] == "no" and puntos(r["_score"], "paises") == 0


# ---------- 3. Contacted ----------

@pytest.mark.parametrize("canal", ["linkedin", "whatsapp", "presencial", "intro"])
def test_contacted_por_otro_canal_manual(canal):
    r = fila(contacto_nombre="Ana Pérez", contacto_fuente="manual", canal_contacto=canal, en_crm_ontop="libre")
    assert crm.blockers(r, "Contacted", HOY) == []


def test_contacted_con_correo_corporativo():
    r = fila(contacto_nombre="Ana", contacto_email="ana@acme.com", contacto_fuente="hunter", email_status="verified")
    assert crm.blockers(r, "Contacted", HOY) == []


@pytest.mark.parametrize("kw", [
    dict(contacto_nombre="Ana", contacto_fuente="manual", canal_contacto="email"),   # email sin correo
    dict(contacto_nombre="Ana", contacto_fuente="hunter", canal_contacto="whatsapp"),  # otro canal pero no manual
    dict(contacto_nombre="Ana", contacto_fuente="manual"),                            # sin canal
    dict(contacto_fuente="manual", canal_contacto="presencial"),                      # sin contacto
    dict(contacto_nombre="Ana", contacto_fuente="manual", canal_contacto="fax"),      # canal inválido
    dict(contacto_nombre="Ana", contacto_email="ana@gmail.com", contacto_fuente="manual"),
])
def test_contacted_bloqueado(kw):
    assert crm.blockers(fila(**kw), "Contacted", HOY)


def test_contacted_bloqueado_si_tomado():
    r = fila(contacto_nombre="Ana", contacto_fuente="manual", canal_contacto="intro", en_crm_ontop="tomado")
    assert any("tomado" in b for b in crm.blockers(r, "Contacted", HOY))


def test_cli_contacto_manual_por_whatsapp():
    r = crm.set_contact(fila(), nombre="Ana Pérez", fuente="manual", canal="whatsapp", rol="decisor", hoy=HOY)
    assert r["canal_contacto"] == "whatsapp" and r["contacto_email"] == ""
    crm.set_stage(r, "Contacted", HOY)


# ---------- teléfonos: nunca en el CRM ----------

@pytest.mark.parametrize("campo,valor", [("notas", "WhatsApp +57 300 123 4567"), ("notas", "3001234567"),
                                         ("contacto_nombre", "Ana 300-123-4567"), ("canal_contacto", "+573001234567")])
def test_telefono_rechazado(tmp_path, campo, valor):
    r = fila(**{campo: valor})
    assert crm.phone_errors(r)
    with pytest.raises(ValueError, match="teléfono"):
        crm.write([r], tmp_path / "crm.csv")
    with pytest.raises(ValueError):
        crm.set_contact(fila(notas="llamar 3001234567"), nombre="Ana", fuente="manual", canal="whatsapp")


@pytest.mark.parametrize("texto", ["Conversación 2026-11-20", "https://remoteok.com/remote-jobs/1138079",
                                   "Tiene 45 contractors en 3 países", "Serie A de USD 12M"])
def test_no_confunde_fechas_urls_ni_cifras(texto):
    assert crm.phone_errors(fila(notas=texto)) == []


# ---------- 4. acceso por rol ----------

@pytest.mark.parametrize("rol,pts", [("decisor", 15), ("influenciador", 10), ("otro", 4), ("", 0)])
def test_acceso_por_rol(icp, rol, pts):
    r = fila(fecha_conversacion="2026-11-10", contacto_nombre="Ana", contacto_fuente="manual",
             canal_contacto="presencial", contacto_rol=rol)
    assert puntos(puntaje(r, icp), "acceso") == pts


def test_acceso_toma_la_mejor_evidencia(icp):
    base = dict(contacto_nombre="Ana", contacto_email="ana@acme.com", contacto_fuente="hunter",
                email_status="verified", fecha_conversacion="2026-11-10")
    assert puntos(puntaje(fila(**base), icp), "acceso") == 10                       # sin rol: vale el correo
    assert puntos(puntaje(fila(**base, contacto_rol="decisor"), icp), "acceso") == 15
    assert puntos(puntaje(fila(**base, contacto_rol="otro"), icp), "acceso") == 10


def test_rol_invalido():
    assert crm.validate_contact(fila(contacto_nombre="Ana", contacto_fuente="manual", contacto_rol="jefe"))
