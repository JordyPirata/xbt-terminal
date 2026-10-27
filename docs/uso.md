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
retiros salen como saltos.

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
