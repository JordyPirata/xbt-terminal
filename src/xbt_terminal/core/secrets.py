"""Secret (never printed) and Redactor for journal and logs.

`Secret` makes leaking a key by accident hard: any conversion to text gives `***`,
and it cannot be serialized. The value only comes out through `.reveal()`, called
solely in the adapter when signing.

`Redactor` is the second barrier: the journal and logs pass their final text through
it, in case a revealed secret comes back inside an exchange response.

Spec: RF-12, CA-07 · design.md §8 · Task: T-03
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
        # longest first, in case one secret contains another
        self.secrets = tuple(sorted(secrets, key=lambda s: len(s.reveal()), reverse=True))

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret.reveal(), MASK)
        return text
