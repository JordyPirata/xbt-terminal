"""Bot de arbitraje taker: límites, decisión pura y máquina de estados (fase 2).

`decide` mira las dos rutas ("comprar en A, vender en B") con lo que le da el
servicio: el book de cada venue, los saldos disponibles y si el venue está
bloqueado (book viejo, desfase de reloj, fallo, envío incierto...). Para cada
ruta (design.md §6.9):

    buy_price  = tick hacia arriba (ref_A × (1 + slippage))
    sell_price = tick hacia abajo  (ref_B × (1 - slippage))
    neta/u     = sell_price × (1 - taker_B) - buy_price × (1 + taker_A)

La referencia es el mejor ask/bid o, con `max_walk_pct` del venue (D-60),
cualquier nivel a no más de ese % del mejor: así una pierna puede tomar varios
escalones del pool AMM de NonKYC hasta llegar al mínimo. Gana el par de
referencias de mayor neta en BTC; no hace falta probarlos todos (D-70,
`_Pairs.search`): la profundidad sale de sumas acumuladas y los pares se podan
por la monotonía de la neta, con el mismo resultado que probándolos todos.

La neta es pesimista: con los precios límite, no con el mejor precio, y con fee
taker en las dos. La fee de la neta es `VenueView.net_fee` si el servicio la
pone: la de un venue que paga en token y tiene saldo de él (`token_fee`, D-63).
La cantidad es el mínimo de la profundidad del book hasta esos precios en los
dos lados, el BTC de A (con su fee completa), el BTCB2 de B, `max_qty` y lo que
queda del tope diario, al lote de los dos venues hacia abajo. El tope de
RF-10 lo aplica después `OrderService.prepare_double`.

Con `max_move_pct` (D-71), no dispara si el mid de algún venue se movió más de
ese % en la ventana de `move_window_ms`: tras un barrido, otros bots se llevan
antes el precio y la pierna queda colgada.

Con `[inventory]` (RF-26, D-46), una ruta tampoco dispara si la doble dejaría
algún venue fuera de la banda de % de BTCB2 y más lejos del objetivo que antes
(`inventory_veto`). El bot nunca compra para llegar al objetivo: solo veta.

Estados: `off → armed → firing → cooldown → armed`; cualquier problema de una
disparada → `halted`, del que solo se sale a mano (`arm`). Sin reloj propio:
todo recibe `now` en ms.

Spec: RF-18 a RF-22, RF-24, RF-26, RNF-22, RNF-23 · design.md §6.9 · D-70, D-71 · Tareas:
T-34, T-35, T-41, T-49, T-57, T-72, T-73
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from itertools import pairwise
from typing import Literal

from .inventory import InventoryTarget, base_pct
from .models import BASE_LABEL, ZERO, Level, MarketSpec, VenueId

MIN_COOLDOWN_S = 2  # Neoxa: 10 órdenes cada 10 s (RNF-03); un disparo usa 1 + 1 cancelación
MIN_FILL_TIMEOUT_MS = 100
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
HUNDRED = Decimal(100)
ROUTES: tuple[tuple[VenueId, VenueId], ...] = (("nk", "nx"), ("nx", "nk"))

BotStatus = Literal["off", "armed", "firing", "cooldown", "halted"]


@dataclass(frozen=True, slots=True)
class BotLimits:
    """Sección `[bot]` de config.toml, sin `enabled`."""

    min_net_pct: Decimal  # neta pesimista mínima, % sobre el precio de compra (RF-19)
    max_slippage_pct: Decimal  # margen de las límites agresivas (RF-20)
    max_qty: Decimal  # BTCB2 por disparo (RF-20)
    fill_timeout_ms: int  # a partir de aquí se cancela el resto (RF-20)
    cooldown_s: int  # entre disparos (RF-22)
    max_trades_per_hour: int
    max_daily_btc: Decimal  # nocional de compra enviado por día UTC
    max_consecutive_failures: int
    # cuánto se aleja del mejor precio el nivel de referencia, por venue (RF-20, D-60)
    max_walk_pct: Mapping[VenueId, Decimal] = field(default_factory=dict)
    # movimiento máximo del mid de un venue en la ventana; None = sin filtro (RF-19, D-71)
    max_move_pct: Decimal | None = None
    move_window_ms: int = 2000


@dataclass(frozen=True, slots=True)
class FeeToken:
    """Token con que un venue cobra la fee con descuento (`fee_token`, RF-24, D-63)."""

    asset: str
    min_available: Decimal  # `fee_token_min`
    discount_pct: Decimal  # `fee_discount_pct`


def token_fee(taker: Decimal, token: FeeToken, available: Decimal | None) -> Decimal | None:
    """Fee taker con descuento si hay al menos `min_available` del token; si no,
    o sin saldo leído, None: la neta va con la fee completa."""
    if available is None or available < token.min_available:
        return None
    return taker * (1 - token.discount_pct / HUNDRED)


@dataclass(frozen=True, slots=True)
class BotState:
    status: BotStatus = "off"
    reason: str | None = None  # por qué está halted
    fired_at: tuple[int, ...] = ()  # disparos de la última hora
    day: int | None = None  # día UTC (ms // DAY_MS) al que corresponde day_btc
    day_btc: Decimal = ZERO  # nocional de compra enviado ese día
    failures: int = 0  # disparos rechazados seguidos
    last_fire_at: int | None = None


@dataclass(frozen=True, slots=True)
class VenueView:
    """Lo que el bot ve de un venue al decidir."""

    spec: MarketSpec
    bids: tuple[Level, ...]  # de mayor a menor
    asks: tuple[Level, ...]  # de menor a mayor
    quote_available: Decimal  # BTC
    base_available: Decimal  # BTCB2
    blocked: str | None = None  # motivo para no operar en este venue ahora
    # ratio de inventario (RF-26): totales (disponible + en órdenes) y mid del book
    quote_total: Decimal = ZERO
    base_total: Decimal = ZERO
    mid: Decimal | None = None
    # fee taker para la neta (RF-19, D-63); None = la del spec. Cantidad e
    # inventario siguen con la del spec
    net_fee: Decimal | None = None
    # cuánto se movió el mid en la ventana de `move_window_ms`; None = sin medir (D-71)
    move_pct: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Fire:
    buy_venue: VenueId
    sell_venue: VenueId
    buy_price: Decimal
    sell_price: Decimal
    qty: Decimal
    net: Decimal  # BTC, pesimista
    net_pct: Decimal  # sobre el nocional de compra

    @property
    def notional(self) -> Decimal:
        return self.qty * self.buy_price


@dataclass(frozen=True, slots=True)
class Skip:
    reason: str  # código: below_threshold, no_depth, no_balance, cooldown, ...
    detail: str = ""


@dataclass(frozen=True, slots=True)
class _Miss:
    skip: Skip
    net_pct: Decimal | None  # para elegir qué motivo se cuenta


# -- decisión -------------------------------------------------------------------


def _spent(state: BotState, now: int) -> Decimal:
    return state.day_btc if state.day == now // DAY_MS else ZERO


def _recent(state: BotState, now: int) -> tuple[int, ...]:
    return tuple(t for t in state.fired_at if now - t < HOUR_MS)


def price_move_pct(mids: Sequence[Decimal]) -> Decimal | None:
    """Movimiento de una ventana de mids: (máx - mín) / mín × 100; None sin
    mids (D-71)."""
    if not mids:
        return None
    low, high = min(mids), max(mids)
    return (high - low) / low * HUNDRED


def _volatile(views: Mapping[VenueId, VenueView], limits: BotLimits) -> Skip | None:
    """El primer venue, por orden, cuyo mid se movió más de `max_move_pct` en la
    ventana: tras un salto, otros bots llegan antes (D-71)."""
    cap = limits.max_move_pct
    if cap is None:
        return None
    for vid in sorted(views):
        move = views[vid].move_pct
        if move is not None and move > cap:
            window = limits.move_window_ms / 1000
            return Skip("volatile", f"{vid}: mid {move:.2f} % en {window:g} s > {cap} %")
    return None


def decide(
    views: Mapping[VenueId, VenueView],
    state: BotState,
    limits: BotLimits,
    now: int,
    target: InventoryTarget | None = None,
) -> Fire | Skip:
    if state.status != "armed":
        return Skip("not_armed", state.status)
    if state.last_fire_at is not None and now - state.last_fire_at < limits.cooldown_s * 1000:
        return Skip("cooldown")
    if len(_recent(state, now)) >= limits.max_trades_per_hour:
        return Skip("hour_cap", f"{limits.max_trades_per_hour} disparos en la última hora")
    budget = limits.max_daily_btc - _spent(state, now)
    if budget <= 0:
        return Skip("day_cap", f"{limits.max_daily_btc} BTC de compra hoy")
    if volatile := _volatile(views, limits):
        return volatile

    fires: list[Fire] = []
    misses: list[_Miss] = []
    for buy, sell in ROUTES:
        a, b = views.get(buy), views.get(sell)
        result = _route(a, b, buy, sell, limits, budget)
        if isinstance(result, Fire) and target is not None and a is not None and b is not None:
            veto = inventory_veto(a, b, result, target)
            if veto is not None:
                result = _Miss(Skip("inventory_band", veto), result.net_pct)
        if isinstance(result, Fire):
            fires.append(result)
        else:
            misses.append(result)
    if fires:
        return max(fires, key=lambda f: f.net)
    blocked = [m.skip for m in misses if m.skip.reason == "blocked"]
    priced = [m for m in misses if m.net_pct is not None]
    if priced:
        return max(priced, key=lambda m: m.net_pct or ZERO).skip
    return blocked[0] if blocked else misses[0].skip


def _route(
    a: VenueView | None,
    b: VenueView | None,
    buy: VenueId,
    sell: VenueId,
    limits: BotLimits,
    budget: Decimal,
) -> Fire | _Miss:
    for vid, v in ((buy, a), (sell, b)):
        if v is not None and v.blocked:
            return _Miss(Skip("blocked", f"{vid}: {v.blocked}"), None)
    if a is None or b is None or not a.asks or not b.bids:
        return _Miss(Skip("no_quotes", f"{buy}→{sell}: falta el ask o el bid"), None)

    walk_a = limits.max_walk_pct.get(buy, ZERO) / HUNDRED
    walk_b = limits.max_walk_pct.get(sell, ZERO) / HUNDRED
    refs_a = [lv.price for lv in a.asks if lv.price <= a.asks[0].price * (1 + walk_a)]
    refs_b = [lv.price for lv in b.bids if lv.price >= b.bids[0].price * (1 - walk_b)]
    asks, bids = _Side(a.asks, up=True), _Side(b.bids, up=False)
    # el primero, a los mejores precios, solo: sin cruce no se prepara nada más
    first = _Pairs(a, b, buy, sell, limits, budget, refs_a[:1], refs_b[:1], asks, bids).pair(0, 0)
    if isinstance(first, _Miss) and first.skip.reason == "below_threshold":
        return first  # los niveles más hondos solo dan peor neta
    r = _Pairs(a, b, buy, sell, limits, budget, refs_a, refs_b, asks, bids)
    if r.ordered:
        return r.search(first)
    # book sin ordenar: sin monotonía no se poda, se prueban todos los pares
    results = [first, *(r.pair(i, j) for i in range(r.n) for j in range(r.m) if i or j)]
    fires = [x for x in results if isinstance(x, Fire)]
    if fires:
        return max(fires, key=lambda f: f.net)
    # con profundidad que no rinde, el motivo es el umbral, no el mínimo del venue
    short = [x for x in results if isinstance(x, _Miss) and x.skip.reason == "below_threshold"]
    return max(short, key=lambda m: m.net_pct or ZERO) if short else first


class _Side:
    """Un lado del book con su profundidad acumulada (D-70): la cantidad hasta
    un precio sale de `bisect` en O(log L), no de sumar el lado entero."""

    __slots__ = ("_cum", "_keys", "_levels", "_up", "ordered")

    def __init__(self, levels: tuple[Level, ...], up: bool) -> None:
        self._levels, self._up = levels, up  # up: asks, de menor a mayor
        prices = [lv.price for lv in levels]
        self.ordered = all((p <= q) if up else (p >= q) for p, q in pairwise(prices))
        self._keys = prices if up else [-p for p in prices]
        cum, acc = [ZERO], ZERO
        for lv in levels:  # mismo orden de suma que antes: mismo Decimal
            acc += lv.qty
            cum.append(acc)
        self._cum = cum

    def depth(self, price: Decimal) -> Decimal:
        """Lo que hay hasta `price`: asks a ese precio o menos, bids a ese o más."""
        if not self.ordered:
            ok = (lambda p: p <= price) if self._up else (lambda p: p >= price)
            return sum((lv.qty for lv in self._levels if ok(lv.price)), ZERO)
        key = price if self._up else -price
        return self._cum[bisect_right(self._keys, key)]


def _first(n: int, below: Callable[[int], bool]) -> int:
    """Primer índice en [0, n) con `below` cierto, si `below` es monótono
    (falso… cierto); n si ninguno."""
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi) // 2
        if below(mid):
            hi = mid
        else:
            lo = mid + 1
    return lo


class _Pairs:
    """Los pares de referencias de una ruta (RF-20), con lo que no depende del
    par calculado una vez: precios límite, netas por unidad, profundidad y
    topes por fila (precio de compra `i`) y por columna (de venta `j`)."""

    def __init__(
        self,
        a: VenueView,
        b: VenueView,
        buy: VenueId,
        sell: VenueId,
        limits: BotLimits,
        budget: Decimal,
        refs_a: list[Decimal],
        refs_b: list[Decimal],
        asks: _Side,
        bids: _Side,
    ) -> None:
        self.a, self.b, self.buy, self.sell, self.limits = a, b, buy, sell, limits
        self.n, self.m = len(refs_a), len(refs_b)
        slip = limits.max_slippage_pct / HUNDRED
        fee_a = a.spec.taker_fee
        net_a = fee_a if a.net_fee is None else a.net_fee
        net_b = b.spec.taker_fee if b.net_fee is None else b.net_fee
        self.ordered = asks.ordered and bids.ordered
        self.buy_price = [a.spec.quantize_price(r * (1 + slip), "buy") for r in refs_a]
        self.sell_price = [b.spec.quantize_price(r * (1 - slip), "sell") for r in refs_b]
        self.buy_cost = [p * (1 + net_a) for p in self.buy_price]  # BTC por unidad
        self.sell_gain = [p * (1 - net_b) for p in self.sell_price]
        self.depth_a = [asks.depth(p) for p in self.buy_price]
        self.depth_b = [bids.depth(p) for p in self.sell_price]
        self.balance = [
            min(a.quote_available / (p * (1 + fee_a)), b.base_available) for p in self.buy_price
        ]
        self.day = [budget / p for p in self.buy_price]

    def unit(self, i: int, j: int) -> Decimal:
        return self.sell_gain[j] - self.buy_cost[i]

    def net_pct(self, i: int, j: int) -> Decimal:
        return self.unit(i, j) / self.buy_price[i] * HUNDRED

    def below(self, i: int, j: int) -> bool:
        return self.net_pct(i, j) < self.limits.min_net_pct

    def pair(self, i: int, j: int) -> Fire | _Miss:
        """Una ruta con un par de precios de referencia (RF-20)."""
        a, b, buy, sell, limits = self.a, self.b, self.buy, self.sell, self.limits
        buy_price, sell_price = self.buy_price[i], self.sell_price[j]
        unit = self.unit(i, j)
        net_pct = unit / buy_price * HUNDRED
        if net_pct < limits.min_net_pct:
            detail = f"{buy}→{sell}: neta {net_pct:.2f} % < {limits.min_net_pct} %"
            return _Miss(Skip("below_threshold", detail), net_pct)

        terms = {
            "no_depth": min(self.depth_a[i], self.depth_b[j]),
            "no_balance": self.balance[i],
            "max_qty": limits.max_qty,
            "day_cap": self.day[i],
        }
        binding = min(terms, key=lambda k: terms[k])
        qty = b.spec.quantize_qty(a.spec.quantize_qty(terms[binding]))
        if a.spec.quantize_qty(qty) != qty:  # lotes que no encajan entre sí
            return _Miss(Skip("lot_mismatch", f"{qty} no es múltiplo del lote de {buy}"), net_pct)
        if qty <= 0 or qty < max(a.spec.min_qty, b.spec.min_qty):
            detail = f"{buy}→{sell}: {terms[binding]:f} {BASE_LABEL} no llega al lote"
            return _Miss(Skip(binding, detail), net_pct)
        if qty * buy_price < a.spec.min_notional or qty * sell_price < b.spec.min_notional:
            detail = f"{buy}→{sell}: {qty} {BASE_LABEL} bajo el mínimo"
            return _Miss(Skip("min_notional", detail), net_pct)
        return Fire(buy, sell, buy_price, sell_price, qty, unit * qty, net_pct)

    def _cap(self, i: int, jc: int) -> Decimal:
        """Cota de la cantidad de la fila `i` en las columnas [0, jc)."""
        return min(
            self.depth_a[i], self.depth_b[jc - 1], self.balance[i], self.limits.max_qty,
            self.day[i],
        )  # fmt: skip

    def _can_fire(self, i: int, cap: Decimal) -> bool:
        """¿Puede disparar algún par de la fila? Con la cantidad máxima (la
        cantidad crece con la cota y el redondeo al lote es monótono)."""
        a, b = self.a.spec, self.b.spec
        top = b.quantize_qty(a.quantize_qty(cap))
        if top <= 0 or top < max(a.min_qty, b.min_qty):
            return False
        return top * self.buy_price[i] >= a.min_notional and (
            top * self.sell_price[0] >= b.min_notional
        )

    def search(self, first: Fire | _Miss) -> Fire | _Miss:
        """El mismo resultado que probar todos los pares, sin probarlos todos
        (D-70). La neta % baja al subir el precio de compra (`i`) y al bajar el
        de venta (`j`); con eso:

        - una fila desde su primer par bajo el umbral, y todas las filas desde
          la primera que empieza bajo el umbral, solo dan motivos `below_threshold`
          de neta menor o igual (y posteriores: en un empate gana el primero);
        - una fila cuya cantidad máxima no llega al lote ni a los mínimos no
          dispara;
        - la neta de un par no pasa de `unit(i, j) × cota de la fila`: las filas
          se recorren de mayor a menor cota y se dejan cuando la cota no llega al
          mejor disparo.
        """
        i_cut = _first(self.n, lambda i: self.below(i, 0))
        short: list[tuple[int, int]] = [(i_cut, 0)] if i_cut < self.n else []
        rows: list[tuple[Decimal, int, int, Decimal]] = []  # (cota, i, jc, cantidad)
        for i in range(i_cut):
            jc = _first(self.m, lambda j, i=i: self.below(i, j))
            if jc < self.m:
                short.append((i, jc))
            cap = self._cap(i, jc)
            if self._can_fire(i, cap):
                unit = self.unit(i, 0)
                rows.append((unit * cap if unit > 0 else ZERO, i, jc, cap))
        rows.sort(key=lambda row: row[0], reverse=True)  # estable: en empate, por i

        best: Fire | None = None
        best_at = (0, 0)
        for bound, i, jc, cap in rows:
            if best is not None and bound < best.net:
                break
            for j in range(jc):
                if best is not None:
                    unit = self.unit(i, j)
                    if (unit * cap if unit > 0 else ZERO) < best.net:
                        break  # las columnas siguientes, con menos neta por unidad
                got = self.pair(i, j)
                if isinstance(got, Fire) and (
                    best is None or got.net > best.net or (got.net == best.net and (i, j) < best_at)
                ):
                    best, best_at = got, (i, j)
        if best is not None:
            return best
        if not short:
            return first
        # el de mayor neta; en un empate, el primero en el orden de los pares
        return self.pair(*max(sorted(short), key=lambda ij: self.net_pct(*ij)))


def inventory_veto(a: VenueView, b: VenueView, fire: Fire, target: InventoryTarget) -> str | None:
    """Por qué la doble empeora el ratio de algún venue (RF-26), o None. Compra
    en `a` y venta en `b`, con la fee taker de cada uno; valorado al mid del
    venue o, sin mid, al precio límite de su pierna."""
    qty = fire.qty
    legs = (
        (fire.buy_venue, a, qty, -qty * fire.buy_price * (1 + a.spec.taker_fee), fire.buy_price),
        (fire.sell_venue, b, -qty, qty * fire.sell_price * (1 - b.spec.taker_fee), fire.sell_price),
    )
    for vid, v, d_base, d_quote, limit in legs:
        price = v.mid if v.mid is not None else limit
        before = base_pct(v.quote_total, v.base_total, price)
        after = base_pct(v.quote_total + d_quote, v.base_total + d_base, price)
        if before is None or after is None:
            continue  # venue sin nada que valorar
        if target.worsens(before, after):
            return (
                f"{vid}: {BASE_LABEL} {before:.1f} % → {after:.1f} %, fuera de "
                f"{target.low}-{target.high} %"
            )
    return None


# -- estados --------------------------------------------------------------------


def arm(state: BotState) -> BotState:
    """Armar a mano: también sale de `halted` (RF-21) y olvida los fallos."""
    return replace(state, status="armed", reason=None, failures=0)


def disarm(state: BotState) -> BotState:
    return replace(state, status="off", reason=None)


def fired(state: BotState, fire: Fire, now: int) -> BotState:
    """Cuenta el disparo al enviarlo, no al llenarse: los topes son de lo enviado."""
    return replace(
        state,
        status="firing",
        fired_at=(*_recent(state, now), now),
        day=now // DAY_MS,
        day_btc=_spent(state, now) + fire.notional,
        last_fire_at=now,
    )


def finished(state: BotState, limits: BotLimits, now: int, *, error: str | None) -> BotState:
    """La doble del disparo terminó. Con `error` (colgada, incierta, rechazo del
    venue, cancelación fallida): `halted` (RF-21). Sin él, a enfriar, salvo que
    ya no quede tope diario (RF-22)."""
    if error is not None:
        return replace(state, status="halted", reason=error)
    if _spent(state, now) >= limits.max_daily_btc:
        return capped(state, limits)
    return replace(state, status="cooldown", failures=0)


def capped(state: BotState, limits: BotLimits) -> BotState:
    """Tope diario agotado (RF-22): `halted`, que solo se rearma a mano. Lo usa
    `finished` y también `BotService` cuando `decide` da `Skip("day_cap")`: el
    tope ya gastado al evaluar (tras rearmar o reiniciar el mismo día) o menos de
    un lote de presupuesto (T-46, D-54)."""
    reason = f"day_cap: {limits.max_daily_btc} BTC de compra hoy"
    return replace(state, status="halted", reason=reason, failures=0)


def rejected(state: BotState, limits: BotLimits, reason: str) -> BotState:
    """La doble no se llegó a enviar (RiskCheck la rechazó): no hay nada abierto,
    así que vuelve a `armed`; con `max_consecutive_failures` seguidos, `halted`."""
    failures = state.failures + 1
    if failures >= limits.max_consecutive_failures:
        msg = f"max_consecutive_failures: {failures} disparos rechazados seguidos ({reason})"
        return replace(state, status="halted", reason=msg, failures=failures)
    return replace(state, status="armed", failures=failures)


def tick(state: BotState, limits: BotLimits, now: int) -> BotState:
    """Paso del tiempo: fin del enfriamiento y olvido de disparos de hace > 1 h."""
    state = replace(state, fired_at=_recent(state, now))
    if (
        state.status == "cooldown"
        and state.last_fire_at is not None
        and now - state.last_fire_at >= limits.cooldown_s * 1000
    ):
        return replace(state, status="armed")
    return state
