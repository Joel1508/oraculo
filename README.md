# Oráculo

Prospección B2B con señales públicas y puntaje explicable. La primera ICP es **Ontop**:
empresas que contratan y pagan contractors en LATAM (`icp/ontop.yaml`).

```bash
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
cp .env.example .env                                   # HUNTER_API_KEY (opcional)
.venv/bin/python -m pytest -q

.venv/bin/python -m oraculo.crm importar data/seed/ontop_crm_v0.csv
.venv/bin/python -m oraculo.run --sync-crm             # fuentes -> puntaje -> crm.csv
.venv/bin/python -m oraculo.crm recalcular
.venv/bin/python -m oraculo.crm etapa <id> Engaged
.venv/bin/python -m oraculo.crm depurar [--aplicar]     # limpia filas auto-* sin trabajo humano
```

## Lo que hace el código

| Paso | Módulo | Qué hace |
|---|---|---|
| Señales automáticas | `sources/getonboard.py` | API pública de Get on Board (`/api/v0/search/jobs`): vacantes remotas freelance o que mencionan contractor/LATAM. Trae el dominio y el país de la empresa. Si la sede está fuera de LATAM, registra el hecho `entidad_extranjera`. |
| | `sources/remoteok.py` | API JSON de Remote OK. Cada señal enlaza a la vacante en Remote OK y la nombra como fuente, como piden sus términos. No usa su logo. Remote OK no publica el dominio de la empresa, así que esas empresas quedan con `resolver_manual`. |
| | `sources/wwr.py` | RSS de We Work Remotely. Saca el dominio de la URL de la empresa que trae el aviso. |
| Filtro estricto (Remote OK, WWR) | `sources.global_filter` | Una vacante se queda solo si la ubicación o el texto mencionan LATAM, Latin America, South America, Colombia, México, Argentina, Chile, Perú o contractor. Se descarta si la ubicación es global o de otra región (Worldwide, USA only, Europe…) o está vacía. La salida cuenta los descartes por fuente y motivo. Las listas se editan en `filtro_global`. |
| Dominio | `sources.company_domain` | Nunca toma como dominio un ATS (Workable, Lever, Greenhouse, Ashby, BambooHR, Breezy, Recruitee…). `jobs.`, `careers.`, `boards.` y `apply.` se reducen al dominio raíz (`jobs.dropbox.com` → `dropbox.com`). Si el nombre no corresponde al dominio (p. ej. «Edward Hulton» y datva.com), el dominio queda en null. En todos esos casos la empresa queda marcada con `resolver_manual`. |
| Señales curadas | `sources/manual_signals.py` | Lee `data/manual_signals.json`. Una señal sin URL de fuente hace fallar la carga. |
| Agrupar | `sources.group_by_domain` | Agrupa las señales por dominio. Una señal sin dominio se une por nombre; si no se puede, queda como `sin-dominio:<nombre>` para resolverla a mano. |
| Puntaje | `score.py` | 0-100 con los pesos de la ICP. Un dato desconocido vale `null` y suma 0. Cada puntaje lista los criterios conocidos con la fuente y la URL de cada dato, los desconocidos y la `cobertura` (peso con datos). Los descalificadores dejan el puntaje en 0 con su motivo. Asigna tiers T1/T2/T3. |
| Exclusiones | `score.is_excluded` | `excluir`: Callbook, Domu, Blinka, Mural, Revelo y Torre, por nombre o dominio. `excluir_grandes`: enterprise con entidades o EOR propios, fuera de ICP Core (Dropbox, GitLab, 6sense…). Se edita en la ICP y cada exclusión sale con su motivo. |
| Orden | `run.sort_key` | Puntaje desc, después cobertura desc, después la señal más reciente. `--top N` (por defecto 25) define cuántos se muestran. |
| CRM | `crm.py` | Importa la semilla y agrega columnas. Recalcula puntaje, tier y cobertura. Etapas: Identified → Researched → Contacted → Engaged → Qualified → Introduced → Activated. |
| Depurar | `crm depurar` | Revisa las filas `auto-*` que nadie ha tocado (etapa Identified, sin contacto ni datos de investigación). Elimina las excluidas y las que ya no tienen señales con las reglas actuales, y deja en null los dominios de ATS o que no coinciden con el nombre. Por defecto solo muestra el plan; con `--aplicar` guarda una copia en `crm.csv.bak`. |
| Compuerta | `crm.blockers` | **Contacted**: `en_crm_ontop` ≠ `tomado` y además (correo corporativo válido **o** contacto manual con `contacto_nombre` por un `canal_contacto` distinto de email: linkedin, whatsapp, presencial o intro). **Engaged o más**: `fecha_conversacion`. **Introduced o más**: `acepta_intro = si`, `contractors_n`, `paises`, `metodo_pago` y `contacto_sabe_ontop = si`. |
| Descalificador | `crm.validate_disqualifier` | `descalificador` puede ser `eor_only`, `domestico`, `sin_contratacion` o `ninguno`; vacío = desconocido. Cualquier valor distinto de `ninguno` deja el puntaje en 0, sin tier y con el motivo. Exige `descalificador_fuente` (llamada, correo o web) y `descalificador_fecha`. Si la fuente es web exige también `descalificador_url`; una llamada no necesita URL. Si falta algo, el descalificador no se aplica y sale como aviso. |
| Entidad extranjera | `crm.prefill_from_signals` | `entidad_extranjera` (si/no/vacío) y `pais_entidad`. Con `si` suma los +5 de países. Se prellena desde Get on Board cuando la sede está fuera de LATAM, con la fuente en `entidad_extranjera_fuente`. Nunca pisa un valor que ya esté. |
| Teléfonos | `crm.phone_errors` | El CRM nunca guarda números de teléfono. `crm.csv` no se escribe si algún campo de texto contiene uno. |
| Decisores | `discover.py`, `enrich.py` | Hunter Domain Search (`--discover`) y Email Finder (`crm buscar-correo`). Solo correos del dominio de la empresa, con caché y tope de llamadas. |
| Mensaje | `outreach.py` | Diagnóstico a partir de la señal concreta, sin nombrar ni atacar al proveedor actual. Aplica la ventana horaria de la Ley 2300 y como máximo un contacto por semana. Hasta que se llenen los datos del remitente en la ICP, el mensaje se marca como "no cumple". |
| Fase 2 (interfaz) | `enrich_company.py` | Contrato Pydantic para extraer datos de **empresa** desde una URL pública con Claude. Cada campo lleva su cita y su URL; sin cita, el campo es null. Aún no está implementado. |

## Lo que se investiga a mano

El código no hace scraping de estas fuentes. Se leen en el navegador y se registran con su URL:

- **YC, Crunchbase, Clutch, Wellfound, Fedesoft y prensa** → `data/manual_signals.json`, con URL obligatoria. Ejemplo en `data/manual_signals.example.json`. De aquí salen rondas, etapa, número de empleados, vertical y entidad extranjera.
- **Lo que solo se sabe conversando** → columnas de `crm.csv`: `contractors_n`, `paises`, `metodo_pago`, `proveedor_actual`, `fecha_conversacion`, `acepta_intro` y `contacto_sabe_ontop`.
- **`en_crm_ontop`** (`pendiente` / `libre` / `tomado`): lo confirma Ontop. Con `tomado`, la empresa queda descalificada y no se puede contactar.
- **Dominio** de las empresas que llegan sin él (`sin-dominio:*`).
- **Contacto manual** con `crm contacto <id> --nombre ... --email ...`, que queda con `contacto_fuente=manual` y `email_status=unknown`.

## Regla de contactos

- Los contactos solo vienen de **(a) Hunter**, con su `email_status` (`verified` | `accept_all` | `unknown`), o de **(b) ingreso manual** marcado `contacto_fuente=manual`.
- **Ningún LLM genera ni completa nombres, correos, cargos ni LinkedIn.** El esquema de la Fase 2 prohíbe campos de personas.
- Sin `email_status`, el estado es `unknown` y el criterio acceso no suma puntos.
- LinkedIn: solo el que devuelve Hunter, y solo en `crm.csv`. Nunca en el JSON público.

Los tests están en `tests/test_contacts.py`.

## Cumplimiento (Ley 1581 de 2012)

- Solo datos públicos y correos corporativos del dominio de la empresa. Se rechazan gmail, hotmail, outlook y similares.
- Nada de scraping de LinkedIn ni de celulares personales. El sistema no guarda teléfonos.
- `public/prospects.json` no lleva nombres ni LinkedIn y enmascara los correos (`a***@empresa.com`).
- `crm.csv`, `data/seed/`, `data/manual_signals.json`, `out/` (cachés) y `.env` están en `.gitignore`.

## Supuestos del puntaje (editables en `icp/ontop.yaml`)

- **acceso**: con `fecha_conversacion`, los puntos dependen de `contacto_rol`: decisor = 15, influenciador que puede presentar al decisor = 10, otro = 4, sin rol = 0. Si además hay un correo verificado, cuenta la evidencia de más puntos. La intro cálida (15) entra como hecho `acceso: intro_calida` en las señales manuales.
- **vertical**: busca palabras clave dentro del texto de `segmento` ("Consultora / staffing TI" → staffing = 10). Si no reconoce ninguna ("Startup CO multi-país"), queda null.
- **tamano_etapa**: Serie D o posterior = 5 aunque tenga más de 20 empleados. Bootstrapped con 20 o más empleados = 10.
