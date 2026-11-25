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
