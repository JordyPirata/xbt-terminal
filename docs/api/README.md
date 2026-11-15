# Documentación de las APIs de los venues

Copias locales de la documentación oficial, para consultar sin depender de la
web. La fuente de verdad sigue siendo lo que responde la API real: cuando no
coinciden, manda lo grabado en `tests/fixtures/` y lo anotado en
`specs/mvp/design.md` §4.

| Venue | Fichero | Fuente | Versión | Descargado |
| --- | --- | --- | --- | --- |
| NonKYC | `nonkyc/openapi.json` | `https://api.nonkyc.io/openapi.json` (el swagger de su web) | 1.3.0 | 2026-10-23 |
| Neoxa | `neoxa/openapi.json` | Generado por `tools/neoxa_openapi.py` desde el bundle de `https://neoxa.exchange/api-docs` (Neoxa no publica OpenAPI) | `bundle-` + 12 primeros del sha256 del bundle (en `info`) | 2026-11-11 |

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

## Neoxa: el spec y sus diferencias conocidas

`neoxa/openapi.json` lo genera `tools/neoxa_openapi.py` (D-84, T-100). La web de
Neoxa lleva toda su documentación dentro del bundle JavaScript de `/api-docs`; la
herramienta lo lee como texto y no ejecuta nada. Con el mismo bundle sale el mismo
fichero, byte a byte:

```
uv run python tools/neoxa_openapi.py                                  # descarga el bundle
uv run python tools/neoxa_openapi.py --bundle FICHERO --url URL       # copia local
```

Contiene 56 operaciones: la v2 (26), la v1 (26) y las de wallet y retiros (4). La
guía de la v2 (autenticación, límites, errores, WebSocket y "Moving from v1") va
copiada en `info.description`.

**La v2 no responde en vivo.** El 2026-11-11, `GET` anónimo a `/api/v2/time`,
`/api/v2/orderbook/...`, `/api/v2/pool/.../edge` y `/api/v2/balances` devuelve
`404 {"success":false,"message":"API endpoint not found"}`; `/api/exchange/*` sí
responde. La documentación describe la v2 como vigente. Mientras no responda, no hay
grabaciones v2 y sus respuestas solo se comprueban contra sus propios ejemplos.

Equivalencias v1 → v2 (`x-v2-equivalent` en el spec), según la tabla "Moving from v1"
de la guía:

| v1 | v2 |
| --- | --- |
| `GET /api/exchange/balances` | `GET /api/v2/balances` |
| `GET /api/exchange/orders` (activas) | `GET /api/v2/orders/open` |
| `GET /api/exchange/orders/history` | `GET /api/v2/orders/history` |
| `GET /api/exchange/trades/my` | `GET /api/v2/trades` |
| `POST /api/exchange/order` | `POST /api/v2/order` |
| `DELETE /api/exchange/order/{orderId}` | `DELETE /api/v2/order` |
| `GET /api/exchange/orderbook/{pair}` | `GET /api/v2/orderbook/{pair}` |

El resto de v1 (pares, trades y velas públicos, tickers, depósitos, retiros, pools y
los formatos CoinGecko y CoinMarketCap) no tiene equivalente v2 en la documentación.

Diferencias entre la documentación y lo grabado o lo que la doc dice de sí misma:

- `POST /api/exchange/order` y `GET /api/exchange/pool/{pair}/my`: el ejemplo tiene
  comentarios `//` (y una coma final en el segundo), así que no es JSON. Sin schema
  de respuesta. El fixture `order_placed.json` queda como `xfail` estricto.
- `GET /api/exchange/trades/my`: el fixture `trades_my.json` trae `fee_token: null`
  en algunas operaciones; el ejemplo de la doc muestra un string. Manda lo grabado
  (D-84), así que el spec no se corrige; el fixture queda como `xfail` estricto.
- `POST /api/v2/order/test`: su cuerpo es un alias ("exactly the fields of Place an
  order"), no una lista de campos; el spec lo marca con `x-body-same-as-description`.
- Los ejemplos de la v2 usan `BTCB2_USDC`; la terminal opera `BTCB2_BTC` (RNF-10).
- Los schemas de respuesta se infieren de los ejemplos: ningún campo se marca como
  obligatorio, porque un ejemplo no dice cuáles lo son. Los parámetros de petición sí
  llevan `required` tomado de la doc. Los importes de la v2 son strings
  decimales (su apartado "Numbers"); los de la v1 son JSON numbers.

Los tests (`tests/adapters/test_neoxa_openapi.py`) validan los 16 fixtures v1 contra
su operación, comprueban que cada ruta que llama el adaptador está en el spec y que
los importes de la v2 son strings, y que los ejemplos de la v2 cumplen su propio
schema. Esto último detecta un spec roto, no uno incorrecto.

