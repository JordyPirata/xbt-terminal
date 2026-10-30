# xbt-terminal — Diseño MVP

Versión 0.17 · 2026-10-24 · Implementa `requirements.md` v0.17 (empezado el
2026-10-19 sobre la v0.1; cada sección dice de qué versión y decisión viene)

## 1. Principios

- **Todo asíncrono en un solo event loop.** Textual corre sobre asyncio; httpx y
  websockets también. Nada de hilos salvo que una librería lo obligue (D-01).
- **El núcleo no hace I/O.** Book, velas, validaciones, parser de comandos y
  cálculo del panel cruzado son funciones puras o clases sin red: se testean sin
  mocks.
- **Un venue = un adaptador** detrás de la interfaz `Venue` (RNF-09). La UI y los
  servicios nunca saben contra quién hablan.
- **`Decimal` de punta a punta** (RNF-07). Un `float` en un precio es un bug.
- **Fallar cerrado.** Ante duda (book viejo, mid desconocido, balance sin
  refrescar) la validación rechaza la orden.

## 2. Capas

```
┌──────────────────────────────────────────────────────────────┐
│ ui/        app · panels · status · clipboard · qr            │  Textual
├──────────────────────────────────────────────────────────────┤
│ services/  market (MarketHub) · account · orders · bot ·     │  estado vivo,
│            faults · results · deposits · rebalance ·         │  tareas asyncio
│            engine · daemon · control · wire · remote         │
├──────────────────────────────────────────────────────────────┤
│ core/      models · book · candles · cross · risk · bot ·    │  puro, sin red
│            double · inventory · sizing · reconcile ·         │  (salvo journal,
│            results · rebalance · commands · journal ·        │  ver abajo)
│            ratelimit · secrets                               │
├──────────────────────────────────────────────────────────────┤
│ adapters/  base · streaming · private · socketio ·           │  httpx /
│            nonkyc · neoxa · dryrun                           │  websockets
└──────────────────────────────────────────────────────────────┘
fuera de capas: config · paths · diaglog · __main__ (arranque e I/O de ficheros)
```

Reglas de importación (se comprueban en revisión):

- `core` no importa nada del proyecto salvo `core`.
- `adapters` importa `core`, nunca `services` ni `ui`.
- `ui` usa los tipos y funciones puras de `core` y el cliente del daemon
  (`services/remote.py`, `services/wire.py`); nunca `adapters` ni los servicios
  que hablan con los venues (`engine`, `market`, `account`, `orders`, `bot`,
  `deposits`, `rebalance`, `results`). D-66.
- `services` puede importar `config` (lo hace `services/bot.py` con `BotConfig`).
- Excepciones aceptadas (D-55): `core/journal.py` escribe ficheros (`open`,
  `chmod`, rotación diaria), porque lo usan todas las capas y moverlo no cambia
  nada de lo que hace. `core/ratelimit.py` no hace red, pero usa el loop y el
  reloj de asyncio (inyectable en los tests).
- `tests/unit/test_layers.py` comprueba las importaciones (T-46).

## 3. Modelo de datos (`core/models.py`)

```python
VenueId = Literal["nk", "nx"]
Side = Literal["buy", "sell"]
Timeframe = Literal["1m", "5m", "1h", "4h", "1d"]

MarketSpec(venue, symbol, base, quote, tick_size, lot_size, min_qty, min_notional,
           maker_fee, taker_fee)                  # quantize_price(side), quantize_qty
Level(price, qty, amm=False)                       # amm: nivel del AMM de Neoxa
BookSnapshot(venue, bids: tuple[Level], asks: tuple[Level], ts, seq=None)
BookDelta(venue, bids, asks, ts, seq=None)         # qty == 0 borra el nivel
Trade(venue, price, qty, side, ts)      # side = lado del taker
Candle(ts_open, open, high, low, close, volume)
Balance(asset, available, locked)
OrderRequest(venue, side, qty, price, client_id)
Order(venue, order_id, client_id | None, side, price, qty, filled, status, ts)
Fill(venue, order_id | None, price, qty, fee, ts, trade_id, side, fee_asset)  # D-14
ConnState(venue, channel, source, status, latency_ms, since)   # RF-14
# channel: market | private · source: ws | rest · status: up | reconnecting | down
DepositAddress(...)    # RF-28, campos en §6.11
WithdrawalFee(...)     # RF-29, campos en §6.12
```

`OrderStatus = pending | open | partial | filled | cancelled | rejected`. Las
piernas de una orden doble usan `LegStatus = OrderStatus | unknown`
(`core/double.py`, RNF-12).

Timestamps en milisegundos epoch UTC (`int`). Todo campo `Decimal` se comprueba
al construir: un `float` lanza `TypeError`. Para literales se usa `D("0.01")`.

## 4. Interfaz de venue (`adapters/base.py`)

```python
class Venue(Protocol):
    id: VenueId
    async def market(self) -> MarketSpec
    async def book_snapshot(self, depth: int) -> BookSnapshot
    async def recent_trades(self, limit: int) -> list[Trade]
    async def candles(self, tf: Timeframe, limit: int) -> list[Candle]
    def market_stream(self) -> AsyncIterator[BookSnapshot | BookDelta | Trade | ConnState]

    # privados: lanzan ReadOnlyError sin credenciales
    async def balances(self) -> dict[str, Balance]
    async def open_orders(self) -> list[Order]
    async def place_limit(self, req: OrderRequest) -> Order
    async def cancel(self, order_id: str) -> None
    async def cancel_all(self) -> None
    def private_stream(self) -> AsyncIterator[Order | Fill | ConnState]
    keeps_client_id: bool                       # ¿devuelve el venue nuestro client_id?
    async def recent_orders(self, since: int) -> list[Order]  # para reconciliar (RNF-12)
```

- La parte pública es el protocolo `MarketData` y `Venue` la amplía con la privada:
  un adaptador solo de datos (T-08, T-09) ya cumple `MarketData`. Errores:
  `VenueError`, y sus subclases `ReadOnlyError` y `UncertainError` (RNF-12).
- `place_limit` lanza `UncertainError` cuando no se sabe si el venue creó la orden
  (D-26): timeout u otro fallo de red *después* de conectar, HTTP 5xx, respuesta
  2xx que no es JSON, o respuesta que no se puede leer como orden. Un fallo al
  conectar (`ConnectError`, `ConnectTimeout`, `PoolTimeout`) o un 4xx/error del
  venue sigue siendo `VenueError`: la orden no existe.
- `recent_orders(since)`: órdenes del par que pueden ser la de un envío incierto.
  NonKYC: las activas más las de sus trades propios desde `since` (`/getorder`).
  Neoxa: las activas más las 100 últimas del historial. `keeps_client_id` es True
  en NonKYC (`userProvidedId`) y en el dry-run, False en Neoxa (PA-09).
- `market_stream` oculta si los datos vienen por WS o por polling REST de
  respaldo (RF-01): el adaptador cambia de fuente solo y emite un `ConnState`
  con `source="rest"`.
- `private_stream` (D-14): los dos venues refrescan por REST órdenes activas y
  trades propios, y `OrderTracker` (`adapters/private.py`) emite `Order` y `Fill`
  por diferencia. En Neoxa es polling cada 3 s (RNF-04); en NonKYC, cada
  `report` del WS privado (login HS256) despierta un refresco, con uno de
  seguridad cada 15 s, y sin WS polling cada 3 s. Cada cambio de estado o de
  fuente emite un `ConnState` del canal privado.
- `DryRunVenue(inner: Venue)` delega market data al venue real y simula
  `place_limit/cancel` en memoria (RF-13).
  - Los balances son los reales del venue en cada consulta más lo simulado
    (D-16): al abrir una orden su importe pasa de `available` a `locked` y al
    cancelarla vuelve. Las órdenes reales de la cuenta no son órdenes del
    dry-run, pero su saldo comprometido cuenta.
  - No hay fills simulados (`simulate_fill` es solo para tests). En modo solo
    lectura no se instancian métodos privados reales.

### Particularidades conocidas (de `btcb2-arb`, 2026-09, comprobadas en real después)

El OpenAPI de NonKYC está en `docs/api/nonkyc/openapi.json` (con sus diferencias
con la API real en `docs/api/README.md`). `btcb2-arb` nunca se probó en real:
sus endpoints son pistas, no datos (D-58).

| | NonKYC | Neoxa |
| --- | --- | --- |
| REST | `https://api.nonkyc.io/api/v2` | `https://neoxa.exchange/api` |
| Auth | HMAC-SHA256 sobre `apiKey + url + body + nonce`; headers `X-API-KEY`, `X-API-NONCE`, `X-API-SIGN`. El body se serializa una sola vez. | Headers planos `X-API-Key` / `X-API-Secret`; las keys solo tienen `read` y `trade`, sin retiros |
| Privado (D-14) | `/balances` (`held` = en órdenes), `/account/orders?status=active` (trae también canceladas: se filtran, D-59), `/account/trades` (con `orderid`); los dos ignoran `symbol` y traen todos los mercados: se filtran por `symbol` / `market.symbol` (D-63), `/createorder` (`strictValidate`, `userProvidedId`), `/cancelorder`, `/cancelallorders` (`symbol` y `side: "all"`, D-58), `/getorder/{id}`. WS: `login` con firma HMAC(secret, nonce), `subscribeReports` → `activeOrders`, `report` (formato sin documentar) | `/exchange/balances` (`in_orders` fuera de `total`), `/exchange/orders?status=active&pair=`, `/exchange/orders/history`, `/exchange/trades/my` (sin id de orden), `POST /exchange/order`, `DELETE /exchange/order/{id}`; sin "cancelar todas" |
| Mercado | `/market/getbysymbol/{sym}`; `/markets` trae duplicados de BTCB2 (spot y pool): nunca escanear la lista | `/exchange/pairs`; símbolo `BTCB2_BTC` (id 47) |
| Book | `/market/orderbook?symbol=&limit=` | `/exchange/orderbook/{sym}?depth=`; mezcla órdenes reales y AMM (`isAmm`) |
| Trades / velas | `/market/trades?symbol=&limit=` (máx. 500, ms); `/market/candles` con `resolution` 5/60/240/1440 y `firstDataRequest=1`; **sin 1m**: se construye con trades (D-10) | `/exchange/trades/{sym}?limit=` (máx. 100, más nuevo primero); `/exchange/candles/{sym}?interval=&limit=` (`1m`…`4h`, `1d`; `time` en segundos, sin velas en minutos sin trades) |
| WS público | JSON-RPC 2.0 en `wss://ws.nonkyc.io`: `subscribeOrderbook` → `snapshotOrderbook` + `updateOrderbook` con `sequence` (llegan desordenados: se reordenan); `subscribeTrades` → `snapshotTrades` + `updateTrades`; un error cierra la conexión. WS privado: `login` HS256 con API key (D-10) | Socket.IO v4 en `/exchange` (`wss://neoxa.exchange/socket.io/?EIO=4`): `subscribe {pair}`; `exchange:orderbook` = book completo sin `seq`; `exchange:trades` al suscribir; `exchange:trade` en vivo (D-09). El WS privado exige JWT de sesión web, no API key |
| Órdenes | Solo límite (`allowMarketOrders=false`); `minQuote` en BTC (≈0.0000126 el 2026-10-20) | Solo límite; `/exchange/orders`, `/exchange/trades/my` |
| Fees | No expuestas por API → config | Por par en `/exchange/pairs` (`makerFeeRate`, `takerFeeRate`) |
| Números | Strings en JSON | **Floats** en JSON: parsear con `json.loads(..., parse_float=Decimal)` |
| Precisión BTCB2/BTC | precio 8 decimales, cantidad 4 | precio 8, cantidad 8 |
| Límites | No publicados (D-10): 300/min supuesto | 600/min por IP, 10 órdenes/10 s, 20 cancel/10 s; cabeceras `RateLimit-*` (verificado 2026-10-20) |
| Depósitos (D-50, 2026-10-22) | Privado: `GET /getdepositaddress/{ticker}` → `address`, `paymentid`, `ticker`, `network`, `minimumDeposit`, `confirmsRequired`. BTC on-chain es **`BTC-MAIN`** (`BTC` da `20010 Must use more specific ticker`); BTCB2 es `BTCB2` (red "XBT (BTCB2 / Blake2b fork)", mín. 0.001, 15 conf.) | Privado: `GET /deposits/addresses` → `addresses` con una dirección por cadena (`bitcoin`, `btcb2`…) y `closedChains` (cadena → motivo); sin mínimo ni confirmaciones |
| Retiros (D-47, D-53, D-56, 2026-10-23) | Público: `/asset/getbyticker/{ticker}` (`withdrawFee`, `minimumWithdraw`, `withdrawalActive`, `isMaintenance`). BTC en red principal es el ticker hijo `BTC-MAIN`: **0.00005** (mín. 0.00001); el padre `BTC` dice 0.0008 y no es el de la red principal. BTCB2 0.0027, sin mínimo | Privado: `/withdrawals/fees` (con login). BTC **0.0001** (mín. 0.0005); BTCB2 0.0001 (mín. 0.01). Comisión **aparte** de lo pedido, confirmada en `/withdrawals/history` (D-56). Público: `/api/v1/cmc/assets` (solo mínimos y fees de trading) |

El book de Neoxa se muestra marcando los niveles AMM (columna o color), porque
su profundidad no se comporta como la de órdenes reales.

## 5. Presupuesto de peticiones de Neoxa (RNF-03)

Un único `RateLimiter` por venue con varias ventanas deslizantes; toda petición
REST pasa por él. Límites efectivos con el 20% de margen:

| Ventana | Publicado | Efectivo |
| --- | --- | --- |
| General | 600 / 60 s | 480 / 60 s |
| Órdenes | 10 / 10 s | 8 / 10 s |
| Cancelaciones | 20 / 10 s | 16 / 10 s |

Consumo previsto en régimen normal:

| Uso | Cadencia | req/min |
| --- | --- | --- |
| Órdenes propias (fills) | 3 s | 20 |
| Trades propios | 3 s | 20 |
| Balances | 10 s | 6 |
| Book REST de respaldo (solo si cae el WS) | 1 s | 60 |
| Trades REST de respaldo (solo si cae el WS) | 5 s | 12 |
| Margen para órdenes, cancelaciones y velas | — | ~362 |

Las peticiones tienen prioridad: `cancel > order > private poll > market poll`.
Si el limitador está saturado, se retrasa primero el polling de mercado, nunca
una cancelación.

`RateLimiter.acquire(kind)` con `kind ∈ cancel | order | private | market`. Todos
consumen la ventana general; `order` y `cancel` además la suya. Con hueco libre
pasa la espera de más prioridad (FIFO dentro de la misma); una orden frenada solo
por su ventana de 8/10 s no bloquea el polling. Un único limitador por venue,
inyectado en su adaptador.

## 6. Flujos

### 6.1 Market data (RF-01, RF-02, RF-03, RF-14)

```
adapter.market_stream ──▶ MarketHub ──▶ L2Book.apply()  ──▶ BookPanel   (throttle 100 ms)
                                    └─▶ CandleBuilder    ──▶ ChartPanel  (throttle 1 s)
                                    └─▶ ConnState        ──▶ StatusBar
```

- Al arrancar: `market()`, `book_snapshot()`, `candles()` por REST; luego stream.
- `L2Book` aplica deltas por `seq`; si detecta un hueco, pide snapshot nuevo.
  Mientras tanto queda inválido y sin precios (`mid` None), así que nadie opera
  con un book incompleto. Deltas viejos o duplicados se ignoran; venues sin
  `seq` aplican todo. Niveles con qty 0 se borran, también en el snapshot.
- `CandleBuilder` (uno por venue y timeframe) se carga con el histórico REST y
  cada trade actualiza la vela en curso. Periodos sin trades = velas planas al
  último cierre con volumen 0; `roll(now)` las abre aunque no lleguen trades.
  Un trade anterior a la vela en curso se ignora. Guarda 500 velas como máximo.
- Render del book con throttle de 100 ms para cumplir RNF-06 (<500 ms) sin
  redibujar por cada mensaje.
- Contraste con el REST (RNF-24, D-76): con `risk.book_check_s`, cada tanto
  `MarketHub.check_book` pide `book_snapshot(5)` y compara mejor bid y mejor
  ask (precio y cantidad) con el `L2Book`. Coincide → `VenueMarket.checked_at`.
  No coincide → `suspect` (hasta el siguiente snapshot o un contraste que
  coincida), `book_resync` en el journal y `resync()` del adaptador: NonKYC
  vuelve a mandar `subscribeOrderbook` en el mismo WS y descarta lo que llegue
  hasta el snapshot; Neoxa (la base) cierra el WS y el supervisor reconecta.
  Si llega un mensaje del book mientras se espera el REST (`book_events`
  cambia), el contraste no cuenta; un REST fallido tampoco.
- Reconexión: backoff exponencial 0.5 s → 30 s con jitter; tras 3 fallos
  seguidos activa polling REST hasta que el WS vuelva (CA-06).
- Latencia: RTT de ping WS o de la última petición REST, media móvil.
- Supervisor en `adapters/streaming.py`, común a los dos venues (D-11):
  - Un fallo es una sesión WS que termina, y el contador vuelve a 0 con el
    primer book de una sesión.
  - `(ws, up)` se emite con el primer book, no al conectar.
  - El estado se reemite cada 5 s con la latencia.

### 6.2 Orden simple (RF-06, RF-10, RF-11, RF-13)

```
comando ─▶ parse ─▶ RiskCheck(ctx) ─┬─ rechazo ─▶ mensaje + journal(reject_local)
                                    └─ ok ─▶ diálogo "BUY 0.01 @ 0.00730000 = 0.00007300 BTC  [y/N]"
                                              └─ y ─▶ journal(order_sent) ─▶ venue.place_limit
                                                       └─▶ journal(order_ack | order_error)
```

`client_id` = `t-<uuid4 corto>` generado localmente, para casar envío, respuesta
y fill en el journal.

**Envío incierto (RNF-12, D-26).** Si `place_limit` lanza `UncertainError`, el
resultado es `Uncertain` (no `Failed`): journal `order_unknown`, aviso en rojo, la
barra de estado cuenta las inciertas y, en una orden doble, la pierna queda
`unknown`. `OrderService.reconcile()` (cada 3 s desde la UI mientras haya alguna)
pide `recent_orders(sent_at - 60 s)` a cada venue con inciertas y busca candidatas
con `core/reconcile.py`:

- Con `keeps_client_id`, la orden con el mismo `client_id`. Sin él (Neoxa,
  PA-09), lado, precio y cantidad iguales y creada a ±60 s de `order_sent`.
- Nunca una orden que ya se conocía al enviar (abiertas de la cuenta y recién
  enviadas) ni una que el venue haya confirmado a la terminal en la sesión.
- Una candidata: `order_ack` con `reconciled: true`, y la orden pasa a contar
  como recién enviada y a su pierna. Varias: sigue incierta y se avisa una vez.
  Ninguna tras 60 s: `order_error` con `reconciled: true` (no existe). Si el venue
  no responde, se reintenta en la vuelta siguiente.

Saldos y órdenes para RiskCheck salen de `AccountService` si tienen menos de
30 s, más las órdenes recién enviadas; si no, se piden al venue (RNF-11, D-18).
Las inciertas cuentan siempre como abiertas. El book siempre es el vivo del hub.

**Envío atómico (RNF-13, D-27).** Un `asyncio.Lock` por venue cubre revalidar →
enviar → apuntar la orden; la orden doble toma los dos en orden fijo. `prepare`
no lo toma.

### 6.3 Validaciones locales (RF-10, PA-06)

`RiskCheck` recibe la petición y un contexto inmutable (mercado, mid, book,
órdenes propias abiertas, balances) y devuelve la lista de violaciones; vacía
significa ok. Se evalúan todas, no solo la primera. `check(req, ctx, limits)` es pura
(`core/risk.py`); `RiskLimits` vive en `core` y `config.py` la reutiliza.

| Código | Regla | Config |
| --- | --- | --- |
| `order_size` | `qty × price > max_order_pct % × inventario total` (BTC + BTCB2 al mid, los dos venues; sin inventario calculable, rechazo) (D-19) | `risk.max_order_pct` |
| `mid_deviation` | `abs(price − mid) / mid × 100 > max_mid_deviation_pct` (el límite exacto se permite) | `risk.max_mid_deviation_pct` |
| `venue_exposure` | `BTC total + BTCB2 total × mid` tras la orden `> max_venue_exposure` (D-07) | `risk.max_venue_exposure` |
| `self_cross` | compra a precio ≥ mi ask abierto más bajo, o venta ≤ mi bid abierto más alto, en el mismo venue | — |
| `tick` / `lot` / `min_qty` / `min_notional` | precio o cantidad fuera de la rejilla o bajo el mínimo del mercado | `MarketSpec` |
| `stale_data` | sin mid (falta un lado o book cruzado), o book sin noticias en más de `stale_after_s`: cuenta la llegada (hora local, `L2Book.received_at`, RNF-16) del último mensaje del book o el último contraste con el REST que coincidió (`checked_at`, RNF-24); con el book en duda (`suspect`), no hay frescura. El latido de la conexión ya no cuenta (D-76, corrige D-13) | `risk.stale_after_s` |
| `insufficient_balance` | compra: `available(quote) < nocional × (1 + taker_fee)`; venta: `available(base) < qty` | — |
| `venue_error` | no se pudieron leer saldos u órdenes del venue para validar (D-16); lo pone `OrderService`, no `check` | — |
| `clock_skew` | solo en live: desfase de reloj con el venue sin medir o `> max_clock_skew_s` (D-30); lo pone `OrderService` | `risk.max_clock_skew_s` |

### 6.4 Orden doble (RF-07, PA-07, PA-08)

`dbl 0.01 nk 850 nx 900` → compra 0.01 en NonKYC a 850 y vende 0.01 en Neoxa a 900.

1. Ambas piernas pasan `RiskCheck`; si una falla, **no se envía ninguna**.
2. Una sola confirmación con los dos nocionales y el spread bruto y neto.
3. Envío con `asyncio.gather` (las dos a la vez).
4. Estado por pierna: `pending → open → partial → filled | cancelled | rejected`,
   o `unknown` si el envío quedó incierto, hasta que se reconcilia (RNF-12).
5. **Pierna colgada** (D-15): las cantidades llenas difieren y la pierna que va
   por detrás está `rejected` o `cancelled`, o el desequilibrio dura más de
   `hanging_leg_after_s` (el reloj empieza cuando las cantidades dejan de
   coincidir). Se muestra en rojo en el panel de piernas y se escribe
   `hanging_leg` en el journal una vez. No se hace nada automático.
6. Lógica pura en `core/double.py`; el flujo en `OrderService` (`prepare_double`,
   `confirm_double`, `on_private_event`, `check_doubles` cada segundo). Las
   órdenes llegan de `AccountService`, que en dry-run lee los `DryRunVenue`.

### 6.5 Fills y balances (RF-05, RF-09)

`AccountService` consume `private_stream` de cada venue. Al recibir un `Fill`:
journal, actualizar la pierna/orden, y pedir `balances()` de ese venue
inmediatamente. Además, refresco periódico cada `balances_every_s` (≤ 10 s).
Si un trade ya avisado vuelve con otra fee (NonKYC la rehace en NKYC, D-64), el
tracker emite `FeeUpdate`: journal `fill_fee`, sin tocar piernas ni saldos.

Al arrancar (D-68), los trades del primer refresco no se dan por historia en
silencio: el tracker los emite como `PastFill`. La cuenta, solo en live, los
compara con el journal de los últimos 7 días: un trade ya apuntado con otra fee
→ `fill_fee`; uno que no está y es de una orden nuestra (un `order_ack` del
journal con ese `order_id`) → `fill` con su `client_id`, `recovered: true` y
`trade_ts`; el resto (otros mercados, de antes del journal, fills de Neoxa sin
orden) se ignora. Ni piernas ni bot ven los `PastFill`.

Inventario neto = Σ BTCB2 (available + locked) en ambos venues, y lo mismo en
BTC; se muestra también valorado en BTC al mid de cada venue (`core/inventory.py`).
Los balances se piden también tras cualquier cambio de una orden propia (D-17).

Valor en el tiempo (RF-32, D-69): `ValueRecorder` (`services/valuation.py`)
espera al siguiente múltiplo UTC de `value_every_s`, calcula `inventory()` con
los saldos de `AccountService` y el mid del hub, y si el total existe apunta
`value` en el journal (`core/valuation.py` arma la muestra). `ResultsService`
lee esas filas con las de las dobles y las devuelve en `Report.values`, así que
la serie viaja al cliente con `results {range}` sin comando nuevo.

### 6.6 Panel cruzado (RF-04)

Por venue: `mid = (best_bid + best_ask) / 2`, `spread = best_ask − best_bid`
(abs y %). Entre venues, las dos direcciones ejecutables:

```
comprar en A, vender en B:  bruto = bid_B − ask_A
                            neto  = bid_B × (1 − taker_B) − ask_A × (1 + taker_A)
```

Con `fee_discount_pct` en un venue (RF-24, D-38), el panel usa
`taker × (1 − descuento)` y lo dice en su pie; el spec del hub no cambia, así
que RiskCheck y el tamaño por % siguen con la fee completa. El bot lo usa solo
con `fee_token` y su saldo (D-63, §6.9).
Se muestran en precio, sats y % sobre `ask_A`, y la neta positiva se resalta
en verde (T-14b, `CrossPanel` en la barra inferior, refrescado con los books). Ambos venues cotizan en BTC
(RNF-10), así que no hay conversión. Precios con 8 decimales: el panel muestra
también la diferencia en satoshis (`sats()`).

`core/cross.py`: `cross(a, b)` → `CrossView` con `stats` por venue (None si falta
un lado o el book está cruzado), diferencia de mids (`mid_b - mid_a`, % sobre
`mid_a`) y las dos rutas. Una ruta solo necesita el ask donde se compra y el bid
donde se vende, así que puede existir aunque al otro venue le falte un lado.

### 6.7 Fallos aislados (RNF-14, D-28)

`services/faults.py`: `Faults` es un registro común a hub, cuenta, órdenes y UI.
`report(where, exc, venue=…)` escribe `error` con `where`, `error` y `traceback`
(redactados por el journal), cuenta el fallo y avisa a la UI, que lo dice en rojo
y muestra `✖ N fallos` en la barra de estado durante toda la sesión.

- Hub: cada venue en `supervised` (backoff exponencial de `retry_s` a 60 s). Un
  bug marca su mercado `down` y se vuelve a cargar; el otro venue sigue.
- Cuenta: stream privado y saldos de cada venue, cada uno en `supervised`.
- Órdenes: sin `order_sent` no se envía (`Failed` local). Cualquier otra
  escritura que falle se reporta y sigue: la orden enviada cuenta igual.
  Cancelar no se deja de hacer porque falle el journal.
- UI: workers con `exit_on_error=False`; el comando, las confirmaciones, la
  reconciliación, cancelar y la revisión de dobles van envueltos: un bug se
  reporta como `ui` y la terminal sigue.

### 6.8 Salir (RNF-15, D-29)

Cada envío (`_send_now`) y cada cancelación por venue (`_cancel_in`) corre en su
tarea, protegida con `asyncio.shield` y apuntada en `OrderService._inflight`:
cancelar a quien la espera no la corta. Al desmontar, la app hace
`engine.aclose()` (`orders.drain()`, D-61) antes de cerrar el journal. `q` en live con órdenes abiertas
abre un `ConfirmScreen` (acción `quit`) con el número; por defecto no se sale.
En dry-run y sin órdenes sale directo.

### 6.9 Bot taker (fase 2: RF-18 a RF-23, RNF-22, D-31)

Se construye en M7, adelantado tras T-31 a petición del usuario (D-34). Reutiliza la orden doble (§6.4) entera: el
bot no envía nada que no pase por `OrderService.prepare_double` y
`confirm_double`, así que hereda RiskCheck, el lock por venue (RNF-13), las
inciertas y su reconciliación (RNF-12), la salida segura (RNF-15) y el journal.

Coste de `decide` (D-70, RNF-23): la profundidad hasta un precio sale de sumas
acumuladas con `bisect`, y los pares de referencias se podan por monotonía de la
neta; con 50 niveles y `max_walk_pct`, por debajo de 1 ms.

Movimiento brusco (D-71, D-72): `BotService` guarda `(hora, mid)` de cada venue en
cada cambio de book durante `move_window_ms`, más el último de antes, que es
el que regía al empezar la ventana (D-72); `VenueView.move_pct` es
`(máx − mín) / mín × 100` de esa ventana. Con `max_move_pct`, `decide` da
`Skip("volatile")` si algún venue lo pasa. `bot_fire` lleva `move` por venue.

**Estados** (`core/bot.py`, `BotState`):

```
off ──bot on──▶ armed ──cruce válido──▶ firing ──doble cerrada──▶ cooldown ──cooldown_s──▶ armed
                  ▲                       │
                  │                       └─ colgada · incierta · rechazo · error ──▶ halted
                  └──────────────bot on (manual)───────────────────────────────────────┘
cualquier estado ──bot off──▶ off      topes diarios / fallos seguidos ──▶ halted
```

**Decisión pura** (`core/bot.py`, RNF-22):

```python
decide(books: tuple[L2Book, L2Book], specs, balances, limits: BotLimits,
       state: BotState, now: int) -> Fire | Skip
Fire(buy_venue, sell_venue, buy_price, sell_price, qty, net, net_pct)
Skip(reason, detail)
```

Motivos de `Skip` en `decide`: `not_armed`, `cooldown`, `hour_cap`, `day_cap`,
`blocked` (un venue con book viejo, desfase o error: lo marca el servicio en
`VenueView.blocked`), `no_quotes`, `below_threshold`, `inventory_band`,
`lot_mismatch`, `min_notional`, y el término que limita la cantidad cuando no
llega al lote (`no_depth`, `no_balance`, `max_qty`, `day_cap`). `BotService`
añade `uncertain_open` y `double_open` antes de llamar a `decide`.

1. Para cada ruta de `core/cross.py`, las referencias de compra son los asks
   de A hasta `best_ask × (1 + max_walk_pct[A])`, y las de venta, los bids de
   B hasta `best_bid × (1 − max_walk_pct[B])` (D-60; walk 0 = solo el mejor
   nivel). Para cada par de referencias: `buy_price = quantize_price(ref_A ×
   (1 + max_slippage_pct), buy)` (hacia arriba), `sell_price = quantize_price(
   ref_B × (1 − max_slippage_pct), sell)` (hacia abajo).
2. Neta pesimista con esos precios: `sell_price × (1 − taker_B) − buy_price ×
   (1 + taker_A)`, en % sobre `buy_price`. Si `< min_net_pct` → `Skip`.
   Aquí, y solo aquí, `taker` es `VenueView.net_fee` cuando el servicio lo
   pone: la fee con `fee_discount_pct` de un venue con `fee_token` cuyo
   disponible es ≥ `fee_token_min` (`core/bot.py`, `token_fee`, D-63). La
   cantidad (paso 3) y `inventory_veto` siguen con la fee completa del spec.
3. `qty = min(Σ asks_A ≤ buy_price, Σ bids_B ≥ sell_price, BTC_A disp. /
   (buy_price × (1 + taker_A)), BTCB2_B disp., max_qty)`, al lote hacia abajo;
   bajo el lote mínimo → `Skip(no_depth | no_balance)`. El tope de RF-10 lo
   aplica `prepare_double` después.
4. De los pares que valen, el de mayor neta en BTC. Si no vale ninguno, el
   motivo es el del par de los mejores precios; si ese no llega al mínimo y uno
   más profundo sí llega pero no alcanza el umbral, `below_threshold` con su
   neta.
5. Si las dos rutas valen, la de mayor neta en BTC.

**Bucle** (`services/bot.py`, `BotService`):

- Se suscribe a `MarketHub` y `AccountService`; en cada cambio de book, si está
  `armed`, comprueba las condiciones de RF-19 que no son de precio (frescura con
  `OrderService.fresh_at`, la misma de `stale_data`, `clock_skew_ms`, `Faults` del venue, inciertas abiertas,
  cualquier doble sin terminar, también manual) y llama a `decide`. Los avisos
  de hub y cuenta solo ponen un `asyncio.Event`: se evalúa una vez por vuelta
  aunque lleguen varios mensajes.
- `Fire` → `firing`, `bot_fire` en el journal, `prepare_double` +
  `confirm_double` sin diálogo (el armado fue la confirmación, RF-18). Si
  `prepare_double` rechaza, cuenta como fallo y vuelve a `armed`.
- Tras `fill_timeout_ms` (o antes, si una pierna se rechaza o queda incierta),
  cancela con `OrderService.cancel_double` lo que quede abierto y espera hasta
  `settle_s` (15 s) a que las piernas cierren. Con la doble cerrada: si las cantidades llenas coinciden →
  `cooldown`; si no, o hubo `Uncertain`/rechazo/error → `halted` (RF-21).
  Pierna colgada = `core/double.py` (`hanging`), pero sin esperar a
  `hanging_leg_after_s`: al cancelar el resto ya se sabe.
- Con `lead_venue` (D-75), el envío va en dos tiempos (abajo).
- Corre bajo `supervised` (§6.7): un bug pasa el bot a `halted`, nunca reintenta
  disparos.
- Contadores (disparos por hora, BTC comprado en el día UTC, fallos seguidos)
  en memoria; al arrancar se reconstruyen del journal del día (`bot_fire`).

**Primera pierna** (`lead_venue`, D-75): Neoxa casa la orden al recibirla y su
respuesta ya dice si llenó (`status` y `quantity_filled` de `POST
/exchange/order`). Con `lead_venue`:

1. `prepare_double` valida las dos piernas como siempre; `bot_fire` lleva
   `lead`. Los contadores de RF-22 cuentan el disparo como hoy.
2. `OrderService` envía solo la pierna de `lead_venue`; la otra queda en la
   doble como `held` (preparada, sin enviar).
3. Según el ack de la primera:
   - `filled` → la otra se envía con `qty = filled`, al precio de `Fire`.
   - llena en parte → `cancel_double` de la primera al momento; la otra con
     `filled` al lote del otro venue hacia abajo. Por debajo de su lote o
     nocional mínimo, la otra pasa a `skipped` y es pierna colgada (`halted`).
   - `open` sin nada lleno → `cancel_double` al momento. Si se cancela, la otra
     pasa a `skipped`, `bot_miss` en el journal (`double`, `venue`, `price`,
     `qty`) y `cooldown`, sin sumar a `max_consecutive_failures`. Si la
     cancelación falla porque ya llenó, se espera (hasta `settle_s`) a que la
     cuenta la traiga cerrada (en Neoxa, por polling) y se sigue como `filled` o
     llena en parte.
   - rechazo, error o incierta → la otra pasa a `skipped`, `halted` (RF-21).
4. Con la otra pierna enviada, sigue el flujo de siempre: `fill_timeout_ms`,
   cancelar el resto, `settle_s` y veredicto.

En `core/double.py`, una pierna `skipped` cuenta como cerrada con 0 lleno:
la doble está terminada cuando la primera lo está y la otra es `skipped`, y
`hanging` compara lo lleno de las dos (0 contra 0 no cuelga). La pierna `held`
no se cancela ni se reconcilia (nunca salió). La doble manual (`dbl`) no cambia.

**Config** `[bot]` (validación estricta como el resto, D-05):

| Clave | Ejemplo | Para |
| --- | --- | --- |
| `enabled` | `false` | permitir armar en live (RF-18) |
| `min_net_pct` | `"0.5"` | umbral de neta pesimista (RF-19) |
| `max_slippage_pct` | `"0.2"` | margen de las límites agresivas (RF-20) |
| `max_walk_pct` | `{ nk = "0.8" }` | opcional, por venue: cuánto se aleja del mejor precio el nivel de referencia (RF-20, D-60) |
| `max_qty` | `"100"` | BTCB2 por disparo (RF-20) |
| `fill_timeout_ms` | `3000` | cancelar el resto (RF-20) |
| `lead_venue` | `"nx"` | opcional: venue de la primera pierna; la otra sale solo si esta llena (RF-20, D-75) |
| `cooldown_s` | `10` | entre disparos (RF-22) |
| `max_trades_per_hour` | `20` | RF-22 |
| `max_daily_btc` | `"0.01"` | nocional comprado por día UTC (RF-22) |
| `max_consecutive_failures` | `3` | → `halted` (RF-22) |

Neoxa permite 10 órdenes cada 10 s (RNF-03): un disparo usa una orden y hasta
una cancelación por venue, así que el cooldown mínimo aceptado es 2 s. La
validación pide además `min_net_pct > 0` y `fill_timeout_ms ≥ 100`
(`core/bot.py`). `settle_s` (15 s) es una constante de `services/bot.py`, no una
clave de la config.

**Topes y `halted` (RF-22, D-55):** el tope diario para el bot (`capped`,
motivo `day_cap: …`) cuando un disparo lo agota (`finished`) y también cuando
`decide` da `Skip("day_cap")`: el tope ya gastado al evaluar (tras `bot on` el
mismo día o tras reiniciar, con los contadores del journal) o menos de un lote
de presupuesto. `hour_cap` solo espera: no para el bot.

**Ratio de inventario (RF-26, D-46):** con `[inventory]`, `decide` recibe un
`InventoryTarget` y cada `VenueView` lleva BTC y BTCB2 totales y el mid. Tras
calcular la cantidad de una ruta, se simula la doble (compra en A: +qty BTCB2,
−qty × buy_price × (1 + taker_A) BTC; venta en B: −qty BTCB2, +qty × sell_price ×
(1 − taker_B) BTC) y se calcula el % de BTCB2 de cada venue antes y después,
valorado al mid (o al precio límite de la pierna si no hay mid). Si para algún
venue el % después está fuera de [objetivo − banda, objetivo + banda] y
|después − objetivo| > |antes − objetivo|, la ruta es `Skip("inventory_band")`.
Un venue sin valor (todo a cero) no se comprueba.

| clave `[inventory]` | ejemplo | |
| --- | --- | --- |
| `target_base_pct` | `"25"` | % del valor del venue en BTCB2 |
| `band_pct` | `"10"` | puntos de margen a cada lado |

**UI (D-37, D-39):** comando `bot on|off|status`; `ctrl+b` lo para al momento desde
cualquier sitio, también escribiendo en la línea de comandos (`p`, solo sin foco en ella).
`bot on` abre un `ConfirmScreen` (acción `bot`) con los parámetros y el modo; en
live sin `enabled` se rechaza sin diálogo. El estado ocupa el texto del banner de
modo (`LIVE · BOT armado`, `DRY-RUN · BOT ✖ PARADO` en rojo); el motivo de la
parada sale en rojo como aviso y en `bot status`, que también dice por qué no
dispara y lo gastado hoy.

### 6.10 Resultados (RF-25, D-44)

`core/results.py`, puro: recibe las líneas del journal como texto (las lee
`services/results.py`), las parsea (`read_rows`) y devuelve un `DoubleResult`
por doble y un `ResultsSummary`.

```
order_sent(client_id, double, side, venue) ─┐
fill(client_id, qty, price, fee, fee_asset) ─┼─▶ piernas por double ─▶ DoubleResult
bot_fire(double, net) / hanging_leg(double) ─┘
```

- Origen `bot` si hay `bot_fire` con ese `double`; si no, `manual`.
- Por pierna: llenado = Σ qty, precio medio = Σ qty·price / llenado, fees en BTC
  (fee en BTCB2 × price de su fill).
- `casada = min(llenado_compra, llenado_venta)`;
  `resultado = casada × (medio_venta − medio_compra) − fees − fees_token`. Las
  fees son las de todos los fills de la doble (la parte descuadrada también pagó
  la suya). `fees_token` = Σ cantidad × precio de las fees en un activo con
  precio en `compute(prices=)` (D-65); las que no tienen precio van aparte.
- `descuadre = llenado_compra − llenado_venta` (BTCB2, con signo), sin valorar.
- Estado: `colgada` si hay `hanging_leg` y las piernas siguen sin igualarse
  (una falsa colgada que luego se llena, como D-41, queda `cerrada`); `sin
  fills` si una pierna no tiene fills; si no, `cerrada`. Solo las `cerrada` suman.
- Asignar un fill a su orden (D-45): por `client_id`; si no, por `order_id` vía
  `order_ack`; si no trae ninguno (Neoxa), a la única orden del mismo venue y
  lado enviada antes, con precio dentro de su límite y cantidad pendiente
  suficiente; si hay varias, a la única enviada en el último minuto. Si no,
  queda sin asignar y el resumen lo cuenta. Fills repetidos (mismo `trade_id`)
  cuentan una vez. Un `fill_fee` (D-64) sustituye la fee de su trade.
- Se ignoran las líneas `mode != "live"` y las que no se pueden leer (se cuentan
  y se dice cuántas).

`services/results.py` lee los `journal-YYYYMMDD.jsonl` del rango (día UTC, 7
días o todo) más el día anterior, para que una doble que cruza medianoche tenga
sus envíos; la doble cuenta en el día en que empezó. Con la pantalla abierta,
recalcula en cada `fill` nuevo y cada 60 s (`RESULTS_EVERY_S`, D-69), sin
redibujar si no cambió y sin mover la fila seleccionada. La única petición es el precio del `fee_token`
de cada venue (`token_price`, mid público de `<token>/BTC`), con caché de 60 s,
al abrir y al cambiar de rango (D-65).

**UI:** `ResultsScreen` (tecla `r`, comando `results [dia|7d|todo]`, `Esc`
cierra, `1/2/3` cambian el rango). Arriba, gráfica plotext: línea del acumulado
en sats y barras verde/rojo por doble. Debajo, resumen (dobles, total BTC y
sats, media, mejor, peor, colgadas aparte) y tabla: hora UTC, origen, ruta
(`nx→nk`), cantidad casada, neta esperada (solo bot), resultado, descuadre.

### 6.11 Direcciones de depósito (RF-28, D-50)

- `core/models.py`: `DepositAddress(venue, asset, ticker, network, address,
  memo, min_deposit, confirmations)`; `asset` es el del par (`BTC`, `BTCB2`),
  `ticker` el que usa el venue (`BTC-MAIN`, `btcb2`…).
- Adaptadores: `deposit_address(asset) -> DepositAddress` (protocolo
  `Depositor` en `adapters/base.py`, aparte de `Venue` para no tocar
  `DryRunVenue`). NonKYC pide `getdepositaddress/{ticker}` y comprueba que el
  `ticker` de la respuesta es el pedido; Neoxa pide `deposits/addresses` (trae
  todas las cadenas; se pide una vez por activo) y da error si la cadena está en
  `closedChains` o no tiene dirección. Dirección vacía → `VenueError`.
- `services/deposits.py`: `DepositService(venues)` con los adaptadores reales
  de los venues con keys, en cualquier modo (solo lee). `load()` pide los dos
  activos de cada venue en paralelo y devuelve una fila por (venue, activo) con
  la dirección o el error. Sin caché.
- `DepositScreen` (tecla `w`, comando `deposit`): tabla venue · activo
  (`XBT (BTCB2)`) · red · dirección completa · mín. · conf.; las filas con
  error en amarillo. Enter o `c` copia la dirección seleccionada y lo dice;
  `r` vuelve a pedir; Esc cierra.
- Copiar (D-52): `ui/clipboard.py`, `system_copy(text)` prueba `wl-copy` (con
  `WAYLAND_DISPLAY`), `xclip -selection clipboard` y `xsel --clipboard --input`
  (con `DISPLAY`), los que estén en el `PATH`, con el texto por stdin y salida a
  `/dev/null` (las tres se quedan en segundo plano sirviendo la selección; con
  la salida en un pipe la espera no acabaría). Devuelve el nombre de la que
  salió bien o `None`. La app la recibe inyectada (`clipboard=`, guardada en `system_clipboard`: `App.clipboard` ya es de Textual): `build_app`
  pasa la real y los tests ninguna, para no tocar el portapapeles de quien los
  corre. OSC 52 se manda siempre además. El clic en el book (RF-16) usa lo
  mismo.
- QR (D-52): `ui/qr.py`, `qr_lines(text)` con `segno` (error `L`, sin micro QR,
  margen 2) y medio bloque `▀▄█`: dos módulos por carácter en vertical, negro
  sobre blanco con colores explícitos (un tema oscuro no lo invierte). Una
  dirección `bc1q…` da 33 × 17 caracteres. Panel a la derecha de la tabla que
  sigue a la fila resaltada.

### 6.12 Plan de reequilibrio (RF-29, D-53)

- `core/models.py`: `WithdrawalFee(venue, asset, ticker, fee, minimum, active,
  on_top)`; `on_top` = la comisión va aparte de lo pedido. Neoxa lo pone a
  `True` (su web: se piden 0.2, llegan 0.2, se descuentan 0.2001); NonKYC a
  `False` (supuesto, D-53).
- Adaptadores: `withdrawal_fee(asset) -> WithdrawalFee` (protocolo
  `WithdrawalFees` en `adapters/base.py`, como `Depositor`). NonKYC: público
  `asset/getbyticker/{ticker}` con los tickers de depósito (`BTC-MAIN`,
  `BTCB2`); comprueba el ticker, `withdrawFee` obligatorio, `minimumWithdraw`
  opcional, cerrado si `withdrawalActive` es falso o `isMaintenance`. Neoxa:
  privado `withdrawals/fees`, la entrada con `token` = activo y `chain` = la de
  `DEPOSIT_CHAINS`; `withdrawalFee` y `minWithdrawal` pueden venir como número
  o texto. Falta la entrada o la comisión → `VenueError`.
- `core/rebalance.py` (puro): `Holding(btc, btcb2, btc_free, btcb2_free,
  price)` por venue (totales para el %, disponibles para lo que se puede enviar;
  `price` = mid). `plan(holdings, fees) -> Plan` con `before`, `total_pct` y las
  dos `Transfer(asset, source, dest, amount, fee, received, debited, cost_btc,
  after, problem)`; `amount` es lo que se pide en el formulario. Con A el venue
  de más % y B el otro (precios p, BTC q, BTCB2 b; iguales los % ⇔ iguales
  base·p/q), fo = comisión si va aparte y fd = comisión si se descuenta (la
  otra, 0); sale `amount + fo`, llega `amount − fd`:
  - BTCB2 de A a B: `x = ((b_A − fo)·p_A·q_B − (b_B − fd)·p_B·q_A) / (p_A·q_B + p_B·q_A)`.
  - BTC de B a A: `y = (b_A·p_A·(q_B − fo) − b_B·p_B·(q_A − fd)) / (b_A·p_A + b_B·p_B)`.
  El disponible se compara con lo que sale. Con la comisión aparte, x ≤ 0 con
  % distintos quiere decir que la comisión pasa de lo que hay que mover.
  Cantidad hacia abajo a 1e-8. Sin comisión, se calcula con 0 y queda no
  viable. `best` = la viable de menor `cost_btc`. `break_even(cost, qty, price,
  net_pct)` = dobles (hacia arriba) a la neta mínima para pagar la comisión.
- `services/rebalance.py`: `RebalanceService(venues)` con los adaptadores
  reales de los venues con keys, en cualquier modo (solo lee); `load()` pide
  las comisiones de los dos activos de cada venue en paralelo y devuelve
  `{(venue, activo): WithdrawalFee | str}` (el texto es el error).
- `RebalanceScreen` (tecla `e`, comando `rebalance`): saldos y % de cada venue y
  del total, las dos alternativas en una tabla (viable o el motivo), la
  recomendada, las dobles que la pagan con `[bot]` y el aviso de banda total con
  `[inventory]`. Los saldos y mids son los de la app al abrir o con `r`, que
  también vuelve a pedir las comisiones. Esc cierra.

### 6.13 Daemon y cliente (RF-30, RF-31, D-61, D-66)

Dos formas de arrancar el mismo binario:

| Comando | Qué corre | Lock (RNF-17) | Journal | Venues |
| --- | --- | --- | --- | --- |
| `xbt daemon [--live]` | `Engine` + servidor de socket, sin Textual | sí | escribe | sí |
| `xbt` (alias `attach`) | TUI cliente | no | no | no |

- `services/engine.py`: `Engine` agrupa hub, cuenta, órdenes, bot, faults,
  resultados, depósitos y reequilibrio, y corre `check_doubles` cada 1 s,
  `reconcile` con inciertas, el desfase de reloj y, al cerrar, `drain()` y
  `aclose()`. Enlaza los eventos de la cuenta con las piernas
  (`account.subscribe_events(orders.on_private_event)`, D-62). Emite
  `Notice(text, error)`. Sin keys, solo hub (solo lectura).
- `services/daemon.py`: `asyncio.run` del `Engine` y del servidor; SIGTERM o
  SIGINT → `bot.disarm()`, `orders.drain()`, cierre. Avisos al diaglog, a los
  clientes y los de error al journal (`notice`).
- `services/control.py`: servidor `asyncio.start_unix_server` en
  `<datos>/xbt.sock` (o `--socket`), umask `0177`; un socket viejo se borra
  (el daemon ya tiene el lock). La ruta no puede pasar de 107 bytes. Cola
  acotada por cliente: el que no lee se desconecta, nunca frena al `Engine`.
  Todo lo que sale pasa por el `Redactor`. Cada petición corre en su tarea.
- Protocolo (`services/wire.py`), JSON por líneas, `Decimal` como texto:
  - Del daemon, sin pedirlo: `hello {mode, version, view}`, donde `view` es lo
    que la pantalla necesita de la config (niveles del book, fees y descuentos
    por venue, objetivo de inventario, límites del bot), nunca keys; `state` con el
    `Snapshot` (bot, cuentas, dobles, colgadas, inciertas, en vuelo, fallos,
    `can_trade`) al conectar, tras cada cambio (agrupado 20 ms) y cada 1 s;
    `notice`; `fault`; mercado: `market_init {venue, spec, book, candles,
    last_trade, conn, ready, error}` al conectar, `book {venue, snapshot}`
    agrupado ~100 ms con los niveles que pinta la pantalla, `trade {venue,
    trade}` y `conn {venue, state}`.
  - Del cliente: `{"id", "cmd", "args"}`; el daemon contesta `reply {id, kind,
    value}` o `reply {id, error}`. Comandos: `ping`, `bot_on`, `bot_off`,
    `prepare {PlaceOrder}` → `Pending` + `token` | `Rejected`; `confirm
    {token}` → `Sent | Failed | Uncertain | Rejected`; `prepare_double
    {DoubleOrder}` / `confirm_double {token}`; `plan_cancel {CancelOrders}` /
    `execute_cancel {token}`; `results {range}` → `Report`; `deposits` →
    `[DepositRow]`; `withdrawal_fees` → comisiones de retiro.
  - Propuestas (`Pending`, `PendingDouble`, `CancelPlan`) guardadas en el
    daemon por `token` 60 s, de un solo uso; uno caducado, usado o desconocido
    se contesta con error. Nunca viajan `Settings` ni credenciales.
- `services/remote.py` (cliente): `RemoteEngine` (conexión, reconexión,
  peticiones con `id` y futuros; `DaemonGoneError` a las pendientes si cae),
  `RemoteMarket` (misma forma que `MarketHub.state`, velas con
  `CandleBuilder`), `RemoteAccount`, `RemoteBot`, `RemoteOrders` (misma cara
  async que `OrderService`), `RemoteResults`, `RemoteDeposits` y
  `RemoteRebalance`.
- `ui/app.py` recibe las caras remotas y un `Runner` (avisos, fallos y bucles):
  en el cliente, un `Engine` sin órdenes sobre `RemoteMarket`; en los tests de
  pantalla, el de verdad (`tests/ui/local.py`).
- `xbt`: `probe` pide `hello` antes de abrir la pantalla (sin daemon, sale
  con el motivo). Log propio `client.log`. Ni lock, ni journal, ni config de
  venues, ni keys. `q` desconecta sin confirmación y avisa de que el daemon
  sigue.

## 7. Journal (RF-11)

`<datos>/journal-YYYYMMDD.jsonl` (§8, por defecto `~/.local/state/xbt`), una
línea por evento, abierto en modo append y `flush` tras cada línea. Directorio
`0700` y fichero `0600`, también si ya existían con más permisos (RNF-18).

```json
{"ts":"2026-10-19T21:04:11.532Z","mode":"dry-run","venue":"nk","event":"order_sent",
 "client_id":"t-3f9a1c","data":{"side":"buy","qty":"0.01","price":"850.00"}}
```

`event ∈ order_sent | order_ack | order_error | order_unknown | reject_local |
cancel_sent | cancel_ack | fill | hanging_leg | conn | error | bot_state | bot_fire |
bot_miss | notice | fill_fee` (`bot_state`, `bot_fire` y `bot_miss`, fase 2: §6.9; `notice`, avisos de error del
daemon: §6.13; `fill_fee`, la fee corregida de un fill ya apuntado: D-64). Decimales serializados como
string. Un evento fuera de la lista es `ValueError`; un tipo no serializable,
`TypeError`, y la línea se codifica entera antes de escribir, así que un error
nunca deja media línea. Fecha del fichero y `ts` en UTC.

El Redactor se aplica a cada string (valores y claves) *antes* de codificar a
JSON: sobre la línea ya codificada, un secreto con `"` o `\` aparecería escapado
y no coincidiría.

## 8. Configuración y secretos (RF-12, RF-13, RNF-08)

- `config.toml` (copiado de `config.example.toml`): pares, fees, límites de
  riesgo, cadencias, niveles de book. Validado al arrancar; un error para la app.
  Validación estricta (D-05): importes como string, clave desconocida = error,
  `balances_every_s ≤ 10` (RF-05) y límites de Neoxa ≤ 80% de lo publicado (RNF-03).
  `risk.max_clock_skew_s` es obligatoria (D-30); `[log]`, `[bot]`, `[inventory]`
  y `fee_discount_pct` son opcionales; una sección `[dryrun]` que quede de la
  v0.1 es error (D-16).
- `.env`: `NONKYC_API_KEY`, `NONKYC_API_SECRET`, `NEOXA_API_KEY`, `NEOXA_API_SECRET`.
- Rutas (`paths.py`, RNF-20, D-32): sin flags, `config.toml` y `.env` en
  `$XDG_CONFIG_HOME/xbt` (`~/.config/xbt`) y los datos (journal y lock) en
  `$XDG_STATE_HOME/xbt` (`~/.local/state/xbt`). `--config`, `--env` y
  `--data-dir` las cambian; una relativa se resuelve contra el cwd al arrancar.
- Permisos del `.env` (RNF-18): con cualquier permiso para grupo u otros, en live
  no se arranca (`ConfigError`) y en los demás modos se avisa en rojo al montar
  la app (`Settings.warnings`).
- Instancia única (RNF-17): `InstanceLock` hace `flock` exclusivo sobre
  `<datos>/xbt.lock` antes de montar la app, en todos los modos, y lo suelta
  al salir. La segunda instancia sale con el pid de la primera.
- Log de diagnóstico (`diaglog.py`, RNF-19, D-33): `<datos>/diag.log`, 1 MB × 5
  ficheros, `0600`, UTC. Logger `xbt_terminal` (cada módulo con
  `getLogger(__name__)`), sin propagar al root y con un `NullHandler` desde la
  importación, para que nada llegue a stderr. El formatter pasa la línea entera,
  traceback incluido, por el `Redactor`. Nivel en `[log] level` (opcional,
  `info`). Apuntan: el supervisor (WS conectado/caído con la causa y el
  reintento, polling REST de respaldo), el hub (carga del mercado), la cuenta
  (errores de saldos y del canal privado, uno por racha) y `Faults.report` (cada
  fallo con traceback).
- Las claves se cargan en un tipo `Secret` cuyo `__repr__` y `__str__` devuelven
  `***`; el valor solo se extrae con `.reveal()` dentro del adaptador al firmar.
- El journal y el log de errores pasan por un `Redactor` que reemplaza cualquier
  aparición literal de un secreto cargado (defensa en profundidad para CA-07).
- Modo efectivo:

| Flag | Keys en `.env` | Modo |
| --- | --- | --- |
| — | no | `read-only` (sin comandos de orden) |
| — | sí | `dry-run` (por defecto, siempre) |
| `--live` | sí | `live`, con banner rojo permanente |
| `--live` | no | error al arrancar |

Las keys son por venue (D-04): con keys de un solo venue el modo es dry-run o
live, pero `Settings.can_trade(venue)` es falso para el otro, que queda en solo
lectura. Una key sin su secret es error.

## 9. Pantalla (RNF-02)

Grid de Textual 2 columnas × 3 filas; mínimo 160×45. Si la terminal es más
pequeña se muestra un aviso en lugar del layout. Filas: velas `1fr`, book
`N + 4` en escalera horizontal (compras | ventas), centrada, con barras de
profundidad acumulada a los lados en el ancho que sobre (D-49), barra inferior
y comandos (D-12). Encima de todo, una línea de estado (T-15):
- Por venue: `ws` o `respaldo`, `●`/`◌`/`✖`, la latencia y el canal privado.
- El modo, con banner rojo en `live`.
- Un aviso si un estado "up" deja de renovarse en 15 s.

Atajos: `b` buy, `s` sell, `d` doble, `o` órdenes abiertas, `x` cancelar todo
(con confirmación), `1/2/3/4/5` timeframe 1m/5m/1h/4h/1d, `r` resultados (§6.10),
`w` direcciones de depósito (§6.11), `e` plan de reequilibrio (§6.12), `p` parar
el bot sin foco en la línea y `ctrl+b` desde cualquier sitio (§6.9, D-39), `Esc`
suelta la línea de comandos, `q` salir. En la lista de órdenes: `c` cancela la
seleccionada, `x` todas las de la lista, `Esc` cierra. Las ventanas de
resultados, depósitos y reequilibrio se cierran con `Esc` o con su propia tecla.
`?` (o `help`) abre la ayuda. Clic en un nivel del book (RF-16, D-18): con la
línea vacía escribe `buy` (clic en ventas) o `sell` (clic en compras) en ese
venue con ese precio y la cantidad del nivel (la acumulada si el clic es en la
columna de acumulado); con texto, inserta solo el precio en el cursor.

Barra inferior (D-17, D-67): panel cruzado; una fila de 5 con los balances a
la izquierda (una línea por venue, con su % de BTCB2 coloreado según
`[inventory]` (RF-27) y `+ en órdenes` solo si hay algo en órdenes; el
inventario neto y el % total en el título; oculto en solo lectura) y, a la
derecha, las piernas de la última orden doble, del bot o manual (64 columnas;
una línea por pierna sin el id de orden, que está en `o`, y otra de estado;
oculto hasta la primera, y entonces los balances ocupan todo el ancho); y la
línea de comandos. Lo que no cabe en una línea se corta con `…`.

Comandos: `buy|sell <venue> <qty|N%> <price>` (`N%`: porcentaje del saldo
total del venue, BTC al comprar con la fee taker descontada, BTCB2 al vender,
hacia abajo al lote; RF-17, D-21), `dbl <qty> <venue> <price> <venue> <price>`,
`orders [venue]`, `cancel <order_id>`, `cancel <venue>`, `cancel all`, `tf <1m|5m|1h|4h|1d>`,
`bot on|off|status` (§6.9), `results [dia|7d|todo]` (§6.10), `deposit` (§6.11),
`rebalance` (§6.12) y `help` (o `?`).

El parser (`core/commands.py`) solo traduce texto a comandos: no cuantiza ni
aplica límites, eso es de RiskCheck. Rechaza venue desconocido, números que no
sean decimales simples (`1e-2`, `NaN`, coma decimal) y cantidades o precios ≤ 0.
Comandos y venues sin distinguir mayúsculas; los ids de orden sí las distinguen.

## 10. Estrategia de pruebas

| Nivel | Qué | Herramienta |
| --- | --- | --- |
| Unit | `core/*` | pytest, sin red |
| Adaptador | parseo de respuestas reales grabadas en `tests/fixtures/` | pytest + respx |
| Resiliencia | caída y vuelta de WS | servidor `websockets` local en el test |
| UI | layout, render de paneles, que no aparezcan secretos | `textual` `App.run_test()` |
| Aceptación | CA-01…07 contra los venues reales con tamaños mínimos; CA-10 (bot en real) | manual, checklist en `tasks.md` T-20 (T-38, ya cerrada, en `specs/archive/tasks-done.md`) |
| Aceptación automática | CA-08, CA-09 y CA-11…14 con books, journals y respuestas sintéticos | pytest |

Cada test lleva `@pytest.mark.spec(...)` con los IDs que verifica.
