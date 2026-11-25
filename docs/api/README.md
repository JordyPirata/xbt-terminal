# Documentación de las APIs de los venues

Copias locales de la documentación oficial, para consultar sin depender de la
web. La fuente de verdad sigue siendo lo que responde la API real: cuando no
coinciden, manda lo grabado en `tests/fixtures/` y lo anotado en
`specs/mvp/design.md` §4.

| Venue | Fichero | Fuente | Versión | Descargado |
| --- | --- | --- | --- | --- |
| NonKYC | `nonkyc/openapi.json` | `https://api.nonkyc.io/openapi.json` (el swagger de su web) | 1.3.0 | 2026-10-23 |
| Neoxa | — | `https://neoxa.exchange/api-docs` (página web, sin OpenAPI) | — | — |

## NonKYC: diferencias conocidas con la API real

- `/account/orders` devuelve `side` en mayúsculas (`BUY`); `createorder` y
  `getorder`, en minúsculas (D-22).
- `/market/trades` no coincide con el schema `HistoricalTrade`: trae `id`,
  `type`, `baseVolume` y `timestamp` en ms (HANDOFF, T-09).
- `/cancelallorders`: el schema no marca `side` como obligatorio, pero sin él
  la API responde HTTP 200 con `{"error": "400 Bad Request"}` (D-58).
- `/account/orders?status=active` también devuelve órdenes ya `Cancelled` o
  `Filled` (con `isActive: false`), minutos después de terminar (D-59).
- `/account/orders` y `/account/trades` ignoran `symbol` (probado con
  `BTCB2/BTC`, `BTCB2_BTC`, `BTCB2-BTC` y `NKYC/BTC`, 2026-10-25): traen todos
  los mercados. La terminal filtra por `symbol` o `market.symbol` (D-63). El
  `limit` también cuenta los otros mercados. No se sabe si `/cancelallorders`
  respeta su `symbol`.
- Los errores llegan a veces dentro de un HTTP 200; la terminal mira siempre
  el campo `error` del cuerpo.
- El BTC en red principal es el ticker hijo `BTC-MAIN` en depósitos y retiros;
  el padre `BTC` no vale (D-50, D-53).
- El formato del `report` del WS privado no está documentado (D-14).

## Exploradores (autocustodia, RF-38)

Comprobado el 2026-11-14 con la dirección de ejemplo de BIP173
(`bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4`, que no es de nadie); respuestas
en `tests/fixtures/explorer/`.

| Cadena | Base (`[custody]`) | Qué es |
| --- | --- | --- |
| BTC | `https://mempool.space/api` | mempool.space, API de Esplora |
| BTCB2 | `https://mempool.guide/api` | mempool 3.4-dev con backend Esplora sobre Knots (`/api/v1/backend-info`) |

- `GET <base>/address/<dirección>` devuelve `{address, chain_stats,
  mempool_stats}`, cada bloque con `funded_txo_sum`, `spent_txo_sum` (en
  satoshis, enteros) y `tx_count`. El formato es el mismo en los dos.
- Una dirección mal formada da HTTP 400 con el texto `Invalid Bitcoin address`.
- Las dos cadenas comparten formato de dirección (`bc1q…`, PA-14) y el
  historial anterior a la bifurcación: la misma dirección tiene transacciones
  en las dos (172 en BTCB2, 227 en BTC), y la altura es distinta (982022 frente
  a 975885). Una misma dirección puede tener saldo en las dos cadenas.
- Límites (2026-11-14, sin documentar): mempool.space dio HTTP 429 a la
  consulta 22 sin pausa, a la 24 con 250 ms y a la 33 con 1 s entre consultas.
  mempool.guide contestó 40 seguidas sin pausa. De ahí `request_every_ms`
  (3000 por defecto); no se ha comprobado que mempool.space lo acepte.
