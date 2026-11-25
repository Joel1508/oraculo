from datetime import datetime

import pytest

from oraculo import outreach
from oraculo.sources import Signal, getonboard, group_by_domain, manual_signals, normalize_domain, remoteok, wwr

from conftest import HOY


def test_normalize_domain():
    assert normalize_domain("https://www.Acme.com/jobs?x=1") == "acme.com"
    assert normalize_domain("ana@acme.co") == "acme.co"
    assert normalize_domain("https://remoteok.com/x") is None
    assert normalize_domain("gmail.com") is None
    assert normalize_domain("") is None


def test_agrupa_por_dominio_y_une_por_nombre():
    ss = [Signal("Acme Inc", "vacante", "a", "We Work Remotely", dominio="https://acme.com"),
          Signal("acme inc", "vacante", "b", "Remote OK"),          # sin dominio: se une por nombre
          Signal("Otra", "vacante", "c", "Remote OK")]
    g = group_by_domain(ss)
    assert len(g["acme.com"]) == 2 and "sin-dominio:otra" in g


def test_remoteok_filtra_y_atribuye(icp):
    items = [{"legal": "API Terms"},
             {"company": "Acme", "position": "Backend Engineer", "location": "LATAM", "tags": [],
              "description": "", "date": "2026-11-20T00:00:00+00:00", "url": "https://remoteOK.com/remote-jobs/1"},
             {"company": "Beta", "position": "PM", "location": "US Only", "tags": [], "description": "full time"}]
    out = remoteok.parse(items, icp)
    assert [s.empresa for s in out] == ["Acme"]
    assert out[0].fuente == "Remote OK" and out[0].url.startswith("https://remoteOK.com")
    assert out[0].criterio == "disparador"


WWR = """<?xml version="1.0"?><rss><channel>
<item><title>Acme: Contract Designer</title><region>Latin America Only</region><country></country>
<type>Contract</type><pubDate>Tue, 17 Nov 2026 10:00:00 +0000</pubDate>
<link>https://weworkremotely.com/remote-jobs/acme-designer</link>
<description>&lt;p&gt;&lt;strong&gt;Headquarters:&lt;/strong&gt; Austin &lt;br /&gt;&lt;strong&gt;URL:&lt;/strong&gt; &lt;a href="https://acme.com"&gt;https://acme.com&lt;/a&gt;&lt;/p&gt;</description></item>
<item><title>Beta: Engineer</title><region>USA Only</region><type>Full-Time</type>
<link>https://weworkremotely.com/remote-jobs/beta</link><description>x</description></item>
</channel></rss>"""


def test_wwr(icp):
    out = wwr.parse(WWR, icp)
    assert len(out) == 1
    s = out[0]
    assert s.empresa == "Acme" and s.dominio == "acme.com" and s.fecha.startswith("2026-11-17")
    assert s.datos["sede"] == "Austin"


def test_getonboard(icp):
    job = lambda modality, remote=True, country="US", text="": {
        "id": f"j{modality}{country}", "links": {"public_url": "https://www.getonbrd.com/jobs/x"},
        "attributes": {"title": "Dev " + text, "remote": remote, "published_at": 1795263209,
                       "modality": {"data": {"id": modality}}, "countries": ["Remote"],
                       "company": {"data": {"attributes": {"name": "Acme", "web": "http://acme.com", "country": country}}}}}
    out = getonboard.parse([job(3), job(1), job(1, text="LATAM", country="CO"), job(3, remote=False)], icp)
    vacantes = [s for s in out if s.tipo == "vacante"]
    assert len(vacantes) == 2   # freelance remoto + mención LATAM; la de tiempo completo sin señal queda fuera
    hechos = [s for s in out if s.criterio == "entidad_extranjera"]
    assert len(hechos) == 1 and hechos[0].valor is True   # solo la de sede US
    assert all(s.dominio == "acme.com" for s in out)


def test_manual_exige_url():
    with pytest.raises(ValueError, match="falta la URL"):
        manual_signals.parse({"empresas": [{"nombre": "Acme", "senales": [{"tipo": "ronda", "texto": "x",
                                                                         "fuente": "Crunchbase"}]}]})
    ok = manual_signals.parse({"empresas": [{"nombre": "Acme", "senales": [
        {"tipo": "ronda", "texto": "x", "fuente": "Crunchbase", "url": "https://example.com"}]}]})
    assert ok[0].manual


# ---------- outreach ----------

def _icp_firmado(icp):
    v = {**icp["vendedor"], "remitente": "Laura Gómez", "cargo": "Partner", "empresa_remitente": "Oráculo",
         "url_baja": "baja@oraculo.example"}
    return {**icp, "vendedor": v}


def test_mensaje_diagnostico(icp):
    m = outreach.compose("Acme", {"texto": "Publicó la vacante remota «Dev»", "url": "https://x"},
                         {"nombre": "Ana Pérez", "cargo": "COO", "correo": "ana@acme.com", "dominio": "acme.com"},
                         _icp_firmado(icp), ["Colombia", "México"],
                         now=datetime(2026, 11, 24, 10, tzinfo=outreach.TZ))
    assert "contratando contractors en Colombia y México" in m["texto"]
    assert "publicó la vacante remota «Dev»" in m["texto"]
    assert m["cumple"], [c for c in m["cumplimiento"] if not c["ok"]]


def test_mensaje_con_marcadores_no_cumple(icp):
    m = outreach.compose("Acme", {"texto": "X"}, {"nombre": "Ana", "correo": "ana@acme.com", "dominio": "acme.com"}, icp)
    assert not m["cumple"]


def test_mensaje_que_nombra_proveedor_no_cumple(icp):
    i = _icp_firmado(icp)
    i["mensaje"] = {**i["mensaje"], "plantilla_sin_paises": i["mensaje"]["plantilla_sin_paises"] + "\nDeel es caro."}
    m = outreach.compose("Acme", {"texto": "X"}, {"nombre": "Ana", "correo": "ana@acme.com", "dominio": "acme.com"}, i)
    assert not next(c for c in m["cumplimiento"] if "proveedor" in c["regla"])["ok"]


def test_ventana_ley_2300(icp):
    domingo = datetime(2026, 11, 29, 12, tzinfo=outreach.TZ)
    assert outreach.next_send_slot(icp["mensaje"], domingo).isoformat().startswith("2026-11-30T07:00")
    festivo = datetime(2026, 12, 8, 9, tzinfo=outreach.TZ)
    assert outreach.next_send_slot(icp["mensaje"], festivo).date().isoformat() == "2026-12-09"


# ---------- ajuste 1: filtro estricto de Remote OK y WWR ----------

def _rok(location, text="", position="Engineer"):
    return {"company": "Acme", "position": position, "location": location, "tags": [],
            "description": text, "date": "2026-11-20T00:00:00+00:00", "url": "https://remoteOK.com/remote-jobs/x"}


@pytest.mark.parametrize("location,text,queda", [
    ("LATAM", "", True),
    ("Colombia", "", True),
    ("Remote - Latin America", "", True),
    ("South America", "", True),
    ("México", "", True),
    ("Perú", "", True),
    ("Remote", "We hire as independent contractor", True),
    ("Remote", "Open to candidates in Argentina", True),
    ("Worldwide", "", False),
    ("Worldwide", "contractor role", False),     # ubicación global: se descarta aunque el texto diga contractor
    ("USA only", "contractor in Colombia", False),
    ("US Only", "", False),
    ("", "contractor", False),                   # ubicación vacía: se descarta
    ("Remote", "full time employee", False),
    ("Europe", "", False),
])
def test_remoteok_filtro_estricto(icp, location, text, queda):
    out = remoteok.parse([_rok(location, text)], icp)
    assert (len(out) == 1) is queda
    assert sum(out.descartadas.values()) == (0 if queda else 1)


def test_remoteok_cuenta_descartes_por_motivo(icp):
    out = remoteok.parse([_rok("Worldwide"), _rok(""), _rok("Remote", "nothing"), _rok("LATAM")], icp)
    assert len(out) == 1
    assert out.descartadas == {"ubicacion_global_o_vacia": 2, "sin_mencion_latam_contractor": 1}
    assert out[0].datos["resolver_manual"]   # Remote OK no trae dominio


def _wwr_item(title, region, typ="Full-Time", url="https://acme.com"):
    return (f"<item><title>{title}</title><region>{region}</region><country></country><type>{typ}</type>"
            f"<pubDate>Tue, 17 Nov 2026 10:00:00 +0000</pubDate><link>https://weworkremotely.com/remote-jobs/x</link>"
            f"<description>&lt;strong&gt;Headquarters:&lt;/strong&gt; Hong Kong &lt;br /&gt;&lt;strong&gt;URL:&lt;/strong&gt; "
            f"&lt;a href=\"{url}\"&gt;{url}&lt;/a&gt; This is an independent contractor role.</description></item>")


def _wwr(*items):
    return "<?xml version=\"1.0\"?><rss><channel>" + "".join(items) + "</channel></rss>"


def test_wwr_anywhere_se_descarta_y_cuenta(icp):
    out = wwr.parse(_wwr(_wwr_item("Edward Hulton: Cold Caller", "Anywhere in the World", "Contract", "https://datva.com"),
                         _wwr_item("Acme: Designer", "Latin America Only")), icp)
    assert [s.empresa for s in out] == ["Acme"]
    assert out.descartadas == {"ubicacion_global_o_vacia": 1}


def test_wwr_tipo_contract_ya_no_basta(icp):
    out = wwr.parse(_wwr(_wwr_item("Acme: Designer", "Remote", "Contract")).replace(
        " This is an independent contractor role.", ""), icp)
    assert len(out) == 0 and out.descartadas == {"sin_mencion_latam_contractor": 1}


# ---------- ajuste 2: dominios ----------

@pytest.mark.parametrize("nombre,url,dominio,motivo", [
    ("Dropbox", "https://jobs.dropbox.com/listing/1", "dropbox.com", None),
    ("Acme", "https://careers.acme.com", "acme.com", None),
    ("Acme", "https://boards.acme.io/x", "acme.io", None),
    ("Acme", "apply.acme.com.co", "acme.com.co", None),
    ("Acme", "https://apply.workable.com/acme/", None, "ATS"),
    ("Acme", "https://jobs.lever.co/acme", None, "ATS"),
    ("Acme", "https://boards.greenhouse.io/acme", None, "ATS"),
    ("Acme", "https://jobs.ashbyhq.com/acme", None, "ATS"),
    ("Acme", "https://acme.bamboohr.com/careers", None, "ATS"),
    ("Acme", "https://acme.breezy.hr", None, "ATS"),
    ("Acme", "https://acme.recruitee.com", None, "ATS"),
    ("Edward Hulton", "https://datva.com", None, "no coincide"),
    ("Clover Health", "https://cloverhealth.com", "cloverhealth.com", None),
    ("I2B Technologies", "https://i2btech.com", "i2btech.com", None),
    ("Babou AI", "https://babou.ai", "babou.ai", None),
    ("Acme", None, None, "sin dominio"),
])
def test_company_domain(nombre, url, dominio, motivo):
    from oraculo.sources import company_domain
    d, m = company_domain(nombre, url)
    assert d == dominio
    assert (m is None) if motivo is None else (motivo in m)


def test_normalize_domain_ats_y_subdominios():
    assert normalize_domain("jobs.dropbox.com") == "dropbox.com"
    assert normalize_domain("https://acme.bamboohr.com") is None
    assert normalize_domain("https://jobs.lever.co/acme") is None


def test_wwr_dominio_que_no_coincide_queda_null(icp):
    out = wwr.parse(_wwr(_wwr_item("Edward Hulton: Cold Caller", "Latin America Only", url="https://datva.com")), icp)
    assert out[0].dominio is None and "no coincide" in out[0].datos["resolver_manual"]


def test_wwr_ats_queda_null(icp):
    out = wwr.parse(_wwr(_wwr_item("Acme: Designer", "LATAM", url="https://apply.workable.com/acme")), icp)
    assert out[0].dominio is None and "ATS" in out[0].datos["resolver_manual"]


def test_getonboard_dominio_ats(icp):
    j = {"id": "1", "links": {"public_url": "https://www.getonbrd.com/jobs/x"},
         "attributes": {"title": "Dev", "remote": True, "published_at": 1795263209, "modality": {"data": {"id": 3}},
                        "countries": [], "company": {"data": {"attributes": {
                            "name": "Acme", "web": "https://acme.bamboohr.com", "country": "US"}}}}}
    out = getonboard.parse([j], icp)
    assert all(s.dominio is None for s in out) and "ATS" in out[0].datos["resolver_manual"]


def test_prospecto_sin_dominio_marca_resolver_manual(icp):
    from oraculo import run
    g = group_by_domain(remoteok.parse([_rok("LATAM")], icp))
    ps, _ = run.build(icp, g, [])
    assert ps[0]["dominio"] is None and ps[0]["resolver_manual"]


def test_orden_desempate():
    from oraculo import run
    ps = [{"nombre": "A", "puntaje": 15, "cobertura": 15, "fecha_ultima_senal": "2026-10-01"},
          {"nombre": "B", "puntaje": 15, "cobertura": 15, "fecha_ultima_senal": "2026-11-20T10:00:00+00:00"},
          {"nombre": "C", "puntaje": 15, "cobertura": 25, "fecha_ultima_senal": None},
          {"nombre": "D", "puntaje": 30, "cobertura": 10, "fecha_ultima_senal": None}]
    assert [p["nombre"] for p in sorted(ps, key=run.sort_key)] == ["D", "C", "B", "A"]
