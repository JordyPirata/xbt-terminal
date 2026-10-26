# Tareas cerradas: detalle

Cuerpo de cada tarea `done` de `specs/mvp/tasks.md`, tal como estaba al
cerrarla. Es historia; el estado vigente está en `tasks.md`.

### T-01 Esqueleto del repositorio · `done`
- Estructura `src/`, `tests/`, `specs/`, `pyproject.toml` con uv, ruff, pytest.
- Marca `@pytest.mark.spec` y `tools/spec_trace.py`.


### T-02 Modelos y aritmética de mercado · `done` · RNF-07
- `core/models.py` completo; `MarketSpec.quantize_price(side)` y `quantize_qty`.
- Tests: compra redondea hacia arriba y venta hacia abajo al tick; qty se trunca
  al lote; construir un modelo con `float` lanza error.


### T-03 Configuración y secretos · `done` · RF-12, RF-13, RNF-08, CA-07
- `config.py`: carga y valida `config.toml`; `.env` → `Secret`; resuelve el modo
  según la tabla de diseño §8.
- `core/secrets.py`: `Secret` y `Redactor`.
- Tests: `repr`/`str`/f-string de `Secret` no revelan el valor; sin keys el modo
  es `read-only`; con keys y sin `--live` es `dry-run`; config inválida falla.


### T-03b Solo pares contra BTC · `done` · RNF-10 · dep T-03
- `config.py` rechaza al arrancar un símbolo cuyo quote no sea BTC o cuyo base
  no sea BTCB2 (`BTCB2/BTC` en NonKYC, `BTCB2_BTC` en Neoxa).
- `config.example.toml` con los pares BTC y topes de riesgo en BTC.
- Tests: el ejemplo es válido; `BTCB2/USDT`, `BTCB2_USDC` y `BTC/BTCB2` se rechazan.


### T-04 Journal JSONL · `done` · RF-11, CA-07 · dep T-03
- `core/journal.py`: append, una línea por evento, flush, rotación diaria,
  Decimals como string, pasa por `Redactor`.
- Tests: N eventos → N líneas JSON válidas; reabrir no trunca; un secreto
  canario metido en `data` sale como `***`.


### T-05 Parser de comandos · `done` · RF-06, RF-07, RF-08 · PA-07
- `core/commands.py`: gramática de diseño §9 → dataclasses de comando.
- Tests: cada comando válido; errores legibles para venue desconocido,
  cantidad no numérica, precio negativo, `dbl` con el mismo venue en las dos piernas.


### T-06 Validaciones de riesgo · `done` · RF-10, CA-04 · dep T-02 · PA-06
- `core/risk.py`: tabla de diseño §6.3; devuelve todas las violaciones.
- Tests: un caso que viola cada regla y uno que no viola ninguna; `self_cross`
  en compra y en venta; datos viejos rechazan.


### T-07 Limitador de peticiones · `done` · RNF-03
- `core/ratelimit.py`: ventanas deslizantes múltiples, async, con prioridad.
- Tests (reloj falso): 481.ª petición en 60 s espera; 9.ª orden en 10 s espera;
  con cola llena, una cancelación adelanta a un poll de mercado.


### T-10 Book L2 · `done` · RF-02 · dep T-02
- `core/book.py`: snapshot + deltas por `seq`, detección de hueco, top-N con
  acumulado, best bid/ask, mid, flag AMM por nivel.
- Tests: aplicar deltas, borrar nivel con qty 0, hueco de seq → pide snapshot.


### T-11 Constructor de velas · `done` · RF-03 · dep T-02
- `core/candles.py`: histórico + trades en vivo → vela actual; 1m, 5m, 1h.
- Tests: trades en el mismo minuto, cambio de vela, minuto sin trades.


### T-14a Cálculo del panel cruzado · `done` · RF-04 · dep T-10
- `core/cross.py`: mid, spread interno, diferencia bruta y neta en ambas direcciones.
- Tests: con fees 0 neto = bruto; ejemplo numérico de diseño §6.6 con precios en BTC.


### T-08 Adaptador Neoxa: datos públicos · `done` · RF-01, RF-03, RNF-03, RNF-09 · dep T-07, T-10 · PA-01
- `market`, `book_snapshot`, `recent_trades`, `candles`, `market_stream` (WS
  público + respaldo REST).
- Primer paso: grabar respuestas reales en `tests/fixtures/neoxa/` y confirmar
  el símbolo (PA-01).
- Tests: parseo de fixtures a modelos `Decimal`; niveles AMM marcados.


### T-09 Adaptador NonKYC: datos públicos · `done` · RF-01, RF-03, RNF-05, RNF-09 · dep T-07, T-10 · PA-02
- Igual que T-08 contra `api.nonkyc.io` / `ws.nonkyc.io`.
- Primer paso: confirmar límites de rate y si el WS privado acepta API key
  (PA-02); anotarlo en `decisions.md`.


### T-12 Supervisor de conexión · `done` · RF-01, RF-14, CA-06 · dep T-08, T-09
- Reconexión con backoff, paso a polling REST tras 3 fallos, vuelta al WS,
  medida de latencia, emisión de `ConnState`.
- Tests: servidor WS local que se cae y vuelve; el stream sigue entregando
  eventos y termina de nuevo en `ws`.


### T-13 Layout, books y gráficos · `done` · RF-02, RF-03, RNF-02, RNF-06 · dep T-10, T-11, T-12
- `ui/app.py` con el grid; `BookPanel` y `ChartPanel` por venue; atajos de timeframe.
- Tests: `run_test()` a 160×45 monta los 6 paneles; por debajo, aviso de tamaño;
  un delta inyectado aparece en el panel en < 500 ms.
- Fix 2026-10-21 (D-24): las velas van a paso de columnas entero y fijo; antes
  quedaban columnas vacías sueltas.


### T-14b Panel cruzado en pantalla · `done` · RF-04 · dep T-13, T-14a
- `CrossPanel` en la barra inferior, en lugar del aviso "pendiente":
  - Por venue: mid y spread (abs, sats, %).
  - Diferencia de mids `nx - nk` (abs, sats, %).
  - Las dos rutas, comprar en un venue y vender en el otro: bruta y neta de fees
    taker (de `MarketSpec`), en precio, sats y %. La neta positiva se resalta.
- Se refresca con el mismo throttle que los books (100 ms).
- Si a un venue le falta un lado, no tiene spec o tiene el book inválido, sus
  cifras salen como "—". Una ruta sale si existen el ask de compra y el bid de
  venta (ver `core/cross.py`).
- Tests: con los precios de `test_cross.py` el panel muestra las cifras exactas;
  datos parciales → "—"; las líneas caben en el ancho del panel a 160×45.


### T-15 Barra de estado · `done` · RF-14 · dep T-13
- Estado y latencia por venue y canal; modo actual (`read-only`/`dry-run`/`live`).
- `StatusBar`, una línea fija arriba del todo:
  - Canal de mercado por venue: fuente (ws, o rest de respaldo), estado
    (up / reconectando / caído) con color y latencia en ms. Si no está up,
    desde cuándo.
  - Canal privado: "sin keys" si el venue no puede operar. Con keys, el estado
    que da AccountService en live (T-17) o "simulado" en dry-run.
  - Aviso si el estado de mercado no se renueva en 15 s: el latido es cada 5 s (D-11).
  - Modo: `SOLO LECTURA`, `DRY-RUN` o un banner rojo permanente `LIVE · ÓRDENES REALES`
    (design.md §8). Sale del subtítulo del panel cruzado.
- Se redibuja al cambiar el estado y cada segundo, para la antigüedad y el reloj.
- Tests: los tres modos, cada combinación de fuente y estado, latencia,
  "sin keys", aviso de estado viejo, y que el layout sigue cabiendo a 160×45.


### T-16 Servicio de órdenes y dry-run · `done` · RF-06, RF-10, RF-11, RF-13, CA-04 · dep T-04, T-05, T-06
- `services/orders.py` con el flujo de diseño §6.2; `adapters/dryrun.py`.
- Diálogo de confirmación con nocional.
- Tests: en dry-run cada violación de RF-10 se rechaza y queda `reject_local` en
  el journal; una orden válida queda `order_sent` + `order_ack` simulado.


### T-16b Dry-run con saldos reales · `done` · RF-10, RF-13 · dep T-16, T-17 · D-16
- `DryRunVenue` toma los saldos del venue real y aplica encima lo que bloquean
  sus órdenes simuladas; fuera `[dryrun.balances]` (error si sigue en la config).
- Un fallo al leer saldos u órdenes al validar rechaza la orden con `venue_error`.
- Tests: saldos reales con bloqueos simulados encima, cambios reales visibles,
  sección vieja rechazada, `venue_error` en dry-run y live.


### T-17 Adaptadores privados · `done` · RF-05, RF-08, RF-09, RNF-04 · dep T-08, T-09, T-16 · PA-02
- `balances`, `open_orders`, `place_limit`, `cancel`, `cancel_all`, `private_stream`.
- Tests: firma NonKYC contra un vector conocido; parseo de fixtures privados
  (con claves falsas); polling de Neoxa respeta cadencia y limitador.
- Hecho además (D-14): seguimiento común de órdenes y fills (`adapters/private.py`),
  `AccountService` (estado del canal privado, órdenes abiertas, fills al journal,
  balances cada ≤ 10 s y tras fill), canal privado en la barra de estado y modo
  live conectado a los adaptadores reales. Los paneles siguen en T-19.
- Fix 2026-10-21 (D-22): NonKYC manda `side` en mayúsculas en `/account/orders`;
  ahora se acepta, y los errores del canal privado y de saldos van al journal.


### T-18 Orden doble · `done` · RF-07, CA-03 · dep T-16, T-17 · PA-08
- Flujo de diseño §6.4 y panel de piernas.
- Tests con dos `DryRunVenue`: una pierna rechazada → no se envía ninguna;
  pierna llena + otra abierta más de N s → `hanging_leg`.
- PA-08 con su supuesto por defecto, precisado en D-15 (desde cuándo cuenta el
  tiempo y el caso de dos parciales distintos). Dry-run monta AccountService
  sobre los venues simulados.


### T-19 Paneles de balances y órdenes · `done` · RF-05, RF-08, CA-05 · dep T-17
- Balances por venue e inventario neto; refresco ≤ 10 s y tras fill.
- Lista de órdenes abiertas; cancelar una, por venue, todas (`x`).
- Tests: `cancel all` con órdenes en ambos venues simulados → cero abiertas.
- Hecho además (D-17): atajo `d`, ids del dry-run con el venue, y diálogos y
  avisos sin markup (el diálogo de confirmación no mostraba `[y]` desde T-16).


### T-21 Órdenes más fáciles y ayuda · `done` · RF-15, RF-16, RNF-11 · dep T-19 · D-18
- Clic en un nivel del book → precio a la línea de comandos (y al portapapeles).
- Validación con saldos y órdenes en memoria (≤ 30 s) en vez de REST en cada paso.
- Pantalla de ayuda con `?` y `help`.
- Tests: mapa de clic a nivel, clic real con línea vacía y con texto, sin
  llamadas REST con caché fresca y con ellas si es vieja, orden recién enviada
  vista por `self_cross`, diálogo < 300 ms con un venue lento, ayuda.
- Hecho además: la línea de comandos ya no selecciona todo al recibir el foco
  (desde T-16, teclear tras `b`/`s` borraba el `buy ` del atajo).


### T-22 Tamaño por orden porcentual · `done` · RF-10, CA-04 · PA-03 · D-19
- Regla `order_size`: nocional ≤ `max_order_pct` (25 %) del inventario total de
  los dos venues; fuera `max_order_qty` y `max_order_notional`.
- Tests: límite exacto, inventario de los dos venues, sin inventario → rechazo,
  orden doble, config (rango 0-100, claves viejas rechazadas).


### T-23 Cantidad como porcentaje del saldo · `done` · RF-17, RF-10 · dep T-21, T-22 · D-21
- `buy|sell <venue> N% <precio>`: cantidad = N % del saldo total del venue (BTC
  en la compra, con la fee taker descontada; BTCB2 en la venta), hacia abajo al
  lote.
- Diálogo con la base del porcentaje; ayuda con un ejemplo.
- Tests: parser (rango, formato), cálculo puro (compra con fee, venta, lote,
  100 %, saldo vacío), servicio (usa la caché, rechazos, confirma la cantidad
  fijada), UI (de `buy nk 50% …` al diálogo).


### T-24 Velas de 4h y diarias · `done` · RF-03 · dep T-11, T-12, T-13 · D-23
- `Timeframe` con `4h` y `1d`; resoluciones 240/1440 en NonKYC e intervalos
  `4h`/`1d` en Neoxa; teclas `4`/`5`, `tf 4h|1d`, ayuda y eje X.
- Tests: alineación del builder, peticiones de cada adaptador, el hub carga los
  cinco timeframes, teclas y comando, formato del eje.


### T-25 Envíos con resultado incierto · `done` · RNF-12 · dep T-18 · PA-09
- `Unknown` además de `Failed`: timeout, 5xx o respuesta ilegible tras el POST.
  Evento de journal `order_unknown`; aviso rojo; en la orden doble la pierna queda
  `unknown`, no `rejected`.
- Reconciliación con el canal privado: NonKYC por `userProvidedId`; Neoxa según
  PA-09. Al resolverse, `order_ack` (con `reconciled`) o `order_error`.
- Tests: timeout tras el POST → `Unknown`; 4xx con error → `Failed`; JSON ilegible
  → `Unknown`; reconciliación por client_id y por coincidencia en Neoxa; dos
  candidatas → sigue incierta.
- Hecho (D-26): `UncertainError` y `StreamingVenue.sending`, `recent_orders` y
  `keeps_client_id` en los venues, `core/reconcile.py`, `OrderService.reconcile`
  cada 3 s desde la UI, barra de estado y pierna `INCIERTA`. 49 tests nuevos.


### T-26 Envíos atómicos por venue · `done` · RNF-13 · dep T-25
- `asyncio.Lock` por venue que cubre revalidar → enviar → apuntar en `_just_sent`
  (también la incierta). Orden doble: los dos locks en orden fijo.
- Tests: dos `confirm` concurrentes que se cruzan → la segunda se rechaza por
  `self_cross` (no `venue_exposure`, ver D-27); venues distintos no se bloquean
  entre sí.
- Hecho: `OrderService._sending`; las inciertas cuentan como abiertas al validar.
  `tests/services/test_atomic.py`.


### T-27 Fallos aislados · `done` · RNF-14 · dep T-26
- Cada venue en su tarea supervisada dentro de hub y cuenta; un bug se escribe
  (`error` con traceback redactado) y se reintenta con backoff; banner rojo.
  Workers sin `exit_on_error`. Un fallo del journal tras enviar no rompe la UI.
- Tests: un adaptador que lanza un bug no para al otro venue ni cierra la app;
  `OSError` del journal en `order_ack` → aviso y la app sigue.
- Hecho (D-28): `services/faults.py` (`Faults`, `supervised`), `OrderService._record`,
  `BTCB2App._guarded`, `✖ N fallos` en la barra. `tests/services/test_faults.py`,
  `tests/ui/test_faults_ui.py`.


### T-28 Salida segura · `done` · RNF-15 · dep T-26
- Envíos y cancelaciones protegidos con `asyncio.shield` y esperados al salir.
  En live, `q` con órdenes abiertas pide confirmación con el número.
- Tests: salir con un envío en vuelo → el journal tiene ack o error; live con
  órdenes abiertas → diálogo; sin órdenes → sale directo.
- Hecho (D-29): `OrderService._protected`, `in_flight`, `drain`;
  `BTCB2App.action_quit`. `tests/services/test_exit.py`, `tests/ui/test_quit.py`.


### T-29 Frescura con reloj local y desfase · `done` · RNF-16 · dep T-12
- `L2Book` guarda la hora local de recepción, y RiskCheck usa esa. Al arrancar se
  mide el desfase con cada venue (cabecera `Date`); `risk.max_clock_skew_s` en la
  config; por encima, aviso y sin live.
- Tests: book con hora del servidor desfasada ±1 min → frescura correcta;
  desfase mayor que el tope → live rechazado, dry-run avisa.
- Hecho (D-30): `L2Book.received_at`, `skew_ms` y `clock_skew_ms` en
  `StreamingVenue`, `MarketHub.clock_skew_ms`, violación `clock_skew` en live,
  aviso en la app. `tests/adapters/test_clock_skew.py`, `tests/services/test_clock.py`,
  `tests/ui/test_clock_ui.py`.


### T-30 Rutas, permisos e instancia única · `done` · RNF-17, RNF-18, RNF-20 · dep T-03, T-04
- Rutas XDG por defecto, con flags `--config`, `--env` y `--data-dir`. Lockfile
  con `fcntl.flock` en el directorio de datos. Permisos de `.env` comprobados al
  arrancar; journal `0600` y directorio `0700`.
- Tests: arrancar desde otro cwd; segunda instancia rechazada; `.env` `0644` →
  live rechazado y dry-run con aviso; journal creado en `0600`.
- Hecho (D-32): `paths.py` (`resolve_paths`, `InstanceLock`, `private_dir`,
  `env_permission_problem`), `Settings.warnings`, `--data-dir`, journal privado.
  `tests/unit/test_paths.py` y tests nuevos en `test_main.py` y `test_journal.py`.


### T-31 Log de diagnóstico · `done` · RNF-19 · dep T-30
- `logging` con `RotatingFileHandler` en el directorio de datos, un filtro que
  pasa por el `Redactor` y el nivel en la config. Hub, cuenta y adaptadores
  apuntan errores, reconexiones y cargas.
- Tests: canario de la key en un error → `***` en el log; nada en stdout;
  rotación.
- Hecho (D-33): `diaglog.py` (`setup_diagnostics`, `DiagHandler`), `[log] level`,
  logs en supervisor, hub, cuenta y `Faults`. `tests/unit/test_diaglog.py`,
  `tests/services/test_diag_events.py`.


### T-34 Config del bot · `done` · RF-18, RF-22 · dep T-03, T-31
- Sección `[bot]` (design.md §6.9) en `Settings` y `config.example.toml`,
  validación estricta; `cooldown_s ≥ 2`, porcentajes entre 0 y 100.
- Tests: config válida; clave desconocida, importe como número, `cooldown_s = 1`
  → error; sin `[bot]` → bot deshabilitado.
- Hecho: `core/bot.py` (`BotLimits`), `BotConfig` y `_Section.bool` en `config.py`,
  `[bot]` en `config.example.toml`. `tests/unit/test_config_bot.py`.


### T-35 Decisión del bot · `done` · RF-19, RF-20, RF-22, RNF-22, CA-08 · dep T-34
- `core/bot.py`: `BotLimits`, `BotState`, `decide` → `Fire | Skip`.
- Tests: cruce por encima y por debajo del umbral; neta con precios límite y
  tick hacia el lado seguro; cantidad limitada por cada término de RF-20; bajo el
  lote → `Skip`; las dos rutas → la mejor; cooldown y topes por hora y por día.
- Hecho (D-35): `core/bot.py` con `decide`, `VenueView`, `Fire`/`Skip` y las
  transiciones `arm`, `disarm`, `fired`, `finished`, `rejected`, `tick`.
  `tests/unit/test_bot.py` (30 tests).


### T-36 Servicio del bot · `done` · RF-19, RF-20, RF-21, RF-23, CA-08, CA-09 · dep T-35
- `services/bot.py`: bucle de §6.9 sobre `OrderService`; cancelación a
  `fill_timeout_ms`; `halted` ante colgada, incierta, rechazo o error;
  `bot_state` y `bot_fire` en el journal; contadores rehechos del journal.
- Tests (dry-run): un cruce → una sola doble; book viejo, desfase, fallo del
  venue o incierta abierta → no dispara; pierna rechazada → `halted` sin órdenes
  abiertas; bug en el bucle → `halted`; contadores tras reiniciar.
- Hecho (D-36): `services/bot.py` (`BotService`), `OrderService.cancel_double`,
  eventos `bot_state` y `bot_fire`. `tests/services/test_bot_service.py`.


### T-37 Bot en la pantalla · `done` · RF-18, RF-23 · dep T-36
- Comando `bot on|off|status` en `core/commands.py`, confirmación al armar,
  tecla de parada, estado en la barra; `help` lo explica.
- Tests: `bot on` en live con `enabled = false` → rechazado; confirmar arma;
  la tecla lo apaga; la barra muestra `halted` y su motivo.
- Hecho (D-37): `BotCommand`, `BTCB2App` con `bot` (diálogo, `p`, avisos, ayuda),
  estado en el banner de la barra, `build_app` crea el `BotService`.
  `tests/ui/test_bot_ui.py` y tests nuevos en barra, comandos y arranque.


### T-38 Bot en real · `done` · CA-10 · dep T-37
- Live con `max_qty` y `max_daily_btc` mínimos hasta un disparo real y la parada
  por tope. Resultado en `decisions.md`.
- Hecho: disparos reales con las dos piernas en el journal (D-43) y parada por
  `day_cap` en live el 2026-10-23 con el código de T-46 (D-57).


### T-39 Descuento de fee en el panel cruzado · `done` · RF-24, RF-04 · dep T-14b · D-38
- `fee_discount_pct` opcional por venue; el panel lo aplica a la taker y lo dice.
- Tests: config por defecto 0, porcentaje válido e inválido; panel con la neta
  descontada y el spec del hub intacto; `build_app` lo pasa a la app y no al bot.
- Hecho: `VenueConfig.fee_discount_pct`, `BTCB2App(fee_discounts=…)`,
  `render_cross(note=…)`.


### T-40 Ventana de resultados · `done` · RF-25, CA-11 · dep T-36, T-18 · D-44, D-45
- `core/results.py`: eventos del journal → `DoubleResult` por doble (origen,
  ruta, cantidad casada, precios medios, fees en BTC, resultado, neta esperada,
  descuadre, estado `cerrada | colgada | sin fills`) y resumen.
- Servicio que lee los `journal-YYYYMMDD.jsonl` del rango y se suscribe a los
  `fill` nuevos.
- Pantalla `ResultsScreen` (tecla `r`, comando `results [día|7d|todo]`):
  gráfica plotext del acumulado y barras por doble verde/rojo, resumen y tabla.
  `help` y README lo explican.
- Tests: doble llena; parciales con cantidades distintas → casada y descuadre;
  fee en BTCB2; varios fills por pierna; colgada y sin fills no suman; bot frente
  a manual; dry-run ignorado; rango de días y cambio de día UTC; línea de journal
  corrupta no rompe; la pantalla abre, filtra y cierra.
- Hecho (D-45): `core/results.py` (`compute`, `read_rows`, `range_start`),
  `services/results.py` (`ResultsService`), `ShowResults` en `core/commands.py`,
  `ResultsScreen` en `ui/app.py` y `ResultsChart` en `ui/panels.py`; tecla `r`,
  `build_app` lo crea con el directorio del journal. Tests en
  `tests/unit/test_results.py`, `tests/services/test_results_service.py` y
  `tests/ui/test_results_ui.py`.


### T-41 Ratio de inventario por venue · `done` · RF-26, RF-27, CA-12 · dep T-35, T-19 · D-46
- `[inventory]` opcional en `AppConfig` y `config.example.toml`; validación:
  objetivo entre 0 y 100 (sin incluirlos), banda > 0 y ≤ 50.
- `core/inventory.py`: `InventoryTarget` (banda, % de BTCB2, ¿empeora?).
- `core/bot.py`: `VenueView` con totales y mid; `decide(..., target)` veta la
  ruta con `Skip("inventory_band")`. `BotService` lo recibe de `build_app`.
- Panel de balances con % por venue y total, con color.
- Tests: config válida, ausente e inválida; ruta que saca un venue de la banda →
  no dispara; ruta que acerca un venue que ya estaba fuera → dispara; sin
  objetivo → dispara; mid ausente → precio límite; fees en el cálculo;
  `bot status` con el motivo; panel con colores y sin `[inventory]`.
- Hecho: `InventoryTarget` y `base_pct` en `core/inventory.py`; `VenueView` con
  `quote_total`, `base_total` y `mid`, `inventory_veto` y `decide(..., target)`
  en `core/bot.py`; `[inventory]` en `config.py` y `config.example.toml`;
  `BotService(target=…)`, `BalancesPanel(target=…)` y `build_app`. Tests en
  `tests/unit/test_inventory_target.py`, `tests/ui/test_inventory_panel.py` y
  nuevos en `test_config_bot.py`, `test_bot_service.py` y `test_main.py`.


### T-42 Ventana de direcciones de depósito · `done` · RF-28, CA-13 · dep T-17 · D-50
- `DepositAddress` en `core/models.py`; protocolo `Depositor` y
  `deposit_address(asset)` en los dos adaptadores (design.md §4 y §6.11).
- `services/deposits.py` (`DepositService`); `build_app` lo crea con los venues
  con keys.
- `ShowDeposits` en `core/commands.py` (`deposit`), tecla `w`, `DepositScreen`
  en `ui/app.py`; `help` y README lo explican.
- Tests: parseo de NonKYC (ticker `BTC-MAIN`, ticker distinto → error, vacía →
  error) y de Neoxa (`bitcoin`/`btcb2`, cadena cerrada, falta la cadena); el
  servicio da una fila por venue y activo y aísla el error de un venue; la
  pantalla muestra venue, ticker y red, las filas con error sin dirección,
  vuelve a pedir al reabrir y copia la dirección exacta; sin keys, lo dice.
- Hecho: `DepositAddress` en `core/models.py`; `Depositor` en
  `adapters/base.py`; `deposit_address` y `parse_deposit` en `nonkyc.py`
  (`DEPOSIT_TICKERS`, BTC = `BTC-MAIN`) y `neoxa.py` (`DEPOSIT_CHAINS`);
  `services/deposits.py`; `ShowDeposits`; `DepositScreen`, tecla `w`;
  `deposit_service` en `__main__.py`. Comprobado en real el 2026-10-22 (las
  cuatro direcciones llegan). Tests en `tests/adapters/test_deposits.py`,
  `tests/services/test_deposits_service.py`, `tests/ui/test_deposits_ui.py` y
  nuevos en `test_commands.py` y `test_main.py`.


### T-44 Copiar de verdad y QR de la dirección · `done` · RF-28, CA-13 · dep T-42 · D-52
- `ui/clipboard.py`: `system_copy(text)` con `wl-copy`/`xclip`/`xsel`.
- `ui/qr.py`: `qr_lines(text)` con `segno` en medio bloque.
- `DepositScreen`: copia por OSC 52 y por el sistema, mensaje según el
  resultado; panel con el QR de la fila resaltada. `BTCB2App(clipboard=…)`,
  `build_app` pasa `system_copy`; el clic en el book también lo usa.
- Tests: elige la herramienta según el entorno y el `PATH`, pasa el texto
  exacto por stdin, la que falla da paso a la siguiente, ninguna → `None`; el QR
  dibujado coincide módulo a módulo con el de `segno` para la dirección; la
  pantalla dice con qué se copió o que solo fue a la terminal, el QR sigue al
  cursor y una fila con error no tiene QR.
- Hecho: `ui/clipboard.py` (`system_copy`, `Clipboard`), `ui/qr.py`
  (`qr_lines`); `BTCB2App(clipboard=…)` → `system_clipboard` (`App.clipboard`
  ya es de Textual) y `copy_text`; `DepositScreen` con panel `#deposit-qr` y
  copia en un worker; el clic en el book copia igual. `segno` en
  `pyproject.toml`. Comprobado con el `xclip` real (X11, GNOME Terminal). Tests
  en `tests/ui/test_clipboard.py`, `tests/ui/test_qr.py` y nuevos en
  `test_deposits_ui.py`, `test_book_click.py` y `test_main.py`.


### T-45 Plan de reequilibrio · `done` · RF-29, CA-14 · dep T-19, T-41, T-42 · D-53
- `WithdrawalFee` en `core/models.py`; protocolo `WithdrawalFees` y
  `withdrawal_fee(asset)` en los dos adaptadores (design.md §4 y §6.12).
- `core/rebalance.py`: `Holding`, `Transfer`, `Plan`, `plan`, `break_even`.
- `services/rebalance.py` (`RebalanceService`); `build_app` lo crea con los
  venues con keys.
- `ShowRebalance` en `core/commands.py` (`rebalance`), tecla `e`,
  `RebalanceScreen` en `ui/app.py`; `help` y README lo explican.
- Tests: parseo de NonKYC (`BTC-MAIN`, sin mínimo, cerrado, mantenimiento,
  otro ticker) y de Neoxa (número o texto, falta la entrada); el plan iguala
  los % al satoshi en las dos alternativas, con la comisión descontada; no
  viables por disponible, mínimo, comisión mayor que lo enviado, cerrado o sin
  comisión; recomienda la más barata; `break_even`; el servicio aísla el error
  de un venue; la pantalla muestra el plan, vuelve a pedir al reabrir y avisa
  sin keys o sin saldos.
- Hecho: `WithdrawalFee` en `core/models.py`; `WithdrawalFees` en
  `adapters/base.py`; `withdrawal_fee` y `parse_withdrawal_fee` en `nonkyc.py`
  (público, tickers de depósito) y `neoxa.py` (privado); `core/rebalance.py`;
  `services/rebalance.py`; `ShowRebalance`; `RebalanceScreen`, tecla `e`;
  `rebalance_service` y `bot_limits` en `__main__.py`. Comprobado en real el
  2026-10-23 con saldos, mids y comisiones de los dos venues (las cuatro
  comisiones llegan; recomienda 0.140 BTCB2 de Neoxa a NonKYC por ~41 sats).
  Corregido el mismo día (D-53): comisión aparte en Neoxa (`on_top`), columnas
  pedir / sale / llega; test con los saldos reales de ese día (0.14002494).
  Tests en `tests/unit/test_rebalance.py`, `tests/adapters/test_withdrawal_fees.py`,
  `tests/services/test_rebalance_service.py`, `tests/ui/test_rebalance_ui.py` y
  nuevos en `test_commands.py` y `test_main.py`.


### T-50 Motor sin UI · `done` · RF-30, RNF-12, RNF-15 · D-61
- `services/engine.py` (`Engine`, `Notice`): hub, cuenta, órdenes, bot,
  faults y los bucles que hoy están en `ui/app.py` (`_check_doubles`,
  `_tick_reconcile`, desfase de reloj, drenaje al cerrar).
- `__main__.build_app` pasa a `build_engine` + `BTCB2App(engine=…)`; la app
  se suscribe a los avisos. Sin cambio de comportamiento.
- Tests: los de la app siguen verdes; `Engine` con venues falsos da `Notice`
  de pierna colgada y reconcilia una incierta.
- Hecho: `services/engine.py` (`Engine`, `Notice`, `watchers()` y
  `services()`); `BTCB2App` recibe `engine=` o se hace uno con sus piezas
  (tests) y corre sus tareas como workers; `build_engine` en `__main__.py`.
  Fallo en `check_doubles` → `Faults` con `where="engine"` ("motor"). Tests en
  `tests/services/test_engine.py`; los de la app que cambiaban `app.account` o
  `app.mode` a mano ahora lo cambian también en `app.engine`.


### T-51 `btcb2t daemon` · `done` · RF-30, RNF-15, RNF-17 · dep T-50 · D-61
- Subcomando `daemon` (sin subcomando, la terminal de siempre).
- `services/daemon.py`: `Engine` sin Textual, lock, diaglog, SIGTERM/SIGINT
  con parada ordenada, avisos al diaglog y los de error al journal.
- Primer plano, sin nada de systemd; errores de arranque por stderr con
  código ≠ 0 y una línea al quedar listo.
- Tests: arranca con el bot `off`; SIGTERM deja en el journal un envío en
  vuelo; un segundo daemon no arranca.
- Hecho: `services/daemon.py` (`run_daemon(engine, stop, on_ready)`), `serve`
  y el subcomando en `__main__.py` (señales → `stop`; avisos de arranque y
  línea de listo por stderr; sin keys no arranca). `Engine.journal`; evento
  `notice` en el journal; mensaje del lock "terminal o daemon". Tests en
  `tests/services/test_daemon.py` y `tests/ui/test_main.py`. Probado el
  2026-10-24 en dry-run con la config real: conecta los dos WS y SIGTERM sale
  con 0.


### T-52 Servidor de socket · `done` · RF-31 · dep T-51 · D-61
- `services/control.py` y `services/wire.py` (design.md §6.13).
- Tests: ida y vuelta de `wire`; socket `0600`; foto al conectar; eventos;
  `bot_on`/`bot_off` arman y paran; otro comando → `error`; un cliente que no
  lee se desconecta sin frenar al resto; ningún secreto en lo enviado
  (canario, CA-07).
- Hecho: `services/wire.py` (`encode`, `decode`, `dumps`, `loads`, `Snapshot`,
  `BotView`, `snapshot`), `services/control.py` (`ControlServer`,
  `socket_problem`), `run_daemon(socket=…)` y `--socket` en `__main__.py`
  (adelantado de T-54: con un `--data-dir` largo el socket no cabía en los 108
  bytes de AF_UNIX y el daemon caía con traceback; ahora sale con un mensaje).
  Tests en `tests/services/test_wire.py`, `test_control.py`, `test_daemon.py`
  y `tests/ui/test_main.py`. Probado el 2026-10-24 en dry-run con la config
  real: foto con los saldos de los dos venues, `buy` rechazado, `ping`,
  socket `0600` y borrado al parar.


### T-53 `btcb2t attach` · `done` · RF-31, CA-15 · dep T-52 · D-61
- `services/remote.py` (`RemoteEngine`); `attach` con `MarketHub` público
  propio y `ResultsService` del directorio de datos.
- `BTCB2App` observadora: solo `bot on/off/status`, `help`, `results`; `q`
  solo desconecta; barra con la conexión al daemon.
- Tests (pilot): `bot on` confirma y manda `bot_on`; `buy` se rechaza; `q` no
  manda nada; el daemon caído se ve en la barra y se reconecta.
- Hecho: `services/remote.py` (`probe`, `Hello`, `DaemonGone`, `RemoteEngine`,
  `RemoteAccount`, `RemoteBot`); `BTCB2App(remote=…)` con `_observer`,
  `ATTACHED_COMMANDS`, `ATTACHED_HINT` y las piernas de la última doble del
  daemon; `daemon=` en la barra; `hint=` en `CommandLine`; `name=` en
  `setup_diagnostics`; `attach` y `build_attached_app` en `__main__.py`
  (`build_venues(public=True)`). Tests en `tests/services/test_remote.py`,
  `tests/ui/test_attach_ui.py` y `tests/ui/test_main.py`. Falta abrirla en una
  terminal de verdad contra un daemon (T-55).


### T-54 Un binario con args · `done` · RF-30, RF-31, RNF-20 · dep T-53 · D-61
- `btcb2t`, `btcb2t daemon`, `btcb2t attach` con los mismos `--config`,
  `--env`, `--data-dir` y `--live` (y `--socket` opcional).
- README: los tres comandos y `ssh -t server btcb2t attach`; cómo supervisar el
  daemon queda al usuario.
- Tests: `test_main.py` con cada subcomando y flags.
- Hecho: subcomandos y `--socket` ya estaban (T-51 a T-53). Tests nuevos en
  `tests/ui/test_main.py`: `daemon` con `--live`, `--config`, `--env` y
  `--data-dir`; `attach` busca el socket en su `--data-dir`; `--help` nombra
  los subcomandos y flags; un subcomando desconocido no arranca. README:
  sección "En un server (daemon)".


### T-43 Errores sin texto · `done` · RF-19, RF-21, RF-11 · dep T-36 · D-51
- `describe(exc)` en `adapters/base.py`: el texto de la excepción o, si viene
  vacío, su tipo (`ConnectTimeout`). Lo usan la cuenta, el hub, las órdenes,
  los cancelados, el respaldo REST, los depósitos y el cancelar todas de Neoxa.
- Pierna que no llega a conectar (`ConnectError`, `ConnectTimeout`,
  `PoolTimeout`): error `no enviada, sin conexión (…)`, para que el motivo del
  paro no diga "rechazada".
- `BotService._blocked`: un error de cuenta o de mercado bloquea aunque su
  texto esté vacío (`is not None`).
- Tests: `describe` con y sin texto; la cuenta guarda `ConnectTimeout` y el
  journal también; el bot no dispara con la cuenta en error sin texto; una
  pierna sin conexión da `order_error` con "no enviada" y el paro lo dice.
- Hecho: `describe` en `adapters/base.py`; `order_error` con "no enviada, sin
  conexión (…)" para `NOT_SENT`; `_blocked` con `is not None`. Tests en
  `tests/adapters/test_describe.py` y nuevos en `test_account.py` y
  `test_bot_service.py` (el de la cuenta con error vacío reproduce el disparo
  del 2026-10-23).


### T-46 Correcciones de la auditoría · `done` · RF-22, RNF-01, CA-10 · dep T-35, T-36 · D-54
- El tope diario (`max_daily_btc`) para el bot también cuando ya está gastado al
  evaluar o queda menos de un lote: `halted` con motivo `day_cap`, como pide
  RF-22, en vez de `Skip("day_cap")` con el bot `armed`.
- `ui/app.py` deja de importar `adapters.streaming.now_ms` (la hora local se
  inyecta o sale de un módulo fuera de `adapters`).
- Decidir con el usuario si la escritura de ficheros de `core/journal.py` sale a
  `services/` o se queda como excepción escrita, y quitar o confirmar la nota de
  design §2.
- `rich` declarado en `pyproject.toml`; RNF-01 nombra las dependencias reales o
  se deja como está (cambio de requirements).
- Tests: tope gastado al arrancar con contadores del journal → `halted`;
  presupuesto por debajo del lote → `halted`; `bot on` el mismo día con el tope
  gastado → `halted` con el motivo.
- Hecho (D-55): `capped` en `core/bot.py`, que usan `finished` y
  `BotService._evaluate` con `Skip("day_cap")`; `MarketHub.clock` y
  `BTCB2App(clock=None)` toman el reloj del hub, sin importar `adapters`; el
  journal se queda en `core/` como excepción escrita (elegido por el usuario);
  `rich>=13.0` en `pyproject.toml`. RNF-01 no cambia: nombra el stack, no todas
  las dependencias. Tests en `tests/unit/test_layers.py` y nuevos en
  `test_bot.py` y `test_bot_service.py` (`TestTopeDiario`).


### T-47 Cancelar todas en NonKYC · `done` · RF-08, CA-05 · dep T-17 · D-58
- `NonKYCVenue.cancel_all`: `/cancelallorders` con `symbol` y `side: "all"`
  (OpenAPI 1.3.0); sin `success: true` es error.
- Tests: el body; error dentro de un HTTP 200; sin `success`.
- Hecho: `cancel_all` en `adapters/nonkyc.py`; tests en
  `tests/adapters/test_nonkyc_private.py`; OpenAPI de NonKYC guardado en
  `docs/api/nonkyc/openapi.json` con `docs/api/README.md`. Falta repetir CA-05
  en real (T-20).


### T-48 Activas de NonKYC sin las terminadas · `done` · RF-08, RF-10, CA-05 · dep T-17 · D-59
- `NonKYCVenue.fetch_active` descarta lo que no esté `open` o `partial`: el
  venue devuelve órdenes ya canceladas o llenas en `status=active`.
- Tests: con una cancelada (forma real) y una llena en la respuesta, solo salen
  las abiertas; una `Active` con `isActive` falso sigue contando.
- Hecho: `fetch_active` en `adapters/nonkyc.py`; tests en
  `tests/adapters/test_nonkyc_private.py`.


### T-49 Bot recorre el book · `done` · RF-20, CA-08 · dep T-35 · D-60
- `BotLimits.max_walk_pct` por venue (vacío = 0) y `[bot].max_walk_pct`
  opcional en config, con `_percent` por clave; un venue desconocido es error.
- `_route` prueba cada par de niveles de referencia dentro del tope y dispara
  el de mayor neta en BTC; con walk 0, igual que antes.
- La confirmación de `bot on` muestra el tope por venue.
- Tests: escalones de NonKYC con walk 0 → no dispara; con walk → dispara con
  dos escalones; elige la mayor neta en BTC; el tope corta el recorrido;
  config válida, ausente, venue desconocido y fuera de rango.
- Hecho: `_route` + `_pair` en `core/bot.py`, `_walk` en `config.py`,
  `_walk_text` en `services/bot.py` y la clave comentada en
  `config.example.toml`. Tests en `test_bot.py` (`TestRecorrerElBook`, con los
  escalones reales del book de NonKYC), `test_config_bot.py` y
  `test_bot_service.py`. Falta verlo disparar en live.


### T-56 El daemon no veía los fills · `done` · RF-07, RF-21, RF-30, CA-15 · dep T-50 · D-62
- En live, primer disparo del daemon (2026-10-24 17:53 UTC, d-68e3ed): las dos
  piernas se llenaron, pero la de NonKYC siguió `open` con 0. El bot la canceló
  ("20002 Not found"), pasó a `halted` (`unsettled`) y avisó de una pierna
  colgada que no existía.
- Causa: `account.subscribe_events(orders.on_private_event)` se quedó en
  `BTCB2App.on_mount` al sacar el motor de la UI (T-50). El daemon no tiene app.
- Arreglo: el enlace pasa al `Engine`. Test en `tests/ui/test_ca15.py`
  (`test_las_piernas_llenas_no_paran_el_bot`) con `BotRig(wire=False)`: los
  tests de CA-15 enlazaban a mano y por eso no lo vieron.


### T-57 El bot con el descuento de fee pagada en token · `done` · RF-19, RF-24, RF-25 · dep T-58 · D-63
- Config: `fee_token` y `fee_token_min` opcionales por venue, las dos o
  ninguna, con `fee_discount_pct` > 0; el token no puede ser un activo del par.
- `parse_balances` de NonKYC añade el token configurado; el bot usa la fee
  con descuento solo con disponible ≥ `fee_token_min`; `bot status` y la
  confirmación de `bot on` dicen qué fee usa cada venue.
- Resultados: `alternateFeeAsset` (id) → ticker con el `assetid` de
  `/balances`.
- Tests: con token sobre el mínimo dispara una ruta que con la fee completa no
  pasa; bajo el mínimo, sin saldo o sin `fee_token`, no dispara; tamaño y
  RiskCheck siguen con la fee completa; fill con `alternateFee` en NKYC.
- Hecho: `FeeToken`, `token_fee` y `VenueView.net_fee` en `core/bot.py` (solo
  la neta; cantidad e inventario con la fee del spec); `_fee_token` en
  `config.py`; `fee_token` en los dos adaptadores (su saldo sale con los del
  par) y `assetid` → ticker en los fills de NonKYC; `BotService` con
  `fee_tokens` (`bot status` y la confirmación de `bot on` dicen qué fee usa);
  `Summary.other_fees` y aviso en el resumen de resultados; `fee_tokens` en
  `__main__.py`; claves comentadas en `config.example.toml`. Tests en
  `test_bot.py`, `test_config.py`, `test_nonkyc_private.py`,
  `test_neoxa_private.py`, `test_bot_service.py`, `test_results.py`,
  `test_results_ui.py` y `test_main.py`. En la config real: `fee_token = "NKYC"`,
  `fee_token_min = "1"` (2026-10-25; 4.82 NKYC disponibles). Falta ver un fill
  de BTCB2/BTC pagado en NKYC.


### T-58 Órdenes y fills de NonKYC de otros mercados · `done` · RF-05, RF-08, RF-09 · D-63
- `/account/trades` y `/account/orders` ignoran `symbol` y traen también
  NKYC/BTC (visto el 2026-10-25). `fetch_fills` y `fetch_active` descartan las
  filas cuyo `market.symbol` o `symbol` no es el del par; sin ninguno, cuentan.
- Tests: respuestas con un trade y una orden reales de NKYC/BTC → solo salen
  los del par; fila sin `market` sigue contando; `recent_orders` no pide la
  orden de otro mercado.
- Hecho: `other_market` en `adapters/nonkyc.py`; tests `TestOtrosMercados` en
  `tests/adapters/test_nonkyc_private.py`. Comprobado en real (solo lectura):
  la venta de NKYC ya no sale entre las activas y los fills son del par.
- Queda abierto: no se sabe si `/cancelallorders` respeta `symbol`. Si no, la
  tecla `x` cancelaría también las órdenes de NKYC. Sin verificar para no
  cancelar la orden real del usuario.
- Ojo: `MY_TRADES` (50) cuenta los trades de todos los mercados.


### T-59 Fee corregida de un fill · `done` · RF-11, RF-25 · dep T-57 · D-64
- `FeeUpdate` en `core/models.py` y en `PrivateEvent`; `OrderTracker.update` lo
  emite cuando un trade ya visto cambia de fee o de activo.
- `AccountService`: `fill_fee` en el journal, el fill de `recent_fills` con la
  fee nueva y aviso `fill`. `fill_fee` en la lista de eventos del journal.
- Resultados: `fill_fee` sustituye la fee del trade en su doble.
- Tests: trade igual → nada; trade con otra fee → un `FeeUpdate` y ningún
  `Fill`; journal con `fill_fee`; la forma real de d-2e18a2 (BTC → NKYC) da la
  fee en NKYC y sin fee en BTC; `fill_fee` de un trade desconocido se ignora.
- Hecho: `FeeUpdate` en `core/models.py` y `adapters/base.py`;
  `OrderTracker._fees` en `adapters/private.py`; `_journal_fee` en
  `services/account.py`; `fill_fee` en `core/journal.py`; `_on_fill_fee` y
  `_add_fee` en `core/results.py`. Tests en `test_private.py`
  (`TestFeeCorregida`), `test_account.py` y `test_results.py`
  (`TestFeeCorregida`, con los fills reales de d-2e18a2).


### T-60 Fees en token valoradas en resultados · `done` · RF-24, RF-25 · dep T-59 · D-65
- `NonKYCVenue.token_price(asset)` y `NeoxaVenue` no (Neoxa sin `fee_token`):
  mid de `<asset>/BTC` por la API pública; sin bid o ask, error.
- `core/results.compute(..., prices=)`: `DoubleResult.token_fees` (BTC) se resta
  del resultado; `Summary.other_fees` solo con lo que queda sin valorar y
  `Summary.prices` con los precios usados.
- `ResultsService(..., pricers=)`: `refresh_prices()` asíncrono con caché de
  60 s; `load` usa lo guardado. La pantalla lo pide al abrir y al cambiar de
  rango y redibuja al llegar.
- Tests: d-2e18a2 con precio → resultado bruto − 0.00017846 NKYC × precio; sin
  precio, como antes; precio cacheado 60 s; error del precio → sin valorar;
  pantalla con el precio en el resumen y la fee en sats.
- Hecho: `parse_token_price` y `NonKYCVenue.token_price` en `adapters/nonkyc.py`,
  `TokenPricer` en `adapters/base.py`; `compute(prices=)`,
  `DoubleResult.token_fees` y `Summary.prices` en `core/results.py`;
  `refresh_prices` con caché en `services/results.py`; `ResultsScreen._price`
  en `ui/app.py`; `results_service` en `__main__.py` (terminal y TUI
  conectada). Tests en `test_results.py`, `test_results_service.py`,
  `test_nonkyc_private.py`, `test_results_ui.py` y `test_main.py`.

### T-62 Mercado por el socket · `done` · RF-31, RNF-03, CA-16 · dep T-61 · D-66
- `hello` con `view`; `market_init`, `book` (agrupado ~100 ms), `trade`, `conn`.
- `RemoteMarket` en `services/remote.py` con la forma de `MarketHub.state`.
- El cliente deja de abrir la conexión pública (`build_venues(public=True)`).
- Tests: tras una secuencia de eventos de un venue falso, el estado del cliente
  coincide con el del hub (book, velas, último trade, conexión); al reconectar
  llega otra foto; el que no lee se desconecta.
- Hecho: `L2Book.snapshot(n)`; `MarketHub.subscribe_events` (eventos que el hub
  acepta); `ControlServer` manda `market_init` al conectar y al recargar un
  venue, `trade` y `conn` al momento y `book` agrupado (`book_every_s`,
  `book_depth` = `ui.book_levels`); la foto inicial no cuenta en la cola.
  `RemoteMarket` en `services/remote.py`; `attach` usa `remote.market` como
  hub. El `view` del `hello` pasa a T-65. `attach` aún crea venues públicos
  solo para el precio del token de resultados, hasta T-64. Tests en
  `tests/services/test_remote_market.py`.

### T-63 Órdenes por el socket · `done` · RF-06, RF-07, RF-08, RF-10, RF-31, RNF-11, CA-16 · dep T-62 · D-66
- Peticiones con `id`, `reply` etiquetado, una tarea por petición.
- `prepare`/`confirm`, `prepare_double`/`confirm_double`, `plan_cancel`/
  `execute_cancel` con propuestas por `token` (60 s, un solo uso).
- `RemoteOrders` con la cara de `OrderService`; la TUI cliente deja de
  rechazar órdenes, doble y cancelar.
- Tests: orden simple y doble confirmadas por el cliente llegan al venue
  simulado y al journal del daemon; token caducado, usado o desconocido →
  error; dos clientes con el mismo token → solo uno envía; RiskCheck rechaza en
  el daemon; daemon caído con una petición en vuelo → `DaemonGone` y aviso.
- Hecho: `ControlServer` con peticiones `{id, cmd, args}` en su tarea (referencia
  fuerte en `_asking`), `reply {id, kind, value, token}` o `{id, error}`;
  propuestas en `_proposals` por `token` (`secrets.token_hex`), `proposal_ttl_s`
  60 s, se quitan antes de ningún await (un solo uso aunque lleguen dos a la
  vez). `plan_cancel` con `{kind, value}` (CancelAll no tiene campos).
  `RemoteEngine.request` con futuros (`DaemonRefused`, `DaemonGone` al caer) y
  `RemoteOrders`; un rechazo al confirmar vuelve como `Failed`/`DoubleRejected`/
  `CancelResult` con error. La app conectada opera: solo `w` y `e` siguen
  bloqueadas (T-64). Tests en `tests/services/test_remote_orders.py` y
  `tests/ui/test_attach_ui.py`.

### T-64 Resultados, depósitos y reequilibrio por el socket · `done` · RF-25, RF-28, RF-29, RF-31 · dep T-63 · D-66
- `results {range}`, `deposits`, `withdrawal_fees`; `Engine` crea los tres
  servicios. `RemoteResults`, `RemoteDeposits`, `RemoteRebalance`.
- Tests: las tres ventanas abren en el cliente con datos del daemon; el
  cliente no lee el journal.
- Hecho: `Engine(results=, deposits=, rebalance=)`, creados en `build_engine`.
  Comandos `results {range}` (precio del token y lectura del journal en un
  hilo), `deposits` (`Rows`) y `withdrawal_fees` (`Fees` en filas: una clave de
  JSON no puede ser tupla). `Snapshot` con `deposit_venues`, `fee_venues` y
  `has_results`. El daemon reenvía `fill` para que el cliente rehaga resultados.
  `RemoteResults` (`refresh(rng)` y `load(rng)` del último informe),
  `RemoteDeposits` y `RemoteRebalance`; sin daemon, cada fila dice el error.
  `ResultsService.refresh(rng)` y `ResultsScreen.refresh_report` dan la misma
  cara a los dos. `attach` ya no crea venues. Tests en
  `tests/services/test_remote_services.py`.

### T-65 `btcb2t` es el cliente · `done` · RF-30, RF-31, RNF-17, CA-15, CA-16 · dep T-64 · D-66
- `hello` con `view` (lo que la pantalla necesita de la config); el cliente
  deja de leer `config.toml`.
- Fuera la terminal todo en uno (`build_app` con motor) y las ramas de la app
  sin daemon. `btcb2t` = `btcb2t attach`. Daemon sin keys en solo lectura.
- `test_layers`: `ui/` solo importa `core/`, `services/remote.py` y
  `services/wire.py`.
- Tests de UI a un fixture común (daemon con venues falsos en el mismo loop +
  `BTCB2App` cliente), con las mismas aserciones. CA-16 automático.
- README: uso con daemon y cliente.
- Hecho: `hello` con `ClientView` (niveles, timeframe, descuentos, inventario,
  límites del bot); `btcb2t` y `btcb2t attach` son el cliente
  (`build_client_app`): sin config, sin keys, sin venues, log `client.log`;
  `--live` en el cliente es error. Fuera `build_app` y la terminal todo en uno;
  el daemon sin keys arranca en solo lectura; mensaje del lock solo de daemon.
  `ui/app.py` solo importa `core/` y `services/remote`: tipos a
  `core/proposals.py`, `core/notices.py` y `DepositRow` a `core/models.py`;
  `Runner` (protocolo) en vez de crear un `Engine`. Los tests de pantalla usan
  `tests/ui/local.py` (D-66, añadido). `test_layers` vigila los módulos de
  `services` que importa `ui`. CA-16 en `tests/ui/test_ca16.py`. Arreglos
  vistos en el camino: las piernas de una doble recién confirmada se pintan
  cuando llega la foto (antes `KeyError`), y una ruta de socket que no cabe se
  explica en vez de dar una traza. Probado con el mercado real: daemon en solo
  lectura con otro `--data-dir` y el cliente sin pantalla recibe books, velas y
  conexión; SIGTERM sale limpio y borra el socket.

### T-67 Piernas junto a los balances · `done` · RF-05, RF-07, RNF-02 · D-67
- Fila de balances y piernas lado a lado; pierna compacta; `+ en órdenes`
  solo si hay; corte con `…`.
- Tests: con doble, balances y piernas en la misma fila y las piernas a la
  derecha; sin doble, balances a todo el ancho; las líneas caben a 160×45;
  `+ 0.00000000` no sale sin órdenes.
- Hecho: `Horizontal#account-row` con `BalancesPanel` (`1fr`, alto 5) y
  `LegsPanel` (64 columnas), los dos con `text-wrap: nowrap` y `…`. Pierna:
  `COMPRA NonKYC 0.0100 @ 0.00725000 · abierta 0.0000` (lo lleno; sin
  `/total` ni id). `_pair` sin `+ 0` cuando no hay nada en órdenes. Título de
  balances más corto (`Balances · BTC … · XBT … · valor … · XBT % (objetivo)`).

### T-68 El journal se pone al día al arrancar · `done` · RF-11, RF-25 · dep T-59 · D-68
- `PastFill` en `core/models.py`; el tracker los emite en el primer refresco.
- `AccountService` (live) compara con el journal: `fill_fee` o `fill`
  recuperado; lo demás se ignora.
- Tests: primer refresco → `PastFill`, después igual que antes; fee distinta →
  `fill_fee`, igual → nada; trade ausente de una orden nuestra → `fill` con
  `client_id`, `recovered` y `trade_ts`; ajeno → nada; dry-run → nada; las
  piernas ignoran `PastFill`.
- Hecho: `PastFill` en `core/models.py` y en `PrivateEvent`; `OrderTracker`
  los emite en el primer refresco, por hora. `core/catchup.py` (`index`,
  `decide`) puro; `AccountService._catch_up` (solo live) lee el journal de
  `CATCHUP_DAYS` = 7 días al primer `PastFill` y mantiene el índice al apuntar.
  Límite visto: NonKYC da los 50 trades más recientes (`MY_TRADES`); lo que se
  corrigió antes de esos no se alcanza (d-550cd7, con 209 fills detrás).
  Tests en `tests/services/test_journal_catchup.py` y
  `tests/adapters/test_private.py`.

### T-69 Valor de la cuenta en el tiempo · `done` · RF-32, CA-17 · dep T-64 · D-69
- `core/valuation.py` (muestra, serie, resumen), `ValueRecorder` en el daemon,
  `Report.values`, pestaña "Valor de la cuenta" en resultados con dos gráficas
  y resumen. `value_every_s` en `[polling]`.
- Hecho: `core/valuation.py` puro (`sample`, `sample_data`, `series`,
  `summarize`, `segments`, `thin`); evento `value` en el journal;
  `services/valuation.py` `ValueRecorder` (múltiplos UTC del intervalo, fallo →
  `Faults` "value") arrancado por `Engine.services()` si hay cuenta y journal;
  `Report.values` (viaja por `results` sin comando nuevo); `ResultsScreen` con
  `TabbedContent` (Dobles · Valor de la cuenta, `Tab` cambia) y `ValueChart`
  (total y por venue, cortado en huecos > 2 intervalos, ≤ 300 puntos). Tests en
  `tests/unit/test_valuation.py`, `tests/services/test_value_recorder.py`,
  `tests/ui/test_ca17.py`, `test_engine.py`, `test_remote_services.py`.

### T-70 Resultados se refrescan solos · `done` · RF-25, RF-32, CA-17 · dep T-69 · D-69
- `ResultsScreen` pide el informe cada `RESULTS_EVERY_S` (60 s) mientras está
  abierta; sin cambios no redibuja; la tabla conserva la fila seleccionada.
- Hecho: `set_interval(RESULTS_EVERY_S, refresh_report)` en `on_mount`;
  `reload` compara el `Report` nuevo con el anterior y no redibuja si es igual;
  `_draw` guarda la `row_key` seleccionada y vuelve a ella tras rehacer la
  tabla. Tests en `tests/ui/test_ca17.py::TestRefresco`.

### T-71 Valor de la cuenta ajustado al último tramo · `done` · RF-32, CA-17 · dep T-69 · D-69
- `core/valuation.py`: `last_stretch(values)` (desde el último salto > 5 %).
  `z` en la pestaña de valor alterna rango entero / último tramo en las dos
  gráficas; el resumen da el cambio del tramo en sats.
- Hecho: `last_stretch` y `JUMP_PCT` en `core/valuation.py` (salto en el total o
  en un venue); `ResultsScreen.zoom` con `z`; en el tramo, `ValueChart` dibuja el
  cambio en sats desde la primera muestra (en BTC las etiquetas del eje no
  distinguen sats) y fija el eje Y a los datos (plano, plotext lo abría ±50 %).
  Comprobado con el journal real: el tramo 04:10–17:55 UTC del 2026-10-26 se ve
  con -34 415 sats, casi todo por el mid de XBT.

### T-72 Bot: menos complejidad, mismo comportamiento, latencia medida · `done` · RF-19, RF-20, RNF-22, RNF-23 · D-70
- Oráculo congelado + test de equivalencia; profundidad con `bisect`, poda de
  pares, constantes fuera del bucle; `L2Book.top` con `heapq`; tests de latencia
  (`tests/perf/test_bot_latency.py`).
- Hecho: `core/bot.py` con `_Side` (sumas acumuladas + `bisect`, con vuelta a la
  suma lineal si el lado llega sin ordenar) y `_Pairs` (precios límite, netas por
  unidad, profundidad y topes por fila y columna calculados una vez; `search`
  poda filas y columnas bajo el umbral con búsqueda binaria, descarta filas que
  no llegan al lote o a los mínimos y recorre las demás por cota de neta
  descendente). El primer par se evalúa solo: sin cruce no se prepara nada más.
  Book sin ordenar → todos los pares, como antes. `L2Book.top` y `snapshot` con
  `heapq`. `BotService._views` lee la cuenta una vez por venue.
- Oráculo `tests/unit/bot_reference.py`; `test_bot_equivalence.py` (3000 casos
  ordenados + 600 desordenados en la suite; 200 000 semillas a mano, sin
  diferencias).
- Medido (mediana, antes → después): `decide` con walk 1 % y 50 niveles,
  50.3 → 0.54 ms; sin cruce 0.22 → 0.11 ms; todos los pares rinden → 0.55 ms;
  cruce → `prepare_double` en `BotService`, 78 → 3.6 ms; `top(50)` de 1000
  niveles, 0.19 → 0.20 ms (ya era barato).

### T-73 El bot no dispara tras un movimiento brusco · `done` · RF-19, CA-08 · dep T-72 · D-71
- `max_move_pct` y `move_window_ms` en `[bot]`; ventana de mids en `BotService`,
  `VenueView.move_pct`, `Skip("volatile")` en `decide`; `move` en `bot_fire`.
- Hecho: `core/bot.py` `price_move_pct` y `_volatile` (primer venue por orden
  que pasa el tope; sin `max_move_pct`, nada: el oráculo de D-70 sigue igual);
  `BotService` guarda `(hora, mid)` en cada cambio de book, olvida lo que sale de
  la ventana y añade el mid actual (un book quieto da 0); `bot_fire.move` por
  venue; una línea en `diag.log` al empezar a frenar; línea en la confirmación
  de `bot on`. Config `_move` con `move_window_ms` entre 100 y 60000.
- Análisis previo (D-71): 12 de 472 dobles del bot con una pierna sin llenar;
  la red no es (WiFi −40 dBm, 3.6 ms al router, 0 % de pérdida; confirmaciones
  normales; sin caídas del WS antes de 10 de 12) y la neta no las distingue.

### T-74 Salto tras un book quieto · `done` · RF-19, CA-08 · dep T-73 · D-72
- La ventana de mids de `BotService` conserva el último mid de antes de su
  inicio (el que regía al empezarla); sin él, un salto tras más de
  `move_window_ms` de book quieto daba `move` 0 (d-60003f).
- Hecho: `BotService._forget` borra el primero solo si el segundo también ha
  salido de la ventana. Test `test_salto_tras_un_book_quieto_cuenta`.
