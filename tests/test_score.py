import pytest

from oraculo import score as sc
from oraculo.sources import Signal

from conftest import HOY, h


def puntos(res, criterio):
    return next((c["puntos"] for c in res["conocidos"] if c["criterio"] == criterio), None)


def test_icp_valido(icp):
    assert sc.validate_icp(icp) == []
    assert sum(c["peso"] for c in icp["criterios"]) == 100


def test_empresa_ideal_100_t1(icp, hechos_completos):
    r = sc.score(hechos_completos, icp, HOY)
    assert r["puntaje"] == 100 and r["tier"] == "T1" and r["cobertura"] == 100
    assert r["desconocidos"] == []


# ---------- null = 0 ----------

def test_sin_datos_todo_null(icp):
    r = sc.score({}, icp, HOY)
    assert r["puntaje"] == 0 and r["tier"] is None and r["cobertura"] == 0
    assert {d["criterio"] for d in r["desconocidos"]} == {c["clave"] for c in icp["criterios"]}
    assert all(d["puntos"] == 0 for d in r["desconocidos"])


@pytest.mark.parametrize("criterio,valor", [("metodo_pago", "desconocido"), ("acceso", "unknown"),
                                            ("metodo_pago", "algo_raro"), ("contractors_activos", None)])
def test_valor_desconocido_suma_cero(icp, hechos_completos, criterio, valor):
    hechos_completos[criterio] = h(valor)
    r = sc.score(hechos_completos, icp, HOY)
    peso = next(c["peso"] for c in icp["criterios"] if c["clave"] == criterio)
    assert criterio in [d["criterio"] for d in r["desconocidos"]]
    assert r["puntaje"] == 100 - peso
    assert r["cobertura"] == 100 - peso


def test_conocidos_listan_fuente(icp, hechos_completos):
    r = sc.score(hechos_completos, icp, HOY)
    for c in r["conocidos"]:
        assert c["fuentes"], c
        assert all(f["fuente"] and f["url"] for f in c["fuentes"])


# ---------- descalificadores ----------

@pytest.mark.parametrize("clave,valor,motivo", [
    ("solo_eor", True, "EOR"),
    ("fuerza_laboral_domestica", "si", "doméstica"),
    ("en_crm_ontop", "tomado", "CRM de Ontop"),
    ("sin_contractors_ni_plan", True, "contractors"),
])
def test_descalificadores(icp, hechos_completos, clave, valor, motivo):
    hechos_completos[clave] = h(valor)
    r = sc.score(hechos_completos, icp, HOY)
    assert r["puntaje"] == 0 and r["tier"] is None and r["descalificado"]
    assert motivo in r["motivos"][0]["motivo"]
    assert r["motivos"][0]["fuente"] == "test"
    assert r["puntaje_sin_descalificar"] == 100  # el desglose se conserva


def test_sin_contractors_ni_plan_se_deriva(icp, hechos_completos):
    hechos_completos["contractors_activos"] = h(0)
    hechos_completos["plan_contratacion"] = h(False)
    assert sc.score(hechos_completos, icp, HOY)["descalificado"]
    hechos_completos["plan_contratacion"] = h(True)  # 0 contractors pero con plan: no descalifica
    assert not sc.score(hechos_completos, icp, HOY)["descalificado"]


@pytest.mark.parametrize("valor", ["libre", "pendiente"])
def test_en_crm_libre_o_pendiente_no_descalifica(icp, hechos_completos, valor):
    hechos_completos["en_crm_ontop"] = h(valor)
    assert not sc.score(hechos_completos, icp, HOY)["descalificado"]


# ---------- tiers ----------

@pytest.mark.parametrize("p,t", [(100, "T1"), (80, "T1"), (79, "T2"), (60, "T2"), (59, "T3"),
                                 (40, "T3"), (39, None), (0, None)])
def test_tiers(icp, p, t):
    assert sc.tier(p, icp) == t


# ---------- escalas ----------

@pytest.mark.parametrize("n,pts", [(0, 0), (1, 5), (9, 5), (10, 15), (19, 15), (20, 20), (49, 20), (50, 25), (500, 25)])
def test_contractors(icp, n, pts):
    assert puntos(sc.score({"contractors_activos": h(n)}, icp, HOY), "contractors_activos") == pts


@pytest.mark.parametrize("v,pts", [("Wise", 15), ("payoneer", 15), ("transferencia", 15), ("manual", 15),
                                   ("Deel insatisfecho", 10), ("remote_insatisfecho", 10), ("deel_satisfecho", 0)])
def test_metodo_pago(icp, v, pts):
    assert puntos(sc.score({"metodo_pago": h(v)}, icp, HOY), "metodo_pago") == pts


def test_deel_satisfecho_queda_en_seguimiento(icp):
    r = sc.score({"metodo_pago": h("deel_satisfecho")}, icp, HOY)
    assert r["seguimiento"] and "metodo_pago" not in [d["criterio"] for d in r["desconocidos"]]


@pytest.mark.parametrize("v,pts", [("intro_calida", 15), ("contacto_conocido", 15), ("verified", 10),
                                   ("accept_all", 4), ("solo_dominio", 0)])
def test_acceso(icp, v, pts):
    assert puntos(sc.score({"acceso": h(v)}, icp, HOY), "acceso") == pts


@pytest.mark.parametrize("tipo,fecha,pts", [("vacante", "2026-10-01", 15), ("vacante", "2026-09-01", 7),
                                            ("ronda", "2026-06-01", 15), ("ronda", "2026-04-01", 7),
                                            ("prensa", "2026-11-20", 7), ("vacante", None, 7)])
def test_disparador(icp, tipo, fecha, pts):
    assert puntos(sc.score({"disparador": h({"tipo": tipo, "fecha": fecha})}, icp, HOY), "disparador") == pts


@pytest.mark.parametrize("hechos,pts", [
    ({"entidad_extranjera": h(True), "paises": h(["CO"])}, 5),
    ({"entidad_extranjera": h(True), "paises": h(["CO", "MX", "PE"])}, 10),
    ({"paises": h("CO;MX;PE")}, 5),                       # entidad desconocida: solo el +5 por países
    ({"entidad_extranjera": h(False), "paises": h(["CO"])}, 0),  # doméstica no descalifica: no es requisito
])
def test_paises(icp, hechos, pts):
    assert puntos(sc.score(hechos, icp, HOY), "paises") == pts


@pytest.mark.parametrize("v,pts", [("Fintech", 10), ("SaaS", 10), ("HR-tech", 10), ("staffing", 10),
                                   ("creator", 10), ("ecommerce", 5), ("restaurante", 0)])
def test_vertical(icp, v, pts):
    assert puntos(sc.score({"vertical": h(v)}, icp, HOY), "vertical") == pts


@pytest.mark.parametrize("emp,etapa,pts", [(30, None, 10), (None, "Serie B", 10), (None, "Series C", 10),
                                           (10, None, 5), (None, "seed", 5), (None, "bootstrapped", 5),
                                           (40, "bootstrapped", 10),   # staffing bootstrapped no se penaliza
                                           (800, "Serie D", 5)])
def test_tamano_etapa(icp, emp, etapa, pts):
    hechos = {}
    if emp is not None:
        hechos["empleados"] = h(emp)
    if etapa:
        hechos["etapa"] = h(etapa)
    assert puntos(sc.score(hechos, icp, HOY), "tamano_etapa") == pts


# ---------- exclusiones y señales ----------

@pytest.mark.parametrize("nombre,dominio", [("Callbook", None), ("TORRE", None), ("Otra", "revelo.com"),
                                            ("Mural", "mural.co"), ("Domu", None), ("Blinka", None)])
def test_excluidos(icp, nombre, dominio):
    assert sc.is_excluded(nombre, dominio, icp)


def test_no_excluido(icp):
    assert not sc.is_excluded("Acme", "acme.com", icp)


def test_hechos_desde_senales_prioriza_manual():
    ss = [Signal("A", "vacante", "x", "Remote OK", "https://r", "2026-11-20", criterio="disparador",
                 valor={"tipo": "vacante", "fecha": "2026-11-20"}),
          Signal("A", "ronda", "y", "Crunchbase", "https://c", "2026-08-01", criterio="disparador",
                 valor={"tipo": "ronda", "fecha": "2026-08-01"}, manual=True)]
    assert sc.facts_from_signals(ss)["disparador"]["fuente"] == "Crunchbase"
