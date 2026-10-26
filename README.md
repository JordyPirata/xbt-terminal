# btcb2-terminal

Terminal de consola para operar BTCB2 contra BTC en NonKYC y Neoxa desde una
sola pantalla: dos gráficos, dos libros de órdenes, panel de spread cruzado,
balances y órdenes límite simples o dobles. Se opera a mano; lo único
automático es un bot de arbitraje taker opcional, apagado por defecto (fase 2).

Estado (spec v0.21): MVP y fase 2 construidos y en uso en `--live`: órdenes
simples y dobles, bot taker, resultados por doble, ratio de inventario,
direcciones de depósito y plan de reequilibrio. Desde la v0.21 todo corre en un
daemon (el servidor) y la pantalla es su cliente (ver [Daemon y
cliente](#daemon-y-cliente)). Faltan pruebas en real (T-20, T-33, T-55, T-66) y
la CI (T-32). Detalle y siguiente paso en `HANDOFF.md`; el plan completo en
`specs/mvp/tasks.md`.

## Arrancar

```sh
uv sync
mkdir -p ~/.config/btcb2t
cp config.example.toml ~/.config/btcb2t/config.toml
uv run btcb2t daemon            # el servidor, en una terminal (o como servicio)
uv run btcb2t                   # la pantalla, en otra de al menos 160×45
```

El daemon lee config y `.env` de `~/.config/btcb2t/` y deja journal y socket en
`~/.local/state/btcb2t/`, arranque desde donde arranque; `--config`, `--env` y
`--data-dir` lo cambian. El `.env` tiene que ser `chmod 600` (en live no arranca
si no). Solo puede correr un daemon por directorio de datos. La pantalla no lee
config ni keys: todo se lo da el daemon.

Atajos: `1`…`5` cambian las velas a 1m/5m/1h/4h/1d; `b`/`s` empiezan una compra o
venta en la línea de comandos (`buy nk 0.01 0.00725000`) y `d` una orden doble
(`dbl 0.01 nk 0.00725000 nx 0.00730000`); `o` abre las órdenes abiertas (`c`
cancela la seleccionada) y `x` cancela todas; `r` resultados, `w` depósitos y
`e` plan de reequilibrio; `ctrl+b` para el bot; `?` abre la ayuda; `Esc` suelta
el campo; `q` sale. Un clic en un nivel del book escribe la orden: con la línea
vacía, una compra (clic en ventas) o una venta (clic en compras) con el precio y
la cantidad de ese nivel (en la columna de acumulado, la acumulada); se pulsa
Enter o se edita antes.
Cada orden y cada cancelación piden confirmación, y por defecto no se hace nada.

**Bot de arbitraje (fase 2):** con la sección `[bot]` de la config, `bot on` arma
un bot que manda una orden doble cuando comprar en un venue y vender en el otro
deja más de la neta mínima (con slippage y fees taker), y cancela lo que no llene
a tiempo. Si una pierna queda colgada, incierta o rechazada, se para y no cubre
nada. `ctrl+b` (o `bot off`) lo apaga desde cualquier sitio; `bot status` dice qué hace. En live solo se
arma con `enabled = true`. Detalle en `specs/mvp/design.md` §6.9.

**Ratio de inventario:** con `[inventory]` en la config (`target_base_pct`,
`band_pct`), el panel de balances muestra el % del valor en BTCB2 de cada venue
y el bot no dispara si dejaría un venue fuera de la banda y más lejos del
objetivo. Reequilibrar es manual. Detalle en `specs/mvp/design.md` §6.9.

**Resultados:** `r` (o `results [dia|7d|todo]`) abre la ganancia o pérdida
realizada de cada orden doble en live, del bot y manual, con gráfica del
acumulado y por doble. Sale solo del journal; no es un PnL de cartera.
Detalle en `specs/mvp/design.md` §6.10. Con `Tab`, la pestaña **Valor de la
cuenta**: el valor total en BTC (BTC + BTCB2 × mid) a lo largo del tiempo, en
total y por venue, que el daemon apunta en el journal cada `value_every_s`
(`[polling]`, 5 min por defecto). Los depósitos y retiros salen como saltos.

**Depósitos:** `w` (o `deposit`) muestra la dirección de depósito de BTC y BTCB2
de cada venue con keys, pedida al venue al abrir la ventana, con el QR de la fila
seleccionada para pagar desde el móvil. Enter o `c` la copia por la terminal
(OSC 52) y con `wl-copy`, `xclip` o `xsel` si hay alguno: GNOME Terminal ignora
OSC 52, así que en X11 conviene tener `xclip`. En la pantalla BTCB2 se llama XBT, pero cada fila dice el ticker real:
la dirección de BTCB2 de NonKYC tiene el mismo formato `bc1q…` que una de BTC.
Solo se leen; la terminal no retira ni transfiere. Detalle en
`specs/mvp/design.md` §6.11.

**Plan de reequilibrio:** `e` (o `rebalance`) calcula una transferencia que deja
los dos venues con el mismo % de BTCB2: BTCB2 del venue con más % al otro, o BTC
del que tiene menos % al otro. Para cada una muestra lo que sale, la comisión de
retiro real del venue (pedida al abrir), lo que hay que pedir en el formulario,
lo que llega, su coste en sats y el % que queda, o por qué no se puede.
Recomienda la más barata y, con `[bot]`, cuántas dobles la pagan. Neoxa cobra la
comisión aparte (se pide X, llega X); en NonKYC se supone descontada de lo
enviado, sin confirmar (PA-10).
Solo calcula: la transferencia se hace en la web del exchange. Detalle en
`specs/mvp/design.md` §6.12.
El dry-run necesita keys en `.env` (el daemon sin keys es solo lectura): lee los
saldos reales de la cuenta y simula las órdenes. Todo queda en
`~/.local/state/btcb2t/journal-*.jsonl`.

## Daemon y cliente

El mismo binario arranca de dos formas:

```sh
btcb2t daemon [--live]     # el servidor: venues, keys, journal, órdenes y bot; sin pantalla
btcb2t                     # la pantalla, cliente del daemon (alias: btcb2t attach)
```

`daemon` es el único proceso que habla con los venues: corre mercado, cuenta,
órdenes, reconciliación, bot, resultados, depósitos y comisiones de retiro.
El bot arranca **apagado**, también tras un reinicio: se arma desde la
pantalla con `bot on`. SIGTERM o SIGINT lo paran en orden: para el bot, espera
a los envíos en vuelo y los apunta en el journal. Las órdenes abiertas siguen
en el exchange (en live lo apunta). Escribe una línea en stderr al quedar
listo y sale con código distinto de 0 si no puede arrancar (config, `.env`,
lock). Sin keys arranca en solo lectura: solo mercado.

La pantalla se conecta por el socket `~/.local/state/btcb2t/btcb2t.sock` (o
`--socket`, o el del `--data-dir` que le digas) y lo hace todo a través del
daemon: books y velas que él reenvía, órdenes simples y dobles (el daemon
valida y la pantalla confirma su propuesta), cancelar, bot, resultados,
depósitos y reequilibrio. No abre conexiones a los venues ni lee config ni
keys. `q` solo desconecta: el daemon, el bot y las órdenes siguen igual. El
modo (dry-run o live) es el del daemon, así que la pantalla no lleva `--live`.
Puede haber varias conectadas a la vez. Escribe su log en `client.log`.

Desde otra máquina:

```sh
ssh -t server btcb2t
```

El `-t` hace falta: es una TUI y necesita terminal. También se puede reenviar
el socket con `ssh -L` y correr la pantalla en tu máquina. Cómo se mantiene
vivo el daemon (systemd, tmux, runit…) queda fuera del repo: el binario no
hace fork ni habla con ningún gestor de servicios. La ruta del socket no puede
pasar de 107 bytes (límite de los sockets Unix); con un `--data-dir` largo,
indica uno corto con `--socket` en los dos comandos.

## Cómo está organizado

```
specs/                  la spec manda: leer specs/README.md primero
  mvp/requirements.md   qué y por qué (RF, RNF, CA, preguntas abiertas)
  mvp/design.md         cómo: capas, interfaz Venue, flujos, formatos
  mvp/tasks.md          en qué orden, trazado a la spec
  mvp/decisions.md      decisiones y respuestas, append-only (con índice arriba)
  archive/              historia: changelog, estados viejos, detalle de tareas done
src/btcb2_terminal/
  core/                 lógica pura, sin red: book, velas, riesgo, comandos, journal
  adapters/             un adaptador por venue + dry-run
  services/             estado vivo que conecta adaptadores y UI
  ui/                   Textual
docs/api/               documentación oficial de las APIs (OpenAPI de NonKYC) y sus diferencias
tests/                  cada test marcado con @pytest.mark.spec("RF-NN")
tools/spec_trace.py     matriz requerimientos ↔ tests
```

## Desarrollo

```sh
uv sync
mkdir -p ~/.config/btcb2t
cp config.example.toml ~/.config/btcb2t/config.toml
cp .env.example ~/.config/btcb2t/.env   # opcional: sin keys el daemon es solo lectura
chmod 600 ~/.config/btcb2t/.env

uv run pytest
uv run ruff check .
uv run python tools/spec_trace.py   # qué requerimientos aún no tienen test
```

## Seguridad

- Keys **sin permiso de retiro**, solo en `.env` (ignorado por git).
- Con keys, el modo por defecto es **dry-run**; las órdenes reales exigen `--live`.
- En dry-run se leen los saldos reales y las órdenes son simuladas: nada sale al
  exchange. En live se envían órdenes reales y se siguen sus fills.
