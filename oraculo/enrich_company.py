"""FASE 2 (interfaz, sin implementar): datos de EMPRESA desde una URL pública con Claude.

Flujo previsto:
  1. fetch_page(url): descarga con requests (sin Firecrawl), respeta robots.txt, extrae el texto.
  2. extract(url): Claude (ANTHROPIC_API_KEY, modelo MODEL) con salida estructurada validada por
     Pydantic -> client.messages.parse(model=MODEL, ..., output_format=EmpresaExtraida).
  3. verify_quotes(): cada cita debe aparecer literalmente en el texto descargado; si no, el campo
     queda en null. Las citas no verificadas nunca llegan al puntaje.
  4. to_signals(): convierte el resultado en Signal(manual=False, url=<la URL de la cita>).

Límites (también los impone el esquema):
  - Solo datos de la empresa. El esquema prohíbe campos extra: no hay dónde poner nombres,
    correos, cargos, teléfonos ni LinkedIn de personas.
  - Cada campo lleva la cita textual y la URL de donde salió. Sin cita, el valor es null.
"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

MODEL = "claude-opus-5-5"
T = TypeVar("T")


class Citado(BaseModel, Generic[T]):
    """Un valor con su cita textual y la URL de origen. Sin cita o sin URL, el valor es null."""
    model_config = ConfigDict(extra="forbid")

    valor: T | None = None
    cita: str | None = Field(default=None, description="Texto copiado literalmente de la página")
    url: str | None = Field(default=None, description="URL pública de donde sale la cita")

    @model_validator(mode="after")
    def _sin_cita_es_null(self):
        if not (self.cita and self.cita.strip()) or not (self.url and self.url.startswith(("http://", "https://"))):
            self.valor, self.cita, self.url = None, None, None
        return self


class EmpresaExtraida(BaseModel):
    """Salida de la extracción. Solo hechos de empresa; nunca datos de personas."""
    model_config = ConfigDict(extra="forbid")

    paises_operacion: list[Citado[str]] = Field(default_factory=list)
    contrata_contractors: Citado[bool] = Field(default_factory=Citado[bool])
    vertical: Citado[str] = Field(default_factory=Citado[str])
    senales: list[Citado[str]] = Field(default_factory=list)


def fetch_page(url: str) -> str:
    """Descarga la página con requests y devuelve su texto plano."""
    raise NotImplementedError("Fase 2")


def extract(url: str) -> EmpresaExtraida:
    """Descarga la URL y extrae EmpresaExtraida con Claude (salida estructurada, Pydantic)."""
    raise NotImplementedError("Fase 2")


def verify_quotes(res: EmpresaExtraida, texto_pagina: str) -> EmpresaExtraida:
    """Pone en null todo campo cuya cita no aparezca literalmente en texto_pagina."""
    raise NotImplementedError("Fase 2")
