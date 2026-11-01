# Cómo aportar

xbt-terminal lo mantiene un grupo pequeño. No buscamos volumen de PRs, sino
cambios que se entiendan, estén probados y sigan funcionando dentro de un año.
Si es tu primera vez, empieza por leer `HANDOFF.md` (dónde está el trabajo) y
`specs/README.md` (cómo se trabaja).

## El proyecto se guía por su spec

La spec (`specs/mvp/`) manda sobre el código. Nada se construye si no lo pide una
tarea `T-NN` de `specs/mvp/tasks.md`, y `requirements.md` §Fuera de alcance es
vinculante: lo que aparece ahí no se acepta en una PR, por buena que sea, hasta que
una versión nueva de la spec lo abra.

`requirements.md` y su §Fuera de alcance solo los cambia el dueño del producto.
`design.md`, `tasks.md` y `decisions.md` los puede proponer cualquiera en su PR.

## El ciclo

1. **Propuesta.** Para algo que no está en `tasks.md` (un venue, un par, una
   estrategia, un cambio de comportamiento), abre antes un issue que empiece por
   `Propuesta:` y cuente qué quieres, por qué y qué métrica o caso mejora. Un
   arreglo pequeño o una tarea `T-NN` ya escrita no necesitan propuesta.
2. **Spec.** Aprobada la propuesta, el cambio de spec va primero: una versión
   nueva de `requirements.md` si toca el qué (su "Cambio vX → vY" va a
   `specs/archive/changelog.md`), una entrada `D-NN` en `decisions.md` con su fila
   en el índice, y las tareas en `tasks.md`.
3. **Tests primero.** Cada test lleva el requerimiento que verifica:

   ```python
   @pytest.mark.spec("RF-10", "CA-04")
   def test_rechaza_cruce_de_orden_propia(): ...
   ```

4. **Código.** Lo mínimo para que pasen los tests.
5. **PR.** Enlaza la propuesta y la tarea. Antes de abrirla, tienen que pasar:

   ```sh
   uv run pytest
   uv run ruff check .
   uv run python tools/spec_trace.py
   ```

## Reglas de código

- Python 3.11+, async en el loop de Textual. httpx y websockets, nunca requests.
- Precios y cantidades siempre `Decimal`: un `float` en dinero es un bug.
- `core/` no hace I/O ni importa de `adapters/`, `services/` o `ui/` (salvo
  `core/journal.py`). `ui/` no importa de `adapters/`. Lo vigila
  `tests/unit/test_layers.py`.
- Los secretos se manejan como `Secret` y nunca se formatean en strings.
- Identificadores en inglés; docstrings, comentarios y mensajes de UI en español.

## Qué se puede proponer (fase 3, D-79)

- **Pares contra BTC**, en cualquier venue que ya esté soportado.
- **Venues nuevos:** el adaptador implementa la interfaz común (RNF-09) y pasa la
  batería de tests de contrato con fixtures grabados en real del venue. Lo que
  compruebes de su API, apúntalo en `docs/api/` y en `tests/fixtures/`.
- **Estrategias:** una función pura en `core/` que propone una orden doble, o
  ninguna, con su motivo. Las validaciones (RF-10), los límites (RF-22) y el paro
  ante anomalías (RF-21) son del daemon y valen para todas: una estrategia no
  puede saltárselos. Toda estrategia nueva, o un cambio de comportamiento en una
  existente, trae su resultado sobre books grabados.

## Pares contra stablecoins

Se aceptan, pero solo con una **propuesta aprobada y una PR**. Hay un motivo para
esta exigencia: el emisor de una stablecoin puede congelar saldos aunque el
exchange funcione bien (D-08), y por eso todo el proyecto se valora en BTC.

**La propuesta** (issue `Propuesta: par <BASE>/<STABLE> en <venue>`) responde a
todo esto:

1. **Qué stablecoin:** ticker, emisor, red, y si el emisor puede congelar o
   poner en lista negra direcciones (casi todos pueden). Enlaza dónde lo dice.
2. **Exposición máxima:** el tope de saldo en esa stablecoin por venue,
   expresado en BTC, y qué pasa al superarlo (se rechazan las órdenes que lo
   aumenten).
3. **Valoración en BTC:** nocionales, topes de RF-10, resultados (RF-25) y valor
   de la cuenta (RF-32) siguen en BTC. Di con qué precio se convierte (qué par y
   de qué venue) y qué pasa si ese precio falta o está viejo (no se opera).
4. **Apagado por defecto:** el par solo se opera con una clave explícita en la
   config, y la pantalla lo marca para que nadie lo opere sin darse cuenta.
5. **Por qué merece la pena:** qué liquidez o qué ruta abre que no dé un par
   contra BTC.

**La PR**, una vez aprobada la propuesta, trae:

- La versión nueva de `requirements.md` con su `D-NN`: cambia RNF-10 y el riesgo
  de congelamiento de §Supuestos y riesgos para ese par.
- Fixtures grabados en real del par (books, órdenes, fills) y sus tests,
  incluidos los de los cinco puntos de arriba: el tope, la conversión, el precio
  que falta y el apagado por defecto.
- El README y `docs/uso.md` actualizados, con el aviso de riesgo de ese par.

## Qué no se acepta

Aunque venga con tests: órdenes market, retiros o transferencias hechos por el
programa, cobertura o reintento automático de piernas, fondos de terceros,
control por red, y telemetría (nada sale de la máquina del usuario). La lista
completa está en `requirements.md` §Fuera de alcance.

## Seguridad

- Nunca pegues keys, `.env`, journals reales ni direcciones propias en issues,
  PRs o fixtures. Los fixtures se redactan antes de subirlos.
- Un fallo de seguridad (una key que se filtra a un log, por ejemplo) no va a un
  issue público: escribe al mantenedor por privado.

## Licencia

El proyecto es AGPL-3.0 (D-74). Al aportar, tu código entra con esa licencia.
