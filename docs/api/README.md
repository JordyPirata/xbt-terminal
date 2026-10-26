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
