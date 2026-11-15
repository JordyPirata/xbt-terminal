# HANDOFF — dónde quedó el trabajo

Solo el estado vigente. Lo viejo (qué se hizo en cada tarea, estados de otros
días) está en `specs/archive/handoff-historia.md`. Leer esto antes de tocar nada;
después `specs/README.md` y la tarea siguiente en `specs/mvp/tasks.md`.

## Estado (2026-11-14)

- **Draft PR de T-100:** rama `t-100-neoxa-openapi`, PR en borrador contra `main`
  (enlace en la PR; el borrador está en la descripción). Se revisa antes de fundir.
- **Comprobado 2026-11-14:** el bundle de `/api-docs` ha cambiado de nombre
  (`index-DLyC5FvO.js` → `index-klQvhdbp.js`). Las 56 operaciones, sus schemas y la
  guía son idénticos; solo cambia el hash de `info`. El spec del repo no se ha
  regenerado por eso. La v2 sigue con 404 en `/api/v2/*` (`time`, `balances`).
- **T-100 en curso (`doing`, D-84), rama `t-100-neoxa-openapi`:**
  `docs/api/neoxa/openapi.json` (56 operaciones: v2, v1 y wallet) generado del bundle
  de `https://neoxa.exchange/api-docs` por `tools/neoxa_openapi.py`; reproducible
  byte a byte. `tests/adapters/test_neoxa_openapi.py`: 31 pasan y 2 `xfail` estrictos
  (POST order y `trades_my` con `fee_token: null`). Suite completa verde salvo el
  test de timing intermitente de siempre (aislado, pasa). `jsonschema` en dev.
  Los dos ficheros de Neoxa se editaron en disco tras la última sesión: los tests y el spec siguen igual.
- **Bloqueo: la v2 no responde en vivo.** `GET /api/v2/*` devuelve 404 en
  `neoxa.exchange` (también `api.neoxa.exchange`); la v1 responde. Sin eso no hay
  fixtures v2 ni se puede contrastar T-101 (migración del adaptador, `todo`).
  Pregunta abierta para el usuario: ¿cerrar T-100 sin fixtures v2 cuando la v2 esté
  desplegada, o esperar?
- **Pendiente de decisión:** T-101 (migrar el adaptador a v2) no se ha empezado; su
  D-NN está por escribir. El book v2 trae el pool en otros niveles que la v1 y puede
  cambiar lo que el bot decide.
- **M20 (D-83):** la PR de T-98 ya está fundida en `main` (`ce7728f`).

## Estado (2026-11-09)

- **M20 — Nombres descriptivos (D-83), rama `t-98-descriptive-names` (PR fundida en `main`):**
  T-98 y T-99 hechas. Nombres completos en `src/`, `tests/` y `tools/`, con dos
  excepciones (`tests/unit/bot_reference.py`, oráculo congelado, y el campo `t` de
  `services/recording.py`); guarda en `tests/unit/test_names.py`. Comentarios: solo se
  quitaron los que repetían el código (criterio elegido). Pendiente de la PR: revisión
  y merge.
- **Test de timing intermitente:** `tests/ui/test_orders_ui.py::test_dialog_does_not_wait_for_network`
  (umbral de 0,3 s) falló una vez en la suite completa con carga; en aislado pasa
  3 de 3. No tiene relación con los renombrados.
- **M19 — Rendimiento, en `tasks.md`:** T-92, T-94 y T-97 hechas; T-93 descartada
  (en `cooldown` el bot no evalúa); quedan T-95 (agrupar evaluaciones, empieza por su
  D-NN) y T-96 (restos menores). Ninguna cambia decisiones del bot.
- **T-97 hecha:** índice de órdenes abiertas por (venue, lado) en
  `core/results.py`; `compute` con 1500 dobles de 622 ms a 60 ms. Oráculo con el
  `_owner` anterior en `tests/unit/test_results_owner.py`.
- **T-94 hecha:** caché por fichero en `ResultsService` (tamaño, `mtime`,
  offset) y recarga periódica en `asyncio.to_thread`. Ganancia pequeña (`todo` de
  ~0,93 s a ~0,58 s): lo caro es `compute`, no leer.
- **T-92 hecha:** `_views` usa `L2Book.best_levels` sin copiar y
  `_check_decimals` cachea campos por clase; en la muestra, `_evaluate` de 0,93 s a
  0,25 s. `tests/fixtures/replay/decisiones.txt` guarda las 542 decisiones de la
  muestra: si un cambio legítimo del bot las altera, se regenera con
  `uv run python -m tests.services.test_bot_decisions_replay`. No desplegada.
- **Revisión del bot en live (2026-11-07, journal del servidor del 10-22 al 11-08,
  la revisión que pedía D-75):** desde T-78 (10-29 20:49 UTC), 1545 disparos,
  +0.00162 BTC en las cerradas y ninguna cerrada con pérdida. 18 `hanging_leg`
  (1.2 %, antes de T-77 era el 3.2 %): 14 en la pierna de NonKYC que no llena y 4
  "sin enviar" el 11-03 entre las 04:02 y las 04:22 UTC (Neoxa `order_unknown` y
  después `filled`). Lo real supera a lo esperado en +0.98 pp: cada pierna llena
  ~0.35 % mejor que su límite y la fee real es 0.105 % (AMM de Neoxa a 0 y NKYC).
  De 240 h, el bot estuvo 40 h parado, unas 13 h esperando `bot on` tras una
  colgada. El 49 % de los disparos salen ≤ 20 s después del anterior (el
  cooldown de 15 s frena). `max_move_pct` no separa las colgadas. Config del
  servidor cambiada y daemon reiniciado el 11-08 00:13 UTC con `min_net_pct =
  "0.8"` (antes 0.4) y `max_daily_btc = "0.02"`; sin `bot_state` desde entonces.
  Propuestas, pendientes de que el usuario decida: en la respuesta de esa sesión.
  Scripts en el scratchpad de esa sesión (no en el repo).

## Avisos operativos

- **Socket:** la ruta no puede pasar de 107 bytes (AF_UNIX). Con un `--data-dir`
  largo, usar `--socket`. El default XDG cabe.
- **Colores por SSH:** `sshd` no pasa `COLORTERM`; sin él, Textual baja a 256
  colores y el fondo de las ventanas sale azul marino. Lanzar la pantalla con
  `env COLORTERM=truecolor xbt` (o `SendEnv`/`AcceptEnv COLORTERM`).
- **Pantalla:** tras escribir un comando, el foco queda en la línea. Pulsar
  `Esc` antes de `q` para salir. `q` solo desconecta: el daemon sigue.
- **NonKYC y NKYC:** no se ha comprobado si `/cancelallorders` respeta `symbol`.
  No usar `x` con órdenes de NKYC abiertas.
- **`VirtualLoop` y `_clock_resolution`:** con la hora epoch, sumar 1 ns a un
  float no lo mueve: un temporizador que vence justo ahora no se dispara nunca y
  el bucle gira sin fin. Por eso `CLOCK_RESOLUTION_S` = 1 µs.
- **Fees:** NonKYC cobra ~0.149 % en NKYC (102 fills desde el 2026-10-25 05:24
  UTC más d-fb32e2). Neoxa cobra el 0.2 % completo en BTC (109 fills y
  d-fb32e2): el `fee_discount_pct = "50"` de la config no se cumple. Los fills
  contra el AMM de Neoxa salen con fee 0.
- **Fee de NKYC al reiniciar:** desde T-68, una corrección que llegue con el
  daemon parado se apunta al arrancar, si el trade sigue entre los 50 más
  recientes de NonKYC. d-550cd7 (16:08 UTC, 4 fills con 0.2 % en BTC) quedó
  fuera: sin corregir.
- **Ver el journal:** `~/.local/state/xbt/journal-YYYYMMDD.jsonl`; el daemon
  escribe `diag.log` y la pantalla `client.log`.
- **Tests inestables:** `test_dialog_does_not_wait_for_network` (límite de 0.3 s
  de reloj real) y, una vez, `test_both_filled_cools_down` (timeout) fallan a
  veces con la suite entera; por separado pasan.

## Config real del usuario

En el **servidor**: `~/.config/xbt/config.toml` y `.env` (`0600`), que solo
lee el daemon; journal, lock y socket en `~/.local/state/xbt`. La copia local
es la de antes de la migración.
- `[venues.nk]`: `fee_discount_pct = "25"`, `fee_token = "NKYC"`,
  `fee_token_min = "1"`. Neoxa: `fee_discount_pct = "50"`.
- `[inventory]`: `target_base_pct = "25"`, `band_pct = "10"`.
- Bot: `max_move_pct = "1.0"`, `move_window_ms = 2000` (D-71).
- Bot: `min_net_pct` 0.4, `max_qty` 0.05, `cooldown_s` 15, `max_daily_btc` 0.01 y
  `max_walk_pct = { nk = "0.8" }` (en la config y disparando en live).
- Comisiones de retiro: NonKYC BTC (`BTC-MAIN`) 0.00005, Neoxa BTC 0.0001 (aparte).
- Inventario (2026-10-23): NonKYC 23.0 %, Neoxa 27.7 %, total 25.5 % de XBT.
- Daemon: servicio systemd de sistema `xbt` en el servidor, configurado por el
  usuario (el ejemplo de `docs/despliegue.md` es `--user`). El bot arranca
  `off` también en el daemon.

## Preguntas abiertas que siguen vivas

| ID | Qué | Bloquea |
| --- | --- | --- |
| PA-09 | ¿Neoxa acepta y devuelve un client_id? Supuesto: no; se reconcilia por lado, precio y cantidad (D-26) | — |
| PA-10 | ¿NonKYC descuenta la comisión de retiro de lo enviado? Supuesto: sí (RF-29, D-53) | — |
| PA-11 | ¿Los books grabados del banco de pruebas van en el repo? Supuesto: solo muestras de minutos en `tests/fixtures/` (D-79) | — |
| PA-12 | ¿La interfaz de estrategia admite órdenes maker? Supuesto: no, solo límite agresiva con cancelación (D-79) | — |

Resueltas: PA-01 → D-08, PA-02 → D-10, PA-03 → D-19, PA-04 → D-42, PA-05 → D-31, PA-06 → D-07, PA-07 → D-06, PA-08 → D-15 (cerrada en D-54).

## Forma de trabajar acordada con el usuario

- Una rama por tarea (`t-NN-nombre`), tests primero con `@pytest.mark.spec(...)`,
  cerrar con pytest + ruff + `tools/spec_trace.py`, marcar `done` en tasks.md,
  anotar decisiones nuevas como `D-NN` en decisions.md.
- Commit al cerrar cada tarea, `merge --ff-only` a `main` y borrar la rama.
  El usuario ha ido aprobando "commit, fusiona y sigue" tarea a tarea; si una
  tarea necesita una decisión suya (una PA), parar y preguntar antes.
- Un cambio de alcance (como el paso a BTC) va primero a la spec: nueva versión
  de requirements.md, `D-NN` y tarea propia, y solo después código.
- Commits en inglés, sin `Co-Authored-By` (desde el squash al remoto privado).
- Código en inglés (identificadores, comentarios, docstrings, logs, nombres de
  test); en español, lo que ve el usuario, los docs y la spec (D-81, T-91).
- Al cerrar la sesión, actualizar este fichero.

## Trampas encontradas

- **Mutation testing a mano y `.pyc`**: al estropear un fichero y restaurarlo
  con `cp`, si el tamaño y el mtime (al segundo) coinciden, Python usa el
  bytecode viejo y un test falla sin motivo. Borrar `__pycache__` tras cada cambio.
- **Timestamps de test**: comprobar que los `T0` estén alineados al timeframe
  (`T0 % 3_600_000 == 0`); hay un test que lo vigila en test_candles.py.
- **ruff y caracteres raros**: `×` está permitido en `allowed-confusables`; el
  signo menos `−` no: usar `-`. Los tests que prueban `%` y `.format` a
  propósito llevan `# noqa: UP031, UP032`.
- **`core/` no importa de fuera de `core/`**: por eso `RiskLimits` está en
  `core/risk.py` y `config.py` la importa, no al revés.
- Los tests de riesgo y modelos usan mercados ficticios en USDT (anteriores a la
  v0.2): prueban lógica genérica y valen, pero los tests nuevos deben usar BTC.
- **Consumir `market_stream` con `aclosing()` o cancelando la tarea.** Un `break`
  solo se evalúa al llegar un evento, y con el mercado quieto pueden pasar minutos.
- **Trades duplicados con las velas** (para MarketHub, T-12/T-13): `recent_trades()`
  y el WS deduplican entre sí, pero un trade que ya cuenta en la última vela del
  histórico REST sumaría volumen dos veces si se pasa a `CandleBuilder.on_trade`.
  Al cargar el histórico, no pasar a las velas los trades con `ts` anterior a la carga.
- **`git stash --keep-index` + `stash pop` choca** con lo que hay en staging
  (conflictos en cada fichero tocado por las dos partes). Si pasa, el stash se
  conserva: `git checkout stash@{0} -- .` y `git read-tree stash@{0}^2` dejan
  árbol e índice como estaban.
- **Markup de Rich/Textual en textos con corchetes**: `Static(texto)` y
  `notify(texto)` interpretan `[y]`, `[red]`… y un `[/bold]` suelto lanza
  `MarkupError`. Texto que venga del venue o con teclas entre corchetes:
  `markup=False` (o `rich.text.Text`).
- **`Input` de Textual con `select_on_focus=True` por defecto**: al recibir el
  foco selecciona todo y la primera tecla lo sustituye. La línea de comandos lo
  lleva a False; un test lo vigila.
- Las velas de 1m de Neoxa vienen dispersas (sin vela en los minutos sin trades):
  50 velas pedidas se convierten en ~235 tras `CandleBuilder.load`.
- **Cola de recepción de websockets (16 frames):** si el consumidor deja de leer
  y cierra con frames encolados, el cierre limpio espera `close_timeout`.
  Por eso `WS_CLOSE_TIMEOUT_S = 1.0`; sin eso, salir tardaría 10 s. Hay un test.
- **NonKYC corta la conexión WS ante una petición errónea** (símbolo o método
  inexistente). No mandar peticiones de prueba por la conexión buena.
- `/market/trades` de NonKYC no coincide con su OpenAPI (`id`, `type`,
  `baseVolume`, `timestamp` en ms): manda lo grabado, no la OpenAPI.
- **Textual: `on_resize` llega antes de que `app.size` se actualice**: usar
  `event.size`. Y una fila `auto` del grid no respeta `styles.height` del hijo:
  las alturas se fijan en `grid_rows`.
- **Capturas de la UI**: `app.export_screenshot()` da un SVG. Para verlo:
  `uv run --no-project --with cairosvg python -c "import cairosvg; ..."` (sin
  tocar las dependencias del proyecto); los marcos salen como cuadraditos por la fuente.
- `ruff format` no se aplica al repo entero (test_candles y test_config no están
  formateados); formatear solo los ficheros nuevos.
- **respx: gana la primera ruta que encaja.** Una ruta más concreta añadida en
  el test después de una regex del fixture no se usa nunca: cambiar el
  `side_effect` de la ruta del fixture (con `name=`).
- **NonKYC firma la url exacta:** construir la query con `urlencode` y pasar la
  url ya hecha a httpx (no `params=`), o la firma no coincide.
- **Commits firmados con GPG:** si el pinentry no se atiende a tiempo, `git
  commit` falla con "signing failed: Timeout" u "Operation cancelled" y no
  deja commit (lo preparado sigue en el índice). Repetir cuando el usuario
  esté para desbloquear la clave.
- **Refactors sin cambio de comportamiento (D-77):** tests de caracterización
  primero (en verde con el código de antes), y medir antes y después si el
  código está en un camino caliente: partir `wire.decode` sin caché la hizo un
  23 % más lenta.
- El trazador cuenta un ID como cubierto con un solo test: RF-05 y RNF-03 solo
  tienen tests de topes en la config, no del comportamiento real.
