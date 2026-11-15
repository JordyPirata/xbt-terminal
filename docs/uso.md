# Uso de la pantalla

Lo que se hace desde `xbt` una vez conectada al daemon. Dentro de la terminal,
`?` abre la ayuda con todo esto.

## Atajos

| Tecla | Qué hace |
| --- | --- |
| `1` … `5` | Velas de 1m, 5m, 1h, 4h y 1d |
| `b` / `s` | Empieza una compra o una venta en la línea de comandos |
| `d` | Empieza una orden doble |
| `o` | Órdenes abiertas (`c` cancela la seleccionada) |
| `x` | Cancela todas |
| `r` | Resultados |
| `w` | Direcciones de depósito |
| `e` | Plan de reequilibrio |
| `ctrl+b` | Para el bot, desde cualquier sitio |
| `?` | Ayuda |
| `Esc` | Suelta el campo de texto |
| `q` | Sale (solo desconecta: el daemon sigue) |

Tras escribir un comando, el foco se queda en la línea: pulsa `Esc` antes de `q`.

## Órdenes

```
buy nk 0.01 0.00725000                   compra 0.01 XBT en NonKYC a ese precio
sell nx 0.01 0.00730000                  venta en Neoxa
dbl 0.01 nk 0.00725000 nx 0.00730000     doble: compra en un venue, venta en el otro
```

Un clic en un nivel del book escribe la orden: con la línea vacía, una compra
(clic en ventas) o una venta (clic en compras) con el precio y la cantidad de
ese nivel (en la columna de acumulado, la acumulada). Se pulsa Enter o se edita
antes.

Cada orden y cada cancelación piden confirmación, y por defecto no se hace
nada. El modo (dry-run o live) es el del daemon.

## Bot de arbitraje

Con la sección `[bot]` en la config, `bot on` arma un bot que manda una orden
doble cuando comprar en un venue y vender en el otro deja más de la neta
mínima (con slippage y fees taker), y cancela lo que no llene a tiempo. Si una
pierna queda colgada, incierta o rechazada, se para y no cubre nada.

- `ctrl+b` o `bot off` lo apagan; `bot status` dice qué hace.
- En live solo se arma con `enabled = true`.
- Arranca apagado siempre, también tras reiniciar el daemon.

Detalle en `specs/mvp/design.md` §6.9.

## Ratio de inventario

Con `[inventory]` en la config (`target_base_pct`, `band_pct`), el panel de
balances muestra el % del valor en XBT de cada venue, y el bot no dispara si
dejaría un venue fuera de la banda y más lejos del objetivo. Reequilibrar es
manual.

## Resultados (`r`)

`r` (o `results [dia|7d|todo]`) muestra la ganancia o pérdida realizada de cada
orden doble en live, del bot y manual, con gráfica del acumulado y por doble.
Sale solo del journal; no es un PnL de cartera.

Con `Tab`, la pestaña **Valor de la cuenta**: el valor total en BTC (BTC + XBT ×
mid) a lo largo del tiempo, en total y por venue, que el daemon apunta en el
journal cada `value_every_s` (`[polling]`, 5 min por defecto). Los depósitos y
retiros salen como saltos. Con autocustodia, el total la incluye, y añadir o
quitar una wallet sale como un salto.

## Autocustodia

Para que el inventario total cuente lo que tienes en wallets propias:

1. En el `.env` del daemon, las claves públicas de cuenta, separadas por comas:
   `WALLET_BTC_XPUBS=zpub…` y `WALLET_BTCB2_XPUBS=zpub…,xpub…`. Vale `xpub` o
   `zpub`; de las dos se derivan **solo direcciones native segwit** (`bc1q…`),
   ramas de recepción y de cambio. Un `ypub` (nested segwit) no arranca, y de
   una cuenta taproot no saldría ningún saldo. Las dos cadenas comparten
   formato de dirección: la misma clave puede ir en las dos variables.
2. En `config.toml`, la sección `[custody]` con el explorador de cada cadena
   (una API tipo Esplora): `btc_url = "https://mempool.space/api"` y
   `btcb2_url = "https://mempool.guide/api"`, o un servidor tuyo.
3. Reiniciar el daemon (la config y el `.env` solo se leen al arrancar).

El daemon pide los saldos al arrancar y cada `every_s` (1 h por defecto),
dirección a dirección, hasta `gap_limit` (20) seguidas sin uso, con una pausa
de `request_every_ms` (3 s) antes de cada consulta: los exploradores públicos
cortan con HTTP 429 si van más seguidas. El primer saldo tarda unos minutos, y
si el explorador sigue cortando, sube esa pausa. El panel de
balances gana una línea `wallet`, y el total, su valor y el % de XBT del título
la incluyen, igual que el aviso de banda del plan de reequilibrio (`e`) y el
valor de la cuenta. Si el explorador no contesta, se conserva el último saldo;
pasadas 3 h sin uno bueno (tres intervalos), el total deja de mostrarse hasta que vuelva.

No cambia cómo opera el bot: el tope por orden, la exposición por venue y el
filtro de `[inventory]` siguen mirando solo los saldos de los venues. La
terminal solo lee: nunca tiene claves privadas ni mueve fondos de una wallet.

**Privacidad:** el xpub no sale de la máquina y no aparece en pantalla, logs
ni journal, pero cada dirección derivada se consulta al explorador
configurado. Quien lo opera puede relacionar esas direcciones entre sí y con
la IP del daemon. Si eso importa, apunta las URLs a un servidor propio.

## Depósitos (`w`)

`w` (o `deposit`) muestra la dirección de depósito de BTC y de XBT de cada venue
con keys, pedida al venue al abrir la ventana, con el QR de la fila
seleccionada. Enter o `c` la copia por la terminal (OSC 52) y con `wl-copy`,
`xclip` o `xsel` si hay alguno.

Cada fila dice el ticker real del venue: la dirección de BTCB2 de NonKYC tiene
el mismo formato `bc1q…` que una de BTC. Solo se leen: la terminal no retira ni
transfiere.

## Plan de reequilibrio (`e`)

`e` (o `rebalance`) calcula una transferencia que deja los dos venues con el
mismo % de XBT: XBT del venue con más % al otro, o BTC del que tiene menos % al
otro. Para cada una muestra lo que sale, la comisión de retiro real del venue,
lo que hay que pedir en el formulario, lo que llega, su coste en sats y el %
que queda, o por qué no se puede. Recomienda la más barata y, con `[bot]`,
cuántas dobles la pagan.

Neoxa cobra la comisión aparte (se pide X, llega X); en NonKYC se supone
descontada de lo enviado, sin confirmar (PA-10). Solo calcula: la
transferencia se hace en la web del exchange.

## Banco de pruebas

`xbt daemon --record` graba books, trades y conexión en
`<datos>/books-<hora UTC>.jsonl`, aparte del journal. Son datos públicos y no
hacen falta keys.

`xbt daemon --replay <fichero> --data-dir <dir>` reproduce una grabación a
velocidad real, sin red, sin keys y sin leer `.env`. `--data-dir` es obligatorio
para no mezclarse con el journal real, y `--live` y `--record` no se admiten.
La sección `[replay]` de la config da los saldos simulados y cómo casan las
órdenes:

- `latency_ms`: una orden llega ese tiempo después de enviarse y casa como taker
  contra el book de ese momento. Lo que no casa queda abierto hasta cancelarlo.
- `competitor_pct`: probabilidad de que otro se lleve cada nivel entero.
- `seed`: fija esa secuencia.

Lo casado queda gastado hasta que la grabación cambie o quite ese nivel.

`xbt report --replay <fichero> [--config <ruta>]` corre lo mismo en tiempo
virtual, con el bot armado desde el principio, y da cruces, disparos, dobles
llenas, tasa de captura, neta, `bot_miss` y piernas colgadas por cada 100
disparos. Tras una pierna colgada el bot se rearma solo, únicamente en el
informe. Misma grabación y misma config, mismo resultado.
