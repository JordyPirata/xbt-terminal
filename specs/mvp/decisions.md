# Decisiones

Append-only. Cada entrada: fecha, contexto, decisión y qué spec toca.
Las respuestas a preguntas abiertas (`PA-NN`) también van aquí.

## Índice

Una línea por decisión; el detalle, en su entrada. Las que otra corrige lo
dicen en su propia nota.

| ID | Fecha | Decisión |
| --- | --- | --- |
| D-01 | 2026-10-19 | Todo asíncrono en el loop de Textual |
| D-02 | 2026-10-19 | Dry-run es el modo por defecto con keys |
| D-03 | 2026-10-19 | ConnState separa canal y fuente |
| D-04 | 2026-10-19 | Credenciales por venue, modo global |
| D-05 | 2026-10-19 | Validación estricta de config.toml |
| D-06 | 2026-10-19 | Sintaxis de la orden doble (resuelve PA-07) |
| D-07 | 2026-10-20 | Exposición por venue = valor custodiado (resuelve PA-06) |
| D-08 | 2026-10-20 | Par BTCB2/BTC en ambos venues (resuelve PA-01, spec v0.2) |
| D-09 | 2026-10-20 | WebSocket de Neoxa: Socket.IO propio y snapshots completos |
| D-10 | 2026-10-20 | NonKYC: PA-02 y rarezas de datos públicos (T-09) |
| D-11 | 2026-10-20 | Supervisor de conexión (T-12) |
| D-12 | 2026-10-20 | Pantalla en solo lectura (T-13) |
| D-13 | 2026-10-20 | Dry-run con balances simulados y frescura del book (T-16) |
| D-14 | 2026-10-20 | Adaptadores privados y seguimiento de órdenes (T-17) |
| D-15 | 2026-10-20 | Orden doble y pierna colgada (T-18, supuesto de PA-08) |
| D-16 | 2026-10-20 | Dry-run con los saldos reales (T-16b, sustituye parte de D-13) |
| D-17 | 2026-10-20 | Paneles de cuenta y cancelar (T-19) |
| D-18 | 2026-10-21 | Órdenes más fáciles y rápidas, y ayuda (T-21, requirements v0.3) |
| D-19 | 2026-10-21 | Tamaño por orden = 25 % del inventario total (T-22, PA-03 en parte) |
| D-20 | 2026-10-21 | Pruebas de aceptación en real, parte 1 (T-20) |
| D-21 | 2026-10-21 | Cantidad como porcentaje del saldo del venue (T-23, requirements v0.5) |
| D-22 | 2026-10-21 | NonKYC: `side` en mayúsculas en `/account/orders` (fix de T-17) |
| D-23 | 2026-10-21 | Velas de 4h y diarias (T-24, requirements v0.6) |
| D-24 | 2026-10-21 | Velas a paso fijo de columnas (fix de T-13) |
| D-25 | 2026-10-21 | Robustez para producción (requirements v0.7) |
| D-26 | 2026-10-21 | Envíos inciertos y reconciliación (T-25, RNF-12) |
| D-27 | 2026-10-21 | Envíos atómicos por venue (T-26, RNF-13) |
| D-28 | 2026-10-21 | Fallos aislados (T-27, RNF-14) |
| D-29 | 2026-10-21 | Salida segura (T-28, RNF-15) |
| D-30 | 2026-10-22 | Reloj local y desfase con los venues (T-29, RNF-16) |
| D-31 | 2026-10-21 | Fase 2: bot de arbitraje taker (requirements v0.8) |
| D-32 | 2026-10-21 | Rutas XDG, permisos e instancia única (T-30) |
| D-33 | 2026-10-21 | Log de diagnóstico (T-31, RNF-19) |
| D-34 | 2026-10-21 | El bot antes que la CI |
| D-35 | 2026-10-21 | Decisión del bot (T-35) |
| D-36 | 2026-10-21 | Servicio del bot (T-36) |
| D-37 | 2026-10-21 | El bot en la pantalla (T-37) |
| D-38 | 2026-10-21 | Descuento de fee en Neoxa (requirements v0.9, T-39) |
| D-39 | 2026-10-21 | ctrl+b para el bot desde cualquier sitio |
| D-40 | 2026-10-22 | Apagar el bot mientras dispara |
| D-41 | 2026-10-22 | Seguir las órdenes desde que se envían |
| D-42 | 2026-10-22 | Fees de NonKYC confirmadas (cierra PA-04) |
| D-43 | 2026-10-22 | Bot en real, parte 1 (T-38); CA-02 cumplido |
| D-44 | 2026-10-22 | Resultado de las dobles (requirements v0.10, T-40) |
| D-45 | 2026-10-22 | Fills de Neoxa sin orden (T-40) |
| D-46 | 2026-10-22 | Ratio de inventario por venue (requirements v0.11, T-41) |
| D-47 | 2026-10-22 | Comisiones de retiro de los venues y coste de reequilibrar |
| D-48 | 2026-10-22 | BTCB2 se muestra como XBT |
| D-49 | 2026-10-22 | Book centrado con barras de profundidad |
| D-50 | 2026-10-22 | Direcciones de depósito en la terminal (requirements v0.12, T-42) |
| D-51 | 2026-10-23 | Errores sin texto y el bot disparando sin red (T-43) |
| D-52 | 2026-10-22 | Copiar con la herramienta del sistema y QR de la dirección (requirements v0.13, T-44) |
| D-53 | 2026-10-23 | Plan de reequilibrio entre venues (requirements v0.14, T-45) |
| D-54 | 2026-10-23 | Auditoría de la spec (requirements v0.15) |
| D-55 | 2026-10-23 | Correcciones de la auditoría (T-46) |
| D-56 | 2026-10-23 | Reequilibrio real Neoxa → NonKYC: comisión aparte confirmada |
| D-57 | 2026-10-23 | Bot en real, parte 2: parada por tope (cierra CA-10, T-38) |
| D-58 | 2026-10-23 | `/cancelallorders` de NonKYC pide `side` (T-47) |
| D-59 | 2026-10-23 | CA-05 en real; `status=active` de NonKYC trae canceladas (T-48) |
| D-60 | 2026-10-23 | El bot recorre el book; sin órdenes market (T-49) |
| D-61 | 2026-10-24 | Daemon sin pantalla y TUI conectada (v0.17) |
| D-62 | 2026-10-24 | El daemon no veía los fills (T-56) |
| D-63 | 2026-10-24 | Fee de NonKYC pagada en NKYC; el bot con descuento (requirements v0.18, T-57, T-58) |
| D-64 | 2026-10-25 | NonKYC corrige la fee de un fill después (requirements v0.19, T-59) |
| D-65 | 2026-10-25 | Fees en token valoradas en resultados (requirements v0.20, T-60) |
| D-66 | 2026-10-25 | El daemon es el servidor y la TUI, su cliente (requirements v0.21, T-61 a T-66) |
| D-67 | 2026-10-25 | Piernas de la doble junto a los balances (T-67) |
| D-68 | 2026-10-25 | Al arrancar, el journal se pone al día con los trades del venue (T-68) |
| D-69 | 2026-10-25 | Valor de la cuenta muestreado en el journal (requirements v0.22, T-69) |
| D-70 | 2026-10-26 | Decisión del bot en O(L² log L) en el peor caso, con oráculo y presupuesto de latencia (requirements v0.25, T-72) |
| D-71 | 2026-10-26 | El bot no dispara tras un movimiento brusco del mid (requirements v0.26, T-73) |
| D-72 | 2026-10-26 | La ventana del movimiento conserva el mid que regía al empezar (T-74) |
| D-73 | 2026-10-26 | El comando y las rutas pasan a `xbt` (requirements v0.27, T-75) |
| D-74 | 2026-10-26 | Licencia AGPL-3.0 y guía de despliegue (T-76) |
| D-75 | 2026-10-27 | El bot envía primero la pierna de Neoxa y la otra solo si llena (requirements v0.28, T-77) |
| D-76 | 2026-10-29 | El book quieto se contrasta con el REST; la conexión viva ya no basta (requirements v0.29, T-78, corrige la frescura de D-13) |
| D-77 | 2026-10-29 | Legibilidad por partes, sin cambio de comportamiento; primero `risk.check` y `commands.parse` (T-79) |
| D-78 | 2026-10-29 | Los fallos se piden al `Engine` (`report_fault`, `fault_count`, `subscribe_faults`), no a su lista (T-82, corrige D-77) |
| D-67 | 2026-10-25 | Eje Y de las velas ajustado a los cuerpos, no a las mechas |

## D-01 · 2026-10-19 · Todo asíncrono en el loop de Textual
**Contexto:** `btcb2-arb` usa `requests` síncrono con hilos. Textual, httpx y
websockets son asyncio nativos.
**Decisión:** adaptadores nuevos en async. Se reutiliza el *conocimiento* de
`btcb2-arb` (auth, endpoints, rarezas de cada API, ver design.md §4), no su código.
**Toca:** RNF-01, RNF-09.

## D-02 · 2026-10-19 · Dry-run es el modo por defecto con keys
**Contexto:** RF-13 pide dry-run y solo lectura, pero no dice cuál es el default.
**Decisión:** con keys, el modo es dry-run salvo `--live` explícito. Sin keys,
solo lectura.
**Toca:** RF-13, design.md §8.

## D-03 · 2026-10-19 · ConnState separa canal y fuente
**Contexto:** design.md §4 codificaba el respaldo REST como `channel="rest-fallback"`,
mezclando qué conexión es (market/private) con por dónde llegan los datos (ws/rest).
**Decisión:** `ConnState(channel, source, status)`: la barra de estado (T-15) puede
mostrar "market: rest · up" sin parsear strings.
**Toca:** RF-01, RF-14, design.md §3-4.

## D-04 · 2026-10-19 · Credenciales por venue, modo global
**Contexto:** design.md §8 no decía qué pasa con keys de un solo venue.
**Decisión:** el modo (`read-only`/`dry-run`/`live`) es global; un venue sin keys
no opera (`Settings.can_trade`) pero sigue mostrando market data. Permite probar
NonKYC sin tener aún keys de Neoxa. La orden doble (T-18) exigirá ambos.
**Toca:** RF-12, RF-13, design.md §8.

## D-05 · 2026-10-19 · Validación estricta de config.toml
**Contexto:** un TOML `0.05` se lee como float, y una clave mal escrita en `[risk]`
se ignoraría en silencio dejando un límite sin aplicar.
**Decisión:** importes como string (`"0.05"`), cualquier clave desconocida es error,
y la config no puede superar los techos de RF-05 (balances ≤ 10 s) ni de RNF-03
(Neoxa ≤ 80% de lo publicado).
**Toca:** RF-05, RF-10, RF-12, RNF-03.

## D-06 · 2026-10-19 · Sintaxis de la orden doble (resuelve PA-07)
**Decisión:** `dbl <cantidad> <venue_compra> <precio_compra> <venue_venta> <precio_venta>`,
p. ej. `dbl 0.01 nk 850 nx 900`. La pierna de compra va siempre primero; las dos
deben ser de venues distintos. No se exige precio de venta > precio de compra: el
spread se muestra en la confirmación y la decisión es del operador.
**Toca:** RF-07, design.md §6.4, §9.

## D-07 · 2026-10-20 · Exposición por venue = valor custodiado (resuelve PA-06)
> Desde D-08 el valor es en BTC (`BTC total + BTCB2 total × mid`), no en USDT.
> `max_qty` y `max_notional` los sustituye `max_order_pct` (D-19). Nota de D-54.

**Decisión:** opción (a). Tras la orden, `USDT total + BTCB2 total × mid` no puede
superar `risk.max_venue_exposure`. Cuenta lo bloqueado en órdenes; ignora fees
(sobrestima el valor, que es lo conservador).
**Consecuencia asumida:** una orden límite apenas mueve el valor custodiado, así
que la regla frena sobre todo cuando el venue ya está por encima del tope. Lo que
limita el tamaño de cada orden son `max_qty` y `max_notional`.
**Toca:** RF-10, design.md §6.3.

## D-08 · 2026-10-20 · Par BTCB2/BTC en ambos venues (resuelve PA-01, spec v0.2)
**Contexto:** el usuario no quiere fondos en USDT ni USDC: el emisor de una
stablecoin puede congelarlos aunque el venue funcione. La v0.1 asumía USDT y
dejaba BTC/BTCB2 fuera de alcance.
**Verificado** en las APIs públicas el 2026-10-20: NonKYC `BTCB2/BTC` (activo,
precio 8 dec., cantidad 4 dec., ~0.00729 BTC, 1,086 BTCB2/24 h) y Neoxa
`BTCB2_BTC` (id 47, sin pausa, 8/8 dec., ~0.00738 BTC, 676 BTCB2/24 h,
maker 0.1 % / taker 0.2 %). Ambos libros tienen profundidad; el de Neoxa mezcla AMM.
**Decisión:** el MVP opera BTCB2 contra BTC en ambos venues. Nuevo RNF-10: la config
rechaza cualquier otro quote. Nocionales, topes y valoraciones en BTC.
**Toca:** requirements.md (v0.2: objetivo, alcance, fuera de alcance, RF-05,
supuestos, riesgos, RNF-10), design.md §4, §6, config.example.toml, T-03b.

## D-09 · 2026-10-20 · WebSocket de Neoxa: Socket.IO propio y snapshots completos
**Contexto:** Al grabar los fixtures de T-08 se vio que el WebSocket público de
Neoxa es Socket.IO v4 (Engine.IO 4) en el namespace `/exchange`, y no un
WebSocket plano. Se suscribe con `subscribe {pair}`. Cada `exchange:orderbook` es el book completo
(50 niveles por lado), sin `seq` ni timestamp, y se reemite cuando cambia.
`exchange:trades` (los últimos 20) llega una vez al suscribirse y `exchange:trade`
(uno) llega en vivo. El WS privado exige un JWT de sesión web y no acepta API key,
así que lo privado de Neoxa queda por polling (RNF-04). Límites confirmados en
`/api-docs` y en las cabeceras `RateLimit-*`: 600/60 s por IP, 10 órdenes y 20
cancelaciones por 10 s.
**Decisión:**
- El cliente Engine.IO/Socket.IO es mínimo y propio (`adapters/socketio.py`) y va sobre
  `websockets`. No se usa `python-socketio`: arrastra aiohttp y trae su propia reconexión,
  que chocaría con el supervisor de T-12.
- Cada `exchange:orderbook` se convierte en un `BookSnapshot` (con `seq` None) y
  `ts` = hora local de recepción.
- Los trades se deduplican por `trade_id` en el adaptador, en todas las fuentes
  (REST, lote inicial del WS y trade en vivo). Así, tras una reconexión, el lote
  inicial solo entrega lo que faltaba.
- Mientras el WS esté caído, el respaldo REST pide el book cada
  `rest_fallback_every_s` y los trades cada 5 vueltas, para no gastar el doble del
  presupuesto de §5. T-08 hace un cambio simple (WS → REST durante una ventana → WS);
  el backoff, la regla de 3 fallos y la latencia son de T-12.
- `side` de los trades públicos se toma como lado del taker; falta confirmarlo
  con un fill propio en T-17.
**Toca:** RF-01, RNF-03, RNF-04, design.md §4-5.

## D-10 · 2026-10-20 · NonKYC: PA-02 y rarezas de datos públicos (T-09)
**Contexto:** se resolvió PA-02 con la OpenAPI (`/openapi.json` v1.3.0), con
llamadas públicas y con el WS `wss://ws.nonkyc.io`, todo el 2026-10-20.
**Hallazgos:**
- **Límites de rate:** ni la OpenAPI ni las cabeceras los publican (no hay
  `RateLimit-*`); solo el error 429. Se mantiene el supuesto: 300/min, que con el
  20 % de margen queda en 240/min (`ratelimit.nk`).
- **WS privado con API key: sí.** El método `login` (`algo: "HS256"`, `pKey`,
  `nonce`, `signature`) existe: con una clave falsa responde 1002 "Valid API keys
  must be supplied". La firma exacta se verifica en T-17 con keys reales.
- **El WS público es JSON-RPC 2.0:**
  - `subscribeOrderbook` trae un `snapshotOrderbook` con `sequence` y `timestamp`,
    y después `updateOrderbook`. Volver a suscribirse trae un snapshot nuevo.
  - `subscribeTrades` trae un `snapshotTrades` (50) y después `updateTrades`.
  - Si una petición es errónea (símbolo o método inexistente), el servidor responde
    con error y corta la conexión.
- **El `sequence` del book REST no es el del WS** (36725 frente a 91658 en el
  mismo momento).
- **No hay velas de 1m:** `resolution` admite 5, 15, 30, 60... y con 1 responde
  `noData`. Además, sin `firstDataRequest=1` o sin `from`/`to`, cualquier
  resolución responde `noData`.
- **Los números van como strings, salvo** `minQuote` y los OHLCV de las velas,
  que son números JSON y se leen con `parse_float=Decimal`.
- **`/market/trades` no sigue su OpenAPI:** trae `id`, `type`, `baseVolume` y
  `timestamp` en ms (int). Admite un máximo de 500 trades, ~4 h a día de hoy. Incluye trades
  del pool AMM, a precios dentro del spread.
**Decisión:**
- Los snapshots REST salen con `seq` None. Los deltas del WS se aplican solo
  sobre el snapshot del WS. Si el adaptador ve un hueco de `sequence`, se vuelve a
  suscribir: `L2Book` queda inválido hasta que llega el snapshot nuevo.
- Las velas de 1m se construyen con `CandleBuilder` a partir de los últimos 500
  trades REST. Si se llega al tope de 500, se descarta la primera vela, porque
  puede estar incompleta.
- Las velas de 5m y 1h se piden con `firstDataRequest=1`.
- Las fees salen de la config (PA-04 sigue abierta).
- Se reconoce el WS como "up" cuando llega el primer snapshot del book, no al
  conectar: es la prueba de que la suscripción funcionó.
- `min_qty` toma el lote cuando `minimumQuantity` es 0, y `min_notional` toma
  `minQuote` si `isMinQuoteActive`.
- `updateOrderbook` (17 grabados): `timestamp` ISO dentro de `params`,
  `sequence` como int, y `quantity` como string o como número `0` para borrar.
  **Llegan desordenados** (91663 antes que 91662, con el mismo timestamp; 3 de 17). El
  adaptador reordena: un delta adelantado espera hasta 1 s al que falta, y solo
  entonces se trata como hueco. Los duplicados y los anteriores al snapshot se
  descartan.
- El book de BTCB2/BTC en NonKYC cambia poco: 17 deltas en 15 min.
**Toca:** PA-02 (resuelta), RF-01, RF-03, RNF-05, design.md §4, config.example.toml.

## D-11 · 2026-10-20 · Supervisor de conexión (T-12)
**Contexto:** design.md §6.1 fija el backoff (0.5 s → 30 s con jitter), el
respaldo REST tras 3 fallos y la latencia como media móvil. Faltaba precisar
qué cuenta como fallo, cuándo se declara "up" y cómo se mide.
**Decisión:**
- **Un fallo es una sesión WS que termina**, sea por error o por cierre. El
  contador de fallos seguidos vuelve a 0 cuando una sesión entrega su primer
  book. No basta con conectar: un servidor que acepta y se cae enseguida
  acabaría pasando a REST.
- **Backoff:** espera = `min(30, 0.5 × 2^(n-1)) × U(0.8, 1.2)`, con el resultado
  capado a 30 s; n es el número de fallos seguidos.
- **Respaldo REST:** al tercer fallo seguido arranca un polling REST en paralelo, y el
  WS se sigue reintentando con el mismo backoff. El polling se para con el
  primer book que llegue por WS, y ese book reemplaza al de REST.
- **`ConnState`** lo emite solo el supervisor; las sesiones de cada venue entregan
  solo datos.
  - `(ws, up)` justo antes del primer book de cada sesión.
  - `(ws, reconnecting)` tras cada fallo, salvo con el REST activo: entonces el
    estado visible es el del REST, `(rest, up)` o `(rest, down)`.
  - Cada `latency_every_s` (5 s) se reemite el estado actual con la latencia.
- **Latencia:** media móvil exponencial (α = 0.3).
  - En WS se toma `ws.latency`, el RTT de los pings keepalive de websockets
    (cada `ping_interval_s`, 5 s).
  - En REST, la duración de cada petición.
  - `latency_ms` es None hasta la primera medida.
**Toca:** RF-01, RF-14, CA-06, design.md §6.1.

## D-12 · 2026-10-20 · Pantalla en solo lectura (T-13)
**Contexto:** con 45 filas no caben unas velas legibles y además 15 niveles por
lado apilados (33 filas solo para el book). Además, `MarketHub` no tenía tarea
propia, aunque su módulo ya se asignaba a T-12 y T-13.
**Decisión:**
- **El book va en escalera horizontal:** compras a la izquierda, ventas a la
  derecha, N filas más cabecera y resumen (`N + 4` de alto, 19 con N = 15).
  Las velas ocupan el resto: unas 20 filas a 160×45.
- **Una fila del book mide 77 columnas**, para caber en media pantalla. Los niveles
  AMM llevan la marca ` A` y la leyenda va en el borde del panel de Neoxa.
- **`MarketHub` se construye en T-13** (`services/market.py`):
  - Carga el mercado y 300 velas por timeframe.
  - Si falla, lo reintenta cada 5 s y publica `(rest, down)`.
  - Descarta los trades anteriores a la carga del histórico.
  - Avisa de los cambios con `(venue, qué)`.
- **Redibujado con throttle:** books cada 100 ms y velas cada 1 s. `roll()` corre
  cada 1 s para abrir velas planas.
- **Floats solo para dibujar:** las velas se pasan a float únicamente para
  plotext. Todo número que se muestra como texto sale del Decimal.
- **Arranque (`btcb2t`):** `config.toml` → venues reales → hub → app. El modo se
  muestra en la barra inferior. La barra de estado completa es de T-15.
**Toca:** RF-02, RF-03, RNF-02, RNF-06, design.md §9.

## D-13 · 2026-10-20 · Dry-run con balances simulados y frescura del book (T-16)
> Los balances simulados los sustituye D-16 (saldos reales). La frescura sigue vigente.
**Contexto:** design.md §4 hace que `DryRunVenue` delegue los balances en el
venue real, pero leer balances reales es de T-17. Además, al medir el WS de
NonKYC (D-10) se vio que su book pasa hasta 352 s sin un solo mensaje porque no
cambia. Con `stale_data` mirando solo la hora del último mensaje del book, casi
todas las órdenes en NonKYC se rechazarían sin motivo.
**Decisión:**
- **Balances simulados (decisión del usuario):**
  - Los saldos del dry-run salen de `[dryrun.balances.<venue>]` en config.toml,
    con BTC y BTCB2 como strings. Cualquier otro activo es error (RNF-10).
  - Sin la sección, los saldos son 0 y toda orden se rechaza por
    `insufficient_balance`.
  - Los valores del ejemplo quedan por debajo de `max_venue_exposure`; si no,
    `venue_exposure` rechazaría cualquier orden.
- **`DryRunVenue`:**
  - Simula `place_limit`, `cancel` y `cancel_all` en memoria.
  - Al abrir una orden bloquea el saldo: en una compra, `qty × precio × (1 + taker)`
    de BTC; en una venta, `qty` de BTCB2. Al cancelar lo libera.
  - **No simula fills:** las órdenes quedan abiertas hasta cancelarlas.
  - Los datos de mercado los sigue dando el adaptador real.
- **Frescura para `stale_data`:** la hora de referencia es la más reciente entre
  el último mensaje del book y el último latido de una conexión de mercado "up"
  (cada 5 s, D-11). Un book quieto con la conexión viva no está viejo; si la
  conexión cae, no hay latidos y a los 15 s se rechaza.
- **Formato de los mensajes de rechazo:** los importes en BTC salen con todos sus
  decimales. Los `:.2f` heredados de la v0.1 (USDT) mostraban 0.00 BTC.
- **Pendiente** (anotado en HANDOFF):
  - En T-17, sustituir los balances simulados por los reales.
  - Decidir con el usuario si el dry-run puede arrancar sin keys: hoy, sin keys
    el modo es solo lectura (D-02).
**Toca:** RF-06, RF-10, RF-13, CA-04, design.md §4, §6.2, §6.3, config.example.toml.

## D-14 · 2026-10-20 · Adaptadores privados y seguimiento de órdenes (T-17)
> "Dry-run no cambia: saldos simulados" lo sustituye D-16 (saldos reales). Nota de D-54.

**Contexto:** se verificó todo con las keys reales del usuario el 2026-10-20,
solo con lecturas (saldos, órdenes abiertas, trades propios, login del WS
privado de NonKYC), sin enviar ninguna orden. Fuentes: OpenAPI de NonKYC v1.3.0,
el ejemplo oficial `NonKYCExchange/websocketapiexample-main` y la página
`/api-docs` de Neoxa.
**Hallazgos:**
- **NonKYC REST:** firma HMAC-SHA256(secret, key + url + body + nonce), con la url
  completa (query incluida) y el body tal como viaja. Órdenes con `status`
  `Active`, `Partly Filled`, `Filled` o `Cancelled`; `/account/trades` trae
  `orderid` y la fee en el activo de cotización. `held` es lo bloqueado en
  órdenes; `pending`, depósitos sin confirmar.
- **NonKYC WS privado:** `login` con `algo: "HS256"`, `pKey`, `nonce` aleatorio y
  `signature` = HMAC-SHA256(secret, nonce). Con las keys reales responde
  `result: true`; `subscribeReports` responde `activeOrders`. **El formato de
  `report` no está documentado** y no se vio ninguno (no había órdenes).
- **Neoxa:** las API keys solo tienen permisos `read` y `trade`: **no pueden
  retirar** (lo dice su `/api-docs`). El WS privado exige JWT de sesión web.
  `/exchange/trades/my` **no dice de qué orden es cada trade**. En `/balances`,
  `total` = `available` + `locked` y no incluye `in_orders` (lo comprometido en
  órdenes). No hay "cancelar todas". Límites por IP: 600/60 s, 10 órdenes y 20
  cancelaciones por 10 s.
**Decisión:**
- **Un único seguimiento para los dos venues** (`adapters/private.py`): cada
  refresco pide por REST las órdenes activas y los últimos 50 trades propios.
  `OrderTracker` saca la diferencia: órdenes nuevas o cambiadas → `Order`, trades
  nuevos → `Fill` (los del primer refresco son historia y no se avisan), y una
  orden que deja de estar activa se consulta hasta saber cómo acabó
  (`/getorder` en NonKYC, `/orders/history` en Neoxa).
- **Neoxa:** refresco cada `neoxa_private_every_s` (3 s), que son 2 peticiones:
  40/min dentro del presupuesto de design.md §5. `Fill.order_id` es None.
  `Balance.locked` = `locked` + `in_orders`. `cancel_all` cancela una a una e
  intenta todas aunque falle alguna. Precio y cantidad van como números JSON con
  el texto exacto del Decimal, nunca por float.
- **NonKYC:** como no se conoce el formato de `report`, cada `report` o
  `activeOrders` del WS solo despierta un refresco REST. Con el WS vivo hay
  además un refresco de seguridad cada 15 s; sin WS, polling cada 3 s. Las
  órdenes van con `strictValidate: true` (si no encajan en la rejilla, el venue
  rechaza en vez de truncar) y `userProvidedId` = client_id. El nonce se fuerza a
  crecer aunque dos peticiones caigan en el mismo ms.
- **`Fill`** gana `trade_id`, `side` y `fee_asset`, y `order_id` pasa a opcional.
- **Balances:** solo BTC y BTCB2; cualquier otro activo de la cuenta se ignora
  (RNF-10).
- **`AccountService`** (`services/account.py`, adelantado de T-19 porque la barra
  de estado lo necesita): consume `private_stream` de cada venue con keys, guarda
  el estado del canal y las órdenes abiertas, escribe cada fill en el journal y
  pide balances al arrancar, cada `balances_every_s` y tras cada fill. Los
  paneles de balances y órdenes siguen siendo de T-19.
- **Barra de estado:** canal privado `● ws`, `● rest` o `✖ caído Ns`; en dry-run,
  "simulado".
- **Modos:** live usa los adaptadores reales para órdenes y cuenta, con un único
  journal. **Dry-run no cambia:** saldos y órdenes simulados (D-13), sin leer la
  cuenta real. Cambiarlo es decisión del usuario (ver HANDOFF).
- **Journal:** los Decimal se escriben en notación fija (`0.00000006`, no `6E-8`).
- **Sin verificar hasta T-20 (CA-02):** las respuestas de enviar y cancelar en
  los dos venues salen de la documentación, no de una orden real.
**Toca:** RF-05, RF-08, RF-09, RF-11, RF-14, RNF-04, design.md §4, §6.5, §9.

## D-15 · 2026-10-20 · Orden doble y pierna colgada (T-18, supuesto de PA-08)
**Contexto:** PA-08 sigue abierta; su supuesto por defecto es "una pierna llena
(total o parcial) y la otra abierta más de `hanging_leg_after_s` o rechazada".
Falta decir desde cuándo se cuenta ese tiempo, y qué pasa si las dos piernas
tienen fills pero distintos.
**Decisión:**
- **Pierna colgada = desequilibrio que no se corrige.** Hay desequilibrio cuando
  las cantidades llenas de las dos piernas difieren. Es colgada si la pierna que
  va por detrás está rechazada o cancelada (inmediato), o si el desequilibrio
  dura más de `hanging_leg_after_s`. El reloj empieza cuando las cantidades dejan
  de coincidir, no al enviar, y se reinicia si vuelven a igualarse. Más fills en
  la pierna que va por delante no lo reinician. Cubre el supuesto de PA-08 y el
  caso de dos parciales distintos.
- Una pierna rechazada con la otra **sin fills** no es colgada (PA-08 pide una
  pierna con fills), pero el mensaje del envío sale en rojo.
- `hanging_leg` se escribe **una vez** por orden doble, con el motivo y el
  estado de las dos piernas. Panel en rojo y aviso en pantalla. Nada automático.
- **Validación:** las dos piernas pasan RiskCheck al preparar y otra vez al
  confirmar; si una falla no sale ninguna, y queda `reject_local` de la que
  falló. Todo lo que escribe una orden doble al journal lleva `double` en `data`.
- **Confirmación:** una sola, con las dos piernas y el spread bruto y neto (neto
  con fee taker en las dos, la cota pesimista), en BTC y sats.
- **Seguimiento:** cada pierna se identifica por su client_id en la respuesta
  del envío y después por `(venue, order_id)`, porque Neoxa no guarda el
  client_id. El estado de la pierna sale de la `Order` (cantidad llena), no de
  los `Fill`, que en Neoxa no traen orden (D-14).
- **Dry-run monta `AccountService`** sobre los mismos `DryRunVenue` que las
  órdenes: así las piernas se siguen igual en los dos modos, y los paneles de
  T-19 tendrán saldos simulados en dry-run. `DryRunVenue.simulate_fill` existe
  solo para los tests; la terminal sigue sin simular fills (D-13).
- **Panel de piernas** (`LegsPanel`): 5 filas entre el panel cruzado y la línea
  de comandos, oculto hasta la primera orden doble; muestra la última.
**Toca:** RF-07, CA-03, PA-08 (supuesto), design.md §6.4.

## D-16 · 2026-10-20 · Dry-run con los saldos reales (T-16b, sustituye parte de D-13)
**Contexto:** D-13 dejó los saldos del dry-run simulados en `[dryrun.balances]`
hasta tener adaptadores privados. Con T-17 hechos, el usuario decide: **el
dry-run usa los saldos reales de la cuenta**; las órdenes siguen simuladas.
**Decisión:**
- `DryRunVenue` lee `balances()` del venue real en cada consulta y le aplica lo
  simulado: lo que bloquean sus órdenes abiertas (pasa de `available` a
  `locked`) y, en los tests, sus fills simulados. Así los saldos siguen los
  cambios reales de la cuenta (fills de órdenes hechas fuera de la terminal) y
  las reglas de RF-10 (`insufficient_balance`, `venue_exposure`) se evalúan contra
  lo que de verdad hay.
- Las órdenes reales que ya tenga la cuenta no aparecen como órdenes del dry-run
  (las órdenes siguen siendo solo las simuladas), pero su saldo comprometido sí
  cuenta en `locked`.
- **`[dryrun.balances]` desaparece** de config.example.toml. Una config que la
  traiga da error al arrancar con un mensaje que dice que se borre (validación
  estricta, D-05): mejor que ignorarla en silencio y que alguien crea que se usa.
- El dry-run sigue exigiendo keys (design.md §8): ahora sí las necesita, para
  leer los saldos.
- Si leer saldos u órdenes falla al validar una orden (red, 401, 429), la orden
  se rechaza con `venue_error` en lugar de lanzar la excepción. Vale para
  dry-run y live: antes de esto, en live, un fallo de red al validar no se
  capturaba.
- El constructor de `DryRunVenue` admite aún saldos fijos, solo para tests.
**Toca:** RF-10, RF-13, D-13, design.md §4, §6.3, §8, config.example.toml.

## D-17 · 2026-10-20 · Paneles de cuenta y cancelar (T-19)
**Decisión:**
- **Balances:** `BalancesPanel` de 4 filas bajo el panel cruzado, una línea por
  venue con BTC y BTCB2 como `disponible + en órdenes`, su valor en BTC al mid y
  la antigüedad. El inventario neto total (BTC, BTCB2 y valor) va en el título
  (`core/inventory.py`). Si falta el mid o los saldos de un venue, el total sale
  "—": un total a medias engañaría. Siempre 8 decimales: el saldo real puede
  tener más que el lote de órdenes (NonKYC opera BTCB2 con 4 y la cuenta tenía
  0.09040708). Oculto en solo lectura.
- **Refresco** (RF-05): cada `balances_every_s`, tras cada fill y también tras
  cualquier cambio de una orden propia, que mueve saldo entre disponible y
  bloqueado.
- **Órdenes abiertas:** pantalla modal (`o` u `orders [venue]`) con las que da
  `AccountService`, que se actualiza sola; `c` cancela la seleccionada, `x`
  todas las de la lista, `Esc` cierra. Una pantalla aparte porque a 160×45 no
  cabe una lista fija sin quitar altura a velas y books.
- **Cancelar:** `cancel <id>|<venue>|all`, `x` o desde la lista, siempre con
  confirmación (por defecto no). Un venue o todos usan el `cancel_all` del
  venue, que cancela **todas las órdenes del par en la cuenta, también las que no
  se enviaron desde la terminal**, y el diálogo lo dice. Tras cancelar se cuentan
  las que siguen abiertas (CA-05 pide cero) y se dice si queda alguna. Journal
  `cancel_sent`, `cancel_ack` con las que quedan, o `error`. Los venues se
  cancelan a la vez y uno que falla no frena al otro.
- **Ids del dry-run con el venue** (`dry-nk-1`): con dos venues simulados,
  `cancel dry-1` era ambiguo. Un id que aparezca en los dos venues se rechaza.
- **Corchetes en pantalla:** el diálogo de confirmación, la lista de órdenes y
  los avisos se pintan sin markup de Rich/Textual. Con markup, desde T-16 el
  diálogo no mostraba `[y]` (la tecla que confirma), y un error del venue con
  `[/bold]` habría lanzado `MarkupError`.
- Atajo `d` empieza una orden doble (design.md §9).
**Toca:** RF-05, RF-06, RF-08, CA-05, design.md §6.5, §9.

## D-18 · 2026-10-21 · Órdenes más fáciles y rápidas, y ayuda (T-21, requirements v0.3)
> `max_order_qty` ya no existe: el tope es `order_size` sobre `max_order_pct` (D-19). Nota de D-54.

**Contexto:** el usuario encontró difícil y lento colocar órdenes. Difícil: había
que teclear un precio de 8 decimales copiado a ojo del book. Lento: cada orden
hacía 3-5 peticiones REST en serie (~0.5 s cada una en Neoxa): saldos y órdenes
al preparar y otra vez al confirmar, más saldos y `market()` dentro de
`DryRunVenue.place_limit`. Eligió: clic en el book, validar con lo que hay en
memoria y ayuda dentro de la app.
**Decisión:**
- **Clic en el book (RF-16):** con la línea de comandos vacía, clic en un ask →
  `buy <venue> <cantidad> <precio>` y clic en un bid → `sell ...` (la
  convención habitual: se compra a lo que se vende). La cantidad es la del nivel
  si el clic es en las columnas de precio o cantidad, y la acumulada hasta ese
  nivel si es en la de acumulado (barrer el book hasta ahí); pedido del usuario
  tras la primera versión, que dejaba la cantidad para teclearla. Queda la orden
  entera: Enter abre el diálogo, o se edita antes. No se recorta a los topes: si
  pasa de `max_order_qty`, RiskCheck la rechaza y dice por qué. Con texto en la línea, inserta solo el precio en el cursor (sirve
  para precios de orden doble o para comprar al bid como maker). El precio se
  copia además al portapapeles con OSC 52, que GNOME Terminal no admite: la vía
  fiable es la línea de comandos.
- **Validación con caché (RNF-11):** `OrderService` toma saldos y órdenes de
  `AccountService` si tienen menos de 30 s (se refrescan cada ≤ 10 s, tras cada
  fill y tras cada cambio de orden propia); si no, los pide al venue como antes.
  Las órdenes recién enviadas cuentan hasta que la cuenta las ve, para que
  `self_cross` y `venue_exposure` no se las salten mientras llega el polling de
  Neoxa (3 s). El book se sigue mirando en vivo al preparar y al confirmar. Si el
  saldo cambió en esos segundos, el exchange rechaza y la terminal lo muestra.
- `DryRunVenue` toma el `MarketSpec` del hub y reutiliza la última lectura real
  de saldos de menos de 30 s, así no hace REST al enviar.
- **Ayuda (RF-15):** pantalla modal con `?` o `help`, sin markup (corchetes).
- **Fallo encontrado:** el `Input` de Textual selecciona todo al recibir el foco
  (`select_on_focus`), así que desde T-16 la primera tecla tras `b`/`s` borraba
  el `buy ` del atajo, y un clic en la línea con texto hacía lo mismo. Se
  desactiva. Medido con mercado y cuenta reales en dry-run: Enter → diálogo
  98 ms y `y` → enviada 85 ms (antes 1-3 s cada uno).
**Toca:** RF-06, RF-15, RF-16, RNF-11, design.md §6.2, §9.

## D-19 · 2026-10-21 · Tamaño por orden = 25 % del inventario total (T-22, PA-03 en parte)
**Contexto:** los topes iniciales (0.05 BTCB2 y 0.0005 BTC por orden) dejaban
cada orden en ~0.0003 BTC; con el clic en el book (D-18) casi cualquier nivel
real los superaba. El usuario pidió primero subirlos y después que el tope fuera
**porcentual: 25 % del total**, del inventario de **los dos exchanges**, y sin
topes fijos.
**Decisión:**
- Regla `order_size`: nocional (`qty × precio`) ≤ `max_order_pct` % del
  inventario total en BTC. Inventario = Σ (BTC total + BTCB2 total × mid de su
  venue) en los venues con keys, con disponible y bloqueado (`core/inventory.py`,
  el mismo valor que el título del panel de balances). Sustituye a `max_qty` y
  `max_order_notional`, que desaparecen de la config.
- **Fallar cerrado:** si el inventario no se puede calcular (faltan saldos o el
  mid de un venue), se rechaza.
- La cantidad máxima en BTCB2 no es fija: sale del precio. Con el inventario del
  2026-10-21 (~0.0068 BTC), 25 % ≈ 0.0017 BTC ≈ 0.26 BTCB2 a 0.0065.
- Cada pierna de una orden doble se mide contra el mismo total.
- El tope sube y baja con el inventario; en dry-run el inventario es el real con
  lo simulado encima.
- Sin cambios: `max_venue_exposure` 0.01 BTC, desviación del mid 5 %,
  `stale_after_s` 15 y `hanging_leg_after_s` 30.
**PA-03: resuelta.** Tamaño por orden, 25 % del inventario total; tope de
exposición por venue, **0.01 BTC**, confirmado por el usuario el 2026-10-21 (se
queda fijo, no porcentual).
**Toca:** RF-10 (requirements v0.4), PA-03, design.md §6.3, config.example.toml.

## D-20 · 2026-10-21 · Pruebas de aceptación en real, parte 1 (T-20)
Sin enviar órdenes reales: la terminal en DRY-RUN con mercado y cuenta reales,
headless a 160×45 (scripts de solo lectura en el scratchpad de la sesión).
- **CA-01 superada.** 30 min sin intervención, una muestra cada 30 s (60): en las
  60, los dos books válidos, conexión `ws/up`, latido ≤ 2.6 s, última vela de 1m
  ≤ 44 s y saldos ≤ 10.4 s. Latencia 220-567 ms en NonKYC y 188-406 ms en
  Neoxa. El mercado casi no se movió (el mid cambió 2-3 veces en 30 min: BTCB2/BTC
  es poco líquido), pero los latidos y las velas planas muestran la terminal viva.
- **Fallo encontrado y arreglado (RF-05):** los saldos llegaban a 10.4 s porque
  `AccountService` esperaba 10 s *después* de cada lectura (~0.4 s). Ahora cuenta
  de inicio a inicio; un test lo vigila.
- **CA-04 superada** con precios y saldos reales: `order_size`, `mid_deviation`,
  `venue_exposure`, `tick`, `lot`, `min_qty`, `min_notional`,
  `insufficient_balance`, `self_cross` (contra una venta simulada) y `stale_data`
  rechazan, 10/10. `venue_exposure` y `stale_data` no se pueden provocar con los
  saldos y conexiones de hoy, así que se probaron bajando el límite (0.005 BTC y
  −1 s) solo durante la prueba. `read_only` queda con su test automático (hay
  keys de los dos venues).
- **CA-07 superada** para lo generado hasta hoy: 26 ficheros (9 journals,
  6 capturas, logs y respuestas grabadas), 4 secretos buscados, 0 apariciones.
  Repetir al final de T-20 con los journals de las órdenes reales.
- Pendiente, con permiso del usuario: CA-06 (corte de red real) y CA-02, CA-03 y
  CA-05 (órdenes reales mínimas en `--live`).

## D-21 · 2026-10-21 · Cantidad como porcentaje del saldo del venue (T-23, requirements v0.5)
**Contexto:** el usuario pidió poder comprar con un porcentaje del BTC basado en
el total que tiene en el exchange, en lugar de calcular la cantidad a mano.
**Decisión:**
- En `buy` y `sell` la cantidad puede escribirse como `N%` (sin espacio,
  0 < N ≤ 100, decimales con punto): `buy nx 25% 0.00725000`. La orden doble
  sigue pidiendo una cantidad fija (las dos piernas deben ser iguales).
- **Base del porcentaje:** el saldo **total** (disponible + en órdenes) del venue
  de la orden, como pidió el usuario: en la compra, el BTC; en la venta, el
  BTCB2. Si parte está bloqueada en órdenes, la regla `insufficient_balance`
  rechaza y dice cuánto hay disponible.
- **Compra:** `qty = N % × BTC total / (precio × (1 + fee taker))`, para que la
  fee quepa en el saldo (con 100 % no se rechaza por la fee). **Venta:**
  `qty = N % × BTCB2 total`. Las dos se redondean **hacia abajo** al lote.
- El cálculo es puro (`core/sizing.py`) y usa los mismos saldos que la
  validación (la caché de la cuenta, D-18). Se hace al preparar; al confirmar se
  revalida la cantidad ya fijada, que es la que se muestra.
- Si el resultado es 0 (saldo vacío o menor que un lote), se rechaza como
  `insufficient_balance`. El resto de reglas de RF-10 se aplica igual: con el
  tope del 25 % del inventario (D-19), un porcentaje alto del saldo de un venue
  puede pasarse y se rechaza como `order_size`.
- El diálogo añade de dónde sale: `(25 % de 0.00622282 BTC en Neoxa)`.
**Toca:** RF-17 (requirements v0.5), design.md §9, ayuda (RF-15).

## D-22 · 2026-10-21 · NonKYC: `side` en mayúsculas en `/account/orders` (fix de T-17)
**Contexto:** primera orden real en NonKYC (`--live`, 10 % del BTC: compra de
0.0103 BTCB2 @ 0.00581, `t-7c87a6`, id `6ad8e12b0c23ade2e155e876`). El venue la
aceptó y la dejó abierta, pero la terminal no la mostraba. El diagnóstico, solo con
lecturas GET, dio:
- la API REST respondía y la orden estaba `Active`, con 0 ejecutado y 0 trades
  propios;
- `/account/orders` devuelve `"side": "BUY"`, mientras que `createorder`/`getorder`
  devuelven `"buy"`. `_side()` solo aceptaba minúsculas, así que cada lectura de
  órdenes abiertas lanzaba `VenueError` y el stream privado se reabría en bucle.
  Sin órdenes no había lista, panel, `self_cross` ni recuento tras cancelar;
- el fixture saneado de T-17 estaba en minúsculas porque esa vez no había órdenes
  reales que grabar, y los tests no lo vieron;
- el error solo quedaba en memoria (`st.error`) y no en el journal (RF-11).
**Decisión:**
- `_side()` normaliza a minúsculas; sigue rechazando lo que no sea buy/sell.
  Afecta a órdenes, trades propios y trades públicos. El fixture pasa a tener la
  forma real (`BUY`/`SELL`, precio sin ceros finales).
- `AccountService` escribe un evento `error` (`where`: private | balances) **una
  vez por racha**. Vuelve a escribirlo si el mensaje cambia o si el fallo vuelve
  tras recuperarse (llegan órdenes o se leen saldos).
- **Lección:** al sanear grabaciones se conservan mayúsculas, formatos y ceros tal
  cual llegan, y cada endpoint se graba por separado: el mismo campo cambia de
  formato entre endpoints del mismo venue.
**Verificado en real (solo lectura):** `fetch_active` devuelve la orden como
`buy 0.0103 @ 0.00581 open`; la app en `--live`, sin teclear nada y con los envíos
bloqueados, la muestra con el canal privado `ws up` y sin error.
**Toca:** T-17, RF-08, RF-11.

## D-23 · 2026-10-21 · Velas de 4h y diarias (T-24, requirements v0.6)
**Contexto:** el usuario pidió velas de 4h y diarias.
**Comprobado en real:** consultas públicas el 2026-10-21. NonKYC acepta en
`/market/candles` `resolution=240` y `1440`; `1D` devuelve `noData`. Neoxa acepta
`interval=4h` y `1d`; `1D` da "Invalid interval". En los dos, las velas empiezan
en múltiplos exactos de 4 h y de 24 h desde la época, es decir, a las 00:00 UTC.
**Decisión:**
- `Timeframe` pasa a ser `1m | 5m | 1h | 4h | 1d`, con teclas `4` y `5`, comando
  `tf 4h|1d` y `default_timeframe` en la config.
- Igual que 5m y 1h: histórico por REST (300 velas: 50 días en 4h y 300 días en
  1d) y vela actual con los trades en vivo. Los días van en UTC, como el resto de
  la terminal.
- El eje X muestra `día hora` en 1h y 4h, y `mes-día` en 1d.
**Toca:** RF-03 (requirements v0.6), design.md §4 y §9, ayuda (RF-15).

## D-24 · 2026-10-21 · Velas a paso fijo de columnas (fix de T-13)
**Contexto:** el usuario vio huecos verticales sueltos entre velas. `ChartPanel`
pasaba 70 velas a plotext, que las reparte en el ancho del área de dibujo
(unas 85 columnas en media pantalla). Si el ancho no es múltiplo del número de
velas, algunas columnas quedan vacías y otras velas salen pegadas.
**Decisión:**
- Se dibuja al pintar, cuando ya se conoce el ancho. El área de dibujo es el
  ancho menos los 2 bordes y las etiquetas del eje Y, que ahora pone la terminal
  con el mismo ancho todas (antes las ponía plotext).
- `candle_layout(n, columnas)`: se dibujan como mucho tantas velas como columnas,
  cada una con un paso entero e igual (1 a 4 columnas), y la última pegada al
  borde derecho. `xlim` se fija para que la vela i caiga en la columna exacta.
- Ya no hay tope fijo de 70 velas: caben más en pantallas más anchas.
**Toca:** RF-03 (sin cambio de requisito), `ui/panels.py`.

## D-25 · 2026-10-21 · Robustez para producción (requirements v0.7)
**Contexto:** revisión general de la app buscando malas prácticas antes de
producción, aparte de los CA pendientes (T-20). Base sana (Decimal de punta a
punta, `Secret`/`Redactor`, RiskCheck que falla cerrado, confirmación explícita),
pero con huecos en los bordes:
- `OrderService._send` da por fallido cualquier `VenueError` o `httpx.HTTPError`,
  incluido un timeout o una respuesta ilegible *después* del POST: la orden puede
  estar viva. Neoxa ni siquiera recibe el `client_id` (`order_body`).
- `_just_sent` se rellena tras el ack: dos confirmaciones seguidas se validan con
  los mismos saldos y pueden pasar `max_venue_exposure`, que el exchange no controla.
- Los workers de Textual salen al primer error y `MarketHub`/`AccountService`
  usan `gather`: un bug o un `OSError` del journal cierran la app sin dejar rastro.
- Salir con un envío en vuelo lo cancela a medias (`order_sent` sin ack ni error).
- NonKYC pone a `book.ts` la hora del servidor y RiskCheck la compara con la local.
- Nada impide dos instancias; `.env` y el journal se crean `664`; no hay log de
  diagnóstico; las rutas dependen del cwd; no hay CI.
**Decisión:** el usuario eligió como alcance los siete bloqueantes, más permisos,
log de diagnóstico, rutas y CI. Quedan como RNF-12 a RNF-21 (v0.7) y tareas T-25 a
T-33 (M6). Quedan fuera por ahora, sin descartarlos: tratar el 429 (`Retry-After`),
`fsync` del journal, confirmar al arrancar en live, tope de reintentos de
`fetch_final` en Neoxa, poda del estado de la sesión, `assert` en tiempo de
ejecución, type checker, el test inestable de 300 ms, topes de versión en las
dependencias, aviso inmediato de pierna fallida, y README/LICENSE/runbook.
PA-09 (id de cliente en Neoxa) se abre con un supuesto por defecto.
**Toca:** RNF-12 a RNF-21, PA-09, M6 en tasks.md.

## D-26 · 2026-10-21 · Envíos inciertos y reconciliación (T-25, RNF-12)
**Contexto:** `OrderService._send` daba por fallido cualquier error, también un
timeout o una respuesta ilegible después del POST, con la orden posiblemente viva.
**Decisión:**
- Incierto = la petición pudo llegar y no se sabe qué hizo el venue: fallo de red
  después de conectar, HTTP 5xx, 2xx que no es JSON, o respuesta que no se puede
  leer como orden. Fallo claro = no se pudo conectar (`ConnectError`,
  `ConnectTimeout`, `PoolTimeout`), 4xx o error explícito del venue. El adaptador
  lo decide (`StreamingVenue.sending`) y lanza `UncertainError`.
- Se reconcilia por sondeo, no con los eventos de la cuenta: una orden que se
  llena entera al instante nunca aparece como activa, y el `OrderTracker` solo
  sigue las que vio activas. NonKYC: activas, y para los trades propios recientes
  de órdenes desconocidas, `/getorder/{id}` (trae `userProvidedId`). Neoxa:
  activas e historial (100). Sin endpoints nuevos.
- Supuesto de PA-09 (Neoxa sin client_id): lado, precio y cantidad iguales a
  ±60 s; a ±60 s y no solo "después" porque las horas son del exchange y aún no
  se corrige el desfase (T-29). Se excluyen las órdenes ya conocidas al enviar y
  las que el venue confirmó a la terminal en la sesión: si no, otra orden igual
  enviada justo después dejaría la incierta como ambigua.
- La UI lo avisa en rojo, la barra de estado cuenta las inciertas (`⚠ N
  inciertas`, en magenta antes del modo) y la pierna se ve `INCIERTA`.
- Ninguna candidata en 60 s → no existe (`order_error`, `reconciled`). Una orden
  creada y cancelada fuera de la terminal sin trades en NonKYC no se encontraría:
  caso aceptado, el journal lo dice.
- Las inciertas cuentan en RiskCheck desde T-26 (D-27).
**Toca:** RNF-12, PA-09, design.md §4, §6.2, §6.4, §7.

## D-27 · 2026-10-21 · Envíos atómicos por venue (T-26, RNF-13)
**Contexto:** `confirm` revalidaba y enviaba sin exclusión: con un envío en vuelo,
otra orden del mismo venue se validaba sin él (no estaba aún en `_just_sent`) y
podía, por ejemplo, cruzarse con él (`self_cross`).
**Decisión:**
- Un `asyncio.Lock` por venue en `OrderService` cubre revalidar → enviar → apuntar
  la orden (`_just_sent` con ack, `uncertain` si es incierta). `prepare` no lo
  toma: el diálogo no espera a la red. La orden doble toma los dos locks en orden
  alfabético de venue, para que dos dobles cruzadas no se bloqueen.
- Las órdenes inciertas cuentan como abiertas al validar (una pseudo-orden
  `?{client_id}`), salvo que la foto ya traiga una orden con ese `client_id`. En
  Neoxa, sin client_id (PA-09), puede contar dos veces hasta reconciliarse (≤ 60
  s): es el lado conservador.
- El test de T-26 usa `self_cross` y no `venue_exposure`: con D-07 la exposición
  sale de los saldos totales y del mid, no de las órdenes abiertas, así que dos
  órdenes a la vez no la pasan más que dos seguidas. El saldo disponible lo sigue
  imponiendo el venue (con la caché de RNF-11 puede ir hasta 10 s por detrás).
**Toca:** RNF-13, design.md §6.2.

## D-28 · 2026-10-21 · Fallos aislados (T-27, RNF-14)
**Contexto:** `MarketHub.run` y `AccountService.run` usaban `asyncio.gather` y los
workers de Textual cerraban la app ante cualquier excepción: un bug en un venue
(el `_Crash` que relanza `market_stream`) o un `OSError` del journal tras enviar
cerraban la terminal sin dejar rastro.
**Decisión:**
- Un registro común, `Faults`, con el journal. Cada fallo: `error` en el journal
  con traceback (el `Journal` lo redacta), aviso en rojo y contador `✖ N fallos`
  en la barra, que no se borra en la sesión: un bug pide mirar el journal.
- Cada bucle por venue (mercado; canal privado y saldos) corre en `supervised`:
  reintento con backoff exponencial de `retry_s` a 60 s, que vuelve a empezar si
  la tarea aguantó más de 60 s. Los errores de red siguen tratándose como antes.
- Journal: sin `order_sent` no se envía, porque no habría rastro de la orden
  (`Failed` con `local`, y la UI no dice "error del venue"). Tras enviar, un
  fallo de escritura no deshace nada: la orden cuenta y se avisa. Cancelar se
  hace aunque falle el journal: reduce riesgo.
- UI: workers con `exit_on_error=False` y los handlers con I/O envueltos en
  `_guarded`. Los de dibujo no: un bug ahí se vería en los tests.
- El aviso inmediato de una pierna fallida y la salida segura quedan para D-25 y
  T-28.
**Toca:** RNF-14, design.md §6.7.

## D-29 · 2026-10-21 · Salida segura (T-28, RNF-15)
**Contexto:** al salir, Textual cancela los workers y luego la app; un envío o
una cancelación cortados a medias dejaban `order_sent` o `cancel_sent` sin
respuesta en el journal, y el journal se cerraba sin esperar.
**Decisión:**
- Envíos y cancelaciones en tareas propias con `asyncio.shield`; `drain()` en
  `on_unmount` antes de cerrar el journal. No hay tope de espera: lo acota el
  timeout de httpx de cada petición.
- Si se cancela un `confirm` con el envío en vuelo, su lock de venue (D-27) se
  suelta antes de que acabe el envío. Solo pasa al salir, así que se acepta.
- `q` en live con órdenes abiertas pide confirmación con el número; Enter/Esc/n
  no salen. Salir no cancela nada: el diálogo lo dice. En dry-run las órdenes
  son simuladas y se sale directo.
- Con el envío en curso dentro del callback del diálogo, el `q` no se atiende
  hasta que acaba; el caso que de verdad se perdía era una tarea cancelada (un
  worker), que es el que prueba el test.
**Toca:** RNF-15, design.md §6.8.

## D-30 · 2026-10-22 · Reloj local y desfase con los venues (T-29, RNF-16)
**Contexto:** NonKYC pone en el book la hora del servidor y RiskCheck la
comparaba con la local: con el reloj desfasado más de `stale_after_s` se
rechazaba todo, y con desfase al otro lado un book viejo pasaba por fresco.
**Decisión:**
- `L2Book.received_at`: hora local a la que el hub aplicó el último mensaje. La
  frescura (`stale_data`) usa esa; `ts` queda como hora del exchange.
- El desfase se mide en `StreamingVenue.timed` con la cabecera `Date` de cada
  respuesta REST (venue − local, con la mitad del segundo y el punto medio de la
  petición: error ≈ ±0,5 s + RTT/2). No hace falta una petición aparte: la
  primera es la carga del mercado. Se actualiza con cada respuesta.
- `risk.max_clock_skew_s` (obligatoria; 5 s en el ejemplo, ya con margen sobre
  el error de la medida). En live, `OrderService` rechaza como `clock_skew` las
  órdenes a un venue desfasado o sin desfase medido (ante la duda, no se envía).
  En dry-run y solo lectura no bloquea. La app avisa en rojo una vez por venue
  al cargar su mercado, en todos los modos.
- Comprobado el 2026-10-22 contra los dos venues: los dos mandan `Date` y el
  desfase de esta máquina sale en ±0,6 s.
- El nonce firmado de NonKYC sigue usando la hora local; con el desfase acotado
  así, basta.
**Toca:** RNF-16, design.md §6.3, config.example.toml.

## D-31 · 2026-10-21 · Fase 2: bot de arbitraje taker (requirements v0.8)
**Contexto:** el usuario pidió un bot de arbitraje automático que tome las dos
puntas (taker). §Fuera de alcance lo excluía ("cualquier bot") junto con las
órdenes market, y exige una nueva versión de la spec antes de construir.
**Decisión** (elegida por el usuario entre opciones):
- Fase 2 en este repo, en la misma carpeta de spec (v0.8, hito M7), en lugar de
  `specs/fase2/` o un proyecto aparte: el bot reutiliza adaptadores, RiskCheck y
  la orden doble, y la terminal es su panel de control.
- Patas taker como **límite agresiva** con margen de slippage máximo, y
  cancelación del resto a `fill_timeout_ms`. Las market siguen fuera: con books
  delgados, una market sin tope de precio es el peor riesgo. Ninguno de los dos
  adaptadores tiene IOC/FOK, así que se emula.
- **Pierna colgada → parar y avisar** (`halted`): cancelar lo abierto y dejar
  el desbalance para resolverlo a mano. Sin cobertura ni reintentos.
- **Empezar tras cerrar M6** (T-30 a T-33): el bot depende de esa robustez.
- Una sola doble del bot a la vez; neta calculada con los precios límite
  (pesimista), no con el mejor bid/ask.
- PA-05 (conector de Hummingbot) se cierra: no se usa.
**Toca:** RF-18 a RF-23, RNF-22, CA-08 a CA-10, §Fuera de alcance, design.md
§6.9, M7 en tasks.md.

## D-32 · 2026-10-21 · Rutas XDG, permisos e instancia única (T-30)
**Contexto:** config, `.env` y `logs/` se buscaban en el cwd: arrancar desde otro
directorio usaba otra config o abría otro journal. El `.env` local del usuario y
el journal se creaban `664`, y nada impedía dos terminales sobre el mismo journal.
**Decisión:**
- `paths.py` fuera de `core/` (hace I/O). Por defecto XDG; una variable XDG
  relativa se ignora, como pide la especificación. No hay respaldo al cwd: ese
  era el problema. Quien quiera la disposición anterior arranca con
  `--config config.toml --env .env --data-dir logs`.
- El `.env` se rechaza con **cualquier** permiso de grupo u otros (`& 0o077`), no
  solo de lectura: uno escribible por otros también deja cambiar las keys.
- El lock es `flock` (lo suelta el kernel si el proceso muere: sin locks
  huérfanos) y se toma también en solo lectura, para no tener que explicar
  cuándo sí y cuándo no. El fichero no se borra al salir.
- El journal fuerza `0600`/`0700` con `fchmod`/`chmod` aunque ya existieran.
- Los avisos de arranque van en `Settings.warnings` y la app los dice en rojo.
- `test_spec_format` no se actualizó con la spec v0.8 (D-31) y fallaba; se
  arregló aquí.
**Toca:** RNF-17, RNF-18, RNF-20, design.md §7 y §8.

## D-33 · 2026-10-21 · Log de diagnóstico (T-31, RNF-19)
**Contexto:** los errores de red se veían solo en pantalla y en el journal (y el
journal no existe en solo lectura); una caída del WS a las 3 de la mañana no
dejaba rastro de su causa ni de los reintentos.
**Decisión:**
- `diaglog.py` fuera de `core/` (hace I/O). `RotatingFileHandler` propio que
  abre cada fichero `0600`, también los que crea al rotar.
- La redacción va en el **formatter**, no en un `logging.Filter`: el filtro ve el
  mensaje antes de formatear y no ve el traceback; redactar la línea final cubre
  mensaje, argumentos y excepción.
- `NullHandler` en `xbt_terminal/__init__.py` y `propagate = False` al
  instalar: sin ellos, `logging.lastResort` escribiría avisos en stderr encima de
  la TUI.
- `[log]` es opcional (por defecto `info`) para no romper los `config.toml`
  existentes; un nivel desconocido sí es error.
- Los errores de la cuenta se apuntan una vez por racha, con la misma regla que
  el journal; ahora también sin journal.
- Se toma en todos los modos, también en solo lectura.
**Toca:** RNF-19, design.md §8, config.example.toml.

## D-34 · 2026-10-21 · El bot antes que la CI
**Contexto:** D-31 dejaba M7 para después de T-33. Con T-30 y T-31 hechas, el
usuario pidió hacer el bot primero.
**Decisión:** M7 (T-34 a T-37) va ahora; T-32 (CI) y T-33 (comprobación en real
de M6) quedan pendientes y T-38 (bot en real) sigue necesitando al usuario. Lo
que más importaba de M6 para un bot (inciertas, locks por venue, fallos
aislados, salida segura, desfase de reloj, instancia única y log) ya está.
**Toca:** tasks.md M7.

## D-35 · 2026-10-21 · Decisión del bot (T-35)
**Decisión:**
- El tope diario (`max_daily_btc`) cuenta el nocional de compra **enviado** al
  disparar, no el llenado: es una cota superior que no depende de que lleguen los
  fills, y en dry-run (que no simula fills) también limita. RF-22 dice
  "comprado"; se lee como "enviado a comprar".
- La cantidad respeta los lotes de los dos venues (se trunca a uno y luego al
  otro); si no encajan entre sí, `Skip("lot_mismatch")`.
- Con las dos rutas sin disparo se devuelve el motivo de la de mayor neta (lo
  más útil para el operador); si ninguna tiene precios, `blocked` o `no_quotes`.
- Un rechazo de RiskCheck antes de enviar (`rejected`) vuelve a `armed`: no hay
  nada abierto. Cualquier problema después de enviar es `halted` (`finished`
  con error), como pide RF-21.
- `halted` por tope diario también es manual: se rearma con `bot on`.
**Toca:** RF-20, RF-22, design.md §6.9.

## D-36 · 2026-10-21 · Servicio del bot (T-36)
**Decisión:**
- `services/bot.py` no envía nada por su cuenta: `prepare_double` +
  `confirm_double`, y `OrderService.cancel_double` (nuevo) para el resto. Así
  hereda RiskCheck, locks, inciertas, salida segura y journal.
- Una doble manual sin terminar también bloquea el bot (`double_open`): una
  doble a la vez, sea de quien sea; igual con cualquier orden incierta.
- Tras cancelar, espera hasta 15 s (`settle_s`) a que las piernas cierren,
  porque Neoxa avisa por polling. Una cancelación que falla no para el bot por
  sí sola (la orden pudo llenarse justo antes): manda cómo cierran las piernas.
  Sin cerrar a los 15 s → `halted` (`unsettled`).
- Plazos con el reloj de asyncio; estados y contadores con el reloj de la app.
- Contadores rehechos del `journal-<hoy>.jsonl` que nombra el reloj del
  journal, con `at` de cada `bot_fire`; el bot arranca siempre `off`.
- `disarm` no corta una doble en vuelo: sigue hasta cerrar y luego queda `off`.
- `OrderService.fresh_at` y `limits` pasan a públicos para el bot.
**Toca:** RF-19 a RF-23, design.md §6.9.

## D-37 · 2026-10-21 · El bot en la pantalla (T-37)
**Contexto:** la barra de estado ya ocupa 159 de 160 columnas en el peor caso
(dos venues caídos, fallos, inciertas y el banner live).
**Decisión:**
- El estado del bot sustituye el texto del banner de modo mientras no está
  apagado: `LIVE · BOT armado | dispara | enfría | ✖ PARADO`. El banner sigue
  siendo rojo en live; `PARADO` va en rojo también en dry-run. Etiquetas cortas
  para caber en el peor caso.
- El motivo de la parada no cabe en la barra: sale como aviso rojo al pararse y
  en `bot status`. T-37 pedía "la barra muestra halted y su motivo"; se queda en
  la barra el estado y el motivo en el aviso.
- Tecla `p` para parar al momento, sin confirmación (parar reduce riesgo).
- Sin `[bot]` en la config o en solo lectura no hay `BotService`; `bot on` lo dice.
**Toca:** RF-18, RF-23, design.md §6.9.

## D-38 · 2026-10-21 · Descuento de fee en Neoxa (requirements v0.9, T-39)
**Contexto:** el usuario paga las fees de Neoxa con su token, con un 50 % de
descuento. La terminal toma las fees de Neoxa de `/exchange/pairs` (0.1 % maker,
0.2 % taker) y esa API no sabe del descuento.
**Decisión** (elegida por el usuario en el formulario de configuración):
- Clave opcional `fee_discount_pct` en `[venues.<id>]` (0 a 100, por defecto 0).
- Solo la usa el panel cruzado, que lo dice en su pie ("Neoxa con fee -50 %").
- El bot, RiskCheck (`insufficient_balance`) y el tamaño por % siguen con la fee
  completa: si se acaba el token, Neoxa cobra la fee entera y el bot no puede
  haber disparado contando con un descuento que ya no hay. Es la cota pesimista.
- No se verificó contra la API que el descuento aplique a la fee taker; lo dice
  el usuario.
**Toca:** RF-24, RF-04, design.md §6.6, config.example.toml.

## D-39 · 2026-10-21 · ctrl+b para el bot desde cualquier sitio
**Contexto:** el usuario no podía parar el bot con `p`. Tras `bot on` + `y`, el
foco vuelve a la línea de comandos y la `p` se escribía en ella (pasa con todas
las teclas de una letra; la ayuda dice que Esc suelta la línea). El test de T-37
pulsaba `p` sin foco en la línea y no lo vio.
**Decisión:** `ctrl+b` como binding con `priority=True` de la app: llega aunque
la línea de comandos o un diálogo tengan el foco. Ni `Input` ni Textual la usan
(`ctrl+p` es la paleta de comandos). `p` se queda para cuando la línea no tiene
el foco. Mensaje de armado, ayuda y README lo dicen.
**Toca:** RF-18, design.md §6.9.

## D-40 · 2026-10-22 · Apagar el bot mientras dispara
**Contexto:** visto en real el 2026-10-22 a las 03:34 UTC (dry-run): `bot off`
con una doble en vuelo; al cerrarla, el bot pasó a `cooldown` y a los
`cooldown_s` se habría rearmado solo. D-36 decía que `disarm` deja seguir la
doble y luego queda `off`, pero `_fire` aplicaba `finished` encima del `off`.
**Decisión:** `BotService._end`: si el bot está `off` al acabar el disparo, se
queda `off`; si el disparo acabó mal (`halted`), pasa a `halted` igualmente para
que el aviso de pierna colgada, incierta o rechazada no se pierda. El disparo
cuenta para los topes en los dos casos.
**Toca:** RF-18, RF-21, design.md §6.9.

## D-41 · 2026-10-22 · Seguir las órdenes desde que se envían
**Contexto:** visto en real el 2026-10-22 a las 04:07 UTC (`d-2185c6`). La venta
del bot en NonKYC se llenó en ~1 s, antes del siguiente refresco del canal
privado (NonKYC respondía lento: errores de saldos y caídas del WS). `OrderTracker`
solo seguía órdenes vistas activas en algún refresco, así que nunca la dio por
desaparecida ni consultó `getorder`: llegó el fill pero no el `Order` lleno. La
pierna quedó "abierta" para la terminal, la cancelación dio `20002 Not found` y
el bot se paró (`unsettled`) con un `hanging_leg` falso. Las dos piernas estaban
llenas (0.05 BTCB2, +0.0000204 BTC).
**Decisión:** `OrderTracker.remember` (lo llama `place_limit` en los dos venues)
apunta la orden enviada si no está terminada. En el siguiente refresco, si no
está entre las activas, cuenta como desaparecida y se consulta hasta saber cómo
acabó; si está, se avisa como orden nueva (la cuenta necesita ese primer `Order`
para el panel de órdenes, el `client_id` de los fills y `_just_sent`). Vale
también para las órdenes manuales.
**Toca:** RF-07, RF-09, design.md §4.

## D-42 · 2026-10-22 · Fees de NonKYC confirmadas (cierra PA-04)
**Contexto:** NonKYC no expone las fees por API; se trabajaba con el supuesto
de PA-04 (0.2 % / 0.2 % en la config). Los fills reales del journal del
2026-10-22 lo confirman para el taker: en todos, `fee` = qty × precio × 0.002
exacto, en BTC (las ventas). Ej.: 0.0149 × 0.00540926 → 0.000000161195948 BTC
(04:28 UTC). Todos esos fills son taker (ventas del bot que se llenan en ≤ 3 s a
un precio mejor que su límite, contra bids que ya estaban en el libro); ninguna
orden maker de NonKYC se ha llenado todavía.
**Decisión:** PA-04 resuelta para la taker, que es la que usan el bot (RF-20) y
el panel cruzado: 0.2 % confirmado. La maker sigue en 0.2 % como supuesto, sin
verificar; si algún día se llena una orden maker, comprobar su `fee`. Las dos
siguen siendo configurables por si NonKYC las cambia.
**Toca:** PA-04, RF-10, RF-20, design.md §4.

## D-43 · 2026-10-22 · Bot en real, parte 1 (T-38); CA-02 cumplido
**Contexto:** el usuario armó el bot en `--live` el 2026-10-22 con `max_qty`
0.05 BTCB2 (subido desde 0.01) y `max_daily_btc` 0.01. El journal tiene 5
disparos reales, todos compra en Neoxa y venta en NonKYC, con las dos piernas
llenas:

| Doble | UTC | Cantidad | Resultado |
| --- | --- | --- | --- |
| `d-aea6df` | 03:36 | 0.0037 | llena; cooldown |
| `d-e24360` | 03:39 | 0.0100 | llena; cooldown |
| `d-e0377f` | 03:41 | 0.0100 | llena; cooldown |
| `d-2185c6` | 04:07 | 0.0500 | llena, pero `halted` por la falsa pierna colgada (D-41) |
| `d-a11e65` | 04:28 | 0.0167 | llena (Neoxa contra el AMM, fee 0); cooldown |

Unos 0.00046 BTC de compra en el día, lejos del tope de 0.01: **no se ha visto
la parada por tope** que pide CA-10. Por eso T-38 queda `doing`.

Del mismo journal, para T-20:
- **CA-02 cumplido:** límite colocada y cancelada en real en cada venue, con
  `order_sent`, `order_ack`, `cancel_sent` y `cancel_ack` en el journal (Neoxa
  `ORD-MVI9COME-78074A2E`, 2026-10-21 15:19 → 15:32; NonKYC
  `6ad8e12b0c23ade2e155e876`, 15:58 → 19:17).
- **CA-03 a medias:** las dobles se ejecutaron en real, pero el journal no guarda
  los saldos, así que no prueba que se vean en < 10 s. Queda pendiente.
- En live, el journal tiene 66 `error` de NonKYC (lentitud y caídas del canal
  privado, ver D-41). Tenerlo en cuenta en CA-01 y T-33.
**Decisión:** CA-02 marcado en T-20. Para cerrar CA-10, bajar `max_daily_btc`
por debajo de lo que lleva el día (o dejar que llegue) y ver la parada en real.
**Toca:** CA-02, CA-03, CA-10, T-20, T-38.

## D-44 · 2026-10-22 · Resultado de las dobles (requirements v0.10, T-40)
**Contexto:** el usuario quiere ver en otra ventana, con gráfica, si el bot gana
o pierde. "Cálculo de PnL" estaba fuera de alcance. El journal ya tiene lo
necesario: `order_sent` lleva el `double` de cada pierna, `fill` lleva
`client_id`, precio, cantidad, `fee` y `fee_asset`, y `bot_fire` la neta esperada.
**Decisión** (el usuario eligió incluir dobles del bot y manuales; el resto son
supuestos propuestos y no discutidos):
- Se abre una excepción acotada al fuera de alcance: resultado **realizado por
  doble**, no PnL de cartera. No se valora inventario ni descuadres, ni se usa
  USD (RNF-10).
- Cantidad casada = mín(llenado compra, llenado venta), a precio medio de cada
  pierna; resultado = venta − compra − fees de todos los fills de la doble.
- Fees reales del fill. En Neoxa ya traen (o no) el descuento del token, así
  que no se usa `fee_discount_pct`. Fee en BTCB2 → × precio del fill.
- Colgada (`hanging_leg`) o una pierna sin fills → sin resultado, no suma.
- Solo journals en live; el dry-run no tiene fills reales.
- Se calcula en `core/` (función pura sobre eventos del journal); la lectura de
  ficheros va en `services/`. Se recalcula al abrir la ventana y con cada `fill`
  nuevo mientras está abierta.
- Filtro por día UTC, 7 días o todo (por defecto el día).
**Toca:** RF-25, CA-11, requirements §Fuera de alcance, design.md §6.10, T-40.

## D-45 · 2026-10-22 · Fills de Neoxa sin orden (T-40)
**Contexto:** al diseñar T-40 se vio que los fills de Neoxa llegan al journal
sin `order_id` ni `client_id`: `/exchange/trades/my` no dice de qué orden es
cada trade (`parse_fill` pone `order_id=None`). Así no se pueden atar a su doble
por id. Los de NonKYC sí traen `client_id` u `order_id`.
**Decisión:** asignarlos por compatibilidad (design.md §6.10): misma venue y
lado, orden enviada antes del fill, precio dentro del límite y cantidad
pendiente suficiente; con varias candidatas, la única enviada en el último
minuto (las tomas del bot y de las dobles se llenan en segundos); si no, sin
asignar y contado en el resumen. No cambia el journal ni los adaptadores.
**Comprobado** con el journal real del 2026-10-22: 25 dobles en live, 24
cerradas (+10 517 sats) y 1 colgada (`d-73fbd4`); un solo fill sin asignar
(Neoxa, 06:16 UTC, sin ninguna orden de la terminal cerca: no es de una doble).
**Riesgo:** una orden manual de Neoxa al mismo lado y límite compatible enviada
en el mismo minuto que una pierna haría ambiguo el fill; queda sin asignar,
nunca se asigna mal en silencio.
**Toca:** RF-25, design.md §6.10, T-40.

## D-46 · 2026-10-22 · Ratio de inventario por venue (requirements v0.11, T-41)
**Contexto:** el objetivo del usuario es acumular BTC y BTCB2 a largo plazo,
asumiendo la volatilidad de BTCB2 sobre una parte acotada de la cartera. El
2026-10-22 el bot dejó NonKYC casi sin BTCB2 (~1 % del valor, Neoxa ~29 %) y
cerró la ruta nx→nk. El total estaba en ~22 %: el problema es el reparto.
**Decisión** (elegida por el usuario: 25 %, ±10, no disparar):
- `[inventory] target_base_pct = "25"`, `band_pct = "10"`, opcional. Banda
  [15 %, 35 %] por valor en BTC al mid de cada venue.
- El filtro es por venue y solo veta: una ruta no dispara si deja algún venue
  fuera de la banda y más lejos del 25 % que antes. Si ya estaba fuera y la
  doble lo acerca, se permite: así un movimiento de precio que saca a los dos
  venues de la banda no bloquea al bot del todo.
- No se reduce la cantidad para caber en la banda: o la doble entera respeta la
  regla, o no se dispara (más simple; el usuario eligió no disparar).
- El bot nunca compra para alcanzar el ratio. Como compra y vende la misma
  cantidad, el BTCB2 total no cambia con él; el % total lo mueven el precio y la
  ganancia en BTC, y se corrige a mano. Reequilibrar al 25 % es lo que acumula
  los dos activos.
- Mid para valorar: el del book del venue; si no hay (book cruzado o bloqueado),
  el precio límite de esa pierna.
- El panel de balances muestra el % por venue y total (RF-27).
**Toca:** RF-26, RF-27, CA-12, requirements §Fuera de alcance, design.md §6.9,
§6.5, config.example.toml, T-41.

## D-47 · 2026-10-22 · Comisiones de retiro de los venues y coste de reequilibrar
> Corregida por D-53: el BTC de NonKYC en red principal (`BTC-MAIN`) cuesta
> 0.00005, no 0.0008 (ese era el ticker padre), y "nunca retirar BTC de NonKYC"
> ya no vale. BTCB2 de NonKYC subió a 0.0027. Nota de D-54.

**Contexto:** el bot, sin el filtro de D-46 (el proceso viejo, antes del
reinicio de las 18:07 UTC), hizo 7 dobles nk→nx seguidas el 2026-10-22 y dejó
NonKYC al ~100 % en BTCB2 (0.3218 BTCB2, 0.00000085 BTC) y Neoxa al ~0.6 %
(0.0050 BTC, 0.0060 BTCB2). El total quedó en ~25 %: solo falla el reparto.
Neoxa cotizó casi todo el día ~4-6 % por encima de NonKYC, así que el flujo del
bot lleva siempre el BTCB2 a NonKYC y el BTC a Neoxa. Para decidir si
reequilibrar con transferencias se consultaron las comisiones de retiro. En la
web casi no hay datos; las fuentes buenas son las APIs de los venues.
**Datos (2026-10-22):**

| Venue | Activo | Comisión de retiro | sats | Mínimo |
| --- | --- | --- | --- | --- |
| NonKYC | BTC | 0.0008 BTC | 80 000 | — |
| NonKYC | BTCB2 | 0.0024 BTCB2 | ~1 250 | — |
| Neoxa | BTC | 0.0001 BTC | 10 000 | 0.0005 BTC |
| Neoxa | BTCB2 | 0.0001 BTCB2 | ~50 | 0.01 BTCB2 |

NonKYC: `GET /api/v2/asset/getbyticker/{BTC|BTCB2}` (público, `withdrawFee`);
para BTCB2 hay además `withdrawFeeAlt` 0.085 en otro activo (solo id interno,
sin identificar). Neoxa: `GET /api/withdrawals/fees` (privado, con las keys de
lectura); `/api/v1/cmc/assets` (público) da los mínimos y las fees de trading.
Trading: NonKYC 0.2 % / 0.2 % (D-42), Neoxa 0.1 % / 0.2 %.
**Decisión / criterio:**
- **Nunca retirar BTC de NonKYC** (80 000 sats). Las dos transferencias que pide
  el flujo del bot son las baratas: BTCB2 NonKYC → Neoxa (~1 250 sats) y BTC
  Neoxa → NonKYC (10 000 sats). Un reequilibrio completo ≈ 11 000 sats.
- La comisión es fija: reequilibrar solo de vez en cuando y en cantidad. Con
  una prima del ~4 % y fees taker, cada reequilibrio necesita ~0.6 BTCB2 movidos
  por arbitraje para pagarse. Con el capital actual (~0.33 BTCB2 en total) sale
  mejor esperar a que el cruce se dé la vuelta (nx→nk), que reequilibra gratis
  con el bot armado (D-46 lo permite).
- Las comisiones cambian: volver a consultarlas en las APIs antes de transferir.
- Queda pendiente (aplazado por el usuario) el reequilibrio automático: neta
  mínima reducida para dobles que acercan los dos venues al objetivo.
**Toca:** design.md §4, D-46, HANDOFF.

## D-48 · 2026-10-22 · BTCB2 se muestra como XBT
**Contexto:** el usuario pidió cambiar visualmente el nombre del par BTCB2 a XBT.
**Decisión:** solo presentación. `core/models.py` tiene `ASSET_LABELS`
(`BTCB2 → XBT`), `BASE_LABEL` y `asset_label()`; los títulos, la ayuda, los
saldos, los resúmenes de órdenes y los mensajes del bot dicen `XBT/BTC` y `XBT`.
No cambian el símbolo de la config (`BTCB2/BTC`, RNF-10), las claves de saldo,
las rutas de los adapters, el journal ni los nombres internos (`btcb2`,
`BTCB2App`). Ojo: en otros sitios XBT es el ticker de Bitcoin.
**Toca:** UI y mensajes; tests de texto actualizados.

## D-49 · 2026-10-22 · Book centrado con barras de profundidad
**Contexto:** el usuario pidió centrar los order books y poner a sus costados la
profundidad del mercado con barras. Con su pantalla cada book tiene ~85
columnas de contenido y la escalera 77: quedan ~4 por lado. El usuario eligió
mantener todas las columnas y barras finas antes que quitar las de acumulado.
**Decisión:**
- La escalera va centrada en el panel; a la izquierda, barra verde por nivel de
  compra que crece hacia fuera; a la derecha, roja para las ventas.
- Longitud = acumulado del nivel / acumulado más profundo de los dos lados
  mostrados (los dos lados en la misma escala). Medias celdas (`▐`, `▌`) para
  más resolución; un nivel con algo ocupa al menos media celda.
- Ancho de barra = lo que sobra a cada lado menos 1 columna de hueco; con el
  panel justo (160 columnas de terminal) no hay barras. Se recalcula al
  redimensionar.
- Clic en una barra = cantidad acumulada del nivel, igual que la columna de
  acumulado (RF-16). El resumen bid/ask/spread también va centrado.
**Toca:** `ui/panels.py` (`BookPanel`, `depth_bar`), design.md §9,
`tests/ui/test_book_depth.py`.

## D-50 · 2026-10-22 · Direcciones de depósito en la terminal (requirements v0.12, T-42)
**Contexto:** el usuario preguntó si la terminal tiene acceso a las direcciones
de depósito y pidió verlas en una ventana aparte, como la de resultados. Los
depósitos estaban fuera de alcance; el usuario aprobó abrir la v0.12 solo para
**verlas** (sin historial, sin retiros ni transferencias).
**Datos (consultados con las keys de lectura, 2026-10-22):** NonKYC
`GET /getdepositaddress/{ticker}`; `BTC` responde `20010 Must use more specific
ticker` y el BTC on-chain es `BTC-MAIN` (hay también `BTC-BEP20` y `BTC-SOL`,
que no se usan). Neoxa `GET /deposits/addresses` devuelve todas las cadenas
de la cuenta (`bitcoin`, `btcb2`…) y `closedChains`.
**Decisión:**
- RF-28 y CA-13; design.md §4 y §6.11; tarea T-42 (M10).
- Se pide al venue en cada apertura: una dirección cacheada que el venue
  cambió sería peor que no tenerla.
- El ticker real del venue se muestra junto a "XBT" (D-48): el nombre mostrado
  no debe llevar a mandar BTC a la dirección de BTCB2 ni al revés.
- Cualquier respuesta dudosa (vacía, de otro ticker, cadena cerrada) es un
  error en su fila, nunca una dirección.
**Toca:** requirements v0.12, design.md, tasks.md, HANDOFF.

## D-51 · 2026-10-23 · Errores sin texto y el bot disparando sin red (T-43)
**Contexto:** el 2026-10-23 a las 01:23 UTC hubo un corte de red en los dos
venues (DNS sin resolver, WS sin conectar hasta las 01:26:32). Los balances
fallaron con `ConnectTimeout`, cuyo `str()` es `""`: la cuenta guardó
`error = ""` y `BotService._blocked` (`if acct.error:`) lo tomó como "sin
error". El bot disparó dos dobles (01:23:28 y 01:27:53) cuyas cuatro piernas
fallaron sin conectar y se paró las dos veces con "rechazada". Se comprobó en
los venues que ninguna de esas piernas existe; no hubo pérdida. Journal y
diag.log quedaron con `"error": ""`.
**Decisión:**
- Ningún error se guarda vacío: `describe(exc)` devuelve el tipo si no hay
  texto.
- Una pierna que no llega a conectar se apunta como "no enviada, sin conexión";
  el bot sigue parándose (RF-21 no cambia): con el bloqueo arreglado, en un
  corte así no llega a disparar.
- Las comprobaciones de error del bot usan `is not None`, no la verdad del
  texto.
**Toca:** T-43; `adapters/base.py`, `services/{account,market,orders,bot,
deposits}.py`, `adapters/{streaming,neoxa}.py`.

## D-52 · 2026-10-22 · Copiar con la herramienta del sistema y QR de la dirección (requirements v0.13, T-44)
**Contexto:** en la ventana de depósitos el usuario copió la dirección de
NonKYC y no llegó al portapapeles: `copy_to_clipboard` de Textual solo manda
OSC 52 y GNOME Terminal (VTE) lo ignora sin avisar. La app decía "copiada"
igualmente. No había forma de pagar a la dirección sin abrir el exchange.
**Decisión (pedida por el usuario):**
- Copiar manda OSC 52 y además prueba `wl-copy`, `xclip` o `xsel`. El mensaje
  nombra la herramienta que funcionó; si ninguna, dice que solo se mandó a la
  terminal y que se puede seleccionar con Shift + arrastrar.
- QR de la dirección seleccionada dentro de la ventana, con la dirección sola
  (sin `bitcoin:`: BTCB2 no es bitcoin y un URI podría llevar a una wallet a
  tratarla como tal).
- Dependencia nueva `segno` (Python puro, sin dependencias) para el QR; se
  dibuja en texto, sin imágenes.
- Copiar con el sistema se inyecta en la app; en tests no se usa.
**Toca:** requirements v0.13 (RF-28, CA-13), design.md §6.11, T-44;
`ui/clipboard.py`, `ui/qr.py`, `ui/app.py`, `__main__.py`, `pyproject.toml`.

## D-53 · 2026-10-23 · Plan de reequilibrio entre venues (requirements v0.14, T-45)
**Contexto:** con `[inventory]` (D-46) el bot deja de disparar cuando un venue
se va de la banda, pero el flujo habitual (Neoxa más cara) sigue llevando el
BTCB2 a NonKYC y el BTC a Neoxa. El usuario desactivó `[inventory]` para
reequilibrar a mano, lo reactivó y pidió una estrategia de rebalanceo. Se le
ofrecieron tres: dobles con neta mínima menor que acercan los dos venues al
objetivo, un plan de transferencias o las dos. **Eligió el plan de
transferencias.**
**Decisión:**
- Ventana que solo calcula (RF-29); la terminal no retira: las keys de Neoxa no
  pueden y las de NonKYC no deben (README).
- Una transferencia basta: una sola, de BTCB2 o de BTC, deja los dos venues en
  el mismo % (el del total), porque mover un activo baja el % del origen y sube
  el del destino. Se muestran las dos y se recomienda la viable más barata;
  hacer las dos (≈ 11 000 sats en D-47) no hace falta.
- El objetivo es igualar los venues, no llegar al 25 %: una transferencia no
  cambia el % total. Si el total está fuera de la banda, se avisa; corregirlo es
  comprar o vender a mano.
- ~~Supuesto: la comisión se descuenta de lo enviado (sale X, llega X − f).~~
  **Corregido el 2026-10-23:** la captura del formulario de retiro de Neoxa
  que mandó el usuario (XBT: Amount 0.2, "You will receive" 0.200000, "Total
  deducted" 0.200100) muestra que **Neoxa cobra la comisión aparte**: se pide
  X, llega X y salen X + f. Con el supuesto viejo el plan pedía 0.14108975 XBT
  en vez de 0.14002494. Ahora cada `WithdrawalFee` dice cómo la cobra su venue
  (`on_top`): Neoxa aparte; NonKYC descontada, que sigue siendo un **supuesto**
  hasta ver su formulario. La ventana muestra lo que hay que pedir, lo que sale
  y lo que llega. Con la comisión aparte y una diferencia menor que ella, no
  hay cantidad que iguale y se dice.
- Comisiones pedidas a los venues cada vez (cambian, D-47), con mínimo y si el
  retiro está abierto.
**Corrección a D-47:** `/asset/getbyticker/BTC` de NonKYC es el activo padre;
la red principal es el hijo `BTC-MAIN` (el mismo ticker que el depósito, D-50),
con `withdrawFee` **0.00005 BTC** (5 000 sats) y `minimumWithdraw` 0.00001.
"Nunca sacar BTC de NonKYC" ya no se sostiene: sacar BTC de NonKYC en la red
principal es más barato que de Neoxa (0.0001). BTCB2 de NonKYC subió a 0.0027.
Consultado el 2026-10-23.
**Toca:** requirements v0.14 (RF-29, CA-14, Alcance, Fuera de alcance),
design.md §4 y §6.12, T-45, D-47.

## D-54 · 2026-10-23 · Auditoría de la spec (requirements v0.15)
**Contexto:** tras 14 versiones de requirements y 53 decisiones, el usuario pidió
revisar el repo para corregir la spec y dejar escritas las inconsistencias. Se
cruzaron requirements, design, tasks, decisions, HANDOFF, README y CLAUDE.md
entre sí y con el código.
**Corregido solo en documentación (sin código):**
- CLAUDE.md decía "nada de bots ni PnL" (anterior a la v0.8 y la v0.10);
  `specs/README.md` decía que la fase 2 iría en `specs/fase2/` (D-31 la dejó en
  `specs/mvp/`).
- requirements v0.15: PA-06 seguía en USDT; PA-08 se cierra con D-15, que ya la
  cubría desde T-18; el "supuesto sin confirmar" de RF-29 (comisión de retiro de
  NonKYC) pasa a **PA-10**; los criterios de cierre nombran CA-11 a CA-14; RF-09
  y RNF-05 citan lo resuelto en D-10.
- design.md: la cabecera seguía en la v0.1. Se pone al día: el diagrama de capas
  con los módulos reales, el modelo de datos (`Fill`, `Level.amm`, `seq` opcional,
  `LegStatus`), los motivos reales de `Skip` (`stale_book` no existe: es
  `blocked`), los mínimos de `[bot]`, `settle_s` como constante, la nota de M7
  (D-34), `read_rows` en §6.10, los atajos y comandos de §9 (`r`, `w`, `e`, `p`,
  `ctrl+b`, `bot`, `results`, `deposit`, `rebalance`) y la aceptación en §10.
- decisions: notas de sustitución en D-07, D-14, D-18 y D-47.
- README: el estado decía "M4, sin órdenes reales" y el plan de reequilibrio
  "supone que la comisión se descuenta" (Neoxa la cobra aparte, D-53).
- HANDOFF: las cabeceras de estado se contradecían y faltaba PA-09 entre las
  abiertas.
- El orden de fechas de este fichero no es cronológico (D-30 antes que D-31,
  D-52 después de D-51). Como es append-only, no se reordena.
**Código que no cumple la spec (se deja para T-46, sin tocar ahora):**
- **RF-22, CA-10:** con el tope diario ya gastado al evaluar (después de `bot on`
  el mismo día o tras reiniciar, porque los contadores se rehacen del journal) o
  con menos de un lote de presupuesto, `decide` da `Skip("day_cap")` y el bot
  sigue `armed` (`core/bot.py`, `decide` y `_route`). RF-22 pide `halted`. Solo
  pasa a `halted` el disparo que agota el tope (`finished`).
- **Capas (CLAUDE.md, design §2):** `core/journal.py` escribe ficheros (`os.open`,
  `open`, `chmod`, `fchmod`) y `core/ratelimit.py` usa el loop y el reloj de
  asyncio; `ui/app.py` importa `adapters.streaming.now_ms`; `services/bot.py`
  importa `config.BotConfig`. Quedan como excepciones escritas en design §2.
- **RNF-01:** el stack real añade `textual-plotext`, `python-dotenv` y `segno`, y
  `rich` se importa directamente sin estar declarado en `pyproject.toml`.
- **RNF-21 / T-32:** no hay CI (no existe `.github/`). El usuario lo aplazó porque
  el repo no tiene remoto (HANDOFF), pero no constaba como decisión: queda aquí.
  T-32 sigue `todo`.
**Toca:** requirements v0.15 (PA-06, PA-08, PA-10, RF-09, RF-29, RNF-05,
§Criterios), design.md §2, §3, §6.9, §6.10, §8, §9, §10, D-07, D-14, D-18, D-47,
tasks.md (T-32, T-46), CLAUDE.md, specs/README.md, README.md, HANDOFF.

## D-55 · 2026-10-23 · Correcciones de la auditoría (T-46)
**Contexto:** D-54 dejó para T-46 lo que el código no cumplía de la spec.
**Decisión:**
- **RF-22:** `core/bot.capped` es la transición a `halted` por tope diario. La
  usan `finished` (un disparo agota el tope) y `BotService` cuando `decide` da
  `Skip("day_cap")`: tope ya gastado (tras rearmar o reiniciar el mismo día) o
  menos de un lote de presupuesto. Un presupuesto que no llega a un lote cuenta
  como tope alcanzado, aunque en otra ruta más barata cupiera: es el lado
  conservador. `hour_cap` sigue siendo una espera.
- **Capas:** `ui/app.py` ya no importa `adapters`: la app toma el reloj del hub
  (`MarketHub.clock`) si no se le pasa uno. `core/journal.py` se queda en `core/`
  como única excepción a "core no hace I/O" (elegido por el usuario: lo usan
  todas las capas y moverlo no cambia nada de lo que hace). `core/ratelimit.py`
  usa el loop de asyncio sin red y se acepta igual. Un test de importaciones
  (`tests/unit/test_layers.py`) vigila las reglas.
- **RNF-01:** `rich` se declara en `pyproject.toml` (se importaba sin
  declararlo). RNF-01 no cambia: nombra el stack, no la lista de dependencias.
- Los tests nuevos del tope no llevan CA-10: ese criterio es la prueba en real
  (T-38), y marcarlo haría que `spec_trace` lo diera por cubierto.
**Toca:** RF-22, RNF-01, design.md §2 y §6.9, CLAUDE.md, T-46.

## D-56 · 2026-10-23 · Reequilibrio real Neoxa → NonKYC: comisión aparte confirmada
**Contexto:** el usuario hizo el 2026-10-23 una transferencia de BTCB2 de Neoxa a
NonKYC para reequilibrar (RF-29) y pidió comprobar las comisiones. Se consultó
con las keys de lectura, solo con GET y fuera de la terminal (el historial de
retiros y depósitos sigue fuera de alcance).
**Datos:**
- Neoxa `GET /api/withdrawals/history`: id 20968, BTCB2 en la cadena `btcb2`,
  `amount` 0.2, `fee` 0.0001, `completed`, creado el 2026-10-23 a las 07:12:53 UTC,
  txid `e9cab346aa43ca42631f6b5f0ba1f21d25850ff33fbd4cdc8b9208587869b1b4`.
- NonKYC `GET /getdeposits`: el mismo txid, `quantity` 0.20000000 BTCB2, `Posted`,
  23 confirmaciones; lo vio a las 07:13:42 y lo acreditó a las 09:27:15 UTC
  (~2 h 14 min, porque pide 15 confirmaciones). La dirección es la de depósito de
  BTCB2 de NonKYC (`getdepositaddress/BTCB2`).
- El retiro de BTC de Neoxa del 2026-10-21 (0.0006, `fee` 0.0001) también llegó
  entero a NonKYC (0.00060000, `BTC-MAIN`).
- Se pidieron 0.2 XBT, no los 0.14002494 del plan, **a propósito**: el plan
  iguala los dos venues, pero una transferencia no cambia el % total, que estaba
  en ~18 %, por debajo del objetivo del 25 % (RF-29 lo avisa). El usuario mandó de
  más y compró a mano en la web de Neoxa para subir el total: 0.1 XBT a 0.004358
  (fills de las 07:26 y 07:27 UTC, sin `order_sent` de la terminal), unos 14 min
  después del retiro. Por la tarde (2026-10-23, con los disparos del bot de por medio):
  NonKYC 23.0 %, Neoxa 27.7 %, total 25.5 %, los tres dentro de la banda 15-35 %.
  Es el uso que prevén RF-26 y D-53: la transferencia reparte y la compra a mano
  corrige el total.
- Comisiones al consultar: Neoxa BTC 0.0001 y BTCB2 0.0001 (iguales que en
  D-53); NonKYC BTC-MAIN 0.00005 y BTCB2 **0.0028** (0.0027 en D-53).
**Decisión:** queda confirmado con datos de las dos APIs que **Neoxa cobra la
comisión de retiro aparte** (`on_top = True`, D-53), y no solo por su web: se
pide X y llega X. No cambia el código. PA-10 (cómo cobra NonKYC al retirar)
sigue abierta, porque esta transferencia salió de Neoxa.
**Toca:** D-53, RF-29, design.md §4.

## D-57 · 2026-10-23 · Bot en real, parte 2: parada por tope (cierra CA-10, T-38)
**Contexto:** D-43 dejó CA-10 a medias: hubo disparos reales con las dos piernas
llenas y en el journal, pero no se había visto a un tope de RF-22 parar el bot.
Con T-46 (D-55) el tope diario también para cuando ya está gastado al armar.
**Prueba:** `--live`, reiniciada con el código de T-46, y `max_daily_btc` bajado
de 0.01 a 0.0015 BTC, por debajo de lo enviado a comprar ese día (25 disparos en
live, 0.00168 BTC). `bot on` + `y`: el journal tiene `bot_state` `armed` a las
15:18:46.506 UTC y `halted` con `day_cap: 0.0015 BTC de compra hoy` a las
15:18:46.510, sin ningún `order_sent` después. El usuario lo vio en pantalla
(aviso rojo y `BOT ✖ PARADO`).
**Decisión:** CA-10 cumplido: cruces reales con las dos piernas en el journal
(D-43, y los 25 disparos del día) y parada por un tope de RF-22 en real. T-38
`done`. La parada tras un disparo que agota el tope (`finished`) queda probada
solo con tests. `max_daily_btc` vuelve a 0.01 en la config del usuario (copia
en `config.toml.bak-20261023-ca10`); entra al reiniciar.
**Toca:** CA-10, RF-22, T-38, D-43, D-55.

## D-58 · 2026-10-23 · `/cancelallorders` de NonKYC pide `side` (T-47)
**Contexto:** primera prueba de CA-05 en live (15:34 UTC): una compra de 0.005
XBT en cada venue y `x`. Neoxa canceló (`cancel_ack`, quedan 0). NonKYC respondió
HTTP 200 con `{"error": "400 Bad Request"}` y la orden siguió abierta; el usuario
la canceló a mano. El body era `{"symbol": "BTCB2/BTC"}`, copiado del
conocimiento de `btcb2-arb`, que nunca se probó en real; el test lo daba por
bueno porque comprobaba ese mismo body. El OpenAPI 1.3.0 de NonKYC (lo pasó el
usuario; es el de `https://api.nonkyc.io/openapi.json`) define el body como
`{"symbol", "side"}` con `side` = "all, buy or sell", y la respuesta como
`{"success": true, "ids": [...]}`.
**Decisión:**
- Una sola petición con `side: "all"`, el valor documentado. (Antes de ver el
  OpenAPI completo se probó en código una petición por lado; se descartó.)
- Sin `success: true`, es error.
- El OpenAPI se guarda en `docs/api/nonkyc/openapi.json`, con `docs/api/README.md`
  listando lo que la API real hace distinto. Pedido por el usuario.
- La terminal ya trataba bien el fallo: `error` en el journal y sin dar por
  cancelado lo que no lo estaba. El recuento de las que quedan (CA-05) lo habría
  mostrado.
- Lección: lo que viene de `btcb2-arb` es una pista, no un dato probado (el
  usuario lo aclaró).
**Toca:** RF-08, CA-05, design.md §4, T-47.

## D-59 · 2026-10-23 · CA-05 en real; `status=active` de NonKYC trae canceladas (T-48)
**Contexto:** segunda prueba de CA-05 en live, ya con T-47 (16:25 UTC): una
compra de 0.005 XBT en cada venue (`t-6a6e3f` en NonKYC, `t-d852d3` en Neoxa) y
`x`. Los dos venues aceptaron la cancelación: `/cancelallorders` con
`side: "all"` funcionó a la primera. Pero el `cancel_ack` de NonKYC apuntó
`remaining: 1`. Consultado después: `GET /account/orders?status=active` seguía
devolviendo la orden con `status: "Cancelled"` e `isActive: false`, minutos
después de cancelarla. `fetch_active` se fiaba del filtro, así que el recuento
de CA-05, `self_cross`, la reconciliación y el panel de órdenes podían contar
una orden muerta.
**Decisión:**
- `fetch_active` se queda solo con `open` y `partial`, según el `status` de cada
  orden. No se usa `isActive`: ante una contradicción, manda el estado (y una
  orden de más cuenta como abierta, que es el lado conservador).
- Una orden que deja de salir así la resuelve el seguimiento (`fetch_final` con
  `/getorder`), como cualquier otra que desaparece (D-14, D-41).
- **CA-05 cumplido:** tras cancelar todo, cero órdenes abiertas en los dos venues,
  comprobado en sus APIs (NonKYC `Cancelled`, Neoxa sin órdenes). El 1 del
  journal era el fallo del recuento, no una orden viva.
- Neoxa no mostró lo mismo (`/exchange/orders?status=active` vacío), pero no se
  ha probado con una orden recién cancelada en ese endpoint.
- **CA-07 repetido** el mismo día, tras las órdenes reales y el bot en live: las
  4 credenciales del `.env` buscadas en 40 ficheros (journals del 21 al 23,
  `diag.log`, `logs/`, fixtures, scratchpad) y en 5 capturas headless en dry-run
  con las keys reales cargadas (pantalla con datos reales, ayuda, depósitos,
  reequilibrio): 0 apariciones.
**Toca:** RF-08, RF-10, CA-05, design.md §4, T-20, T-48, `docs/api/README.md`.

## D-60 · 2026-10-23 · El bot recorre el book; sin órdenes market (T-49)
**Contexto:** en NonKYC, el pool AMM de BTCB2/BTC (1.807 BTCB2 / 0.008 BTC)
aparece en el book como escalones de ~0.0018 BTCB2, separados entre 0.15 % y
0.35 %. El mínimo del venue es `minQuote` 0.0000126 BTC, ≈ 0.0029 BTCB2 a
0.0044, así que hacen falta dos escalones. Con `max_slippage_pct = 0.2`, RF-20
solo contaba la profundidad hasta `ask × 1.002`, y muchas veces eso no llegaba
al mínimo, aunque el cruce diera. El usuario propuso un segundo bot con
órdenes market.
**Decisión:**
- Sin órdenes market ni segundo bot (§Fuera de alcance). El journal muestra que
  una límite agresiva ya llena contra el pool escalón a escalón: d-64d12e,
  compra de 0.0036, con dos fills de 0.0018 a 0.00443152 y 0.00442476. Una
  orden market haría el mismo recorrido, pero sin precio tope, en un pool de
  0.008 BTC.
- Nueva clave opcional `bot.max_walk_pct`, una tabla por venue
  (`{ nk = "0.8", nx = "0.1" }`). El precio de referencia de cada pierna puede
  ser cualquier nivel a no más de ese % del mejor, y `max_slippage_pct` se
  suma encima, como colchón. Si falta, vale 0 y todo queda igual que en v0.15.
- Se prueban todos los pares de niveles (los books son cortos) y gana el de
  mayor neta en BTC, no el de mayor cantidad: más profundidad solo se toma si
  rinde más en total y sigue sobre `min_net_pct`.
- La neta sigue siendo pesimista: se calcula con los precios límite y no con el
  precio medio que de verdad se llena.
- Por venue, porque Neoxa tiene profundidad al mejor precio y no le hace falta
  recorrer el book.
**Toca:** RF-20, design.md §6.9, T-49.


## D-61 · 2026-10-24 · Daemon sin pantalla y TUI conectada (v0.17)
**Contexto:** el bot vive dentro de la TUI: si se cierra la terminal o se cae
la sesión SSH, el bot muere. El usuario quiere dejarlo en un server y entrar a
mirarlo sin cambiar su estado. Además, parte de la operación vive en la UI y
no en los servicios: `_check_doubles` (piernas colgadas, cada 1 s),
`_tick_reconcile` (inciertas, RNF-12), el aviso de desfase de reloj y el
drenaje al salir (`on_unmount`, RNF-15).
**Decisión:**
- Un solo binario con subcomandos: `btcb2t` (terminal, como hasta ahora),
  `btcb2t daemon` y `btcb2t attach`, con los mismos `--config`, `--env`,
  `--data-dir` y `--live`. El usuario lo pidió así para configurar el servicio
  a mano: el daemon corre en primer plano y no sabe nada de systemd (sin
  unidad en el repo, sin `sd_notify`, sin fork ni pidfile; el lock de RNF-17
  basta). Solo POSIX: SIGTERM/SIGINT y código de salida.
- El bot arranca `off` también en el daemon (RF-18): tras un reinicio se
  rearma a mano desde `attach`. El usuario descartó un `--arm`.
- La operación que vive en la UI pasa a un `Engine` en `services/` que usan
  la terminal y el daemon; la UI solo se suscribe a sus avisos.
- Daemon y `attach` hablan por un socket Unix `0600` (`<datos>/btcb2t.sock`,
  directorio `0700`) con JSON por líneas (`Decimal` como texto): al conectar,
  una foto del estado; después, eventos. El cliente solo puede mandar
  `bot_on`, `bot_off` y `ping`: la TUI conectada es observadora, y llevar las
  órdenes por el socket obligaría a pasar por RPC toda la superficie de
  `OrderService`. Nada de TCP: se entra por SSH.
- `attach` pide books y velas él mismo, por la conexión pública y sin keys,
  como el modo solo lectura: reutiliza los paneles sin mandar los books por el
  socket. Riesgo: Neoxa cuenta el límite por IP (RNF-03) y el limitador del
  daemon no ve esas peticiones. Se acota con WS, REST solo al arrancar (velas)
  y un limitador propio pequeño. Si hiciera falta, el daemon reenviaría los
  books (no ahora).
- Resultados (RF-25) en `attach`: lee el journal del directorio de datos, como
  ya hace `ResultsService`.
**Toca:** RF-30, RF-31, RNF-17, CA-15, §Fuera de alcance, design.md §2 y §6.13,
T-50 a T-55.

## D-62 · 2026-10-24 · El daemon no veía los fills (T-56)
**Contexto:** primera prueba del daemon en live (`btcb2t daemon --live` con
`attach`, 2026-10-24 17:53 UTC). El bot disparó d-68e3ed (compra 0.0034 en
NonKYC a 0.00462809, venta en Neoxa a 0.00468121). Las dos piernas se
llenaron: NonKYC en dos fills de 0.0017 (0.00462587 y 0.00461885) en 1 s,
Neoxa al enviar (0.0046906). Pero el daemon siguió viendo la compra `open` con
0 llenado, la canceló a los `fill_timeout_ms` (NonKYC: "20002 Not found",
porque ya estaba llena), pasó a `halted` por `unsettled` y a los 30 s escribió
un `hanging_leg` falso. El inventario real quedó cuadrado.
**Decisión:** el enlace entre los eventos de la cuenta y las piernas de las
dobles es del motor, no de la pantalla: pasa de `BTCB2App.on_mount` al
`Engine`. Los tests del daemon montan las piezas sin ese enlace a mano
(`BotRig(wire=False)`), para que un olvido así falle en los tests.
**Toca:** design.md §6.13, T-56.

## D-63 · 2026-10-24 · Fee de NonKYC pagada en NKYC; el bot con descuento (requirements v0.18, T-57, T-58)
**Contexto:** el usuario compró NKYC (el token de NonKYC) para pagar las fees
con un 25 % de descuento y pide que el bot cuente con él. D-38 lo impide a
propósito: si se acaba el token, el venue cobra la fee entera. Medido en
`GET /account/trades` (2026-10-25 04:10-04:19 UTC):
- Los 461 fills de BTCB2/BTC pagaron 0.2 % en BTC (`alternateFeeAsset` nulo),
  incluido el último, de las 04:10, anterior a la compra de NKYC.
- Los 5 fills de la compra de NKYC (04:18, 10.0 NKYC a 0.0001312) pagaron
  `fee` 0 y `alternateFee` = 0.15033 % de la cantidad, en NKYC: 0.2 % × 0.75
  del valor en BTC, pasado a NKYC al precio de mercado (≈ 0.00013092). El 25 %
  se confirma, pero **aún no en un fill de BTCB2/BTC**.
- `alternateFeeAsset` es un id (`64369c724f86081e9d3f7fc9`), no el ticker; en
  `/balances` es el `assetid` de NKYC. Saldo al medir: 4.54 NKYC disponibles y
  5.09 en órdenes.
- Hallazgo aparte: `/account/trades?symbol=BTCB2/BTC` también devuelve los
  trades de NKYC/BTC. `fetch_fills` no filtra por mercado, así que esos fills
  entran en el tracker, en el journal como fills de `nk` y en `recent_orders`
  (D-26). `/account/orders?status=active` hace lo mismo: la venta de NKYC
  abierta salía como orden activa de BTCB2/BTC, y con ella el bloqueo de orden
  propia cruzada (RF-10) habría rechazado cualquier compra del par. Probado con
  `BTCB2/BTC`, `BTCB2_BTC`, `BTCB2-BTC` y `NKYC/BTC`: el venue ignora `symbol`
  en los dos. Es un bug de RF-05, RF-08 y RF-09, no un cambio de alcance; se
  arregla ya (T-58, `done`): las filas que dicen ser de otro mercado se
  descartan; una sin `symbol` cuenta como del par.
**Decisión** (aprobada por el usuario el 2026-10-24, con `fee_token_min = 1` en su config; cambia RF-19, RF-24 y RF-25):
- Claves opcionales en `[venues.<id>]`: `fee_token` (ticker, p. ej. `"NKYC"`) y
  `fee_token_min` (cantidad, > 0), las dos o ninguna, y con `fee_discount_pct`
  > 0. Sin `fee_token`, todo igual que antes (D-38).
- Con `fee_token`, el bot usa la fee taker con `fee_discount_pct` en el umbral
  (RF-19) y al elegir ruta (RF-20) **solo mientras el disponible del token en
  ese venue sea ≥ `fee_token_min`**. Por debajo, o si no hay saldo fresco, usa
  la fee completa y `bot status` lo dice ("NonKYC: NKYC bajo el mínimo, fee
  completa"). Se mide en saldo y no en valor para no pedir el mercado
  NKYC/BTC: con `max_qty` 0.05 a ~0.0043, una doble paga ≈ 0.0025 NKYC, y
  1 NKYC da para ~400 dobles.
- El saldo del token se lee en el mismo `/balances` (RNF-10 no cambia: no se
  opera ni se valora el token; no es un par contra stablecoin).
- Saldos, tamaño por % (RF-17), cantidad del bot (RF-20), RiskCheck y RF-26
  siguen con la fee completa: es la cota pesimista y, si la fee sale en NKYC,
  solo sobra un poco de BTC.
- Resultados (RF-25): la fee en NKYC sale como "+ x NKYC" (con el ticker, no
  el id) y no se valora en BTC, como hoy con otros activos. El resultado en
  sats queda algo optimista; lo dice el pie.
- Neoxa queda como está (sin `fee_token`) hasta saber el ticker de su token y
  si su API da el saldo.
- Antes de activarlo en `--live`: ver un fill de BTCB2/BTC con
  `alternateFeeAsset` de NKYC y ≈ 0.15 %.
**Toca:** RF-19, RF-24, RF-25, design.md §6.6 y §6.9, config.example.toml,
T-57, T-58.

## D-64 · 2026-10-25 · NonKYC corrige la fee de un fill después (requirements v0.19, T-59)
**Contexto:** primera doble del bot con la fee en NKYC (d-2e18a2, 2026-10-25
05:24 UTC). El journal apuntó los dos fills de la compra en NonKYC con `fee`
0.2 % en BTC (0.000000015606828 y 0.00000001557666) y `fee_asset` BTC. Minutos
después, `/account/trades` daba para los mismos `id`: `fee` 0, `alternateFee`
0.0000893166 y 0.0000891439 NKYC (0.1498 % a 0.00013089). NonKYC publica el
trade y después rehace la fee. `OrderTracker` solo avisa de un `trade_id` la
primera vez, así que la corrección no llegaba: la ventana de resultados restó
3.12 sats en BTC en vez de 0.00017846 NKYC (≈ 2.34 sats) y daba +26.71 sats en
vez de ≈ +27.49.
**Decisión:**
- `OrderTracker` recuerda la fee y el activo de los trades del último refresco.
  Si un trade ya visto vuelve con otra fee u otro activo, emite `FeeUpdate`
  (`venue`, `trade_id`, `order_id`, `fee`, `fee_asset`, `ts`), nunca un segundo
  `Fill`: la cantidad no cambia y las piernas no deben contarla dos veces.
- `AccountService` apunta `fill_fee` en el journal (mismos campos que `fill`
  salvo lado, cantidad y precio) y avisa `fill`, para que los resultados
  abiertos se recalculen. La línea del `fill` no se toca (RF-11, append-only).
- Resultados: un `fill_fee` sustituye la fee de ese `(venue, trade_id)` en su
  doble. Uno de un trade que no se conoce (sin asignar o de antes del journal)
  se ignora.
- Vale para los dos venues, aunque solo se ha visto en NonKYC.
- La doble d-2e18a2 se quedó con la fee vieja: su corrección llegó con la
  versión sin T-59, y al arrancar el tracker toma los trades como historia.
**Toca:** RF-11, RF-25, design.md §6.5, §6.10 y §7, T-59.

## D-65 · 2026-10-25 · Fees en token valoradas en resultados (requirements v0.20, T-60)
**Contexto:** con las fees de NonKYC en NKYC, la ventana de resultados no las
restaba: d-2e18a2 salía con +29.83 sats, y pagando en BTC (0.2 %) habría salido
con +26.71. El usuario entendió que NKYC le quitaba profit, cuando le ahorra un
25 % de la fee de NonKYC (≈ 0.78 sats por doble de 0.0036 XBT).
**Decisión** (pedida por el usuario):
- Una fee pagada en el `fee_token` de un venue se valora en BTC al **mid actual**
  de `<token>/BTC` en ese venue (`/market/getbysymbol/NKYC_BTC`, público) y se
  resta del resultado. El journal no guarda el precio del token en cada fill, y
  suscribirse a su mercado para eso sería mucho para fees de ~2 sats: el error
  de usar el precio de ahora es una fracción de esas fees.
- El precio se pide al abrir la ventana y al cambiar de rango, a lo sumo una vez
  por minuto (caché en `ResultsService`); un fill nuevo recalcula con el
  guardado. Mientras no llega, o si falla, la fee queda sin valorar y aparte,
  como antes, y el resumen lo dice.
- La fila muestra las fees en sats con el token incluido y su cantidad entre
  paréntesis; el resumen dice el precio usado. Una fee en un activo que no es
  `fee_token` de ningún venue sigue sin valorarse.
- No es un PnL de cartera (§Fuera de alcance): no se valora el saldo de NKYC,
  solo lo gastado en fees de cada doble. RNF-10 no cambia: el token no se opera.
- También en la TUI conectada (RF-31): pide el precio con su conexión pública.
**Toca:** RF-24, RF-25, design.md §6.10, T-60.

## D-66 · 2026-10-25 · El daemon es el servidor y la TUI, su cliente (requirements v0.21, T-61 a T-66)
**Contexto:** con D-61 hay dos sitios con lógica: la terminal todo en uno
(`Engine` y TUI en el mismo proceso) y el daemon, con un `attach` que solo
mira y arma o para el bot y que pide el mercado por su cuenta. El usuario
quiere toda la lógica en el daemon y la TUI como cliente puro.
**Decisión (elegida por el usuario el 2026-10-25):**
- `btcb2t` a secas es el cliente (`btcb2t attach` queda como alias). Sin daemon
  no abre la pantalla. Desaparece la terminal todo en uno: un solo camino de
  código. Sustituye la tabla de tres modos de D-61 y design.md §6.13.
- El daemon reenvía el mercado por el socket: al conectar, una foto por venue
  (spec, book, velas de cada timeframe, último trade, conexión); después, books
  agrupados cada ~100 ms con profundidad acotada, trades y estado de conexión.
  El cliente rehace las velas con `CandleBuilder`, como el hub. Sustituye "attach
  pide books y velas él mismo" de D-61: se acaba el consumo doble del límite por
  IP de Neoxa (RNF-03).
- Órdenes por el socket con propuesta y referencia: `prepare` (y
  `prepare_double`, `plan_cancel`) corre en el daemon con RiskCheck y devuelve
  la propuesta y un `token`; `confirm` manda solo el token. El daemon guarda las
  propuestas 60 s y cada una vale una vez. El cliente nunca manda una orden ya
  validada. Sustituye "el cliente solo puede mandar `bot_on`, `bot_off` y
  `ping`" de D-61.
- Resultados, depósitos y comisiones de retiro también los da el daemon: el
  cliente puede estar en otra máquina (socket reenviado con `ssh -L`) y no lee
  el journal ni las keys.
- El daemon sin keys arranca en solo lectura (antes no arrancaba): es la única
  forma que queda de mirar el mercado sin keys (RF-13).
- Peticiones con `id` y respuesta `reply {id, …}` con el tipo etiquetado
  (`kind`); cada petición corre en su tarea, para que un envío lento no
  bloquee al cliente. Sigue el socket Unix `0600`; nada de TCP.
- La TUI no importa `Engine`, `OrderService`, `MarketHub` ni ningún servicio
  que hable con los venues: solo `core/` y el cliente. `test_layers` lo vigila.
**Toca:** RF-30, RF-31, RNF-11, RNF-17, CA-15, CA-16, §Alcance, §Fuera de
alcance, design.md §2 y §6.13, T-61 a T-66.
**Añadido al cerrar T-65 (2026-10-25):** los tests de la pantalla no pasan
por un daemon: le dan los servicios de verdad en el mismo proceso
(`tests/ui/local.py`), que tienen la misma cara que los remotos. Por el socket
irían más lentos y frágiles sin probar más de la pantalla; el camino del
socket lo prueban los tests de `services/remote`, de la TUI conectada y de
CA-16. Los tipos que la pantalla necesita sin I/O pasan a `core/`
(`proposals.py`, `notices.py`, `DepositRow`).
**Añadido en T-66, parte local (2026-10-26):** con `btcb2t daemon --live` en
local y la pantalla conectada por el socket, una orden manual mínima colocada
y cancelada desde la pantalla: `buy nk 0.01 0.00365` (4.5 % bajo el mid),
t-2c6b97, `order_ack` 23:32:34 UTC (id 6adfe312113f972253a9db5b, `open`) y
`cancel <id>` con `cancel_ack` 23:32:44, `remaining: 0`, sin fills. Antes,
tres intentos más lejos del mid (0.0020, 0.0030, 0.0036) los rechazó el daemon
en local por `mid_deviation` (máx. 5 %) sin llegar al venue. Queda la parte del
server por SSH.

## D-67 · 2026-10-25 · Eje Y de las velas ajustado a los cuerpos, no a las mechas
**Contexto:** con poca liquidez una sola mecha (p. ej. un pico en Neoxa) estiraba
el eje Y y dejaba todas las velas aplastadas en una franja.
**Decisión:** `ChartPanel._range` toma el mínimo y el máximo de apertura y cierre;
la mecha que se sale de ese rango la corta plotext en el borde del área de dibujo.
**Toca:** RF-03 (sin cambio de requisito), `ui/panels.py`.

## D-67 · 2026-10-25 · Piernas de la doble junto a los balances (T-67)
**Contexto:** el usuario quiere las órdenes del bot abajo a la derecha, al
mismo nivel que los balances, en vez de en una fila propia debajo. A 160
columnas no caben las dos líneas como estaban (balances ~113, pierna ~95).
**Decisión:**
- Una fila de 5 alturas: balances a la izquierda (`1fr`) y piernas de la
  última doble a la derecha (64 columnas). Sin doble, los balances ocupan
  todo el ancho. Ahorra 4 filas a los gráficos cuando hay doble.
- Pierna compacta: lado, venue, cantidad @ precio, estado y llena/total; sin
  el id de orden (está en la ventana `o`) ni el estado repetido.
- Balances: `+ en órdenes` solo si hay algo en órdenes en ese activo.
- Lo que aún no quepa se corta con `…` en vez de partir la línea.
**Toca:** design.md §9, T-67.

## D-68 · 2026-10-25 · Al arrancar, el journal se pone al día con los trades del venue (T-68)
**Contexto:** d-550cd7 (2026-10-25 16:08 UTC) se quedó con la fee de NonKYC en
BTC: NonKYC la rehízo en NKYC después y la terminal se reinició a las 16:20. El
exchange sí la da (`/account/trades` devuelve la corregida), pero el
`OrderTracker` toma los trades del primer refresco como historia sin compararlos
con nada, así que la corrección no llegó al journal (el caso que D-64 dejó
abierto). Lo mismo pasaría con un fill de una orden nuestra mientras el daemon
está parado: no se apuntaría nunca. Es responsabilidad de la terminal.
**Decisión:**
- El tracker emite los trades del primer refresco como `PastFill` (un `Fill`
  envuelto), no como `Fill`: las piernas y el bot no los ven, así que nada
  cambia en vivo.
- `AccountService`, solo en live, carga del journal de los últimos 7 días la
  última fee de cada `(venue, trade_id)` y los `order_id` de sus `order_ack`
  (con su `client_id`). Con cada `PastFill`: si el trade está y la fee o el
  activo difieren → `fill_fee`; si no está y su `order_id` es nuestro → `fill`
  con `recovered: true` y `trade_ts` (la hora del trade, que la fila lleva la
  de ahora); si no, nada. En dry-run no se hace: los trades reales no son de
  la sesión simulada.
- Límite: un fill de Neoxa que se pierda no se recupera, porque Neoxa no da la
  orden del fill (D-45); sus correcciones de fee sí. Y solo se alcanzan los
  trades del primer refresco: en NonKYC, los 50 más recientes (`MY_TRADES`).
  d-550cd7 queda fuera (209 fills de NonKYC detrás): se corrige a mano.
**Toca:** RF-11, RF-25, design.md §6.5, T-68.

## D-69 · 2026-10-25 · Valor de la cuenta muestreado en el journal (requirements v0.22, T-69)
**Contexto:** el usuario quiere ver el valor total de la cuenta en BTC a lo
largo del tiempo, en una pestaña aparte de resultados. No hay historial de
saldos: el journal guarda órdenes y fills, pero no depósitos, retiros ni saldos.
§Fuera de alcance excluía el "PnL de cartera".
**Decisión:**
- El daemon muestrea: cada `value_every_s` (300 s; `[polling]`, mínimo 60), en
  múltiplos del reloj UTC, evento `value` con el total y el desglose por venue
  (BTC, BTCB2, mid, valor). Sin saldo o mid de algún venue con keys, no se
  apunta: un total a medias engañaría (como RF-05).
- Rehacer la serie hacia atrás desde los fills se descarta: sin depósitos ni
  retiros en el journal saldría mal. La serie empieza con el daemon de T-69.
- Se muestrea en cualquier modo con keys: los saldos son reales también en
  dry-run.
- Solo BTC y BTCB2 (RNF-10); el token de fee no cuenta.
- La serie viaja en `Report.values` por el `results` que ya existe (D-66).
- Es una serie de valor, no PnL: depósitos y retiros salen como saltos y la
  pestaña lo dice. §Fuera de alcance la exceptúa en v0.22.
**Toca:** RF-32, CA-17, design.md §6.5, T-69.
**Adenda (2026-10-26, requirements v0.23, T-70):** la ventana de resultados se
releía solo al abrirla, al cambiar de rango y con cada fill: con la pestaña
abierta, la serie se quedaba congelada aunque el daemon siguiera muestreando
(el usuario lo tomó por un daemon parado). Ahora pide el informe cada 60 s
(`RESULTS_EVERY_S`) mientras está abierta. Si el informe no cambió, no redibuja;
si cambió, la tabla de dobles conserva la fila seleccionada por su id. 60 s y no
`value_every_s`: el cliente no conoce el intervalo del daemon y una petición por
minuto al socket local no pesa.
**Adenda (2026-10-26, requirements v0.24, T-71):** el usuario pidió escala
logarítmica para ver las variaciones pequeñas. Las caídas que aplastaban la
escala eran transferencias en tránsito (p. ej. 19:35 UTC: 0.0050 BTC salidos
de NonKYC y aún no llegados a Neoxa; total de 0.0135 a 0.0047). Con un rango
de ~3×, en log las variaciones del 0.1–0.5 % siguen planas, así que se descarta.
En su lugar, `z` ajusta las dos gráficas al último tramo: las muestras desde el
último salto de más del 5 % (`JUMP_PCT`) entre dos seguidas, en el total o en
un venue. El bot y el mid no mueven el total un 5 % en 5 min; una transferencia
sí. Se mira también por venue porque la llegada de una transferencia puede
compensar en el total la salida de otra.

## D-70 · 2026-10-26 · Decisión del bot más barata, mismo resultado (requirements v0.25, T-72)
**Contexto:** el usuario pidió bajar la complejidad del bot sin cambiar lo que
hace y medir su latencia. Medido: con `max_walk_pct` = 1 % y 50 niveles por
lado, `decide` tardaba ~50 ms de mediana, y se llama en cada cambio de book (la
config real tiene `max_walk_pct = { nk = "0.8" }`). `_route` probaba todos los
pares de referencias (hasta 50 × 50) y `_pair` sumaba la profundidad recorriendo
el book entero en cada par: O(L³) por ruta.
**Decisión:**
- Profundidad hasta un precio con sumas acumuladas y `bisect` (`_Depth`, una
  vez por venue y `decide`): O(log L). La suma se acumula en el mismo orden que
  el `sum` de antes (del mejor precio hacia fuera): mismo `Decimal`.
- Poda exacta en `_route`: la neta % = venta × (1 − fee_B) / compra − (1 +
  fee_A) baja con el precio de compra y sube con el de venta, y
  `quantize_price` es monótono. Recorriendo `ra` de menor a mayor y `rb` de
  mayor a menor, un par `below_threshold` deja por debajo a los `rb` siguientes
  (se corta el interno) y, si es el primero de su `ra`, a todos los `ra`
  siguientes (se corta el externo). Los podados van después del que los poda y
  tienen neta ≤: `max` (que se queda con el primero en un empate) elige el mismo
  motivo y el mismo disparo.
- Slippage y fees se calculan una vez por ruta, no por par.
- `L2Book.top` toma los N mejores con `heapq` en vez de ordenar el book entero.
- Red de seguridad: `tests/unit/bot_reference.py` congela la decisión de antes y
  `test_bot_equivalence.py` compara las dos en miles de casos aleatorios con
  semilla fija. Si un cambio futuro del bot es de comportamiento, se actualiza el
  oráculo a propósito, con su propia decisión.
- RNF-23 fija un presupuesto con margen amplio (5 ms, 50 ms, 2 ms) para que la
  suite cargada no lo rompa; lo medido tras el cambio va en T-72.
**Toca:** RF-19, RF-20, RNF-22, RNF-23, design.md §6.9, T-72.

## D-71 · 2026-10-26 · El bot no dispara tras un movimiento brusco del mid (requirements v0.26, T-73)
**Contexto:** 12 de 472 dobles del bot (~2.5 %) quedaron con una pierna sin
llenar, 11 de ellas en Neoxa. En d-e988bd (2026-10-26 20:53:56 UTC) los trades
públicos de Neoxa lo enseñan: un barrido de ventas bajó el precio de 0.003796 a
0.0036911 en 100 ms; otro comprador se llevó 0.79 XBT a 0.00369105 a las .361 y
el AMM rebotó a 0.00372465; el bot decidió a las .376 con límite 0.00369844 y
la orden quedó abierta sin cruzar. No es la red: WiFi a −40 dBm, 3.6 ms al
router sin pérdidas, confirmación de esa orden 446 ms (mediana de las cerradas
327 ms) y sin caídas del WS antes de 10 de las 12. La neta tampoco las separa
(mediana 0.96 % las fallidas, 1.01 % las cerradas): un umbral más alto no
sirve. El usuario nota más bots y cambios muy repentinos.
**Decisión:**
- `BotService` guarda el mid de cada venue en cada cambio de book (ventana de
  `move_window_ms`, 2000 por defecto) y pasa a `decide` el movimiento
  `(máx − mín) / mín × 100` de esa ventana (`VenueView.move_pct`).
- Con `max_move_pct`, `decide` devuelve `Skip("volatile")` si algún venue lo
  pasa. Sin la clave no hay filtro y la decisión es la de antes (el oráculo de
  D-70 sigue valiendo).
- El movimiento se apunta en cada `bot_fire` (`move`, por venue), con filtro o
  sin él, para ajustar el umbral con datos: hoy no hay historial del book. Cada
  vez que el filtro empieza a frenar, una línea en `diag.log`.
- El mid y no los trades: el book refleja el barrido y el rebote, y el bot ya
  se despierta con cada cambio de book.
**Toca:** RF-19, CA-08, design.md §6.9, T-73.

## D-72 · 2026-10-26 · La ventana del movimiento conserva el mid que regía al empezar (T-74)
**Contexto:** d-60003f (2026-10-26 23:42:42 UTC) disparó con `move` 0 en los
dos venues y dejó colgada la venta en Neoxa. Los trades públicos de Neoxa
enseñan un barrido comprador de 0.0038496 a 0.00387863 entre .390 y .626, tras
~37 s sin trades; el bot vendió contra ese bid y otra venta de 0.168 XBT se lo
llevó a las .750. El filtro de D-71 no lo vio: la ventana olvidaba todos los
mids de antes de `move_window_ms`, también el que seguía vigente al empezarla.
Tras un book quieto más que la ventana, un salto en un solo cambio dejaba solo
el mid nuevo y daba 0 %. Casi todos los `bot_fire` desde D-71 llevan `move` 0:
el máximo de 0.27 % de los primeros datos estaba medido por debajo.
**Decisión:** la ventana olvida los mids de antes de su inicio salvo el último,
que es el que regía al empezarla. RF-19 no cambia: "se ha movido en los últimos
`move_window_ms`" ya incluía ese mid. Los `move` de `bot_fire` de antes de este
cambio no sirven para ajustar `max_move_pct`.
**Toca:** RF-19, CA-08, design.md §6.9, T-74.

## D-73 · 2026-10-26 · El comando y las rutas pasan a `xbt` (requirements v0.27, T-75)
**Contexto:** el proyecto se llama `xbt-terminal` desde el renombrado de la
carpeta y el paquete, pero el comando, las carpetas XDG, el socket y el lock
seguían como `btcb2t`. Para quien lo instala de cero, dos nombres despistan.
Pedido por el usuario, junto con la guía de despliegue (D-74).
**Decisión:**
- El comando es `xbt` (`xbt daemon [--live]`, `xbt`, alias `xbt attach`). Las
  rutas por defecto pasan a `~/.config/xbt` y `~/.local/state/xbt`, y el socket
  y el lock a `xbt.sock` y `xbt.lock`. La app es `XbtApp`; los títulos de la
  spec, `xbt-terminal`.
- No cambia nada de lo que habla con los venues: símbolos (`BTCB2/BTC`,
  `BTCB2_BTC`), tickers de saldos, depósitos y comisiones, ni el `BTCB2` interno
  del par. En pantalla ya se llamaba XBT (D-48).
- Sin migración en el código: los datos viejos se mueven a mano con el daemon
  parado (`mv ~/.config/btcb2t ~/.config/xbt`, `mv ~/.local/state/btcb2t
  ~/.local/state/xbt`, y `btcb2t.lock` → `xbt.lock`; el socket se crea solo).
  Un daemon viejo y un cliente nuevo no se encuentran: hay que actualizar los dos.
- Las decisiones anteriores y los títulos de tareas cerradas conservan
  `btcb2t`: son historia.
**Toca:** RF-30, RF-31, RNF-20, design.md §6.13 y §8, T-75.

## D-74 · 2026-10-26 · Licencia AGPL-3.0 y guía de despliegue (T-76)
**Contexto:** el README explicaba qué hace la terminal, pero no lo que necesita
alguien sin contexto para ponerla en su máquina o su servidor: requisitos
(`uv`, Linux/macOS por el socket Unix, reloj con NTP por RNF-16, terminal de
160×45), keys de cada venue, instalar el comando en el PATH, servicio,
actualizar, datos y problemas comunes. Y sin licencia nadie más puede usarlo.
**Decisión:**
- Licencia AGPL-3.0 (elegida por el usuario): quien lo modifique y lo
  distribuya o lo sirva publica sus cambios.
- `README.md` queda en qué es, instalar, primera puesta en marcha, seguridad y
  aviso de riesgo; `docs/despliegue.md` (servidor, servicio, actualizar, datos,
  problemas comunes) y `docs/uso.md` (atajos y ventanas). El estado de
  desarrollo sigue en `HANDOFF.md` y la spec.
- El ejemplo de unidad `systemd --user` va en la documentación, no como fichero
  del repo: el binario sigue sin hablar con ningún gestor de servicios (D-61).
**Toca:** RNF-08, RNF-16, RNF-20, T-76.

## D-75 · 2026-10-27 · El bot envía primero la pierna de Neoxa y la otra solo si llena (requirements v0.28, T-77)
**Contexto:** d-8c32f8 (20:55 UTC) y d-97dc8e (21:15 UTC) dejaron 0.0097 XBT
vendidos de más en NonKYC: la compra en Neoxa volvió `open` y se canceló a los
3 s sin llenar. En el journal live (2026-10-21 a 27), de 615 piernas del bot en
Neoxa, 600 volvieron `filled` en el propio ack y 15 `open`; 14 de esas 15
acabaron colgadas (la otra, d-609e15, la llenó alguien a tiempo). Son las 14
colgadas de Neoxa de las 20 del bot; las 6 restantes son de NonKYC con Neoxa
llena. Neoxa casa al recibir la orden: si vuelve `open`, el ask o el bid que
vio el bot ya no estaba. `max_move_pct` no las separa (`move` de Neoxa 0.14 y
0.43 % en las colgadas de hoy, hasta 0.80 % en dobles buenas del mismo minuto).
**Decisión:**
- `bot.lead_venue` opcional (en el servidor, `"nx"`): se envía primero esa
  pierna y la otra solo por lo que llenó, al precio calculado al disparar (sin
  recalcular, elegido por el usuario). Llena en parte: se cancela el resto y la
  otra va por lo lleno, al lote hacia abajo.
- Si no llena nada, se cancela al momento y la otra no sale: `bot_miss`,
  `cooldown`, y no cuenta para `max_consecutive_failures` (elegido por el
  usuario: no deja posición). Sí cuenta para los topes por hora y por día, como
  todo disparo.
- Sin la clave, el bot sigue enviando en paralelo; la doble manual no cambia.
- Con estos datos: las 14 colgadas de Neoxa habrían sido 14 `bot_miss` y se
  habría perdido d-609e15. Coste: la pierna de NonKYC sale ~0.3 s después (ack
  de Neoxa: p50 305 ms, máximo 757 ms el 2026-10-27), lo que puede subir las
  colgadas de NonKYC (6 en 634 disparos). Se revisa con `bot_fire` y
  `hanging_leg` tras unos días.
**Toca:** RF-07, RF-20, RF-21, RF-23, CA-08, design.md §6.9, T-77.

## D-76 · 2026-10-29 · El book quieto se contrasta con el REST; la conexión viva ya no basta (requirements v0.29, T-78)
**Contexto:** d-9820a4 (19:58:48 UTC), d-f1fe90 (20:00:25) y d-6eca2e
(20:01:40) del 2026-10-29: compra en Neoxa llena y venta en NonKYC a
0.00318575 cancelada con 0.0021, 0 y 0 XBT llenos; 0.0234 XBT comprados de más
en Neoxa. Las tres con `move` 0 y una neta esperada del 3.1 %. El mid de NonKYC
del journal (`value`) quedó fijo en 0.003216845 desde las 17:50 UTC, con trades
de ~1 BTCB2 cada minuto en el venue y el book REST cambiando: el WS de NonKYC
siguió conectado (sin caídas desde las 16:46) pero dejó de mandar
`updateOrderbook`. Un WS nuevo, a las 20:05, solo recibió el snapshot y ningún
cambio en 40 s; con el REST cambiando, parece que NonKYC no los publica. Coincide con
liquidez añadida al pool de NonKYC, sin comprobar si es la causa. Por D-13,
un book quieto con la conexión "up" cuenta como fresco: el bot no lo podía ver.
Al volver a suscribirse el usuario, el book quedó bien.
**Decisión:**
- Cada `risk.book_check_s` (5 s) el daemon pide el book REST de cada venue
  (`book_snapshot`) y compara mejor bid y mejor ask, precio y cantidad, con el
  local. Solo el tope: el resto del book REST puede venir recortado.
- Coincide → ese momento es la frescura del book. La frescura pasa a ser la más
  reciente entre el último mensaje del book y el último contraste que coincide;
  el latido de la conexión "up" de D-13 deja de contar.
- No coincide → el book local deja de ser fresco (el bot dice "book viejo", las
  órdenes se rechazan por `stale_data`) hasta que llega uno nuevo del venue;
  NonKYC se vuelve a suscribir en el mismo WS (como ante un hueco) y Neoxa
  vuelve a conectar (cada mensaje suyo es el book entero). `book_resync` en el
  journal con los dos topes y aviso en `diag.log`.
- Si llega un mensaje del book mientras se espera el REST, el contraste no
  cuenta: la carrera daría un falso desacuerdo, y el mensaje ya da frescura.
  Un falso desacuerdo que pase igual solo cuesta una suscripción.
- Un REST fallido no confirma nada; con `stale_after_s` = 15 y contraste cada
  5 s, aguanta dos fallos seguidos antes de bloquear.
- Coste: 12 peticiones/min por venue, dentro de los 240 de NonKYC y de los 480
  de Neoxa compartidos con el polling (RNF-03, RNF-05).
- Descartado: dar el book por malo cuando un trade sale a un precio que el book
  local no permite. Los trades contra el pool de NonKYC salen dentro del spread
  (0.00318713 con bid 0.00318273 y ask 0.0031987, 2026-10-29 20:03 UTC): habría
  falsos positivos continuos.
- Neoxa, comprobado en T-78 (2026-10-29 ~20:40 UTC, 4 min, contraste cada
  3 s, solo lecturas): 68 iguales, 6 descartados por un mensaje durante el REST
  y 1 distinto (la cantidad del mejor bid, una carrera: el REST ya traía el
  cambio que el WS mandó después). El contraste vale para los dos venues; en
  Neoxa un falso desacuerdo cuesta una reconexión (~1 cada 3 a 4 min).
- Con esto, el book congelado de hoy se habría visto en el primer contraste tras
  el primer cambio que no llegó, ~2 h antes de d-9820a4.
**Toca:** RNF-16, RNF-24, RF-10, RF-19, CA-18, design.md §4 (tabla de
`stale_data`), §6.9, config.example.toml, T-78.

## D-77 · 2026-10-29 · Legibilidad: funciones más cortas, mismo comportamiento (T-79)
**Contexto:** revisión de legibilidad (Clean Code y PEP 8) del 2026-10-29. Con
la config del proyecto, ruff no da avisos; con `C90`, `PLR`, `N`, `BLE` y `ARG`
salen funciones de complejidad alta (`wire.decode` 28, `risk.check` 20,
`commands.parse` 18), constructores de hasta 21 parámetros (`XbtApp`),
`ui/app.py` con 1 634 líneas, acceso a privados entre clases
(`services/remote.py`), `faults` como lista de un elemento y cinco
formateadores de sats y % casi iguales en `ui/`.
**Decisión:**
- Se arregla por partes, una tarea por parte, sin cambio de comportamiento:
  los tests existentes no se tocan salvo para añadir casos, y ningún requisito
  cambia (no hay versión nueva de requirements.md).
- Primera parte (T-79): `core/risk.py` `check` y `core/commands.py` `parse`,
  que son puros y con tests propios. `check` se parte en una función por
  sección (datos de mercado, tamaño, rejilla, mid, cruce propio, saldo,
  exposición) que mantiene el orden de las violaciones; `parse` pasa a un
  diccionario de comando → función, y un helper para los `Literal` quita los
  `# type: ignore`. Las dos quedan con complejidad ≤ 10 (C901) y ≤ 6 returns.
- Siguientes partes, por orden, cada una con su tarea cuando el usuario la
  pida: `wire.decode`; formateadores en `ui/format.py`; `faults: Faults | None`
  en vez de lista; `ui/app.py` en `ui/screens/`; parámetros de `XbtApp`
  agrupados.
- Activar `C90` y `N` en `pyproject.toml` queda para cuando las partes que
  saltan estén hechas (si no, habría que llenar el código de `noqa`).
**Toca:** T-79 (sin cambios en requirements ni design).

## D-78 · 2026-10-29 · Los fallos se piden al `Engine`, no a su lista (T-82, corrige D-77)
**Contexto:** D-77 proponía cambiar `Engine.faults: list[Faults]` por
`Faults | None`. Al mirarlo, la lista es real: en la app hay un registro
compartido, pero cuando cada servicio se crea suelto (los tests, y cualquier
`Engine` sin el `__main__`) órdenes, cuenta y hub traen cada uno el suyo, y el
`Engine` se suscribe a todos y suma sus cuentas. Lo que confunde es que
`control.py`, `wire.py` y `ui/app.py` miren dentro: `faults[0].report(...)`,
`sum(f.count for f in faults)` y `faults: list[Any]` en el protocolo `Runner`.
El hub siempre trae un registro, así que la lista nunca está vacía y los
`if engine.faults:` no tenían otra rama posible.
**Decisión:**
- `Engine.faults` sigue siendo la lista de registros distintos.
- `Engine.report_fault(where, exc)` apunta en el primero (el de órdenes si
  hay, si no el de la cuenta, si no el del hub: el mismo que antes),
  `Engine.fault_count` suma los de todos y `Engine.subscribe_faults(listener)`
  se suscribe a todos.
- `control.py`, `wire.py` y `ui/app.py` usan solo esas tres; `Runner` las
  declara con su tipo en vez de `faults: list[Any]`.
**Toca:** T-82 (sin cambios en requirements ni design).
