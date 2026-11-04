"""Minimal Engine.IO 4 / Socket.IO 5 text codec (D-09).

Only what Neoxa's public WS uses: open, ping/pong, close, and within messages
connect, disconnect, connect_error and event. No binaries or own acks.

    0{json}                   open (pingInterval, pingTimeout)
    2 / 3                     server ping / client pong
    40/ns,{json}              namespace connect
    42/ns,[id]["event",d]     event (the ack id is ignored)

JSON numbers are read as Decimal (RNF-07): Neoxa sends floats.

Spec: RF-01 · design.md §4 · Task: T-08
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

PONG = "3"

_SIO_KINDS = {"0": "connect", "1": "disconnect", "2": "event", "4": "connect_error"}


class ProtocolError(ValueError):
    """Frame that is not valid Engine.IO/Socket.IO."""


@dataclass(frozen=True, slots=True)
class Packet:
    kind: str  # open | ping | pong | close | noop | connect | disconnect | connect_error | event
    namespace: str = "/"
    event: str | None = None
    data: Any = None


def _json(text: str) -> Any:
    try:
        return json.loads(text, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"JSON inválido: {text[:80]!r}") from exc


def decode(frame: str) -> Packet:
    if not frame:
        raise ProtocolError("frame vacío")
    engine, rest = frame[0], frame[1:]
    if engine == "0":
        return Packet("open", data=_json(rest))
    if engine in "1236" and not rest:
        return Packet({"1": "close", "2": "ping", "3": "pong", "6": "noop"}[engine])
    if engine != "4" or not rest:
        raise ProtocolError(f"frame Engine.IO desconocido: {frame[:40]!r}")

    kind = _SIO_KINDS.get(rest[0])
    if kind is None:
        raise ProtocolError(f"paquete Socket.IO desconocido: {frame[:40]!r}")
    rest = rest[1:]
    namespace = "/"
    if rest.startswith("/"):
        namespace, _, rest = rest.partition(",")
    if kind != "event":
        return Packet(kind, namespace, data=_json(rest) if rest else None)

    payload = _json(rest.lstrip("0123456789"))
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], str):
        raise ProtocolError(f"evento sin nombre: {frame[:40]!r}")
    return Packet("event", namespace, payload[0], payload[1] if len(payload) > 1 else None)


def _prefix(namespace: str) -> str:
    return "" if namespace == "/" else namespace + ","


def connect_frame(namespace: str) -> str:
    return "40" + _prefix(namespace)


def event_frame(namespace: str, event: str, data: Any = None) -> str:
    args = [event] if data is None else [event, data]
    return "42" + _prefix(namespace) + json.dumps(args, separators=(",", ":"))
