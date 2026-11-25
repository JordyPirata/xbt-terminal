"""Validation of a Bitcoin mainnet address typed by the user (RF-37).

Pure, standard library only: checks the checksum of bech32 / bech32m (BIP173,
BIP350) and base58check addresses, any type, so a typo never reaches the explorer.
The balance is read per address, so nothing is derived (D-85).

Spec: RF-37, RNF-25, CA-22 · design.md §6.5 · D-85 · Task: T-107
"""

from __future__ import annotations

import hashlib

_HRP = "bc"
_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_CONSTANT = 1
_BECH32M_CONSTANT = 0x2BC830A3
_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_MAINNET_VERSIONS = (0x00, 0x05)  # P2PKH `1…`, P2SH `3…`


class AddressError(ValueError):
    """Not a valid mainnet address; the message never carries the address (RNF-25)."""


def validate_address(text: str) -> str:
    """The address in its canonical form (bech32 in lowercase), or `AddressError`."""
    text = text.strip()
    if not text:
        raise AddressError("dirección vacía")
    if text.lower().startswith(_HRP + "1"):
        return _validate_segwit(text)
    if text[0] in "13":
        return _validate_base58(text)
    raise AddressError("no es una dirección de Bitcoin (mainnet)")


def _validate_segwit(text: str) -> str:
    if text != text.lower() and text != text.upper():
        raise AddressError("mezcla mayúsculas y minúsculas")
    text = text.lower()
    separator = text.rfind("1")
    payload = text[separator + 1 :]
    if len(text) > 90 or len(payload) < 7 or any(char not in _BECH32_CHARSET for char in payload):
        raise AddressError("bech32 mal formada")
    data = [_BECH32_CHARSET.index(char) for char in payload]
    expanded = [ord(char) >> 5 for char in _HRP] + [0] + [ord(char) & 31 for char in _HRP]
    polymod = _bech32_polymod(expanded + data)
    version, program = data[0], _program(data[1:-6])
    if version > 16:
        raise AddressError("versión de witness no válida")
    expected = _BECH32_CONSTANT if version == 0 else _BECH32M_CONSTANT
    if polymod != expected:
        raise AddressError("checksum incorrecto (dirección mal copiada)")
    if not 2 <= len(program) <= 40 or (version == 0 and len(program) not in (20, 32)):
        raise AddressError("longitud de programa de witness no válida")
    return text


def _program(groups: list[int]) -> bytes:
    """Repack 5-bit groups into bytes; leftover padding must be short and zero."""
    accumulator = bits = 0
    out = bytearray()
    for group in groups:
        accumulator = (accumulator << 5) | group
        bits += 5
        while bits >= 8:
            bits -= 8
            out.append((accumulator >> bits) & 0xFF)
    if bits >= 5 or accumulator & ((1 << bits) - 1):
        raise AddressError("relleno no válido")
    return bytes(out)


def _bech32_polymod(values: list[int]) -> int:
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = (checksum & 0x1FFFFFF) << 5 ^ value
        for i, generator in enumerate(
            (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
        ):
            if (top >> i) & 1:
                checksum ^= generator
    return checksum


def _validate_base58(text: str) -> str:
    number = 0
    for char in text:
        digit = _BASE58.find(char)
        if digit < 0:
            raise AddressError("base58 mal formada")
        number = number * 58 + digit
    zeros = len(text) - len(text.lstrip("1"))
    raw = b"\0" * zeros + number.to_bytes((number.bit_length() + 7) // 8, "big")
    if len(raw) != 25:
        raise AddressError("longitud no válida")
    payload, checksum = raw[:-4], raw[-4:]
    if hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] != checksum:
        raise AddressError("checksum incorrecto (dirección mal copiada)")
    if payload[0] not in _BASE58_MAINNET_VERSIONS:
        raise AddressError("no es de la red principal de Bitcoin")
    return text
