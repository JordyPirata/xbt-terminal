"""Ayuda de comandos y teclas según el modo.

Spec: RF-06 · design.md §9 · Tareas: T-21, T-83
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ...core.models import BASE_LABEL

MODE_HELP = {
    "read-only": "SOLO LECTURA: sin keys en .env. Se ven books, velas y el panel cruzado; "
    "no hay órdenes ni saldos.",
    "dry-run": "DRY-RUN: lee tus saldos reales y SIMULA las órdenes. Nada sale al exchange. "
    "Es el modo por defecto con keys.",
    "live": "LIVE · ÓRDENES REALES: cada orden confirmada se envía al exchange. "
    "Se arranca con --live.",
}


def help_text(mode: str) -> str:
    return f"""AYUDA · Terminal {BASE_LABEL} ({BASE_LABEL}/BTC en NonKYC y Neoxa)

MODO ACTUAL
  {MODE_HELP.get(mode, mode)}
  Ninguna orden ni cancelación sale sin pulsar [y] en el diálogo; n, Esc o Enter
  cancelan, que es lo que pasa por defecto.

TECLAS (con la línea de comandos sin foco; Esc la suelta)
  b                 empieza una compra en la línea de comandos
  s                 empieza una venta
  d                 empieza una orden doble
  o                 órdenes abiertas (en la lista: c cancela la seleccionada,
                    x cancela todas, Esc cierra)
  x                 cancelar todas las órdenes abiertas (pide confirmación)
  r                 resultados de las órdenes dobles (1 día, 2 7 días, 3 todo)
  ctrl+b  /  p      parar el bot de arbitraje al momento (ctrl+b funciona
                    también con la línea de comandos activa)
  1 2 3 4 5         velas de 1m, 5m, 1h, 4h y 1d (hora UTC)
  ?                 esta ayuda
  q                 salir (en live, con órdenes abiertas, pide confirmación:
                    siguen en el exchange)

CLIC EN EL BOOK
  Con la línea de comandos vacía, un clic en un nivel de VENTA (rojo) escribe
  una compra a ese precio en ese venue, y uno de COMPRA (verde) una venta, con
  la cantidad de ese nivel; un clic en la columna "acumulado" usa la cantidad
  acumulada hasta ahí. Pulsa Enter para ver la confirmación, o edítala antes.
  Si pasa de los topes de riesgo, se rechaza y dice por qué.
  Con algo escrito, el clic solo pega el precio donde está el cursor (útil para
  la orden doble). El precio se intenta copiar también al portapapeles.

COMANDOS (venue: nk = NonKYC, nx = Neoxa)
  buy nk 0.01 0.00725000                      compra límite de 0.01 {BASE_LABEL}
  sell nx 0.01 0.00730000                     venta límite
  buy nx 25% 0.00725000                       compra con el 25 % del BTC de Neoxa
  sell nk 100% 0.00730000                     vende todo el {BASE_LABEL} de NonKYC
    (% del saldo total del venue: BTC al comprar, con la fee descontada; {BASE_LABEL}
    al vender; hacia abajo al lote. Los topes de riesgo se aplican igual.)
  dbl 0.01 nk 0.00725000 nx 0.00730000        orden doble: compra en nk, vende en nx
  orders  /  orders nk                        órdenes abiertas (todas o de un venue)
  cancel <id>                                 cancela una orden (el id sale en orders)
  cancel nk  /  cancel all                    todas las de un venue, o todas
  tf 1m|5m|1h|4h|1d                           timeframe de las velas
  bot on  /  bot off  /  bot status           bot de arbitraje (ver abajo)
  results  /  results 7d  /  results todo     resultados de las dobles (ver abajo)
  deposit                                     direcciones de depósito (tecla w)
  rebalance                                   plan de reequilibrio (tecla e)
  help                                        esta ayuda
  Ojo: cancelar un venue o todo cancela todas las órdenes {BASE_LABEL}/BTC de la
  cuenta, también las que no se enviaron desde la terminal.

CÓMO LEER LA PANTALLA
  Arriba, por venue: ws o respaldo (REST), ● conectado / ◌ reconectando / ✖ caído,
  latencia, y "privado" (● ws o ● rest: el canal de tu cuenta; simulado en dry-run).
  "sin noticias" = el estado no se ha renovado en 15 s.
  Book: compras en verde a la izquierda, ventas en rojo a la derecha; un nivel
  marcado con " A " es liquidez del pool AMM de Neoxa.
  Panel cruzado: mid y spread de cada venue, y las dos rutas comprar-aquí /
  vender-allí, en bruto y neto de fees taker (verde si el neto es positivo).
  Balances: disponible + en órdenes, valor en BTC al mid y % del valor en {BASE_LABEL}
  (verde dentro de la banda de [inventory], amarillo fuera); total en el título.
  Orden doble: estado de cada pierna; "PIERNA COLGADA" en rojo si una se llena y
  la otra no la sigue en 30 s (no se hace nada automático).

BOT DE ARBITRAJE (sección [bot] de config.toml)
  bot on pide confirmación con sus límites y lo arma; en live, solo con
  enabled = true. Cuando comprar en un venue y vender en el otro deja más de la
  neta mínima (con slippage y fee taker), manda una orden doble con límites
  agresivas y cancela lo que no llene a tiempo. Una doble a la vez, con pausa
  entre disparos y topes por hora y por día.
  Con [inventory] en config.toml, no dispara si la doble dejaría un venue fuera
  de la banda de % en {BASE_LABEL} y más lejos del objetivo (bot status lo dice como
  inventory_band). Nunca compra para llegar al objetivo: reequilibrar es a mano.
  Si una pierna queda colgada, incierta o rechazada, el bot cancela lo abierto,
  no cubre nada y queda PARADO ("BOT ✖ PARADO" en rojo en la barra): revisa los
  saldos y rearma con bot on. bot status dice su estado y por qué no dispara;
  ctrl+b, p o bot off lo apagan.

RESULTADOS (tecla r o results)
  Ganancia o pérdida realizada de cada orden doble en live, del bot y manual,
  sacada del journal: lo cobrado en la venta menos lo pagado en la compra, menos
  las fees reales de los fills, sobre la cantidad que se llenó en las dos
  piernas. Lo llenado de más en una pierna sale como descuadre en {BASE_LABEL}, sin
  valorar. Las dobles colgadas o sin fills no suman. No es un PnL de cartera:
  no valora tu inventario ni los rebalanceos.

DIRECCIONES DE DEPÓSITO (tecla w o deposit)
  Dirección de BTC y de {BASE_LABEL} de cada venue con keys, pedida al venue cada vez
  que abres la ventana. Cada fila dice el ticker real del venue ({BASE_LABEL} es
  BTCB2): no mandes un activo a la dirección de otro. A la derecha, el QR de la
  fila seleccionada, para escanearla con el móvil. Enter o c copia la dirección
  (con xclip, xsel o wl-copy si están instalados; si no, solo por la terminal y
  se puede seleccionar con Shift + arrastrar); r vuelve a pedirlas. Solo se
  leen: la terminal no retira ni transfiere.

PLAN DE REEQUILIBRIO (tecla e o rebalance)
  Con tus saldos y el mid de cada venue, calcula UNA transferencia que deja los
  dos venues con el mismo % de {BASE_LABEL}: mandar {BASE_LABEL} del que tiene más % al
  otro, o BTC del que tiene menos % al otro. Para cada una: lo que hay que
  pedir en el formulario de retiro, la comisión real del venue (pedida al
  abrir), lo que sale y lo que llega (Neoxa cobra la comisión aparte), su coste
  en sats y el % que queda; si no se puede (disponible, mínimo, retiro cerrado),
  dice por qué. Recomienda la más barata y, con [bot], cuántas dobles la pagan.
  Una transferencia no cambia el % total: eso se corrige comprando o vendiendo.
  Solo calcula: la transferencia la haces tú en la web del exchange (las
  direcciones, con w). r vuelve a calcular.

[Esc] cerrar"""


class HelpScreen(ModalScreen[None]):
    """Ayuda (RF-15). Sin markup: lleva corchetes."""

    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen > VerticalScroll {
        width: 100; height: 40; border: thick $accent; background: $panel; padding: 0 1;
    }
    """
    BINDINGS = [  # noqa: RUF012  (así lo declara Textual)
        Binding("escape", "close", "cerrar"),
        Binding("question_mark", "close", show=False),
        Binding("q", "close", show=False),
    ]

    def __init__(self, mode: str) -> None:
        super().__init__()
        self.text = help_text(mode)

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(self.text, markup=False)

    def action_close(self) -> None:
        self.dismiss(None)
