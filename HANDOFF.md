# HANDOFF — dónde quedó el trabajo

Solo el estado vigente. Lo viejo (qué se hizo en cada tarea, estados de otros
días) está en `specs/archive/handoff-historia.md`. Leer esto antes de tocar nada;
después `specs/README.md` y la tarea siguiente en `specs/mvp/tasks.md`.

## Estado (2026-10-29, 20:30 UTC)

- Spec en **v0.29** (D-76, T-78 `todo`): el book quieto se contrasta con el REST
  cada `book_check_s`; la conexión viva ya no basta como frescura. Hechas T-61
  a T-65 y T-67 a T-77. 1378 tests, ruff limpio, `spec_trace` 67/74 (sin test:
  RNF-01, RNF-21/T-32, RNF-24 y CA-18 (T-78), CA-01, CA-02 y CA-10, pasados
  en real: D-20, D-43, D-57).
- **T-77 desplegada:** daemon reiniciado el 2026-10-28 23:55 UTC con
  `lead_venue = "nx"`.
- **2026-10-29, book de NonKYC congelado (D-76):** el WS siguió conectado pero
  sin `updateOrderbook` desde ~17:45 UTC; el bot disparó d-9820a4, d-f1fe90 y
  d-6eca2e (19:58 a 20:01 UTC) contra un bid que ya no estaba: 0.0234 XBT
  comprados de más en Neoxa, sin cuadrar. El usuario se volvió a suscribir y el
  book quedó bien. Hasta T-78 puede repetirse: si el mid de NonKYC en `value`
  no se mueve, volver a suscribirse antes de rearmar.
- **Arquitectura:** `xbt daemon [--live]` es el único proceso que habla con
  los venues (keys, lock, journal, órdenes, bot, resultados, depósitos,
  comisiones, muestreo del valor). `xbt` (alias `attach`) es la pantalla:
  opera todo por el socket, sin config ni keys. Protocolo en design.md §6.13.
- **T-75 y T-76 (D-73, D-74, spec v0.27):** el comando pasa a `xbt` y las
  rutas a `~/.config/xbt` y `~/.local/state/xbt` (socket `xbt.sock`, lock
  `xbt.lock`); licencia AGPL-3.0; README reescrito, `docs/despliegue.md` y
  `docs/uso.md`. Hechas en el worktree `~/Code/xbt-terminal-t75` (rama
  `t-75-xbt`), fusionadas en `main`.
- **El daemon corre en el servidor** (por Tailscale; la IP no va al repo:
  usar un alias `servidor` en `~/.ssh/config`; hora local CST) desde el 2026-10-27 03:56:02 UTC: servicio systemd de
  sistema `xbt` (no `--user`; pararlo o arrancarlo pide sudo), `ExecStart=
  /home/jordypirata/.local/bin/xbt daemon --live`, `Restart=on-failure`.
  `xbt` instalado con `uv tool install --reinstall ~/xbt-terminal`, un clon
  que no llega a GitHub: el código se le sube desde local con
  `git push servidor:xbt-terminal main:refs/heads/incoming` y allí
  `git merge --ff-only incoming && git branch -d incoming`. Bot `armed` desde las
  03:56:42 UTC. Pantalla: `ssh -t servidor env COLORTERM=truecolor
  xbt`.
- **Migración desde local (2026-10-27):** daemon local parado a las 03:38 UTC
  (bot `off` desde las 03:32, nada en vuelo). `config.toml`, `.env` y todo el
  journal copiados con rsync al servidor, idénticos (sha256). Los logs del
  dry-run previo del servidor quedaron en `~/xbt-dryrun-logs/` del servidor.
  El journal sigue sin huecos salvo 03:38 a 03:56 UTC.
- **En local** (`~/Code/xbt-terminal`, paquete `xbt_terminal`) ya no corre
  daemon. El 2026-10-27 se borraron en local `~/.local/state/xbt`,
  `~/.config/xbt` (con el `.env`) y el `xbt` de `uv tool`: los únicos datos,
  config y keys son los del servidor. **No arrancar un daemon `--live` en
  local**: el lock es por máquina y no lo impide; serían dos bots con las mismas
  keys. Para probar en local, `uv run` desde el repo con otro `--data-dir`.
- **Lo último (detalle en decisions.md y `specs/archive/tasks-done.md`):**
  - T-69/T-70/T-71 (D-69): evento `value` cada 5 min en el journal; en
    resultados (`r`), `Tab` → pestaña "Valor de la cuenta"; se refresca sola
    cada 60 s; `z` → último tramo en sats (desde el último salto > 5 %, que son
    transferencias en tránsito). Serie desde 2026-10-25 22:25 UTC, sin huecos.
  - T-72 (D-70): `decide` del bot ~100× más barata con el mismo resultado
    (50 → 0.5 ms con walk). Oráculo congelado en `tests/unit/bot_reference.py`:
    un cambio de comportamiento del bot a propósito lo actualiza con su D-NN.
    Latencia en `tests/perf/` (`uv run pytest tests/perf -s`).
  - T-73 (D-71): con `max_move_pct`, el bot no dispara si el mid de un venue se
    movió más de ese % en la ventana (no espera: mira hacia atrás; tras un salto
    tarda hasta `move_window_ms` en volver a disparar). `bot_fire.move` apunta el
    movimiento de cada venue, con filtro o sin él. Al frenar, `diag.log` dice
    "sin disparar por movimiento brusco".
  - T-74 (D-72): la ventana de T-73 olvidaba también el mid vigente al
    empezarla; tras un book quieto, un salto en un solo cambio daba `move` 0.
    Así disparó d-60003f (23:42:42 UTC, venta en Neoxa colgada, 0.0038 XBT
    comprados de más en NonKYC). Daemon con T-74 desde el 2026-10-27 00:01:59
    UTC, bot `armed` desde las 00:03:52. Primeras dobles con el arreglo:
    d-052717 (23:57:40, +1.41 %) y d-0fbda9 (00:05:05), las dos llenas y
    disparadas ~2 s después de que el filtro frenara un barrido en Neoxa.
- **Piernas colgadas:** 20 de 634 dobles del bot en live (2026-10-21 a 27): 14
  en Neoxa y 6 en NonKYC. Las dos últimas, d-8c32f8 (20:55 UTC) y d-97dc8e
  (21:15 UTC) del 2026-10-27: compra en Neoxa que volvió `open` y se canceló a
  los 3 s, 0.0097 XBT vendidos de más en NonKYC. Neoxa casa al recibir la
  orden: de las 615 piernas del bot en Neoxa, 600 volvieron `filled` en el ack
  y 15 `open`, de las que 14 colgaron. `max_move_pct` no las separa (`move`
  0.14 y 0.43 %). De ahí D-75/T-77. No es el WiFi ni la latencia (ack de Neoxa
  p50 305 ms).
- **Descuadres** (journal en live, 2026-10-26): neto +0.0601 XBT sobrante
  (+0.0817 comprado de más, −0.0216 vendido de más), coste neto 0.00026685 BTC
  (media 0.00444).

### Tareas abiertas

- **T-66** `todo`: CA-16 en real. Parte local hecha (2026-10-26 23:32 UTC,
  t-2c6b97 colocada y cancelada desde la pantalla, en D-66). Falta la del
  server por SSH, con permiso del usuario.
- **T-55** `doing`: falta la parte manual de CA-15 en live en el server.
- **T-20** `doing`: CA-03 y CA-06 aplazados por el usuario (2026-10-23).
- **T-33** `todo` (comprobación en real de M6) y **T-32** `todo` (CI; ya hay
  remoto en GitHub, falta que el usuario la pida).

### Siguiente

1. **T-78** (D-76): primero comparar en real el tope WS/REST de Neoxa; después
   el contraste periódico. Mientras tanto, las tareas de T-77 siguen: mirar
   `bot_miss` y `hanging_leg` tras unos días (consulta en
   `specs/archive/handoff-historia.md`).
2. Corregir d-550cd7 a mano (queda fuera de los 50 trades de NonKYC): leer sus
   4 trades de `/account/trades` con un límite mayor y añadir los `fill_fee`,
   con el daemon parado. Pendiente de que el usuario lo pida.
3. Apuntar en `decisions.md` la prueba en live de d-fb32e2 (el usuario aún no
   ha dicho si quiere).
4. T-66 y T-55 por SSH contra el daemon del servidor (ya con la versión
   nueva), con permiso del usuario.
5. Decidir `fee_discount_pct` de Neoxa: los fills reales cobran el 0.2 %
   completo; el 50 % solo infla el neto del panel cruzado (no el del bot).

### Cómo retomar en una sesión nueva

- Estado del daemon y del bot, en el servidor: `ssh -o BatchMode=yes
  servidor 'systemctl is-active xbt; tail
  ~/.local/state/xbt/diag.log'` y los `bot_state` del journal de hoy (el
  servidor saca un aviso legal por stderr en cada conexión).
- Si se reinicia el daemon, el bot arranca `off`: rearmar con `bot on` desde la
  pantalla. La config solo se lee al arrancar. Cambiar la config: editarla en
  el servidor y que el usuario haga `sudo systemctl restart xbt`.
- Resultados y descuadres sin pantalla: `ResultsService(Path.home() /
  ".local/state/xbt").load("todo")` (`services/results.py`).
- Trades públicos de Neoxa para ver qué pasó en una colgada (solo los 100
  últimos): `curl -s "https://neoxa.exchange/api/exchange/trades/BTCB2_BTC?limit=100"`.

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
- **Tests inestables:** `test_el_dialogo_no_espera_a_la_red` (límite de 0.3 s
  de reloj real) y, una vez, `test_las_dos_llenas_enfria` (timeout) fallan a
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
- Todo en español (docs, mensajes, docstrings); identificadores en inglés.
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
- El trazador cuenta un ID como cubierto con un solo test: RF-05 y RNF-03 solo
  tienen tests de topes en la config, no del comportamiento real.
