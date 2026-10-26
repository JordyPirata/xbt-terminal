# CLAUDE.md

Proyecto con **spec driven development**. Antes de tocar código, leer
`HANDOFF.md` (estado y siguiente paso), `specs/README.md` y la tarea en curso
de `specs/mvp/tasks.md`. Al terminar una sesión, actualizar `HANDOFF.md`
(solo el estado vigente; lo que deja de serlo pasa a
`specs/archive/handoff-historia.md`). `specs/archive/` es historia: no hace
falta leerlo.

## Reglas

- Solo se implementa lo que pide una tarea `T-NN`. Si hace falta algo más, se
  propone un cambio a la spec; no se construye.
- `requirements.md` §Fuera de alcance es vinculante (nada de automatismos
  distintos del bot taker de RF-18 a RF-23, órdenes market, retiros o
  transferencias, PnL de cartera, otros venues ni pares contra stablecoins).
- Si una tarea depende de una `PA-NN` abierta, usar su supuesto por defecto y
  decirlo; si el supuesto no sirve, parar y preguntar.
- Tests antes que código, cada uno con `@pytest.mark.spec("RF-NN", ...)`.
- Al cerrar una tarea: `uv run pytest`, `uv run ruff check .`,
  `uv run python tools/spec_trace.py`, y marcar `done` en `tasks.md` (la
  tarea queda en una línea; su cuerpo, a `specs/archive/tasks-done.md`).
- Decisiones de diseño nuevas → entrada `D-NN` en `specs/mvp/decisions.md` y
  su fila en el índice de arriba.
- Una versión nueva de `requirements.md` → su "Cambio vX → vY" va a
  `specs/archive/changelog.md`, no a la cabecera.

## Código

- Python 3.11+, async en el loop de Textual. httpx y websockets, nunca requests.
- Precios y cantidades siempre `Decimal`; un `float` en dinero es un bug.
- `core/` no hace I/O ni importa de `adapters/`, `services/` o `ui/`. Única
  excepción: `core/journal.py` escribe el journal (D-55). `ui/` no importa de
  `adapters/`. `tests/unit/test_layers.py` lo vigila.
- Los secretos se manejan como `Secret` y nunca se formatean en strings.
- Identificadores en inglés; docstrings, comentarios y mensajes de UI en español.

## Referencia

`../btcb2-arb` tiene clientes síncronos de NonKYC y Neoxa ya probados en real:
consultar para auth, endpoints y rarezas de las APIs (resumen en design.md §4).
No copiar su código tal cual: es síncrono y con otro modelo.
