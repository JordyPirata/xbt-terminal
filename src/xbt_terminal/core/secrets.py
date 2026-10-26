"""Secret (nunca se imprime) y Redactor para journal y logs.

`Secret` hace que filtrar una key por descuido sea difícil: cualquier forma de
convertirlo en texto da `***`, y no se puede serializar. El valor solo sale con
`.reveal()`, que se llama exclusivamente en el adaptador al firmar.

`Redactor` es la segunda barrera: el journal y los logs pasan su texto final por
él, por si un secreto ya revelado vuelve dentro de una respuesta del exchange.

Spec: RF-12, CA-07 · design.md §8 · Tarea: T-03
"""

from __future__ import annotations

from collections.abc import Iterable

MASK = "***"


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("un Secret no puede estar vacío")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __str__(self) -> str:
        return MASK

    def __repr__(self) -> str:
        return f"Secret({MASK!r})"

    def __format__(self, spec: str) -> str:
        return MASK

    def __reduce__(self):
        raise TypeError("un Secret no se serializa")


class Redactor:
    def __init__(self, secrets: Iterable[Secret]) -> None:
        # el más largo primero, por si un secreto contiene a otro
        self.secrets = tuple(sorted(secrets, key=lambda s: len(s.reveal()), reverse=True))

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret.reveal(), MASK)
        return text
