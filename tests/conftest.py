from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
HOY = date(2026, 11, 24)


@pytest.fixture(scope="session")
def icp():
    return yaml.safe_load((ROOT / "icp" / "ontop.yaml").read_text(encoding="utf-8"))


def h(valor, fuente="test", url="https://example.com/fuente"):
    return {"valor": valor, "fuente": fuente, "url": url}


@pytest.fixture
def hechos_completos():
    """Empresa ideal: 100 puntos."""
    return {
        "contractors_activos": h(60),
        "metodo_pago": h("wise"),
        "acceso": h("intro_calida"),
        "disparador": h({"tipo": "vacante", "fecha": "2026-11-01"}),
        "entidad_extranjera": h(True),
        "paises": h(["CO", "MX", "AR"]),
        "vertical": h("staffing"),
        "empleados": h(30),
        "etapa": h("bootstrapped"),
    }
