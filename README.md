# xbt-terminal

Terminal de consola para operar BTCB2 contra BTC en NonKYC y Neoxa desde una
sola pantalla: dos gráficos, dos libros de órdenes, panel de spread cruzado,
balances y órdenes límite simples o dobles. Se opera a mano; lo único
automático es un bot de arbitraje taker opcional, apagado por defecto.

Corre en dos partes: un **daemon** (`xbt daemon`) que es el único que habla con
los exchanges, tiene las keys y escribe el journal, y una **pantalla** (`xbt`)
que se conecta a él por un socket Unix, en la misma máquina o por SSH. Cerrar la
pantalla no para nada: el daemon, el bot y las órdenes siguen.

En pantalla BTCB2 aparece como **XBT**.

> **Aviso de riesgo.** Este programa envía órdenes reales con tu dinero cuando
> lo arrancas con `--live`. Se distribuye sin ninguna garantía (ver `LICENSE`).
> Empieza en solo lectura y en dry-run, y en live con importes mínimos. Las
> pruebas en real pendientes están en `HANDOFF.md`.

## Requisitos

- **Linux o macOS.** El daemon y la pantalla hablan por un socket Unix; en
  Windows, solo dentro de WSL.
- [**uv**](https://docs.astral.sh/uv/): `curl -LsSf https://astral.sh/uv/install.sh | sh`.
  Se encarga de Python (3.11 o más; el repo fija 3.12) y de las dependencias.
- **Reloj sincronizado** (NTP: `timedatectl` debe decir `System clock
  synchronized: yes`). Con más de `max_clock_skew_s` (5 s) de desfase con un
  venue, el daemon no deja operar en live.
- Una terminal de al menos **160×45** con color.
- Opcional: `xclip`, `xsel` o `wl-copy` para copiar direcciones de depósito
  (algunas terminales, como GNOME Terminal, ignoran OSC 52).
- Cuenta en NonKYC y/o en Neoxa con saldo en BTC y BTCB2 para operar. Sin keys
  funciona en solo lectura (mercado).

## Instalar

```sh
git clone git@github.com:JordyPirata/xbt-terminal.git
cd xbt-terminal
uv tool install .          # deja `xbt` en ~/.local/bin (que tiene que estar en el PATH)

mkdir -p ~/.config/xbt
cp config.example.toml ~/.config/xbt/config.toml
cp .env.example ~/.config/xbt/.env
chmod 600 ~/.config/xbt/.env
```

El daemon lee `config.toml` y `.env` de `~/.config/xbt/` y deja journal, logs,
lock y socket en `~/.local/state/xbt/`, se arranque desde donde se arranque
(`--config`, `--env` y `--data-dir` lo cambian). La pantalla no lee ni config
ni keys: todo se lo da el daemon.

Para desarrollar, en lugar de `uv tool install`: `uv sync` y `uv run xbt …`
(ver [Desarrollo](#desarrollo)).

## API keys

Una key por venue, en `~/.config/xbt/.env`. Con keys de un solo venue se opera
solo en ese; el otro queda en solo lectura.

```sh
NONKYC_API_KEY=...
NONKYC_API_SECRET=...
NEOXA_API_KEY=...
NEOXA_API_SECRET=...
```

- **Nunca con permiso de retiro.** Solo lectura y trading. La terminal no
  retira ni transfiere: no lo necesita, y una key sin retiro limita el daño si
  se filtra.
- **NonKYC:** se crean en la sección de API de la cuenta; marca lectura y
  trading y deja el retiro sin marcar. Si la restringes por IP, usa la del
  servidor donde corre el daemon.
- **Neoxa:** las keys de API solo tienen `read` y `trade`; no hay retiro que
  desmarcar. Su límite de peticiones es **por IP**: no corras dos daemons (ni
  otro bot) desde la misma IP.
- El `.env` debe ser `chmod 600`: en live, el daemon no arranca si el grupo u
  otros pueden leerlo.

## Primera puesta en marcha

Hazlo en este orden y no pases al siguiente paso hasta que el anterior se vea
bien.

1. **Solo lectura**, sin keys (deja el `.env` vacío):

   ```sh
   xbt daemon        # en una terminal; dice "xbt daemon en …" al quedar listo
   xbt               # la pantalla, en otra
   ```

   Deben verse los dos books y los gráficos moviéndose.

2. **Dry-run**, con keys: rellena el `.env` y reinicia el daemon (`Ctrl+C` y
   `xbt daemon`). Lee los saldos reales y **simula** las órdenes: nada sale al
   exchange. Prueba una compra, una venta, una doble y cancelar.

3. **Revisa `~/.config/xbt/config.toml`** antes de live. Los valores de
   `config.example.toml` son un ejemplo, no una recomendación:
   - `[risk]`: `max_venue_exposure` (BTC que aceptas tener en cada exchange),
     `max_order_pct` y `max_mid_deviation_pct`.
   - `[venues.*]`: fees. Las de NonKYC no salen por API.
   - `[bot]`: déjalo con `enabled = false` hasta conocer la terminal. Sin la
     sección no hay bot.

4. **Live con importes mínimos:** `xbt daemon --live`. Coloca y cancela una
   orden pequeña en cada venue y comprueba que la ves en la web del exchange.
   El modo lo decide el daemon; la pantalla no lleva `--live`.

El bot arranca **apagado** siempre, también tras reiniciar el daemon. Se arma
desde la pantalla con `bot on` y se para con `ctrl+b`.

Para dejarlo corriendo en un servidor (servicio `systemd`, acceso por SSH,
actualizar, copias de seguridad): [`docs/despliegue.md`](docs/despliegue.md).
Atajos, comandos y ventanas de la pantalla: [`docs/uso.md`](docs/uso.md), o `?`
dentro de la terminal.

## Seguridad

- Keys **sin permiso de retiro**, solo en `.env` (`0600`, ignorado por git).
  Nunca se muestran en pantalla ni en logs.
- Con keys, el modo por defecto es **dry-run**; las órdenes reales exigen
  `xbt daemon --live`.
- Cada orden y cada cancelación piden confirmación en la pantalla, y por
  defecto no se hace nada.
- El journal y los logs se crean solo legibles por tu usuario.
- El socket del daemon es `0600`: quien pueda abrirlo puede operar. No lo
  expongas fuera de tu usuario; para acceso remoto, SSH.

## Cómo está organizado

```
specs/                  la spec manda: leer specs/README.md primero
  mvp/requirements.md   qué y por qué (RF, RNF, CA, preguntas abiertas)
  mvp/design.md         cómo: capas, interfaz Venue, flujos, formatos
  mvp/tasks.md          en qué orden, trazado a la spec
  mvp/decisions.md      decisiones y respuestas, append-only (con índice arriba)
  archive/              historia: changelog, estados viejos, detalle de tareas done
src/xbt_terminal/
  core/                 lógica pura, sin red: book, velas, riesgo, comandos, journal
  adapters/             un adaptador por venue + dry-run
  services/             estado vivo que conecta adaptadores y UI
  ui/                   Textual
docs/                   uso, despliegue y documentación de las APIs de los venues
tests/                  cada test marcado con @pytest.mark.spec("RF-NN")
tools/spec_trace.py     matriz requerimientos ↔ tests
```

El estado del desarrollo y el siguiente paso están en `HANDOFF.md`; el plan,
en `specs/mvp/tasks.md`.

## Desarrollo

```sh
uv sync
uv run xbt daemon               # usa ~/.config/xbt como el comando instalado
uv run xbt

uv run pytest
uv run ruff check .
uv run python tools/spec_trace.py   # qué requerimientos aún no tienen test
```

Proyecto con spec driven development: ver `CLAUDE.md` y `specs/README.md`
antes de cambiar código.

## Licencia

[AGPL-3.0](LICENSE). Si modificas el programa y lo distribuyes, o lo ofreces a
otros por red, tienes que publicar tus cambios con la misma licencia.
