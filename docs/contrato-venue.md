# Contrato de venue

Qué tiene que cumplir un adaptador para que xbt-terminal opere en un venue (RF-36,
D-82). La interfaz está en `src/xbt_terminal/adapters/base.py`; este documento dice
lo que los tipos no pueden decir. La batería de `tests/contract/` comprueba cada
cláusula, y un test exige que la tabla de abajo y la batería coincidan.

Cambiar una cláusula es cambiar la spec: pide versión nueva de `requirements.md`.

## Interfaz

### `MarketData`: la parte pública

| Miembro | Qué promete |
| --- | --- |
| `id` | El `VenueId` del venue. Todo lo que devuelve el adaptador lleva este `venue`. |
| `market()` | Las reglas del par, leídas del venue: nunca fijas en el código. Si el venue no las da (fees en NonKYC), las pasa la config. |
| `book_snapshot(depth)` | Book entero por REST. Pide `depth` niveles, pero no promete recortar a `depth`. |
| `recent_trades(limit)` | Trades públicos recientes, en orden cronológico. El tope real es el del venue, y no promete recortar a `limit`. |
| `candles(tf, limit)` | Velas de `1m`, `5m`, `1h`, `4h` y `1d`, de vieja a nueva. Si el venue no da una, se construye (NonKYC, `1m`). |
| `market_stream()` | Generador asíncrono de `BookSnapshot`, `BookDelta`, `Trade` y `ConnState`. Oculta si los datos llegan por WS o por REST: se reconecta y cambia de fuente solo. Un fallo de red no lo termina; un bug se relanza. |

### `Venue`: la parte privada

Amplía `MarketData`. Sin credenciales, cada método lanza `ReadOnlyError` sin hacer
ninguna petición.

| Miembro | Qué promete |
| --- | --- |
| `balances()` | Saldos por activo. `locked` incluye lo comprometido en órdenes. |
| `open_orders()` | Solo órdenes `open` o `partial` del par, aunque el venue traiga más. |
| `place_limit(req)` | Crea una orden límite y devuelve la `Order` con el `client_id` de `req`. Los números viajan como texto exacto. |
| `cancel(order_id)` | Cancela una orden. Un fallo lanza `VenueError`. |
| `cancel_all()` | Cancela todas las órdenes del par. Si el venue no sabe, una a una. |
| `private_stream()` | Generador de `Order`, `Fill`, `FeeUpdate`, `PastFill` y `ConnState` del canal `private`. Los trades que ya estaban al arrancar son historia: `PastFill`, nunca `Fill`. |
| `keeps_client_id` | `True` si el venue devuelve el `client_id` de cada orden. Con `False`, un envío incierto se reconcilia por lado, precio y cantidad. |
| `recent_orders(since)` | Órdenes que pueden ser la de un envío incierto: las activas y las recién terminadas. `since` es una pista: el que llama aplica la ventana. |

### `FeedUpkeep`: mantenimiento

Lo que usan los servicios de un adaptador de venue real, aparte de `Venue` porque
`DryRunVenue` y `ReplayVenue` son venues sin esto. Sin ello el daemon arranca, pero
en live no mide el desfase de reloj ni recupera un book malo: es obligatorio.

| Miembro | Qué promete |
| --- | --- |
| `clock_skew_ms` | Venue menos local, en ms, según la cabecera `Date` de la última respuesta (RNF-16). `None` hasta tener una. |
| `resync()` | El book local no coincide con REST: pide uno nuevo y lo entrega como `BookSnapshot` (RNF-24). |
| `aclose()` | Cierra lo que abrió el adaptador. |

### Opcionales

Un adaptador los implementa si el venue lo permite. La pantalla los usa si están.
La batería exige lo que declare el `Harness` en `implements`, y que declare la verdad.

| Protocolo | Miembro | Qué promete |
| --- | --- | --- |
| `Depositor` | `deposit_address(asset)` | Dirección de un activo del par (RF-28). Vacía, de otro activo o de cadena cerrada es `VenueError`: mejor ninguna dirección que una mala. |
| `WithdrawalFees` | `withdrawal_fee(asset)` | Comisión de retiro (RF-29), y si se cobra aparte (`on_top`). |
| `TokenPricer` | `token_price(asset)` | Precio en BTC del token de fee (RF-25). |

## Errores

| Situación | Qué se lanza |
| --- | --- |
| Sin credenciales | `ReadOnlyError` |
| El venue responde error, o algo que no se entiende | `VenueError` |
| Fallo de red en una petición | `httpx.HTTPError`, tal cual. Los servicios capturan los dos |
| Envío rechazado (4xx, error del venue) o que no llegó a conectar | `VenueError` o `httpx.HTTPError`: la orden **no** existe |
| Envío sin respuesta clara (timeout tras conectar, 5xx, 2xx que no es JSON u orden ilegible) | `UncertainError`: la orden **puede** existir |

Confundir las dos últimas filas es el error más caro que puede tener un adaptador:
una orden real que se da por no enviada, o un envío bueno que queda en duda.
`StreamingVenue.sending` ya hace la distinción.

## Cláusulas

Cada cláusula es una comprobación de `tests/contract/battery.py`.

| Comprobación | Cláusula |
| --- | --- |
| `satisfies_protocols` | Cumple `Venue` y `FeedUpkeep`, y sin keys, `MarketData`. Declara la verdad sobre los opcionales. |
| `market_rules` | `MarketSpec` coherente, de su venue, con cotización en BTC (RNF-10) y fees entre 0 y 1. |
| `market_garbage_is_venue_error` | Una respuesta ilegible es `VenueError`, nunca un `KeyError` suelto. |
| `book_is_ordered` | Bids de mayor a menor y asks de menor a mayor, sin precios repetidos, cantidades positivas, sin cruzar y con hora en ms. |
| `trades_are_chronological` | Trades de viejo a nuevo, con lado, precio y cantidad válidos y hora en ms. |
| `candles_every_timeframe` | Velas en los cinco timeframes, sin repetir, de vieja a nueva y con `low` y `high` coherentes. |
| `stream_reports_connection` | El stream emite `ConnState` de canal `market` con estado `up`. |
| `stream_book_chains` | Empieza con un snapshot, y con `seq` los deltas son consecutivos, aunque el venue los mande desordenados. |
| `stream_repeats_no_trade` | Una reconexión no repite trades ya entregados. |
| `stream_falls_back_to_rest` | Sin WS avisa de la caída y sigue por REST, con `ConnState` de fuente `rest`. |
| `stream_closes_cleanly` | Cerrar el stream no deja tareas vivas. |
| `resync_brings_a_snapshot` | `resync()` hace llegar un `BookSnapshot` nuevo por el stream. |
| `clock_skew_from_date` | `clock_skew_ms` sale de la cabecera `Date`. |
| `private_needs_keys` | Sin keys, todo lo privado lanza `ReadOnlyError` y nada llega al venue. |
| `balances_cover_the_pair` | Saldos de los dos activos del par, con lo comprometido en `locked`. |
| `open_orders_are_live` | Solo órdenes vivas y solo del par. |
| `place_limit_echoes_the_request` | La orden devuelta tiene id y coincide con lo pedido, `client_id` incluido. |
| `place_limit_sends_exact_numbers` | Precio y cantidad viajan exactos, sin pasar por float. |
| `refused_send_is_not_uncertain` | Un envío rechazado o que no conectó no es incierto. |
| `unclear_send_is_uncertain` | 5xx, timeout, respuesta no JSON u orden ilegible son `UncertainError`. |
| `cancel_reaches_the_venue` | El id de la orden llega al venue. |
| `cancel_failure_raises` | Un cancel fallido lanza `VenueError`. |
| `cancel_all_reaches_the_venue` | Cancela las órdenes del par y las de ningún otro. |
| `private_stream_reports_new_fill_once` | Un trade propio nuevo sale como `Fill` una sola vez, y la historia no sale como `Fill`. |
| `private_stream_resolves_gone_order` | Una orden que deja de estar activa acaba saliendo con su estado final. |
| `recent_orders_find_a_known_order` | Encuentra una orden del venue por `client_id` o, si `keeps_client_id` es `False`, por lado, precio y cantidad. |
| `requests_pass_the_limiter` | Cada petición pasa por el `Limiter`, con su tipo: `order`, `cancel`, `private` o `market`. |
| `secrets_stay_out` | Las keys no aparecen en errores ni en el estado visible del adaptador. |
| `deposit_address` | Si implementa `Depositor`: dirección del activo pedido; otro activo, una dirección vacía o sin keys son error. |
| `withdrawal_fee` | Si implementa `WithdrawalFees`: comisión de los dos activos del par; otro activo es error. |
| `token_price` | Si implementa `TokenPricer`: precio `Decimal` positivo. |

## Añadir un venue

1. **Propuesta**, según `CONTRIBUTING.md`, y versión nueva de la spec: entrar un
   venue es un cambio de alcance (D-79).
2. **Grabar** las respuestas reales en `tests/fixtures/<venue>/`, sin keys, saldos
   reales ni direcciones propias. Lo que no se pueda grabar sin operar puede salir
   de la documentación del venue, y el fixture o su `Harness` lo dicen. Lo comprobado
   va a `docs/api/`.
3. **Adaptador** en `src/xbt_terminal/adapters/`. `StreamingVenue` trae reconexión,
   respaldo por REST, deduplicación de trades, reloj y envíos inciertos;
   `OrderTracker` y `poll_private` traen el seguimiento de órdenes y fills.
4. **Batería:** un `Harness` en `tests/contract/venues.py` y añadirlo a `HARNESSES`
   en `test_contract.py`. Las comprobaciones no se tocan: si una no encaja con el
   venue, eso se discute en la propuesta.
5. **Lo que el contrato no cubre:** `VenueId` en `core/models.py`, la sección del
   venue en la config (`config.py`), crear el adaptador en `__main__.py` y su límite
   de peticiones (`core/ratelimit.py`).

```sh
uv run pytest tests/contract
```

Pasar la batería es necesario, no suficiente: no prueba que el venue se comporte así
en real. Antes de operar, tamaños mínimos y comprobación a mano, como con NonKYC y
Neoxa.
